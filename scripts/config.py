"""Code and memory roots are independent, so upgrades never replace memory data."""
import os
from pathlib import Path
from datetime import datetime, timezone

CODE_DIR = Path(__file__).resolve().parent.parent
ROOT_DIR = Path(os.environ.get("CODEX_MEMORY_ROOT", CODE_DIR)).expanduser().resolve()
DAILY_DIR = ROOT_DIR / "daily"
KNOWLEDGE_DIR = ROOT_DIR / "knowledge"
CONCEPTS_DIR = KNOWLEDGE_DIR / "concepts"
CONNECTIONS_DIR = KNOWLEDGE_DIR / "connections"
QA_DIR = KNOWLEDGE_DIR / "qa"
REPORTS_DIR = ROOT_DIR / "reports"
SCRIPTS_DIR = CODE_DIR / "scripts"
STATE_DIR = ROOT_DIR / ".codex-memory-compiler"
HOOKS_DIR = CODE_DIR / "hooks"
AGENTS_FILE = CODE_DIR / "AGENTS.md"
INDEX_FILE = KNOWLEDGE_DIR / "index.md"
LOG_FILE = KNOWLEDGE_DIR / "log.md"
STATE_FILE = STATE_DIR / "state.json"


def now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def today_iso() -> str:
    return datetime.now(timezone.utc).astimezone().strftime("%Y-%m-%d")
