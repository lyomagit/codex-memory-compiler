"""OS-managed lock; release also happens if a worker crashes."""
from contextlib import contextmanager
import os
import time

from config import STATE_DIR


@contextmanager
def memory_lock():
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    with (STATE_DIR / ".memory.lock").open("a+b") as handle:
        handle.seek(0)
        if os.name == "nt":
            import msvcrt
            if not handle.read(1):
                handle.write(b"0")
                handle.flush()
            deadline = time.monotonic() + 1200
            while True:
                handle.seek(0)
                try:
                    msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                    break
                except OSError:
                    if time.monotonic() >= deadline:
                        raise TimeoutError("Memory compiler is busy")
                    time.sleep(0.2)
        else:
            import fcntl
            fcntl.flock(handle, fcntl.LOCK_EX)
        try:
            yield
        finally:
            if os.name == "nt":
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(handle, fcntl.LOCK_UN)
