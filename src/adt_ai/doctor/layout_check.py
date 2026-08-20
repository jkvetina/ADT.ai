"""Reporting a repo whose schema folders disagree with the configured template.

Flipping `path_objects` between `<schema>` and `<SCHEMA>` changes what the next
export WRITES, and moves nothing that is already on disk. On Linux and in CI
that leaves two parallel trees; on macOS and Windows the case-only difference is
invisible to the filesystem and shows up later as a rename nobody made. So the
switch alone does not finish the customer's migration, and the part that does is
a rename ADT will not perform on its own: a repo-wide `git mv` is the user's
call, not a side effect of running `doctor` (ADT #411).

`doctor` diagnoses setups before any config exists, so every branch here is
optional by construction: no config, no template, no schema level, or no folders
on disk each mean there is nothing to report, and the whole section disappears.
"""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any

from adt_ai.shared.config import (
    DEFAULT_PATH_OBJECTS,
    ConfigError,
    reject_unresolved_placeholders,
)
from adt_ai.shared.path_template import (
    is_schema_placeholder,
    object_type_token,
    render_cased,
    schema_token,
)

# The keys whose value is a path template carrying a schema level, with the
# tokens each one can actually resolve.
_TEMPLATE_KEYS = (
    ("path_objects", ("schema", "object_type")),
    ("path_apex", ("schema",)),
)


def schema_case_action_lines(root: Path, config: Mapping[str, Any] | None) -> list[str]:
    """Rows offering the rename, or nothing at all when the tree already agrees."""
    if not isinstance(config, Mapping):
        return []

    lines: list[str] = []
    for key, allowed in _TEMPLATE_KEYS:
        template = _template_for(config, key, allowed)
        if template is None:
            continue
        mismatched = _mismatched_folders(Path(root), template)
        if not mismatched:
            continue
        token = schema_token(template)
        spelling = "uppercase" if render_cased(token or "", "a") == "A" else "lowercase"
        lines.append(
            f"  Schema folders do not match {key}: {token} writes them {spelling}, "
            f"the repo has {', '.join(sorted(current.name for current, _ in mismatched))}."
        )
        lines.append("  Rename them before the next export, git records a case-only move:")
        lines.extend(
            f"    git mv {current.relative_to(root).as_posix()} "
            f"{renamed.relative_to(root).as_posix()}"
            for current, renamed in mismatched
        )
    return lines


def _template_for(
    config : Mapping[str, Any],
    key    : str,
    allowed: tuple[str, ...],
) -> str | None:
    raw = config.get(key)
    if not raw and key != "path_objects":
        return None
    template = str(raw or DEFAULT_PATH_OBJECTS)
    try:
        template = reject_unresolved_placeholders(template, key=key, allowed=allowed)
    except ConfigError:
        # A template ADT refuses outright is the export's error to report, with
        # its own remedy. Repeating it here would be a second, worse telling.
        return None
    return template if schema_token(template) else None


def _mismatched_folders(root: Path, template: str) -> list[tuple[Path, Path]]:
    """Every existing schema folder whose case the template would not write."""
    token = schema_token(template)
    if token is None:
        return []
    head, _, _ = template.strip("/").partition(object_type_token(template) or "<object_type>")
    head = head.strip("/")
    before, _, _ = head.partition(token)
    depth = len(Path(before.strip("/")).parts) if before.strip("/") else 0

    mismatched: list[tuple[Path, Path]] = []
    for candidate in sorted(root.glob(head.replace(token, "*"))):
        if not candidate.is_dir():
            continue
        parts = candidate.relative_to(root).parts
        if depth >= len(parts):
            continue
        name = parts[depth]
        if is_schema_placeholder(name):
            continue
        expected = render_cased(token, name)
        if expected == name:
            continue
        current = root.joinpath(*parts[: depth + 1])
        renamed = root.joinpath(*parts[:depth], expected)
        if (current, renamed) not in mismatched:
            mismatched.append((current, renamed))
    return mismatched
