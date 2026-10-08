"""Stable hook entrypoint with an explicit data root, independent of session cwd."""
import argparse
import os
from pathlib import Path
import runpy

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("operation", choices=("start", "capture"))
    parser.add_argument("--root", required=True)
    args = parser.parse_args()
    os.environ["CODEX_MEMORY_ROOT"] = str(Path(args.root).expanduser().resolve())
    name = "session-start.py" if args.operation == "start" else "session-end.py"
    runpy.run_path(str(Path(__file__).with_name(name)), run_name="__main__")
