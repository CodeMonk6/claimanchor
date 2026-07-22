#!/usr/bin/env python3
"""Local stub of the Anthropic Messages API — for KEY-FREE integration testing.

This is the legitimate form of "use a function as the Claude API": it lets the REAL
`ClaimAnchorHandler` (real Anthropic SDK client, real scholarly-API tool calls, real
verification) run end-to-end **without an API key**. It scripts ClaimAnchor's tool
loop:
  turn 1 (no tool_result yet) -> tool_use `search_literature`
  turn 2 (tool_result present) -> tool_use `submit_report` citing one REAL DOI
         (kept via live Crossref) and one FABRICATED DOI (must be dropped).

It validates PLUMBING/INTEGRATION only — it returns scripted output and does NO
reasoning, so it cannot judge a real claim. A real-key run (or the model bridge in
`model_bridge.py`) is still required to prove reasoning quality.

The scripted flow is:
  turn 1 (no search yet)      -> tool_use `search_literature`
  turn 2 (no verify_doi yet)  -> tool_use `verify_doi` x3 (real, retracted, fabricated)
  turn 3                      -> tool_use `submit_report` that *naively* asserts all
                                 three as supported, so the verification layer has to
                                 keep the real one and drop the retracted + fabricated.
"""
from __future__ import annotations

import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

REAL_DOI = "10.1038/171737a0"                       # Watson & Crick, Nature 1953 — resolves
RETRACTED_DOI = "10.1016/S0140-6736(97)11096-0"     # Wakefield MMR — resolves, retracted
FAKE_DOI = "10.9999/claimanchor.integration.fake"   # never resolves


def _message(content: list, stop_reason: str) -> dict:
    return {
        "id": "msg_stub_0001",
        "type": "message",
        "role": "assistant",
        "model": "claude-stub",
        "content": content,
        "stop_reason": stop_reason,
        "stop_sequence": None,
        "usage": {"input_tokens": 1, "output_tokens": 1},
    }


def _tools_called(messages: list) -> set:
    names: set = set()
    for m in messages or []:
        c = m.get("content")
        if isinstance(c, list):
            for b in c:
                if isinstance(b, dict) and b.get("type") == "tool_use":
                    names.add(b.get("name"))
    return names


def build_response(body: dict) -> dict:
    messages = body.get("messages", []) if isinstance(body, dict) else []
    called = _tools_called(messages)

    # turn 1: search the literature
    if "search_literature" not in called:
        return _message([
            {"type": "text", "text": "Searching the literature for the claims."},
            {"type": "tool_use", "id": "toolu_search", "name": "search_literature",
             "input": {"query": "molecular structure of nucleic acids DNA double helix", "max_results": 5}},
        ], "tool_use")

    # turn 2: verify each DOI against Crossref before citing (real, retracted, fabricated)
    if "verify_doi" not in called:
        return _message([
            {"type": "text", "text": "Verifying each DOI against Crossref before citing."},
            {"type": "tool_use", "id": "toolu_v1", "name": "verify_doi",
             "input": {"doi": REAL_DOI, "claimed_title": "Molecular Structure of Nucleic Acids"}},
            {"type": "tool_use", "id": "toolu_v2", "name": "verify_doi",
             "input": {"doi": RETRACTED_DOI, "claimed_title": "Ileal-lymphoid-nodular hyperplasia, non-specific colitis, and pervasive developmental disorder in children"}},
            {"type": "tool_use", "id": "toolu_v3", "name": "verify_doi",
             "input": {"doi": FAKE_DOI, "claimed_title": "A fabricated paper that does not exist"}},
        ], "tool_use")

    # turn 3: NAIVELY assert all three as supported. The deterministic verification
    # layer must keep the real source and drop the retracted + fabricated ones — the
    # structural safety net, exercised even when the model over-claims.
    report = {
        "report_markdown": (
            "DNA has a double-helical structure [S1]. An MMR-autism claim [S2] and a "
            "fabricated claim [S3] are asserted here to check that verification removes them."
        ),
        "claims": [
            {"claim": "DNA has a double-helical structure", "verdict": "supported",
             "confidence": "high", "source_ids": ["S1"],
             "supporting_quote": "a structure for deoxyribose nucleic acid"},
            {"claim": "The MMR vaccine causes autism", "verdict": "supported",
             "confidence": "high", "source_ids": ["S2"], "supporting_quote": "n/a"},
            {"claim": "A fabricated claim backed by a non-existent citation",
             "verdict": "supported", "confidence": "high", "source_ids": ["S3"],
             "supporting_quote": "n/a"},
        ],
        "sources": [
            {"id": "S1", "doi": REAL_DOI, "title": "Molecular Structure of Nucleic Acids",
             "authors": "Watson JD; Crick FHC", "year": "1953", "journal": "Nature"},
            {"id": "S2", "doi": RETRACTED_DOI,
             "title": "Ileal-lymphoid-nodular hyperplasia, non-specific colitis, and pervasive developmental disorder in children",
             "authors": "Wakefield AJ; et al.", "year": "1998", "journal": "The Lancet"},
            {"id": "S3", "doi": FAKE_DOI, "title": "A fabricated paper that does not exist",
             "authors": "Nobody", "year": "2025", "journal": "Journal of Nonexistence"},
        ],
        "limitations": ["scripted demo backend — deterministic, no real reasoning"],
        "overall_confidence": "high",
    }
    return _message(
        [{"type": "tool_use", "id": "toolu_submit", "name": "submit_report", "input": report}],
        "tool_use",
    )


class _Handler(BaseHTTPRequestHandler):
    def log_message(self, *_a):  # silence
        pass

    def do_POST(self):  # noqa: N802 (http.server API)
        length = int(self.headers.get("Content-Length", "0") or 0)
        raw = self.rfile.read(length) if length else b"{}"
        try:
            body = json.loads(raw.decode("utf-8"))
        except Exception:
            body = {}
        data = json.dumps(build_response(body)).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("request-id", "req_stub")
        self.send_header("anthropic-version", "2023-06-01")
        self.end_headers()
        self.wfile.write(data)


def serve(host: str = "127.0.0.1", port: int = 0) -> ThreadingHTTPServer:
    """Create (not start) a stub server. port=0 picks a free port."""
    return ThreadingHTTPServer((host, port), _Handler)


if __name__ == "__main__":
    httpd = serve(port=8787)
    h, p = httpd.server_address
    print(f"stub Anthropic Messages API at http://{h}:{p}/v1/messages (Ctrl-C to stop)")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        httpd.shutdown()
