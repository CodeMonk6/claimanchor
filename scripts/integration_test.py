#!/usr/bin/env python3
"""Key-free end-to-end integration test.

Runs the REAL `ClaimAnchorHandler` (real Anthropic SDK client + real tool dispatch +
real verification) against REAL scholarly APIs, with the LLM replaced by the local
stub server (`mock_anthropic_server.py`). Exercises the whole plumbing without a key:
  real AsyncAnthropic -> stub -> live Europe PMC search -> provenance ledger ->
  live Crossref DOI resolution.

Asserts the anti-fabrication guarantee holds through the real loop: the fabricated
DOI is dropped to `unverified_sources`, the real DOI is kept in `sources`, and an
`answer` is returned.

Run:  pip install anthropic  (and optionally: pip install -e .)
      python scripts/integration_test.py

NOTE: this validates INTEGRATION/PLUMBING, not reasoning quality. A real-key run
(`scripts/smoke_test.py` with ANTHROPIC_API_KEY) is still required before submission.
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

# make agent_skeleton importable without an editable install
if "agent_skeleton" not in sys.modules:
    try:
        import agent_skeleton  # noqa: F401
    except Exception:
        _pkg = types.ModuleType("agent_skeleton")
        _pkg.__path__ = [str(_ROOT)]
        sys.modules["agent_skeleton"] = _pkg
sys.path.insert(0, str(_HERE))

import mock_anthropic_server as stub  # noqa: E402


def main() -> int:
    try:
        import anthropic  # noqa: F401
    except Exception:
        print("SKIP: `pip install anthropic` is required to run the integration test.")
        return 0

    httpd = stub.serve(port=0)
    host, port = httpd.server_address
    threading.Thread(target=httpd.serve_forever, daemon=True).start()

    os.environ["ANTHROPIC_BASE_URL"] = f"http://{host}:{port}"
    os.environ["ANTHROPIC_API_KEY"] = "dummy-key-for-stub"

    try:
        from agent_skeleton.handler import ClaimAnchorHandler

        handler = ClaimAnchorHandler({"model": "claude-opus-4-8"})
        out = asyncio.run(handler.handle_structured(
            "Verify: DNA has a double-helical structure; also validate the DOI "
            f"{stub.FAKE_DOI}."
        ))
    finally:
        httpd.shutdown()

    answer = out.get("answer", "")
    sources = out.get("sources", []) or []
    unver = out.get("unverified_sources", []) or []

    real_kept = any(stub.REAL_DOI in (s.get("doi") or "") for s in sources)
    fake_unverified = any(stub.FAKE_DOI in (s.get("doi") or "") for s in unver)
    fake_not_cited = not any(stub.FAKE_DOI in (s.get("doi") or "") for s in sources)

    print("--- integration result ---")
    print("answer present:        ", bool(answer))
    print("sources:               ", [(s.get("id"), s.get("doi"), s.get("verification")) for s in sources])
    print("unverified_sources:    ", [(s.get("id"), s.get("doi"), s.get("verification")) for s in unver])
    print("real DOI kept:         ", real_kept)
    print("fake DOI -> unverified:", fake_unverified)
    print("fake DOI NOT cited:    ", fake_not_cited)
    if out.get("error"):
        print("handler error:         ", out.get("error"))
    print("\n--- answer (first 600 chars) ---\n" + (answer[:600] if answer else "(none)"))

    checks = {
        "answer_present": bool(answer),
        "real_doi_kept": real_kept,
        "fake_doi_unverified": fake_unverified,
        "fake_doi_not_cited": fake_not_cited,
    }
    failed = [k for k, v in checks.items() if not v]
    print("\nINTEGRATION TEST:", "PASS" if not failed else f"FAIL ({', '.join(failed)})")
    return 0 if not failed else 1


if __name__ == "__main__":
    sys.exit(main())
