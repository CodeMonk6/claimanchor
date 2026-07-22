"""ClaimAnchor — a biomedical claim & citation verification agent (A2A Path B).

Subclass of ``AgentHandler``: the starter template's frozen ``HandlerExecutor`` wraps
this in the A2A protocol (heartbeat, runtime cap, dual-channel output, credentials),
so we implement only ``handle_structured``.

Design (see the repo README for the full write-up):
  * Claude Opus 4.8 runs a tool loop over free scholarly APIs (Europe PMC, Crossref,
    Unpaywall) — the model may cite ONLY what those tools return.
  * The model ends by calling the terminal ``submit_report`` tool with a structured
    evidence graph.
  * ``verification.verify_report`` then checks every citation, in code, against a
    provenance ledger + live Crossref resolution and strips anything unverifiable —
    so fabricated citations cannot reach the answer.

The Anthropic SDK is imported lazily so this module (and the offline test suite +
``serve check``) import with the standard library alone.
"""
from __future__ import annotations

import asyncio
import json
import os
from typing import Any, Callable

from agent_skeleton import config as _config
from agent_skeleton.base import AgentHandler, FileInput
from agent_skeleton.prompts_biomed import ALL_TOOL_SCHEMAS, SUBMIT_TOOL_NAME, SYSTEM_PROMPT
from agent_skeleton.research_tools import TOOL_FUNCTIONS, verify_doi
from agent_skeleton.verification import ProvenanceLedger, verify_report

DEFAULT_MAX_TOKENS = 16000
DEFAULT_MAX_STEPS = 8           # tool-loop rounds before we stop
DEFAULT_EFFORT = "high"
MAX_TOOL_RESULT_CHARS = 30000   # cap serialized tool output fed back to the model


def _default_client_factory(api_key: str):
    """Build a real AsyncAnthropic client (lazy import so stdlib-only paths work)."""
    import anthropic  # noqa: PLC0415 — deliberately lazy

    kwargs: dict[str, Any] = {"api_key": api_key}
    base_url = os.getenv("ANTHROPIC_BASE_URL")
    if base_url:
        kwargs["base_url"] = base_url
    return anthropic.AsyncAnthropic(**kwargs)


class ClaimAnchorHandler(AgentHandler):
    """Verification-first biomedical research agent."""

    def __init__(self, config: dict | None = None):
        config = config or {}
        super().__init__(config)
        self.model: str = config.get("model") or _config.env_model()
        self.max_tokens: int = int(config.get("max_tokens", DEFAULT_MAX_TOKENS))
        self.max_steps: int = int(config.get("max_steps", DEFAULT_MAX_STEPS))
        self.effort: str = config.get("effort", DEFAULT_EFFORT)
        # Injection seams for tests (default to the real client / real tools).
        self._client_factory: Callable[[str], Any] = config.get("client_factory") or _default_client_factory
        self._tool_functions: dict[str, Callable[..., dict]] = config.get("tool_functions") or TOOL_FUNCTIONS
        self._resolver: Callable[..., dict] = config.get("resolver") or verify_doi

    # -- Public entry point --------------------------------------------------
    async def handle_structured(
        self,
        user_input: str,
        files: list[FileInput] = [],
        context: dict | None = None,
    ) -> dict:
        prompt = self._compose_input(user_input, files)
        if not prompt.strip():
            return self._need_input()

        api_key = self._resolve_api_key(context)
        # Real client needs a key; an injected factory (tests) may not.
        if self._client_factory is _default_client_factory and not api_key:
            return self._no_key()

        try:
            client = self._client_factory(api_key or "")
        except Exception as exc:  # e.g. anthropic not installed at deploy time
            return {
                "answer": f"ClaimAnchor could not initialize its language model: {exc}",
                "error": "client_init_failed",
                "claims": [], "sources": [],
            }

        ledger = ProvenanceLedger()
        try:
            final_report, fallback_text, refused = await self._run_tool_loop(client, prompt, ledger)
        except Exception as exc:  # never crash the A2A task — report cleanly
            return {
                "answer": f"ClaimAnchor hit an error while researching: {exc}",
                "error": "loop_failed",
                "claims": [], "sources": [],
            }

        if refused:
            return {
                "answer": ("I can't help with that request. ClaimAnchor verifies biomedical "
                           "research claims and citations against the published literature."),
                "refused": True, "claims": [], "sources": [],
            }

        if final_report is None:
            # The model never submitted a structured report — return its text WITHOUT
            # inventing any citations, plus the honest limitation.
            return {
                "answer": (fallback_text or "ClaimAnchor could not complete a verified answer.")
                + "\n\n_Note: no verified citations were produced for this request._",
                "claims": [], "sources": [], "unverified_sources": [],
                "overall_confidence": "low",
            }

        # Deterministic, code-side verification (runs blocking HTTP → use a thread).
        result = await asyncio.to_thread(verify_report, final_report, ledger, self._resolver)
        return result

    # -- Tool loop -----------------------------------------------------------
    async def _run_tool_loop(self, client, prompt: str, ledger: ProvenanceLedger):
        """Returns (final_report | None, fallback_text | None, refused: bool)."""
        messages: list[dict] = [{"role": "user", "content": prompt}]
        for _ in range(self.max_steps):
            resp = await client.messages.create(**self._create_kwargs(messages))
            content = list(getattr(resp, "content", None) or [])
            stop = getattr(resp, "stop_reason", None)

            tool_uses = [b for b in content if getattr(b, "type", None) == "tool_use"]
            submit = next((b for b in tool_uses if getattr(b, "name", None) == SUBMIT_TOOL_NAME), None)
            if submit is not None:
                return dict(getattr(submit, "input", {}) or {}), None, False

            if stop == "refusal":
                return None, None, True

            if tool_uses:
                messages.append({"role": "assistant", "content": content})
                results = []
                for b in tool_uses:
                    out = await self._dispatch(getattr(b, "name", ""), getattr(b, "input", {}) or {})
                    ledger.add_from_tool(getattr(b, "name", ""), out)
                    payload = json.dumps(out, default=str)[:MAX_TOOL_RESULT_CHARS]
                    results.append({"type": "tool_result", "tool_use_id": getattr(b, "id", ""), "content": payload})
                messages.append({"role": "user", "content": results})
                continue

            # No tool call and not a submit — the model answered in prose. Return it as
            # a fallback (verification below will add no citations it didn't verify).
            return None, _extract_text(content), False

        return None, "Reached the maximum number of research steps before finishing.", False

    def _create_kwargs(self, messages: list[dict]) -> dict:
        kwargs = {
            "model": self.model,
            "max_tokens": self.max_tokens,
            "system": SYSTEM_PROMPT,
            "tools": ALL_TOOL_SCHEMAS,
            "messages": messages,
        }
        # Adaptive thinking + effort are GA on Opus 4.8 and improve verification rigor.
        if str(self.model).startswith("claude-"):
            kwargs["thinking"] = {"type": "adaptive"}
            kwargs["output_config"] = {"effort": self.effort}
        return kwargs

    async def _dispatch(self, name: str, tool_input: dict) -> dict:
        fn = self._tool_functions.get(name)
        if fn is None:
            return {"ok": False, "error": f"unknown tool: {name}"}
        try:
            return await asyncio.to_thread(fn, **tool_input)
        except TypeError as exc:  # bad args from the model
            return {"ok": False, "error": f"bad arguments for {name}: {exc}"}
        except Exception as exc:  # a tool must never crash the loop
            return {"ok": False, "error": f"{name} failed: {exc}"}

    # -- Helpers -------------------------------------------------------------
    def _compose_input(self, user_input: str, files: list[FileInput]) -> str:
        parts = [user_input or ""]
        for f in files or []:
            mime = (f.mime_type or "").lower()
            name = (f.name or "").lower()
            if mime.startswith("text/") or name.endswith((".txt", ".md", ".markdown", ".bib")):
                try:
                    text = f.bytes.decode("utf-8", errors="replace")
                except Exception:
                    continue
                parts.append(f"\n\n--- Attached document ({f.name or 'uploaded'}) ---\n{text}")
            else:
                parts.append(
                    f"\n\n[Attached file '{f.name or 'file'}' ({mime or 'unknown type'}) could not be "
                    "read as text; ClaimAnchor only reads plain-text/markdown documents.]"
                )
        return "\n".join(parts)

    def _resolve_api_key(self, context: dict | None) -> str | None:
        # config injection (tests) > per-user credential (A2A) > environment.
        if self.config.get("api_key"):
            return self.config["api_key"]
        creds = (context or {}).get("credentials", {}) or {}
        cred = creds.get("anthropic_api_key") or {}
        return cred.get("api_key") or os.getenv("ANTHROPIC_API_KEY")

    @staticmethod
    def _need_input() -> dict:
        return {
            "answer": (
                "Please give me something to verify. ClaimAnchor can:\n"
                "• check a factual claim (e.g. \"Statins reduce cardiovascular mortality in "
                "primary prevention\"),\n"
                "• validate a reference list or DOIs against the real literature, or\n"
                "• answer a biomedical question using only verified citations.\n\n"
                "Paste a claim, a paragraph with citations, or a list of DOIs."
            ),
            "input_required": True,
            "claims": [], "sources": [],
        }

    @staticmethod
    def _no_key() -> dict:
        return {
            "answer": (
                "ClaimAnchor is not configured with a language-model credential. Provide an "
                "ANTHROPIC_API_KEY (via the deployment's credential configuration or the "
                "ANTHROPIC_API_KEY environment variable) and try again."
            ),
            "error": "missing_credential",
            "claims": [], "sources": [],
        }


def _extract_text(content: list) -> str:
    return "\n".join(
        getattr(b, "text", "") for b in content if getattr(b, "type", None) == "text"
    ).strip()
