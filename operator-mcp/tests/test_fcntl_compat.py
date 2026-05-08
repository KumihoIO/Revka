"""Tests for the cross-platform `_fcntl_compat` shim.

Two layers:
  - POSIX: verify real `fcntl.flock` behavior is preserved (the shim
    just re-exports `fcntl` on POSIX).
  - Windows: simulate `sys.platform == "win32"` plus a stub `msvcrt`
    module, then reload the shim and verify a second non-blocking
    flock raises `BlockingIOError` while the first lock is held.
"""
from __future__ import annotations

import errno
import importlib
import sys
import types

import pytest


# --- POSIX path ----------------------------------------------------------

@pytest.mark.skipif(sys.platform == "win32", reason="POSIX-only check")
def test_posix_reexports_fcntl(tmp_path):
    """On POSIX the shim is a thin re-export — verify real flock semantics."""
    import fcntl as real_fcntl

    from operator_mcp import _fcntl_compat as shim

    # Constants come from the real module on POSIX.
    assert shim.LOCK_EX == real_fcntl.LOCK_EX
    assert shim.LOCK_NB == real_fcntl.LOCK_NB
    assert shim.LOCK_UN == real_fcntl.LOCK_UN
    assert shim.flock is real_fcntl.flock

    lock_path = tmp_path / "p.lock"
    fd1 = open(lock_path, "w")
    fd2 = open(lock_path, "w")
    try:
        shim.flock(fd1, shim.LOCK_EX | shim.LOCK_NB)
        with pytest.raises((BlockingIOError, OSError)):
            shim.flock(fd2, shim.LOCK_EX | shim.LOCK_NB)
        shim.flock(fd1, shim.LOCK_UN)
        # After release, fd2 should now succeed.
        shim.flock(fd2, shim.LOCK_EX | shim.LOCK_NB)
        shim.flock(fd2, shim.LOCK_UN)
    finally:
        fd1.close()
        fd2.close()


# --- Windows path (simulated) -------------------------------------------

class _FakeMsvcrt:
    """Minimal stand-in for the `msvcrt` module's `locking` API.

    Tracks which file descriptors hold the (single, exclusive) byte lock
    and raises `OSError(EACCES)` on contention — matching real msvcrt
    semantics closely enough to exercise the shim's translation logic.
    """

    LK_LOCK = 0
    LK_NBLCK = 1
    LK_NBRLCK = 2
    LK_RLCK = 3
    LK_UNLCK = 4

    def __init__(self):
        self._held: set[int] = set()

    def locking(self, fileno, mode, nbytes):
        if mode == self.LK_UNLCK:
            self._held.discard(fileno)
            return
        if mode in (self.LK_NBLCK, self.LK_NBRLCK):
            if self._held and fileno not in self._held:
                raise OSError(errno.EACCES, "lock contention")
            self._held.add(fileno)
            return
        if mode in (self.LK_LOCK, self.LK_RLCK):
            # Blocking — for the test we don't actually block; the
            # blocking path isn't exercised by the contention assertion.
            self._held.add(fileno)
            return
        raise ValueError(f"unknown mode {mode!r}")


def _reload_shim_as_windows(monkeypatch, fake_msvcrt):
    """Reload `_fcntl_compat` with `sys.platform` patched to win32."""
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setitem(sys.modules, "msvcrt", fake_msvcrt)
    # Drop any cached import so the win32 branch executes on reload.
    sys.modules.pop("operator_mcp._fcntl_compat", None)
    shim = importlib.import_module("operator_mcp._fcntl_compat")
    return shim


def test_windows_nonblocking_contention_raises_blockingio(monkeypatch, tmp_path):
    """Second LOCK_NB while first lock held → BlockingIOError."""
    fake = types.SimpleNamespace()
    impl = _FakeMsvcrt()
    fake.LK_LOCK = impl.LK_LOCK
    fake.LK_NBLCK = impl.LK_NBLCK
    fake.LK_NBRLCK = impl.LK_NBRLCK
    fake.LK_RLCK = impl.LK_RLCK
    fake.LK_UNLCK = impl.LK_UNLCK
    fake.locking = impl.locking

    shim = _reload_shim_as_windows(monkeypatch, fake)
    try:
        lock_path = tmp_path / "w.lock"
        fd1 = open(lock_path, "w")
        fd2 = open(lock_path, "w")
        try:
            shim.flock(fd1, shim.LOCK_EX | shim.LOCK_NB)
            with pytest.raises(BlockingIOError):
                shim.flock(fd2, shim.LOCK_EX | shim.LOCK_NB)
            # Release fd1 — fd2 can now acquire.
            shim.flock(fd1, shim.LOCK_UN)
            shim.flock(fd2, shim.LOCK_EX | shim.LOCK_NB)
            shim.flock(fd2, shim.LOCK_UN)
        finally:
            fd1.close()
            fd2.close()
    finally:
        # Drop the win32-patched module so subsequent tests get the real one.
        sys.modules.pop("operator_mcp._fcntl_compat", None)
