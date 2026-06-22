from __future__ import annotations

import subprocess


def current_git_identity() -> tuple[str | None, str | None]:
    return git_config("user.name"), git_config("user.email")


def git_config(key: str) -> str | None:
    result = subprocess.run(
        ["git", "config", key],
        capture_output = True,
        check          = False,
        text           = True,
    )
    value = result.stdout.strip()
    return value or None
