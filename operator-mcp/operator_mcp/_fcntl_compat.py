"""Cross-platform `fcntl` shim — re-export on POSIX, no-op on Windows.

The operator uses `fcntl.flock` for *advisory* file locks (recovery
singleton, event-listener singleton, per-run locks, sidecar spawn lock).
On POSIX this re-exports the stdlib `fcntl` module verbatim so semantics
are unchanged.

On Windows `fcntl` does not exist; importing it raises ImportError, which
disables whole subsystems (recovery, event listener) even though the
calling code logs the error as "non-fatal". This shim provides no-op
implementations of the lock primitives on Windows. That is acceptable
because:

  - All current uses are best-effort advisory locks.
  - Operator-mcp on Windows runs single-process per session today, so
    cross-process file locking is not load-bearing for correctness.
  - `msvcrt.locking` is finicky for advisory cross-process use, and the
    error-tolerant call sites already swallow lock failures.

If real cross-process locking on Windows becomes necessary later, swap
the no-op `flock` for an `msvcrt.locking`-based implementation.
"""
from __future__ import annotations

import sys

if sys.platform != "win32":
    # POSIX: re-export the real module verbatim.
    from fcntl import (  # type: ignore[import-not-found]  # noqa: F401
        flock,
        LOCK_EX,
        LOCK_SH,
        LOCK_NB,
        LOCK_UN,
    )
else:
    # Windows: best-effort no-op locking. Constants kept as distinct
    # ints so any bitmask check (e.g. `LOCK_EX | LOCK_NB`) still works.
    LOCK_SH = 1
    LOCK_EX = 2
    LOCK_NB = 4
    LOCK_UN = 8

    def flock(fd, op):  # type: ignore[no-redef]
        """No-op on Windows. Operator's locks are best-effort advisory."""
        return None
