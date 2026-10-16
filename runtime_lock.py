"""One operation at a time for the CLI, local UI and scheduled jobs."""

import fcntl
import os
from pathlib import Path


class OperationBusy(RuntimeError):
    """Another process already owns the LinkedIn data/browser profile."""


class OperationLock:
    """Non-blocking process lock; the OS releases it even after a crash."""

    def __init__(self, path: Path):
        self.path = Path(path)
        self._file = None

    def __enter__(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        handle = self.path.open("a+", encoding="utf-8")
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            handle.close()
            raise OperationBusy(
                "Une opération LinkedIn est déjà en cours (interface, terminal ou tâche planifiée)."
            ) from None
        except BaseException:
            handle.close()
            raise
        try:
            handle.seek(0)
            handle.truncate()
            handle.write(str(os.getpid()))
            handle.flush()
        except BaseException:
            handle.close()
            raise
        self._file = handle
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        if self._file is not None:
            try:
                fcntl.flock(self._file.fileno(), fcntl.LOCK_UN)
            finally:
                self._file.close()
                self._file = None


def operation_in_progress(path: Path) -> bool:
    """Read current contention; callers must still acquire the lock to act."""
    try:
        with OperationLock(path):
            return False
    except OperationBusy:
        return True
