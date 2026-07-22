"""Serve — wire an agent into an HTTP (A2A) server, and run it locally.

Runtime layering:

    uvicorn (HTTP server)
      -> A2AStarletteApplication(card, handler).build()   (Starlette/ASGI app)
        -> DefaultRequestHandler(executor, InMemoryTaskStore())  (A2A protocol)
          -> executor.execute(context, event_queue)  (your code)

Subcommands:
    python -m agent_skeleton.serve check          # validate schemas <-> functions (no deps)
    python -m agent_skeleton.serve serve-a2a      # Path A: run the tool-loop agent (demo tools)
    python -m agent_skeleton.serve serve-handler \\
        --file handler.py --class MyHandler       # Path B: run a custom handler locally
"""
from __future__ import annotations

import argparse
import importlib.util
import json
from pathlib import Path
from typing import Any

from .a2a_runtime import (
    A2AStarletteApplication,
    AgentCard,
    DefaultRequestHandler,
    InMemoryTaskStore,
    require_a2a,
)
from .config import (
    AGENT_NAME,
    DEFAULT_CARD_PATH,
    env_advertise_url,
    env_host,
    env_model,
    env_port,
)
from .executor import SkeletonAgentExecutor
from .tools import validate_tool_registry


def load_agent_card(card_path: Path | str = DEFAULT_CARD_PATH) -> Any:
    require_a2a()
    with Path(card_path).open("r", encoding="utf-8") as fh:
        return AgentCard.model_validate(json.load(fh))


def create_app(agent_card: Any, model: str | None = None, spec: Any = None) -> Any:
    """Build the Path-A (tool-loop) ASGI app for a card + optional spec."""
    require_a2a()
    # Fail fast if the schemas and functions disagree.
    # With a spec, validate ITS tools; without one, validate the demo defaults.
    if spec is not None:
        spec.validate()
    else:
        validate_tool_registry()
    handler = DefaultRequestHandler(
        agent_executor=SkeletonAgentExecutor(model=model, spec=spec),
        task_store=InMemoryTaskStore(),
    )
    return A2AStarletteApplication(agent_card=agent_card, http_handler=handler).build()


# --- Path B: run a custom AgentHandler locally ----------------------------

def _load_handler_class(path: str, class_name: str):
    spec = importlib.util.spec_from_file_location("custom_handler_module", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Could not load handler module from {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    if not hasattr(module, class_name):
        raise RuntimeError(f"Class '{class_name}' not found in {path}")
    return getattr(module, class_name)


def _minimal_handler_card(name: str, description: str, url: str) -> Any:
    """A minimal streaming AgentCard so a handler can serve without a card file."""
    require_a2a()
    from a2a.types import AgentCapabilities, AgentSkill

    return AgentCard(
        name=name,
        description=description or f"{name} (custom handler)",
        url=url,
        version="0.1.0",
        capabilities=AgentCapabilities(streaming=True),  # heartbeat support
        skills=[AgentSkill(id="handle", name="handle", description=description or name, tags=[])],
        default_input_modes=["text"],
        default_output_modes=["text"],
    )


# --- CLI ------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Skeleton A2A agent")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("check", help="Validate tool schema/function alignment and exit.")

    serve = sub.add_parser("serve-a2a", help="Path A: run the tool-loop agent (demo tools).")
    serve.add_argument("--model", default=env_model())
    serve.add_argument("--host", default=env_host())
    serve.add_argument("--port", type=int, default=env_port())
    serve.add_argument("--card", type=Path, default=DEFAULT_CARD_PATH)
    serve.add_argument("--advertise-url", default=env_advertise_url())

    handler = sub.add_parser("serve-handler", help="Path B: run a custom AgentHandler locally.")
    handler.add_argument("--file", type=Path, required=True, help="Path to the handler .py file.")
    handler.add_argument("--class", dest="cls", required=True, help="The AgentHandler subclass name.")
    handler.add_argument("--host", default=env_host())
    handler.add_argument("--port", type=int, default=env_port())
    handler.add_argument("--name", default=None, help="Agent name (defaults to the class name).")
    handler.add_argument("--description", default="", help="One-line description for the card.")
    handler.add_argument("--card", type=Path, default=None, help="Optional card file (else a minimal one is built).")
    handler.add_argument("--advertise-url", default=env_advertise_url(),
                         help="Public JSON-RPC URL to advertise in the card (defaults to AGENT_A2A_URL).")
    return parser


def _serve_a2a(args: argparse.Namespace) -> None:
    require_a2a()
    try:
        import uvicorn
    except ImportError as exc:
        raise RuntimeError("The uvicorn package is required to serve.") from exc

    card = load_agent_card(args.card)
    if args.advertise_url:
        try:
            card = card.model_copy(update={"url": args.advertise_url})
        except Exception:
            card.url = args.advertise_url

    print(f"Serving {getattr(card, 'name', AGENT_NAME)} on {args.host}:{args.port} (url={getattr(card, 'url', '')})")
    uvicorn.run(create_app(card, model=args.model), host=args.host, port=args.port)


def _serve_handler(args: argparse.Namespace) -> None:
    require_a2a()
    try:
        import uvicorn
    except ImportError as exc:
        raise RuntimeError("The uvicorn package is required to serve.") from exc

    from .handler_executor import HandlerExecutor

    handler_class = _load_handler_class(str(args.file), args.cls)
    handler = handler_class({})  # AgentHandler.__init__(config) — non-secret config
    executor = HandlerExecutor(handler)

    url = args.advertise_url or f"http://{args.host}:{args.port}/"
    if args.card is not None:
        card = load_agent_card(args.card)
        if args.advertise_url:   # advertise the deployment URL, not the loopback in the file
            try:
                card = card.model_copy(update={"url": args.advertise_url})
            except AttributeError:
                card.url = args.advertise_url
    else:
        card = _minimal_handler_card(args.name or handler_class.__name__, args.description, url)

    app = A2AStarletteApplication(
        agent_card=card,
        http_handler=DefaultRequestHandler(agent_executor=executor, task_store=InMemoryTaskStore()),
    ).build()
    print(f"Serving handler {handler_class.__name__} on {args.host}:{args.port}")
    uvicorn.run(app, host=args.host, port=args.port)


def main() -> None:
    args = build_parser().parse_args()

    if args.command == "check":
        validate_tool_registry()
        print("OK: tool schemas and functions are aligned.")
        return

    if args.command == "serve-a2a":
        _serve_a2a(args)
        return

    if args.command == "serve-handler":
        _serve_handler(args)
        return


if __name__ == "__main__":
    main()
