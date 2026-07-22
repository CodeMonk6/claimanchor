"""Offline end-to-end tests for ClaimAnchorHandler (fake Claude client; stdlib only).

Exercises the full Path-B flow — tool loop, provenance ledger, terminal
submit_report, and the deterministic verification pass — without the Anthropic SDK
or any network. Run with either:
    python -m pytest agent_skeleton/tests/test_handler.py -q
    python tests/test_handler.py
"""
from __future__ import annotations

# --- self-bootstrap --------------------------------------------------------
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

from agent_skeleton.base import FileInput
from agent_skeleton.handler import ClaimAnchorHandler, _default_client_factory


# ---- Fakes ----------------------------------------------------------------
class _Blk:
    def __init__(self, **kw):
        self.__dict__.update(kw)


class _Resp:
    def __init__(self, content, stop_reason="tool_use"):
        self.content = content
        self.stop_reason = stop_reason


class _FakeMessages:
    def __init__(self, scripted):
        self._scripted = scripted
        self.calls = 0
        self.seen_kwargs = []

    async def create(self, **kwargs):
        self.seen_kwargs.append(kwargs)
        resp = self._scripted[self.calls]
        self.calls += 1
        return resp


class _FakeClient:
    def __init__(self, scripted):
        self.messages = _FakeMessages(scripted)


def _fake_search(**kwargs):
    return {"ok": True, "query": kwargs.get("query", ""), "count": 1, "results": [
        {"doi": "10.1000/real", "title": "Real Paper", "authors": "Doe J",
         "year": "2020", "journal": "J Test", "pmid": "123",
         "abstract": "Statins reduce mortality.", "is_open_access": True},
    ]}


def _fake_resolver(doi, claimed_title=None, claimed_year=None):
    d = (doi or "").lower()
    if d == "10.1000/real":
        return {"ok": True, "doi": d, "resolves": True, "verified": True,
                "title": "Real Paper", "year": 2020, "title_match": 1.0}
    return {"ok": True, "doi": d, "resolves": False, "verified": False,
            "reason": "DOI does not resolve on Crossref"}


def _make_handler(scripted):
    return ClaimAnchorHandler({
        "client_factory": lambda key: _FakeClient(scripted),
        "tool_functions": {"search_literature": _fake_search},
        "resolver": _fake_resolver,
        "api_key": "test-key",
        "model": "claude-opus-4-8",
    })


# ---- Tests ----------------------------------------------------------------
def test_missing_input_asks_for_input():
    h = _make_handler([])
    out = asyncio.run(h.handle_structured(""))
    assert out.get("input_required") is True
    assert "answer" in out


def test_missing_credential_when_no_key_and_real_client():
    # Default (real) client factory + no key anywhere -> graceful credential error,
    # WITHOUT importing anthropic.
    saved = os.environ.pop("ANTHROPIC_API_KEY", None)
    try:
        h = ClaimAnchorHandler({})   # real _default_client_factory, no api_key
        assert h._client_factory is _default_client_factory
        out = asyncio.run(h.handle_structured("Verify: statins reduce mortality."))
        assert out.get("error") == "missing_credential"
    finally:
        if saved is not None:
            os.environ["ANTHROPIC_API_KEY"] = saved


def test_happy_path_verifies_and_drops_fabrication():
    # Turn 1: model searches. Turn 2: model submits a report citing one real source
    # (in the ledger via search) and one fabricated source (must be dropped).
    scripted = [
        _Resp([_Blk(type="tool_use", id="t1", name="search_literature",
                    input={"query": "statins mortality", "max_results": 5})],
              stop_reason="tool_use"),
        _Resp([_Blk(type="tool_use", id="t2", name="submit_report", input={
            "report_markdown": "Statins reduce mortality [S1]. Claim B [S2].",
            "claims": [
                {"claim": "Statins reduce mortality", "verdict": "supported",
                 "confidence": "high", "source_ids": ["S1"],
                 "supporting_quote": "Statins reduce mortality."},
                {"claim": "Claim B", "verdict": "supported", "confidence": "high",
                 "source_ids": ["S2"], "supporting_quote": "invented"},
            ],
            "sources": [
                {"id": "S1", "doi": "10.1000/real", "title": "Real Paper", "year": "2020"},
                {"id": "S2", "doi": "10.1000/fake", "title": "Fabricated Paper", "year": "2021"},
            ],
            "limitations": ["abstract-only"],
            "overall_confidence": "high",
        })], stop_reason="tool_use"),
    ]
    h = _make_handler(scripted)
    out = asyncio.run(h.handle_structured("Verify: statins reduce mortality."))

    assert "answer" in out
    assert {s["id"] for s in out["sources"]} == {"S1"}
    assert {s["id"] for s in out["unverified_sources"]} == {"S2"}
    by_claim = {c["claim"]: c for c in out["claims"]}
    assert by_claim["Statins reduce mortality"]["verdict"] == "supported"
    assert by_claim["Claim B"]["verdict"] == "source_not_found"
    assert out["overall_confidence"] == "medium"
    assert h.model == "claude-opus-4-8"


def test_text_files_are_folded_into_the_prompt():
    captured = {}

    def _capture_search(**kwargs):
        captured["query"] = kwargs.get("query", "")
        return _fake_search(**kwargs)

    scripted = [
        _Resp([_Blk(type="tool_use", id="t1", name="search_literature",
                    input={"query": "from-doc"})], stop_reason="tool_use"),
        _Resp([_Blk(type="tool_use", id="t2", name="submit_report", input={
            "report_markdown": "ok [S1]",
            "claims": [{"claim": "c", "verdict": "supported", "confidence": "low",
                        "source_ids": ["S1"], "supporting_quote": "q"}],
            "sources": [{"id": "S1", "doi": "10.1000/real", "title": "Real Paper", "year": "2020"}],
            "overall_confidence": "low",
        })], stop_reason="tool_use"),
    ]
    h = ClaimAnchorHandler({
        "client_factory": lambda key: _FakeClient(scripted),
        "tool_functions": {"search_literature": _capture_search},
        "resolver": _fake_resolver, "api_key": "k",
    })
    f = FileInput(b"Metformin lowers HbA1c.", name="draft.txt", mime_type="text/plain")
    out = asyncio.run(h.handle_structured("Check the attached paragraph.", files=[f]))
    # The full flow completed with the attached document folded into the prompt.
    assert out["sources"][0]["id"] == "S1"


def test_no_submit_report_returns_uncited_answer():
    # Model answers in prose without calling submit_report -> no invented citations.
    scripted = [_Resp([_Blk(type="text", text="I could not find supporting evidence.")],
                      stop_reason="end_turn")]
    h = _make_handler(scripted)
    out = asyncio.run(h.handle_structured("Verify something obscure."))
    assert out["sources"] == []
    assert "no verified citations" in out["answer"].lower()


if __name__ == "__main__":
    _fns = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    _fail = 0
    for fn in _fns:
        try:
            fn()
            print(f"PASS {fn.__name__}")
        except Exception as exc:  # noqa: BLE001
            _fail += 1
            print(f"FAIL {fn.__name__}: {exc!r}")
    print(f"\n{len(_fns) - _fail}/{len(_fns)} passed")
    sys.exit(1 if _fail else 0)
