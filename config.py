"""Shared configuration & constants.

SUPPORT FILE — you normally only edit the DEFAULT_* values below.
"""
from __future__ import annotations

import os
from pathlib import Path

# --- Identity -------------------------------------------------------------
AGENT_NAME = "ClaimAnchor"          # biomedical claim & citation verification agent

# --- Networking -----------------------------------------------------------
DEFAULT_HOST = "0.0.0.0"          # bind address (listen on all interfaces)
DEFAULT_PORT = 9110               # pick a free port

# --- LLM ------------------------------------------------------------------
# ClaimAnchor runs on Claude via the official Anthropic SDK (see handler.py).
# Override at deploy time with the AGENT_MODEL env var.
DEFAULT_MODEL = "claude-opus-4-8"  # Anthropic model id
MAX_TOOL_STEPS = 8                # cap on tool-call loop iterations

# --- Paths ----------------------------------------------------------------
PACKAGE_DIR = Path(__file__).resolve().parent
DEFAULT_CARD_PATH = PACKAGE_DIR / "agent.card.json"


# --- Env readers (so deployment can override without code edits) ----------
def env_model() -> str:
    return os.getenv("AGENT_MODEL", DEFAULT_MODEL)


def env_host() -> str:
    return os.getenv("AGENT_A2A_HOST", DEFAULT_HOST)


def env_port() -> int:
    return int(os.getenv("AGENT_A2A_PORT", str(DEFAULT_PORT)))


def env_advertise_url() -> str | None:
    return os.getenv("AGENT_A2A_URL")
