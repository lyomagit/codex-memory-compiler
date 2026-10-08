"""Install hooks into an explicitly selected project, preserving existing handlers."""
import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parent.parent


def hook_config():
    hooks = {}
    for event, script in (("SessionStart", "session-start.py"), ("Stop", "session-end.py"),
                          ("PreCompact", "pre-compact.py"), ("SessionEnd", "session-end.py")):
        arguments = [sys.executable, str(ROOT / "hooks" / script)]
        command = subprocess.list2cmdline(arguments) if os.name == "nt" else shlex.join(arguments)
        handler = {"type": "command", "command": command, "timeout": 3 if event == "SessionEnd" else 10}
        if event == "SessionStart":
            handler["additionalContextLimit"] = 21000
        hooks[event] = [{"hooks": [handler]}]
    return {"hooks": hooks}


def install(project: Path):
    if not project.is_dir():
        raise ValueError("The target project must already exist")
    target = project / ".codex/hooks.json"
    data = json.loads(target.read_text(encoding="utf-8")) if target.exists() else {}
    hooks = data.setdefault("hooks", {})
    for event, groups in hook_config()["hooks"].items():
        existing = hooks.setdefault(event, [])
        for group in groups:
            if group not in existing:
                existing.append(group)
    rendered = json.dumps(data, indent=2) + "\n"
    if target.exists() and target.read_text(encoding="utf-8") == rendered:
        return target
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists():
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
        shutil.copy2(target, target.with_name(f"hooks.json.backup-{stamp}"))
    temporary = target.with_suffix(".tmp")
    temporary.write_text(rendered, encoding="utf-8")
    temporary.replace(target)
    return target


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project", type=Path, help="Merge into this project's .codex/hooks.json")
    args = parser.parse_args()
    if args.project:
        print(install(args.project.resolve()))
        print("Review and trust these hooks in Codex before starting a new session.")
    else:
        print(json.dumps(hook_config(), indent=2))
