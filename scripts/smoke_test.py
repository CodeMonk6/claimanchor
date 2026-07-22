#!/usr/bin/env python3
"""Live end-to-end smoke test for ClaimAnchor.

Run this on a machine with network access (and, for Part B, a real
ANTHROPIC_API_KEY + the `anthropic` package installed) to verify the agent works
against the real scholarly APIs and the real Claude model — the one thing the
offline unit tests can't cover.

    pip install -e .            # or at least: pip install anthropic
    export ANTHROPIC_API_KEY=…
    export UNPAYWALL_EMAIL=you@wustl.edu
    python scripts/smoke_test.py

Part A (network only, no key) exercises the scholarly-API clients.
Part B (needs a key) runs the full agent: claim verification + fabricated-DOI
detection. Part B is SKIPPED (not failed) if no key / no `anthropic`.
"""
from __future__ import annotations

# --- make `agent_skeleton` importable without an editable install ----------
import pathlib
import sys
import types

_ROOT = pathlib.Path(__file__).resolve().parent.parent
if "agent_skeleton" not in sys.modules:
    try:
        import agent_skeleton  # noqa: F401
    except Exception:
        _pkg = types.ModuleType("agent_skeleton")
        _pkg.__path__ = [str(_ROOT)]
        sys.modules["agent_skeleton"] = _pkg
# ---------------------------------------------------------------------------

import asyncio
import os

from agent_skeleton import research_tools as rt

# A rock-stable, always-resolvable DOI (Watson & Crick, Nature 1953) and a clear fake.
REAL_DOI = "10.1038/171737a0"
REAL_TITLE = "Molecular Structure of Nucleic Acids"
FAKE_DOI = "10.9999/this.doi.is.fabricated.000"

_results: list[tuple[str, str]] = []


def _record(status: str, name: str, detail: str = "") -> None:
    mark = {"PASS": "✅", "FAIL": "❌", "SKIP": "⏭ "}.get(status, "  ")
    print(f"{mark} {status:4} {name}" + (f" — {detail}" if detail else ""))
    _results.append((status, name))


def check(name: str, fn) -> None:
    try:
        ok, detail = fn()
        _record("PASS" if ok else "FAIL", name, detail)
    except Exception as exc:  # noqa: BLE001
        _record("FAIL", name, f"raised {exc!r}")


# --- Part A: live scholarly APIs (no key) ---------------------------------
def part_a() -> None:
    print("\n=== Part A: scholarly APIs (network, no key) ===")

    def _search():
        out = rt.search_literature("statins cardiovascular mortality", max_results=5)
        n = out.get("count", 0)
        with_doi = sum(1 for r in out.get("results", []) if r.get("doi"))
        return out.get("ok") and n > 0, f"{n} results, {with_doi} with DOIs"

    def _verify_real():
        out = rt.verify_doi(REAL_DOI, claimed_title=REAL_TITLE)
        return out.get("resolves") and out.get("verified"), f"title={out.get('title')!r}"

    def _verify_fake():
        out = rt.verify_doi(FAKE_DOI)
        return out.get("ok") and out.get("resolves") is False, "correctly does not resolve"

    def _oa():
        out = rt.find_open_access(REAL_DOI)
        return out.get("ok"), f"is_oa={out.get('is_oa')}"

    def _pubmed():
        out = rt.search_pubmed("CRISPR gene editing", max_results=5)
        return out.get("ok") and out.get("count", 0) > 0, f"{out.get('count', 0)} results"

    def _openalex():
        out = rt.openalex_lookup(REAL_DOI)
        if not out.get("ok"):
            # metered/keyless failure is acceptable — the agent degrades to Crossref
            _record("SKIP", "openalex_lookup", out.get("error", "unavailable"))
            return None
        return out.get("found"), f"cited_by={out.get('record', {}).get('citation_count')}"

    check("search_literature (Europe PMC)", _search)
    check("verify_doi resolves real DOI", _verify_real)
    check("verify_doi rejects fake DOI", _verify_fake)
    check("find_open_access (Unpaywall)", _oa)
    check("search_pubmed (NCBI)", _pubmed)
    # openalex handles its own SKIP:
    res = _openalex()
    if res is not None:
        _record("PASS" if res[0] else "FAIL", "openalex_lookup", res[1])


# --- Part B: full agent (needs a key) -------------------------------------
def part_b() -> None:
    print("\n=== Part B: full agent (Claude) ===")
    if not os.getenv("ANTHROPIC_API_KEY"):
        _record("SKIP", "agent run", "set ANTHROPIC_API_KEY to run Part B")
        return
    try:
        import anthropic  # noqa: F401
    except Exception:
        _record("SKIP", "agent run", "`pip install anthropic` to run Part B")
        return

    from agent_skeleton.handler import ClaimAnchorHandler

    handler = ClaimAnchorHandler({})

    async def _run(text: str) -> dict:
        return await handler.handle_structured(text)

    # 1) Claim verification against the real literature.
    def _claim():
        out = asyncio.run(_run(
            "Verify this claim: 'Statins reduce cardiovascular mortality in secondary prevention.'"
        ))
        print("\n--- agent answer (claim verification) ---")
        print(out.get("answer", "")[:1200])
        print("--- end ---")
        has_answer = bool(out.get("answer"))
        # Either it found verified support, or it honestly abstained — both are valid;
        # what must NOT happen is unverified sources leaking into `sources`.
        clean = all(s.get("verification") for s in out.get("sources", []))
        return has_answer and clean, (
            f"{len(out.get('sources', []))} verified sources, "
            f"{len(out.get('unverified_sources', []))} unverified"
        )

    # 2) Fabricated-citation detection.
    def _fabrication():
        out = asyncio.run(_run(
            f"Validate these DOIs against the literature: {FAKE_DOI} and {REAL_DOI}."
        ))
        unverified = out.get("unverified_sources", [])
        flagged = any(FAKE_DOI.lower() in (s.get("doi") or "").lower() for s in unverified)
        # The fabricated DOI must never appear as a verified source.
        leaked = any(FAKE_DOI.lower() in (s.get("doi") or "").lower() for s in out.get("sources", []))
        return flagged and not leaked, (
            "fake DOI flagged as unverified" if flagged else "fake DOI NOT flagged (investigate)"
        )

    check("claim verification end-to-end", _claim)
    check("fabricated DOI is flagged, not cited", _fabrication)


def main() -> int:
    print("ClaimAnchor live smoke test")
    print(f"Contact email for polite APIs: {rt._contact_email()}")
    part_a()
    part_b()

    passed = sum(1 for s, _ in _results if s == "PASS")
    failed = sum(1 for s, _ in _results if s == "FAIL")
    skipped = sum(1 for s, _ in _results if s == "SKIP")
    print(f"\n{passed} passed, {failed} failed, {skipped} skipped")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
