"""Use the same incremental capture path before compaction."""
from pathlib import Path
import runpy

if __name__ == "__main__":
    runpy.run_path(str(Path(__file__).with_name("session-end.py")), run_name="__main__")
