#!/usr/bin/env python3
"""CI guard: every runnable Docker stage must run as a non-root user.

Parses the root ``Dockerfile`` and, for each *runnable* stage (one that declares
``ENTRYPOINT`` or ``CMD`` — i.e. could be the shipped image), asserts the
effective ``USER`` is a non-root identity. The production runtime image
(``release``) plus ``dev`` and ``cloudrun`` must never ship as root (UID 0).

This enforces the hardening SECURITY.md documents — cheaply and deterministically,
with no Docker build — so a future edit that drops or weakens a runtime ``USER``
directive fails CI instead of silently shipping a root container. For a true
runtime assertion against a built image, see ``dev/ci.sh docker-nonroot``.

Usage:
    python3 scripts/ci/check_docker_nonroot.py [path/to/Dockerfile]
"""
from __future__ import annotations

import sys
from pathlib import Path

# A USER value whose uid resolves to one of these is root.
_ROOT_UIDS = {"", "0", "root"}


def _uid(user: str) -> str:
    """The uid portion of a ``USER uid[:gid]`` value."""
    return user.split(":", 1)[0].strip()


def parse_stages(dockerfile_text: str) -> list[dict]:
    """Parse the Dockerfile into stages.

    Returns a list of dicts: ``{name, base, users: [...], runnable: bool}``.
    Instructions are single-line (FROM/USER/ENTRYPOINT/CMD), so line-by-line
    parsing is sufficient — continuation lines of multi-line RUN blocks never
    begin with a tracked instruction keyword.
    """
    stages: list[dict] = []
    cur: dict | None = None
    for raw in dockerfile_text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        toks = line.split()
        instr = toks[0].upper()
        if instr == "FROM":
            name = ""
            if len(toks) >= 4 and toks[-2].upper() == "AS":
                name = toks[-1]
            cur = {
                "name": name,
                "base": toks[1] if len(toks) > 1 else "",
                "users": [],
                "runnable": False,
            }
            stages.append(cur)
        elif cur is None:
            continue
        elif instr == "USER":
            cur["users"].append(line.split(None, 1)[1].strip() if len(toks) > 1 else "")
        elif instr in ("ENTRYPOINT", "CMD"):
            cur["runnable"] = True
    return stages


def effective_user(stage: dict, by_name: dict[str, dict]) -> str | None:
    """Effective USER for a stage: its last USER directive, else inherited from
    an internal parent stage. ``None`` means no USER is set anywhere in the
    chain (Docker then defaults to root)."""
    if stage["users"]:
        return stage["users"][-1]
    parent = by_name.get(stage["base"])
    if parent is not None:
        return effective_user(parent, by_name)
    return None


def check(dockerfile_text: str) -> list[str]:
    """Return a list of failure messages (empty == all runnable stages non-root)."""
    stages = parse_stages(dockerfile_text)
    by_name = {s["name"]: s for s in stages if s["name"]}
    runnable = [s for s in stages if s["runnable"]]
    if not runnable:
        return ["no runnable stage (ENTRYPOINT/CMD) found — cannot verify non-root"]

    failures: list[str] = []
    for s in runnable:
        label = s["name"] or f"(base {s['base']})"
        user = effective_user(s, by_name)
        if user is None:
            failures.append(f"stage '{label}' sets no USER - would run as root")
        elif _uid(user) in _ROOT_UIDS:
            failures.append(f"stage '{label}' runs as root (USER {user!r})")
    return failures


def main(argv: list[str]) -> int:
    if len(argv) > 1:
        dockerfile = Path(argv[1])
    else:
        dockerfile = Path(__file__).resolve().parents[2] / "Dockerfile"
    if not dockerfile.is_file():
        print(f"::error::Dockerfile not found at {dockerfile}", file=sys.stderr)
        return 2

    failures = check(dockerfile.read_text(encoding="utf-8"))
    if failures:
        for f in failures:
            print(f"::error::Docker non-root check: {f}", file=sys.stderr)
        print(
            f"FAIL: {len(failures)} runnable Docker stage(s) are root or unverified",
            file=sys.stderr,
        )
        return 1
    print("OK: all runnable Docker stages declare a non-root USER")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
