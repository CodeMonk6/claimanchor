"""agent_skeleton — a copy-to-start template for building an A2A agent.

Two main ways to build on this skeleton:

  Path A — hand-author an LLM tool loop. Edit these; everything else is plumbing:
       tool_schemas.py  — your tools' JSON schemas        (ZONE 1)
       prompt.py        — system prompt + result shape     (ZONE 2)
       tools.py         — your tool functions + registry   (ZONE 4)
       agent.card.json  — your skills & endpoint
       Verify with `python -m agent_skeleton.serve check`, run with `serve-a2a`.

  Path B — wrap your own code as a custom handler. Subclass AgentHandler and
       implement one method; run it locally with `serve-handler`:
       class MyHandler(AgentHandler):
           async def handle_structured(self, user_input, files=[], context=None) -> dict:
               return {"answer": "..."}
       See INTEGRATION_GUIDE.md.

An optional third path — fronting an existing HTTP/API endpoint with an LLM loop —
lives in the ``agent_skeleton.endpoint_wrapper`` subpackage (self-contained; see its
own README).

See README.md for the walkthrough.

Exposed API:
    AgentSpec, default_demo_spec, llm_wrapper_spec   — the Path-A spec engine
    AgentHandler, FileInput, HandlerExecutor          — Path-B custom handlers
"""
from __future__ import annotations

from .spec import AgentSpec, default_demo_spec, llm_wrapper_spec

__all__ = [
    "AgentSpec",
    "default_demo_spec",
    "llm_wrapper_spec",
]

# Path-B custom-handler API. Imported defensively so the Path-A engine still
# imports (and `serve check` still runs) even if base/handler_executor are absent.
try:
    from .base import AgentHandler, FileInput
    from .handler_executor import HandlerExecutor

    __all__ += ["AgentHandler", "FileInput", "HandlerExecutor"]
except ImportError:
    pass
