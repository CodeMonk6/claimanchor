#!/usr/bin/env python3
"""Run ClaimAnchor end-to-end with NO hosted API key — a reproducible demo.

This runs the **real** `ClaimAnchorHandler` (real tool loop, real scholarly-API calls,
real verification) against **live** Europe PMC / Crossref. The only substitute is the
*model*: instead of the paid Anthropic API, the tool-choice turns come from a local,
deterministic backend (`mock_anthropic_server.py`) reached via `ANTHROPIC_BASE_URL`.
The backend deliberately over-claims (asserts three sources as "supported") so you can
watch the deterministic verification layer keep the real source and drop the retracted
and fabricated ones.

Needs the Anthropic SDK (`pip install -e .` or `pip install anthropic`) — but NO key.
For a genuine, model-reasoned run, point `ANTHROPIC_BASE_URL` at any Anthropic-compatible
endpoint (a local LLM, a gateway, or `scripts/model_bridge.py`).

Run:  python scripts/demo.py
"""
from __future__ import annotations

import asyncio
import os
import pathlib
import sys
import threading
import types

_HERE = pathlib.Path(__file__).resolve().parent
_ROOT = _HERE.parent
if "agent_skeleton" not in sys.modules:
    try:
        import agent_skeleton  # noqa: F401
    except Exception:
        _pkg = types.ModuleType("agent_skeleton")
        _pkg.__path__ = [str(_ROOT)]
        sys.modules["agent_skeleton"] = _pkg
sys.path.insert(0, str(_HERE))

import mock_anthropic_server as stub  # noqa: E402


def _line(c: str = "-") -> str:
    return c * 74


def main() -> int:
    try:
        import anthropic  # noqa: F401
    except Exception:
        print("SKIP: install the Anthropic SDK first — `pip install -e .` (no API key needed).")
        return 0

    httpd = stub.serve(port=0)
    host, port = httpd.server_address
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    os.environ["ANTHROPIC_BASE_URL"] = f"http://{host}:{port}"
    os.environ["ANTHROPIC_API_KEY"] = "no-key-local-backend"

    prompt = (
        "Verify these three citations for my manuscript: (1) DNA has a double-helical "
        f"structure; (2) the MMR vaccine causes autism, cited to DOI {stub.RETRACTED_DOI}; "
        f"(3) a result cited to DOI {stub.FAKE_DOI}."
    )

    print(_line("="))
    print("ClaimAnchor — key-free demo (real retrieval + real verification;")
    print("model turns served by a local deterministic backend, NOT the hosted API)")
    print(_line("="))
    print(f"\nRequest:\n  {prompt}\n")

    try:
        from agent_skeleton.handler import ClaimAnchorHandler
        handler = ClaimAnchorHandler({"model": "claude-opus-4-8"})
        out = asyncio.run(handler.handle_structured(prompt))
    finally:
        httpd.shutdown()

    sources = out.get("sources", []) or []
    unver = out.get("unverified_sources", []) or []
    claims = out.get("claims", []) or []

    print("Per-claim verdicts (after verification):")
    for c in claims:
        print(f"  • [{c['verdict']:<16}] {c['claim'][:60]}")
        if c.get("verification_note"):
            print(f"      ↳ {c['verification_note']}")
    print("\nKept sources:")
    for s in sources:
        print(f"  ✓ [{s['id']}] {s.get('verification')} — {s.get('doi')}")
    print("\nExcluded (fabricated / retracted / mismatched):")
    for s in unver:
        tag = "RETRACTED" if s.get("verification") == "retracted" else "DROPPED"
        print(f"  ✗ [{s['id']}] {tag} — {s.get('doi')} — {s.get('reason', '')[:70]}")
    print("\nverification_summary:", out.get("verification_summary", {}))

    # Assertions: the real source is kept; the retracted + fabricated ones are excluded
    # and their claims downgraded away from "supported".
    real_kept = any(stub.REAL_DOI in (s.get("doi") or "") for s in sources)
    retracted_excluded = any(
        stub.RETRACTED_DOI.lower() in (s.get("doi") or "") and s.get("verification") == "retracted"
        for s in unver
    )
    fake_dropped = any(stub.FAKE_DOI in (s.get("doi") or "") for s in unver)
    none_supported_on_bad = all(
        c["verdict"] != "supported" for c in claims
        if c["claim"] != "DNA has a double-helical structure"
    )
    checks = {
        "real_source_kept": real_kept,
        "retracted_excluded": retracted_excluded,
        "fabricated_dropped": fake_dropped,
        "bad_claims_downgraded": none_supported_on_bad,
    }
    failed = [k for k, v in checks.items() if not v]
    print("\n" + _line())
    print("DEMO:", "PASS — real kept, retracted + fabricated removed" if not failed
          else f"FAIL ({', '.join(failed)})")
    return 0 if not failed else 1


if __name__ == "__main__":
    sys.exit(main())
