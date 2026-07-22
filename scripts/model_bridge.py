#!/usr/bin/env python3
"""Local Anthropic-Messages-compatible **bridge** — bring your own reasoner, no key.

ClaimAnchor talks to its model through the Anthropic SDK, which honors
``ANTHROPIC_BASE_URL``. This server implements ``POST /v1/messages`` but, instead of
calling a hosted API, it hands each turn to an *external* reasoner: on every request it
writes the full body (system + messages + tools) to ``<BRIDGE_DIR>/req_NNN.json`` and
blocks until ``<BRIDGE_DIR>/resp_NNN.json`` appears, then returns that file verbatim as
the Messages response (same block shape the SDK expects).

That external reasoner can be anything that can read/write JSON files — a local LLM, an
internal gateway, or a human/LLM in the loop. It lets you run the **real** agent (real
tool loop, real scholarly-API calls, real verification) with **no hosted API key**.

Run:
    BRIDGE_DIR=./bridge python scripts/model_bridge.py      # serves on :8799
Then point the agent at it:  ANTHROPIC_BASE_URL=http://127.0.0.1:8799
"""
from __future__ import annotations

import json
import os
import pathlib
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


def _dir() -> pathlib.Path:
    return pathlib.Path(os.environ.get("BRIDGE_DIR", "./bridge"))


def _timeout() -> float:
    return float(os.environ.get("BRIDGE_TIMEOUT", "1800"))


_lock = threading.Lock()
_counter = {"n": 0}


def _error_message(text: str) -> dict:
    return {
        "id": "msg_bridge_err", "type": "message", "role": "assistant",
        "model": "bridge", "content": [{"type": "text", "text": text}],
        "stop_reason": "end_turn", "stop_sequence": None,
        "usage": {"input_tokens": 1, "output_tokens": 1},
    }


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

        d = _dir()
        d.mkdir(parents=True, exist_ok=True)
        with _lock:
            _counter["n"] += 1
            n = _counter["n"]
        (d / f"req_{n:03d}.json").write_text(json.dumps(body, indent=2))
        (d / "LAST").write_text(str(n))

        resp_path = d / f"resp_{n:03d}.json"
        deadline = time.monotonic() + _timeout()
        while not resp_path.exists():
            if time.monotonic() > deadline:
                self._send(json.dumps(_error_message("bridge timed out waiting for a response")).encode())
                return
            time.sleep(0.4)
        self._send(resp_path.read_bytes())

    def _send(self, data: bytes) -> None:
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("request-id", "req_bridge")
        self.send_header("anthropic-version", "2023-06-01")
        self.end_headers()
        self.wfile.write(data)


def serve(host: str = "127.0.0.1", port: int = 0) -> ThreadingHTTPServer:
    """Create (not start) a bridge server. port=0 picks a free port."""
    return ThreadingHTTPServer((host, port), _Handler)


if __name__ == "__main__":
    httpd = serve(port=int(os.environ.get("BRIDGE_PORT", "8799")))
    h, p = httpd.server_address
    print(f"bridge at http://{h}:{p}/v1/messages   dir={_dir()}   (Ctrl-C to stop)")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        httpd.shutdown()
