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
reasoning, so it cannot judge a real claim. A real-key run is still required to
prove reasoning quality and deployability.
"""
from __future__ import annotations

import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

REAL_DOI = "10.1038/171737a0"                     # Watson & Crick, Nature 1953 — always resolves
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


def _has_tool_result(messages: list) -> bool:
    for m in messages or []:
        c = m.get("content")
        if isinstance(c, list):
            for b in c:
                if isinstance(b, dict) and b.get("type") == "tool_result":
                    return True
    return False


def build_response(body: dict) -> dict:
    messages = body.get("messages", []) if isinstance(body, dict) else []
    if not _has_tool_result(messages):
        return _message(
            [
                {"type": "text", "text": "Searching the literature."},
                {"type": "tool_use", "id": "toolu_search", "name": "search_literature",
                 "input": {"query": "molecular structure of nucleic acids DNA double helix",
                           "max_results": 5}},
            ],
            "tool_use",
        )
    report = {
        "report_markdown": (
            "Integration check: DNA has a double-helical structure [S1]. "
            "A fabricated claim [S2] cites a non-existent DOI and must be dropped."
        ),
        "claims": [
            {"claim": "DNA has a double-helical structure", "verdict": "supported",
             "confidence": "high", "source_ids": ["S1"],
             "supporting_quote": "a structure for deoxyribose nucleic acid (D.N.A.)"},
            {"claim": "A fabricated claim backed by a non-existent citation",
             "verdict": "supported", "confidence": "high", "source_ids": ["S2"],
             "supporting_quote": "n/a"},
        ],
        "sources": [
            {"id": "S1", "doi": REAL_DOI, "title": "Molecular Structure of Nucleic Acids",
             "authors": "Watson JD; Crick FHC", "year": "1953", "journal": "Nature"},
            {"id": "S2", "doi": FAKE_DOI, "title": "A fabricated paper that does not exist",
             "authors": "Nobody", "year": "2025", "journal": "Journal of Nonexistence"},
        ],
        "limitations": ["integration stub — no real reasoning"],
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
