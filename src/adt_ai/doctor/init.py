from __future__ import annotations

from importlib.resources.abc import Traversable
from pathlib import Path

import yaml

from adt_ai.doctor._base import (
    PROJECT_CONFIG_TEMPLATE,
    DoctorHost,
    DoctorRequest,
    DoctorResult,
    _init_group_lines,
)
from adt_ai.shared import text_files
from adt_ai.shared.git_files import git_config_value

# The patch template scaffold ships with ADT.ai but is read from the *project*
# root, so `-init` is what actually puts it where `patch -create` looks (ADT #256).
# `#254` shipped it as a reference copy the tool never reads, which left every
# project that did not copy it in by hand with six silently empty template slots.
# Copied verbatim from the package, never re-authored here, the same way the
# root `.gitignore` above is copied rather than templated.
PATCH_TEMPLATE_DIR = Path("config/patch_template")


def _yaml_scalar(value: str) -> str:
    """`value` as a one-line YAML scalar, quoted only when the value needs it.

    `yaml.safe_dump` on a plain scalar appends a `...` end-of-document marker
    on its own line (there is nothing else in the string to say the document
    ended); a quoted scalar carries no such ambiguity and gets none. Taking the
    first line handles both shapes with one rule rather than two.
    """
    return yaml.safe_dump(value).split("\n", 1)[0]


def _identity_template(root: Path) -> str:
    """`config/IDENTITY.yaml`, prefilled from the project folder's git identity.

    Jan, 2026-08-24: *"If config/IDENTITY.yaml is empty, it should be prefiled
    based on the github account used in that project folder."* `git config
    user.name`/`user.email` already answer this for the COMMIT half at READ
    time (`shared/identity.py`, ADT #469); this writes what they answer to a
    file a developer can see and edit, rather than leaving it invisible until
    someone reads the source to learn the file has a git fallback at all.

    `db_schema`, the DATABASE half, has no git equivalent, so it ships
    commented rather than guessed: an invented schema name would be silently
    wrong the moment `-my` narrowed an export to it, and a DDL trigger reading
    `DBMS_SESSION.CLIENT_IDENTIFIER` would tag every change with the guess.

    `-init` is what scaffolds `root` into existence, so it may not exist yet
    when this runs; `subprocess.run(cwd=...)` requires a real directory, and
    "that project folder's git identity" has no answer for a folder that is
    not there, so the lookup falls back to the process's own directory rather
    than crashing the whole scaffold over one optional field.
    """
    identity_root = root if root.is_dir() else None
    account = git_config_value("user.name", identity_root).strip()
    email = git_config_value("user.email", identity_root).strip()
    account_line = f"apex_account: {_yaml_scalar(account)}" if account else "# apex_account: "
    email_line = f"email: {_yaml_scalar(email)}" if email else "# email: "
    return (
        "# Per-developer identity: who ran this. Read by every -my/-by that\n"
        "# filters git history, and by every new database connection's\n"
        "# DBMS_SESSION.SET_IDENTIFIER before STARTUP.sql runs. Gitignored,\n"
        "# never committed. Full shape: docs/config.md.\n"
        f"{account_line}\n"
        f"{email_line}\n"
        "# db_schema: YOUR_SCHEMA\n"
    )


def _resource(root: Traversable, relative_path: Path) -> Traversable:
    return root.joinpath(*relative_path.parts)


def _patch_template_files(resource_root: Traversable) -> list[tuple[Path, str]]:
    source_root = _resource(resource_root, PATCH_TEMPLATE_DIR)
    if not source_root.is_dir():
        return []

    files: list[tuple[Path, str]] = []

    def collect(directory: Traversable, relative: Path) -> None:
        for item in sorted(directory.iterdir(), key=lambda entry: entry.name):
            item_relative = relative / item.name
            if item.is_dir():
                collect(item, item_relative)
            elif item.is_file() and item.name != ".DS_Store":
                files.append(
                    (
                        PATCH_TEMPLATE_DIR / item_relative,
                        item.read_text(encoding="utf-8"),
                    )
                )

    collect(source_root, Path())
    return files


class DoctorInitMixin(DoctorHost):
    def _init_project(self, request: DoctorRequest) -> DoctorResult:
        root = (request.root or Path(".")).expanduser().resolve()
        lines: list[str] = []
        self._add(lines, "PROJECT INIT:")
        created: list[Path] = []
        skipped: list[Path] = []
        source_gitignore = _resource(self.resource_root, Path(".gitignore")).read_text(
            encoding="utf-8"
        )

        scaffold: list[tuple[Path, str]] = [
            (Path("config/config.yaml"), PROJECT_CONFIG_TEMPLATE),
            (Path("config/IDENTITY.yaml"), _identity_template(root)),
            (Path(".gitignore"), source_gitignore),
            (Path("connections/.gitkeep"), ""),
            (Path("connections/wallets/.gitkeep"), ""),
            *_patch_template_files(self.resource_root),
        ]

        for relative_path, content in scaffold:
            path = root / relative_path
            if path.exists() and not request.force:
                skipped.append(relative_path)
                continue
            path.parent.mkdir(parents=True, exist_ok=True)
            text_files.write_text(path, content)
            created.append(relative_path)

        self._extend(lines, _init_group_lines("CREATED", root, created))
        self._extend(lines, _init_group_lines("SKIPPED", root, skipped))

        if skipped:
            self._add(lines, "")
            self._add(lines, "Use `adtai doctor -init -force` to overwrite generated files.")

        return DoctorResult(lines, performed_actions=[], exit_code=0)
