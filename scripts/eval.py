#!/usr/bin/env python3
"""Reproducible, key-free evaluation of ClaimAnchor's verification layer.

This measures the part of ClaimAnchor that must never be wrong: the deterministic
anti-fabrication layer (``verification.verify_report``). It runs a fixed gold set of
citations — real, fabricated, title-hijacked, and retracted — through the verifier
with an EMPTY provenance ledger, which forces the live Crossref + Retraction Watch
checks to decide each case on their own. No language model and no API key are
involved; Crossref is keyless.

Each fixture is turned into a one-claim, one-source report asserting the claim is
``supported``. A correct verifier must:
  * keep a real, resolvable, correctly-cited paper  -> outcome "verified"
  * drop a fabricated DOI or a title-hijacked DOI    -> outcome "dropped"
  * exclude a retracted paper from support            -> outcome "retracted"
…and downgrade the dependent claim whenever the source is not kept.

Usage:
    python scripts/eval.py            # live Crossref (network, no key)
    python scripts/eval.py --offline  # bundled deterministic stub (no network)
"""
from __future__ import annotations

# --- self-bootstrap so `agent_skeleton` imports without an editable install ---
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

from agent_skeleton.research_tools import _title_similarity, normalize_doi, verify_doi
from agent_skeleton.verification import ProvenanceLedger, verify_report

# Gold set. `title` is omitted for cases where we want to isolate resolution/integrity
# (a title is only supplied for the hijack case, where it is deliberately wrong).
FIXTURES = [
    {"label": "real: Watson & Crick 1953", "doi": "10.1038/171737a0", "expect": "verified"},
    {"label": "real: SARS-CoV-2 genome (has addendum, not blocking)",
     "doi": "10.1038/s41586-020-2012-7", "expect": "verified"},
    {"label": "retracted: Wakefield MMR", "doi": "10.1016/S0140-6736(97)11096-0", "expect": "retracted"},
    {"label": "retracted: Surgisphere/Lancet HCQ", "doi": "10.1016/S0140-6736(20)31180-6", "expect": "retracted"},
    {"label": "retracted: Surgisphere/NEJM", "doi": "10.1056/NEJMoa2007621", "expect": "retracted"},
    {"label": "fabricated DOI (nonexistent registrant)", "doi": "10.9999/fabricated.claimanchor.001", "expect": "dropped"},
    {"label": "fabricated DOI (bad suffix)", "doi": "10.1234/nonexistent.99999.zzz", "expect": "dropped"},
    {"label": "title-hijacked (real DOI, wrong title)", "doi": "10.1136/bmj.39489.470347.AD",
     "title": "Deep reinforcement learning for humanoid locomotion", "expect": "dropped"},
]

# Canned Crossref titles/integrity for the deterministic --offline resolver. Mirrors
# what live Crossref returns for the same DOIs (verified against the API when authored).
_STUB = {
    "10.1038/171737a0": {"title": "Molecular Structure of Nucleic Acids", "integrity_status": "ok"},
    "10.1038/s41586-020-2012-7": {"title": "A pneumonia outbreak associated with a new coronavirus", "integrity_status": "ok"},
    "10.1016/s0140-6736(97)11096-0": {"title": "RETRACTED: Ileal-lymphoid-nodular hyperplasia", "integrity_status": "retracted", "integrity_detail": "Retraction (2010) — notice 10.1016/s0140-6736(10)60175-4"},
    "10.1016/s0140-6736(20)31180-6": {"title": "RETRACTED: Hydroxychloroquine or chloroquine", "integrity_status": "retracted", "integrity_detail": "Retraction (2020)"},
    "10.1056/nejmoa2007621": {"title": "RETRACTED: Cardiovascular Disease, Drug Therapy, and Mortality", "integrity_status": "retracted", "integrity_detail": "Retraction (2020)"},
    "10.1136/bmj.39489.470347.ad": {"title": "GRADE: an emerging consensus on rating quality of evidence", "integrity_status": "ok"},
}


def _stub_resolver(doi, claimed_title=None, claimed_year=None):
    d = normalize_doi(doi)
    rec = _STUB.get(d)
    if not rec:
        return {"ok": True, "doi": d, "resolves": False, "verified": False,
                "reason": "DOI does not resolve on Crossref",
                "integrity_status": "ok", "integrity_detail": None}
    sim = _title_similarity(claimed_title, rec["title"]) if claimed_title else None
    verified = True if claimed_title is None else (sim or 0.0) >= 0.35
    return {"ok": True, "doi": d, "resolves": True, "verified": verified,
            "title": rec["title"], "title_match": sim,
            "integrity_status": rec["integrity_status"],
            "integrity_detail": rec.get("integrity_detail")}


def _outcome(out: dict) -> str:
    if any(s.get("id") == "S1" for s in out.get("sources", [])):
        return "verified"
    for s in out.get("unverified_sources", []):
        if s.get("id") == "S1" and s.get("verification") == "retracted":
            return "retracted"
    return "dropped"


def run(resolver) -> int:
    rows = []
    tp = fp = fn = tn = correct = 0
    for fx in FIXTURES:
        src = {"id": "S1", "doi": fx["doi"]}
        if fx.get("title"):
            src["title"] = fx["title"]
        report = {
            "report_markdown": f"Claim about {fx['doi']} [S1].",
            "claims": [{"claim": fx["label"], "verdict": "supported", "confidence": "high",
                        "source_ids": ["S1"], "supporting_quote": ""}],
            "sources": [src],
            "overall_confidence": "high",
        }
        out = verify_report(report, ProvenanceLedger(), resolver=resolver)
        got = _outcome(out)
        verdict = out["claims"][0]["verdict"]
        must_flag = fx["expect"] in ("dropped", "retracted")
        flagged = got in ("dropped", "retracted")
        ok = (got == fx["expect"])
        correct += ok
        if flagged and must_flag:
            tp += 1
        elif flagged and not must_flag:
            fp += 1
        elif not flagged and must_flag:
            fn += 1
        else:
            tn += 1
        rows.append((fx["label"], fx["expect"], got, verdict, "PASS" if ok else "FAIL"))

    n = len(FIXTURES)
    precision = tp / (tp + fp) if (tp + fp) else 1.0
    recall = tp / (tp + fn) if (tp + fn) else 1.0

    print(f"\n{'fixture':<48}{'expected':<11}{'got':<11}{'claim verdict':<18}result")
    print("-" * 96)
    for label, exp, got, verdict, res in rows:
        print(f"{label:<48}{exp:<11}{got:<11}{verdict:<18}{res}")
    print("-" * 96)
    print(f"exact-match accuracy : {correct}/{n} = {correct / n:.0%}")
    print(f"bad-citation recall  : {recall:.0%}   (fraction of fabricated/hijacked/retracted correctly excluded)")
    print(f"kept-source precision: {precision:.0%}   (fraction of kept sources that were genuinely valid)")
    print(f"confusion            : TP={tp} FP={fp} FN={fn} TN={tn}")
    if fn:
        print("\nFAIL: a fabricated/retracted citation was accepted as support (FN>0).")
        return 1
    print("\nPASS: no fabricated or retracted citation slipped through.")
    return 0


if __name__ == "__main__":
    offline = "--offline" in sys.argv[1:]
    print(f"ClaimAnchor verification eval ({'offline stub' if offline else 'live Crossref'})")
    sys.exit(run(_stub_resolver if offline else verify_doi))
