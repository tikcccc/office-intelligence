"""Paths, model choice and runtime switches."""

import os
import math
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _load_dotenv(path: Path) -> None:
    """Read KEY=value lines from .env (kept out of git) without overriding variables already set."""
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        key, sep, value = line.partition("=")
        if sep and not key.strip().startswith("#"):
            os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


_load_dotenv(ROOT / ".env")

SNAPSHOT_DIR = Path(__file__).resolve().parent / "snapshot"
DATA_DIR = ROOT / "data"
OUTPUT_DIR = ROOT / "output"
DB_PATH = DATA_DIR / "office_agent.db"
RUN_LOG_PATH = DATA_DIR / "runs.jsonl"

# Longest one question may take end to end (all model calls and skills). A weekly brief or a broker refresh
# requested in chat fits inside this; a stuck provider does not hold the user for minutes.
QUESTION_DEADLINE_SECONDS = float(os.environ.get("OFFICE_AGENT_DEADLINE_SECONDS", "240"))
if not math.isfinite(QUESTION_DEADLINE_SECONDS) or QUESTION_DEADLINE_SECONDS <= 0:
    raise ValueError("OFFICE_AGENT_DEADLINE_SECONDS must be a positive finite number.")
MAX_QUESTION_CHARS = 8000
MAX_TOOL_CALLS = 6
MAX_HISTORY_CHARS = 240000
MAX_HISTORY_TURNS = 8

# Xiaomi MiMo via its OpenAI-compatible API. Token Plan keys (tp-...) use https://token-plan-cn.xiaomimimo.com/v1.
MODEL = os.environ.get("OFFICE_AGENT_MODEL", "mimo-v2.6-pro-ultraspeed")
BASE_URL = os.environ.get("MIMO_BASE_URL", "https://api.xiaomimimo.com/v1")

# Some public sites (Bank of England, Knight Frank) reject requests without a browser UA.
HTTP_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/130.0 Safari/537.36"
}
HTTP_TIMEOUT = 20


def offline() -> bool:
    """Serve bundled source snapshots. Model APIs remain available unless --no-llm is also used."""
    from .runtime import current
    context = current()
    return context.offline if context else os.environ.get("OFFICE_AGENT_OFFLINE", "").lower() in {"1", "true", "yes"}


def set_offline(value: bool) -> None:
    os.environ["OFFICE_AGENT_OFFLINE"] = "1" if value else "0"
