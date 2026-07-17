from __future__ import annotations

from adt_ai.shared.git_files import git_config_value


def current_git_identity() -> tuple[str | None, str | None]:
    # Reads from the process working directory on purpose: the export flows ask
    # "who is running this" wherever the CLI was launched, not in a given repo.
    return git_config_value("user.name") or None, git_config_value("user.email") or None
