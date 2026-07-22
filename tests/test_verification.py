"""Offline tests for the deterministic anti-fabrication layer (stdlib only).

Run with either:
    python -m pytest agent_skeleton/tests/test_verification.py -q
    python tests/test_verification.py
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

from agent_skeleton.verification import ProvenanceLedger, verify_report


def _fake_resolver(doi, claimed_title=None, claimed_year=None):
    """Only 10.1000/real resolves; anything else is a fabrication."""
    d = (doi or "").lower()
    if d == "10.1000/real":
        return {"ok": True, "doi": d, "resolves": True, "verified": True,
                "title": "Real Paper", "year": 2020, "title_match": 1.0}
    return {"ok": True, "doi": d, "resolves": False, "verified": False,
            "reason": "DOI does not resolve on Crossref"}


def test_ledger_records_search_results():
    led = ProvenanceLedger()
    led.add_from_tool("search_literature", {"ok": True, "results": [
        {"doi": "10.1000/Real", "pmid": "123", "title": "Real Paper"},
    ]})
    assert led.has_doi("https://doi.org/10.1000/real")   # normalized match
    assert led.has_id({"pmid": "123"})
    assert not led.has_doi("10.1000/fake")


def test_fabricated_citation_is_dropped_and_claim_downgraded():
    led = ProvenanceLedger()
    led.add_from_tool("search_literature", {"ok": True, "results": [
        {"doi": "10.1000/real", "title": "Real Paper", "year": "2020", "pmid": "123"},
    ]})
    report = {
        "report_markdown": "Claim A holds [S1]; Claim B holds [S2].",
        "claims": [
            {"claim": "A", "verdict": "supported", "confidence": "high",
             "source_ids": ["S1"], "supporting_quote": "real quote"},
            {"claim": "B", "verdict": "supported", "confidence": "high",
             "source_ids": ["S2"], "supporting_quote": "invented quote"},
        ],
        "sources": [
            {"id": "S1", "doi": "10.1000/real", "title": "Real Paper", "year": "2020"},
            {"id": "S2", "doi": "10.1000/fake", "title": "Fabricated Paper", "year": "2021"},
        ],
        "limitations": ["test"],
        "overall_confidence": "high",
    }
    out = verify_report(report, led, resolver=_fake_resolver)

    ids_verified = {s["id"] for s in out["sources"]}
    ids_unverified = {s["id"] for s in out["unverified_sources"]}
    assert ids_verified == {"S1"}
    assert ids_unverified == {"S2"}

    by_claim = {c["claim"]: c for c in out["claims"]}
    assert by_claim["A"]["verdict"] == "supported"
    assert by_claim["A"]["source_ids"] == ["S1"]
    # The fabricated-only claim must be downgraded, not asserted.
    assert by_claim["B"]["verdict"] == "source_not_found"
    assert by_claim["B"]["source_ids"] == []

    assert out["overall_confidence"] == "medium"   # downgraded from high after adjustment
    assert out["verification_summary"]["sources_verified"] == 1
    assert out["verification_summary"]["sources_unverified"] == 1
    # The answer must surface the fabricated source as unverified and cite the real one.
    assert "Unverified" in out["answer"]
    assert "10.1000/real" in out["answer"]
    verified_section = out["answer"].split("### Verified sources")[-1]
    assert "10.1000/fake" not in verified_section   # never presented as a real citation


def test_crossref_resolvable_source_not_in_ledger_is_kept():
    led = ProvenanceLedger()  # empty ledger — model cites a DOI it "knows"
    report = {
        "report_markdown": "Claim [S1].",
        "claims": [{"claim": "A", "verdict": "supported", "confidence": "medium",
                    "source_ids": ["S1"], "supporting_quote": "q"}],
        "sources": [{"id": "S1", "doi": "10.1000/real", "title": "Real Paper", "year": "2020"}],
        "overall_confidence": "medium",
    }
    out = verify_report(report, led, resolver=_fake_resolver)
    assert {s["id"] for s in out["sources"]} == {"S1"}
    assert out["sources"][0]["verification"] == "crossref-resolved"
    assert out["claims"][0]["verdict"] == "supported"


def _integrity_resolver(doi, claimed_title=None, claimed_year=None):
    """Like _fake_resolver, but 10.1000/retracted resolves yet is retracted."""
    d = (doi or "").lower()
    if d == "10.1000/real":
        return {"ok": True, "doi": d, "resolves": True, "verified": True,
                "title": "Real Paper", "year": 2020, "title_match": 1.0,
                "integrity_status": "ok", "integrity_detail": None}
    if d == "10.1000/retracted":
        return {"ok": True, "doi": d, "resolves": True, "verified": True,
                "title": "Retracted Paper", "year": 2012, "title_match": 1.0,
                "integrity_status": "retracted",
                "integrity_detail": "Retraction (2013) — notice 10.1/notice"}
    return {"ok": True, "doi": d, "resolves": False, "verified": False,
            "reason": "DOI does not resolve on Crossref"}


def test_retracted_source_is_excluded_and_claim_downgraded():
    led = ProvenanceLedger()
    # The model *retrieved* the retracted paper via a tool — it is in the ledger …
    led.add_from_tool("search_literature", {"ok": True, "results": [
        {"doi": "10.1000/retracted", "title": "Retracted Paper", "year": "2012"},
    ]})
    report = {
        "report_markdown": "Claim C holds [S1].",
        "claims": [{"claim": "C", "verdict": "supported", "confidence": "high",
                    "source_ids": ["S1"], "supporting_quote": "q"}],
        "sources": [{"id": "S1", "doi": "10.1000/retracted", "title": "Retracted Paper", "year": "2012"}],
        "overall_confidence": "high",
    }
    out = verify_report(report, led, resolver=_integrity_resolver)
    # … but the retraction moves it out of support entirely.
    assert {s["id"] for s in out["sources"]} == set()
    assert out["unverified_sources"][0]["verification"] == "retracted"
    assert out["unverified_sources"][0]["integrity_status"] == "retracted"
    assert out["claims"][0]["verdict"] == "source_not_found"
    assert out["verification_summary"]["sources_retracted"] == 1
    assert "Retracted" in out["answer"]


def test_supporting_quote_grounding_flags_invented_quote():
    led = ProvenanceLedger()
    led.add_from_tool("search_literature", {"ok": True, "results": [
        {"doi": "10.1000/real", "title": "Real Paper", "year": "2020",
         "abstract": "Metformin reduced fasting glucose by twenty percent in the trial cohort."},
    ]})
    report = {
        "report_markdown": "Two claims.",
        "claims": [
            {"claim": "grounded", "verdict": "supported", "confidence": "medium", "source_ids": ["S1"],
             "supporting_quote": "Metformin reduced fasting glucose by twenty percent"},
            {"claim": "invented", "verdict": "supported", "confidence": "medium", "source_ids": ["S1"],
             "supporting_quote": "The compound eliminated the tumor in every single participant outright."},
        ],
        "sources": [{"id": "S1", "doi": "10.1000/real", "title": "Real Paper", "year": "2020"}],
        "overall_confidence": "medium",
    }
    out = verify_report(report, led, resolver=_integrity_resolver)
    by = {c["claim"]: c for c in out["claims"]}
    assert by["grounded"].get("quote_grounded") is True
    assert by["invented"].get("quote_grounded") is False
    assert "could not be located" in by["invented"].get("verification_note", "")


def test_empty_report_yields_answer_and_no_sources():
    out = verify_report({}, ProvenanceLedger(), resolver=_fake_resolver)
    assert "answer" in out
    assert out["sources"] == []
    assert out["claims"] == []


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
