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
from adt_ai.shared import git_meta_sync, text_files
from adt_ai.shared.config import is_enabled
from adt_ai.shared.file_list import capped, more_row, nested_files
from adt_ai.shared.file_list import row as file_list_row
from adt_ai.shared.git_files import git_config_value

# Read from the project root by `patch -create`, so `-init` copies it there (ADT #256).
PATCH_TEMPLATE_DIR = Path("config/patch_template")

# The project's git files, never ADT.ai's own root files (ADT #733, #946).
GITATTRIBUTES_TEMPLATE = Path("config/gitattributes_template")
GITIGNORE_TEMPLATE = Path("config/gitignore_template")


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


#: The group of existing files `-init` left alone, naming the flag that would not.
SKIPPED_LABEL = "SKIPPED (use -force to overwrite)"

#: The two root files that carry an ADT-owned block (ADT #938).
GITATTRIBUTES_NAME = Path(".gitattributes")
GITIGNORE_NAME = Path(".gitignore")


class DoctorInitMixin(DoctorHost):
    def _init_project(self, request: DoctorRequest) -> DoctorResult:
        root = (request.root or Path(".")).expanduser().resolve()
        lines: list[str] = []
        self._add(lines, "PROJECT INIT:")
        created: list[Path] = []
        skipped: list[Path] = []
        # Paths `-sync` rewrote because they differed from the template in
        # nothing but their line endings (ADT #944).
        eol_rewritten: list[Path] = []
        file_crlf = is_enabled((request.config or {}).get("file_crlf"))
        # `doctor` never runs the startup that applies `file_crlf` process-wide,
        # so every write here names its ending (ADT #944).
        newline = text_files.newline_for(file_crlf)
        nested = nested_files(request.config)
        source_gitignore = _resource(self.resource_root, GITIGNORE_TEMPLATE).read_text(
            encoding="utf-8"
        )
        # The block follows the project's effective `file_crlf` (the config the
        # CLI already loaded for doctor: `<root>/config/config.yaml` over the
        # shipped defaults), so a fresh scaffold and every later `-sync` agree
        # with what the exporter writes (ADT #938).
        source_gitattributes = git_meta_sync.render_gitattributes_block(
            _resource(self.resource_root, GITATTRIBUTES_TEMPLATE).read_text(encoding="utf-8"),
            file_crlf=file_crlf,
        )
        # A FRESH `.gitattributes`/`.gitignore` is scaffolded WITH the managed
        # markers already around the template, so a later `-sync` recognizes
        # the block immediately instead of reading a legacy file it has to
        # migrate on its very first run (ADT #938).
        scaffolded_gitattributes = git_meta_sync.merge_block("", source_gitattributes).new_text
        scaffolded_gitignore = git_meta_sync.merge_block("", source_gitignore).new_text

        scaffold: list[tuple[Path, str]] = [
            (Path("config/config.yaml"), PROJECT_CONFIG_TEMPLATE),
            (Path("config/IDENTITY.yaml"), _identity_template(root)),
            (GITATTRIBUTES_NAME, scaffolded_gitattributes),
            (GITIGNORE_NAME, scaffolded_gitignore),
            (Path("connections/.gitkeep"), ""),
            (Path("connections/wallets/.gitkeep"), ""),
            *_patch_template_files(self.resource_root),
        ]

        # Under `-sync` the two git meta files belong to the sync pass alone: it
        # creates a missing one with the block, so the scaffold neither creates
        # nor skips it, and each appears exactly once, under the sync groups.
        sync_owned = {GITATTRIBUTES_NAME, GITIGNORE_NAME} if request.sync else set()
        for relative_path, content in scaffold:
            if relative_path in sync_owned:
                continue
            path = root / relative_path
            if path.exists() and not request.force:
                if request.sync and _differs_only_in_line_endings(path, content, newline):
                    text_files.write_text(path, content, newline)
                    eol_rewritten.append(relative_path)
                    continue
                skipped.append(relative_path)
                continue
            path.parent.mkdir(parents=True, exist_ok=True)
            text_files.write_text(path, content, newline)
            created.append(relative_path)

        self._extend(lines, _init_group_lines("CREATED", _posix(created), nested=nested))
        # The label carries the way out, so no hint line follows the group (ADT
        # #945, Jan on `#944`'s live run in `APEXDEV_JANK`).
        self._extend(
            lines,
            _init_group_lines(SKIPPED_LABEL, _posix(skipped), nested=nested),
        )

        if request.sync:
            self._extend(
                lines,
                _sync_git_meta_lines(
                    root,
                    source_gitattributes,
                    source_gitignore,
                    newline       = newline,
                    nested        = nested,
                    eol_rewritten = _posix(eol_rewritten),
                ),
            )

        return DoctorResult(lines, performed_actions=[], exit_code=0)


def _posix(paths: list[Path]) -> list[str]:
    return [path.as_posix() for path in paths]


def _differs_only_in_line_endings(path: Path, content: str, newline: str) -> bool:
    """Is `path` the scaffold's own `content`, ending its lines other than `newline`?

    Such a file carries no edit of the developer's, only the ending an older
    ADT wrote it with, so `-sync` may rewrite it where plain `-init` skips it
    (ADT #944). One already in `newline` is current and stays skipped, and a
    file that is not valid UTF-8 is somebody's, and kept.
    """
    try:
        data = path.read_bytes()
        existing = data.decode("utf-8")
    except (OSError, UnicodeDecodeError):
        return False
    return data != text_files.rendered_bytes(content, newline) and (
        text_files.normalize(existing) == text_files.normalize(content)
    )


def _sync_one(
    root: Path, relative: Path, block_content: str, newline: str
) -> tuple[str, git_meta_sync.BlockMerge]:
    """Sync one file's managed block on disk; `(status, merge)`.

    `status` is one of `CREATED` (the file did not exist), `SYNCED` (it
    existed and the block changed), `UNCHANGED` (it existed and already
    matched) or `REFUSED` (broken markers, nothing written). Read and written
    as raw bytes, never through the newline-normalizing `text_files.write_text`,
    so a user's own lines outside the block survive byte for byte (ADT #938
    requirement 2) -- only the block itself, sourced from the shipped
    template, takes the project's `file_crlf` ending (ADT #944).
    """
    path = root / relative
    existing = path.read_bytes().decode("utf-8") if path.exists() else ""
    merge = git_meta_sync.merge_block(existing, block_content, newline)
    if merge.refused:
        return "REFUSED", merge
    existed = path.exists()
    if merge.changed:
        path.parent.mkdir(parents=True, exist_ok=True)
        text_files.write_bytes(path, merge.new_text.encode("utf-8"))
    return ("CREATED" if not existed else "SYNCED" if merge.changed else "UNCHANGED"), merge


def _sync_git_meta_lines(
    root: Path,
    gitattributes_block: str,
    gitignore_block: str,
    *,
    newline: str,
    nested: bool,
    eol_rewritten: list[str],
) -> list[str]:
    """`doctor -init -sync`'s report: which files synced, which refused, and what
    the normalize/override passes over `.gitattributes` found (ADT #938)."""
    synced: list[Path] = []
    unchanged: list[Path] = []
    refused: list[tuple[Path, int | None, str]] = []
    duplicate_lines: list[tuple[Path, tuple[str, ...]]] = []

    for relative, block in (
        (GITATTRIBUTES_NAME, gitattributes_block),
        (GITIGNORE_NAME, gitignore_block),
    ):
        status, merge = _sync_one(root, relative, block, newline)
        if status == "REFUSED":
            refused.append((relative, merge.refused_line, merge.refused_reason))
            continue
        (unchanged if status == "UNCHANGED" else synced).append(relative)
        if merge.removed_duplicates:
            duplicate_lines.append((relative, merge.removed_duplicates))

    # Staged beside the renormalized files below, so a commit of the index never
    # carries LF sources without the rule that explains them. Only what this
    # run created or rewrote; outside a git work tree this is a no-op.
    git_meta_sync.stage_paths(root, _posix(synced))

    lines: list[str] = []
    lines.extend(_init_group_lines("SYNCED", _posix(synced), nested=nested))
    lines.extend(_init_group_lines("UNCHANGED", _posix(unchanged), nested=nested))
    if refused:
        lines.append("")
        lines.append("  REFUSED:")
        for relative, refused_line, reason in refused:
            lines.append(file_list_row(relative.as_posix(), depth=2))
            if refused_line is not None:
                lines.append(f"      line {refused_line}: {reason}")

    if duplicate_lines:
        lines.append("")
        lines.append("  REMOVED DUPLICATE LINES:")
        for relative, removed in duplicate_lines:
            lines.append(f"    {relative.as_posix()}:")
            for removed_line in removed:
                lines.append(file_list_row(removed_line, depth=3))

    # The scaffold files `-sync` rewrote for their ending alone took the
    # project's own ending, whatever git later says about them.
    scaffold_eol = "CRLF" if newline == "\r\n" else "LF"
    normalized: dict[str, list[str]] = {"CRLF": [], "LF": []}

    # The renormalize and override passes only make sense once `.gitattributes`
    # itself carries a trustworthy block; a refused file leaves both silent.
    overrides: list[str] = []
    if not any(relative == GITATTRIBUTES_NAME for relative, _line, _reason in refused):
        patterns = git_meta_sync.block_patterns(gitattributes_block)
        renormalized = git_meta_sync.renormalize_staged(root, patterns)
        for path, eol in git_meta_sync.checkout_eols(root, renormalized).items():
            normalized[eol].append(path)
        overrides = git_meta_sync.attribute_overrides(root, gitattributes_block)

    # Named by the ending the reader now has, never by the git verb that got
    # it there (ADT #944): the APEXlang pins stay LF under `file_crlf: True`,
    # so one run can fill both groups.
    for eol in ("CRLF", "LF"):
        lines.extend(
            _normalized_group_lines(
                eol,
                eol_rewritten if eol == scaffold_eol else [],
                normalized[eol],
                nested=nested,
            )
        )

    if overrides:
        lines.append("")
        lines.append("  OVERRIDES:")
        for finding in overrides:
            lines.append(file_list_row(f".gitattributes: {finding}", depth=2))

    return lines


def _normalized_group_lines(
    eol: str, scaffold: list[str], renormalized: list[str], *, nested: bool
) -> list[str]:
    """`NORMALIZED EOL TO <eol>:`: ADT's own files in full, then the first ten
    renormalized ones and one count row.

    Only the renormalized half is capped, because only it is bounded by the
    repository: a big one printed a row per file, 20 000 of them (ADT #943).
    The scaffold half is bounded by the template, and it goes first, since on a
    real project `apex_deployment/` sorts ahead of `config/` and the cap hid
    every scaffold file ADT had just put right (ADT #944, measured live).
    """
    first = _sorted_paths(scaffold)
    rest = _sorted_paths([path for path in renormalized if path not in set(first)])
    if not first and not rest:
        return []
    kept, remaining = capped(rest)
    lines = _init_group_lines(
        f"NORMALIZED EOL TO {eol}", [*first, *kept], nested=nested, ordered=True
    )
    if remaining:
        lines.append(more_row(remaining, depth=2))
    return lines


def _sorted_paths(paths: list[str]) -> list[str]:
    return sorted(set(paths), key=lambda path: path.lstrip(".").lower())
