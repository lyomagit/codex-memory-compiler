"""Generate or merge project/user hooks, with explicit legacy-memory replacement."""
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


def hook_config(memory_root=None):
    memory_root = Path(memory_root or ROOT).expanduser().resolve()
    hooks = {}
    for event in ("SessionStart", "Stop", "PreCompact", "SessionEnd"):
        operation = "start" if event == "SessionStart" else "capture"
        arguments = [sys.executable, str(ROOT / "hooks/dispatch.py"), operation, "--root", str(memory_root)]
        command = subprocess.list2cmdline(arguments) if os.name == "nt" else shlex.join(arguments)
        handler = {"type": "command", "command": command, "timeout": 3 if event == "SessionEnd" else 10}
        if event == "SessionStart":
            handler["additionalContextLimit"] = 6000
        hooks[event] = [{"hooks": [handler]}]
    return {"hooks": hooks}


def is_memory_handler(handler, replace_legacy):
    command = handler.get("command", "")
    if str(ROOT / "hooks/dispatch.py") in command:
        return True
    if not replace_legacy:
        return False
    try:
        tokens = shlex.split(command)
    except ValueError:
        return False
    return (len(tokens) > 1 and Path(tokens[0]).name in ("codex-memory", "codex-memory.exe")
            and tokens[1] in ("capture", "inject"))


def install(project=None, *, user=False, memory_root=None, replace_legacy=False, dry_run=False):
    if user:
        directory = Path(os.environ.get("CODEX_HOME", Path.home() / ".codex"))
    else:
        if project is None or not Path(project).is_dir():
            raise ValueError("The target project must already exist")
        directory = Path(project) / ".codex"
    target = directory / "hooks.json"
    data = json.loads(target.read_text(encoding="utf-8")) if target.exists() else {}
    hooks = data.setdefault("hooks", {})
    for event, groups in hook_config(memory_root)["hooks"].items():
        existing = []
        replaced = False
        for group in hooks.get(event, []):
            kept = []
            for handler in group.get("hooks", []):
                if is_memory_handler(handler, replace_legacy):
                    if not replaced:
                        kept.append(groups[0]["hooks"][0])
                        replaced = True
                else:
                    kept.append(handler)
            if kept:
                existing.append(dict(group, hooks=kept))
        hooks[event] = existing if replaced else existing + groups
    rendered = json.dumps(data, indent=2) + "\n"
    if dry_run:
        return {"target": str(target), "configuration": data}
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
    scope = parser.add_mutually_exclusive_group()
    scope.add_argument("--project", type=Path)
    scope.add_argument("--user", action="store_true", help="Install at CODEX_HOME/hooks.json")
    parser.add_argument("--root", type=Path, help="Memory data directory (default: this checkout)")
    parser.add_argument("--replace-legacy", action="store_true", help="Replace codex-memory capture/inject only")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    if args.project or args.user:
        result = install(args.project, user=args.user, memory_root=args.root,
                         replace_legacy=args.replace_legacy, dry_run=args.dry_run)
        print(json.dumps(result, indent=2) if isinstance(result, dict) else result)
        if not args.dry_run:
            print("Review and trust these hooks through Codex /hooks before starting a new session.")
    else:
        print(json.dumps(hook_config(args.root), indent=2))
