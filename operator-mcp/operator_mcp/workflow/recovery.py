"""Workflow run-lock helpers.

Workflow execution uses a per-run advisory lock so duplicate operator
processes cannot drive the same run concurrently. Interrupted runs are not
auto-resumed on startup; startup marks stale in-progress runs failed and leaves
retry as an explicit user action.
"""
from __future__ import annotations

import os
from typing import Any


_RUN_LOCK_DIR = os.path.expanduser("~/.revka/workflow_locks")


def _acquire_run_lock(run_id: str) -> Any:
    """Acquire a per-run lock file.

    Returns the fd when acquired, or None if another process already holds the
    lock.
    """
    from .. import _fcntl_compat as fcntl

    os.makedirs(_RUN_LOCK_DIR, exist_ok=True)
    lock_path = os.path.join(_RUN_LOCK_DIR, f"{run_id[:12]}.lock")
    try:
        fd = open(lock_path, "w")
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        fd.write(f"{os.getpid()}\n")
        fd.flush()
        return fd
    except (OSError, BlockingIOError):
        return None


def _release_run_lock(fd: Any, run_id: str) -> None:
    """Release a per-run lock file."""
    from .. import _fcntl_compat as fcntl

    try:
        fcntl.flock(fd, fcntl.LOCK_UN)
        fd.close()
        lock_path = os.path.join(_RUN_LOCK_DIR, f"{run_id[:12]}.lock")
        os.unlink(lock_path)
    except Exception:
        pass
