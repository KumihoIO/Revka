"""Cross-platform `fcntl` shim — re-export on POSIX, msvcrt-backed on Windows.

The operator uses `fcntl.flock` for *advisory* file locks (recovery
singleton, event-listener singleton, per-run locks, sidecar spawn lock).
On POSIX this re-exports the stdlib `fcntl` module verbatim so semantics
are unchanged.

On Windows `fcntl` does not exist; importing it raises ImportError, which
would otherwise disable whole subsystems (recovery, event listener). The
Windows backend uses `msvcrt.locking` on the first byte of the lock file;
cross-process exclusion is real.

Limitations of the Windows backend:
  - `msvcrt.locking` only supports exclusive locks. `LOCK_SH` is mapped
    to an exclusive lock, which is stricter than POSIX semantics but
    safe for all current call sites (they all use `LOCK_EX`).
  - The lock byte is the first byte at file offset 0; callers must keep
    the file position at 0 (the existing call sites do — they `open(...,
    "w")` and either don't seek or write a small pid string after the
    lock, which is fine because the lock is on the byte at offset 0).
  - On contention, `msvcrt.locking` raises `OSError` with `EACCES` /
    `EDEADLOCK`. We translate that to `BlockingIOError` so the existing
    `except (OSError, BlockingIOError)` call sites continue to work
    identically to POSIX `flock(LOCK_EX | LOCK_NB)`.
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
    # Windows: msvcrt.locking-backed implementation. Constants kept as
    # distinct ints so any bitmask check (e.g. `LOCK_EX | LOCK_NB`)
    # decomposes correctly.
    import errno as _errno
    import msvcrt as _msvcrt  # type: ignore[import-not-found]

    LOCK_SH = 1
    LOCK_EX = 2
    LOCK_NB = 4
    LOCK_UN = 8

    # `errno.EDEADLOCK` only exists on Windows builds of Python; fall back
    # to `EDEADLK` on platforms where it's missing (defensive — this branch
    # runs on win32 in production but tests may simulate it from POSIX).
    _CONTENTION_ERRNOS = {
        _errno.EACCES,
        getattr(_errno, "EDEADLOCK", getattr(_errno, "EDEADLK", -1)),
    }

    def _fileno(fd) -> int:
        # Accept either a file-like object or a raw int fd, matching
        # POSIX `fcntl.flock` which accepts both.
        if hasattr(fd, "fileno"):
            return fd.fileno()
        return int(fd)

    def flock(fd, op):  # type: ignore[no-redef]
        """`fcntl.flock`-compatible lock backed by `msvcrt.locking`.

        Locks/unlocks the first byte at file offset 0. `LOCK_SH` is
        mapped to an exclusive lock (msvcrt has no shared variant);
        all current call sites use `LOCK_EX` so this is benign.

        msvcrt.locking operates at the *current* file position, not a
        fixed byte. We always seek to 0 before calling so the locked
        byte is consistent with the docstring guarantee — even when
        the caller wrote a pid sentinel after acquire (the unlock
        position would otherwise drift past byte 0 and fail).
        """
        import os as _os
        fileno = _fileno(fd)
        try:
            _os.lseek(fileno, 0, _os.SEEK_SET)
        except OSError:
            # Non-seekable fd (rare; pipes, sockets). Fall through and
            # let msvcrt.locking raise its own error if the op isn't
            # supported.
            pass

        if op & LOCK_UN:
            _msvcrt.locking(fileno, _msvcrt.LK_UNLCK, 1)
            return None

        # Acquire (LOCK_EX or LOCK_SH; LOCK_SH degrades to exclusive).
        mode = _msvcrt.LK_NBLCK if (op & LOCK_NB) else _msvcrt.LK_LOCK
        try:
            _msvcrt.locking(fileno, mode, 1)
        except OSError as exc:
            # Contention on a non-blocking lock surfaces as EACCES /
            # EDEADLOCK from msvcrt. Re-raise as BlockingIOError to
            # match POSIX `flock(LOCK_EX | LOCK_NB)` semantics.
            if (op & LOCK_NB) and exc.errno in _CONTENTION_ERRNOS:
                raise BlockingIOError(exc.errno, exc.strerror) from exc
            raise
        return None
