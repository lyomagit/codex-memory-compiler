"""Queue a local worker; the hook performs no model calls or transcript copies."""
import json
import os
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parent.parent


def main():
    if os.environ.get("CODEX_MEMORY_COMPILER_ACTIVE"):
        return
    payload = json.load(sys.stdin)
    if not isinstance(payload, dict):
        raise ValueError("Expected a hook JSON object")
    transcript = payload.get("transcript_path")
    session = payload.get("session_id")
    if not transcript or not isinstance(transcript, str) or not isinstance(session, str):
        return
    path = Path(transcript).resolve()
    if not path.is_file():
        return
    options = ({"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt"
               else {"start_new_session": True})
    with (ROOT / "scripts/flush.log").open("a", encoding="utf-8") as log:
        subprocess.Popen(
            [sys.executable, str(ROOT / "scripts/flush.py"), str(path), session],
            cwd=ROOT, stdin=subprocess.DEVNULL, stdout=log, stderr=log,
            close_fds=True, **options,
        )


if __name__ == "__main__":
    main()
