from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from adt_ai.shared.dict_merge import deep_merge
from adt_ai.shared.path_template import (
    KNOWN_TOKEN_NAMES,
    supported_spelling,
    unsupported_curly_tokens,
    unsupported_tokens,
)

# Default export layout when `path_objects` is not configured. Shared by
# export_db, export_data and patch (`patch/layout.py`, `patch/files.py`) so every
# reader of the key lands on one tree.
#
# `patch` spelled its own fallback twice until ADT #554, and the two disagreed:
# `layout.py` matched this string, `files.py` read a bare `database` carrying no
# placeholder at all, so an unconfigured project collapsed every schema into one
# unscoped INSTALL.sql. A default spelled per module is a default nothing can
# check, which is the whole reason this constant exists rather than a literal.
DEFAULT_PATH_OBJECTS = "database/<schema>/<object_type>"


class ConfigError(Exception):
    """Base error for configuration loading failures."""


class ConfigNotFoundError(ConfigError):
    """Raised when a requested configuration file cannot be found."""


class ConfigCycleError(ConfigError):
    """Raised when explicit configuration inheritance contains a cycle."""


class InvalidConfigValueError(ConfigError):
    """A config file that was found and read, but holds a value ADT cannot use.

    Separated from the not-found failures because the remedies have nothing in
    common: "run from a project folder" is noise above a malformed value, and
    `CONFIGURATION NOT FOUND` above one sends the reader hunting for a file that
    is sitting right there. The CLI branches its error banner on this class, so
    a new invalid-value error only has to inherit it to be reported correctly.
    """


class UnresolvedPlaceholderError(InvalidConfigValueError):
    """Raised when a path template still carries an old-ADT `{$NAME}` token."""


@dataclass(frozen=True)
class ConfigResult:
    data    : dict[str, Any]
    files   : list[Path]


class ConfigLoader:
    def __init__(self, search_paths: list[Path] | tuple[Path, ...]) -> None:
        self.search_paths = [Path(path) for path in search_paths]

    def load(self, filename: str = "config.yaml") -> ConfigResult:
        candidates = [path / filename for path in self.search_paths if (path / filename).is_file()]
        if not candidates:
            raise ConfigNotFoundError(f"Config file not found in search paths: {filename}")

        result = ConfigResult(data={}, files=[])
        for candidate in candidates:
            loaded = self._load_file(candidate.resolve(), stack=[])
            result = _merge_results(result, loaded)
        return result

    def _load_file(self, path: Path, stack: list[Path]) -> ConfigResult:
        if path in stack:
            raise ConfigCycleError(f"Config inheritance cycle detected: {path}")

        raw_data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        if not isinstance(raw_data, dict):
            raise ConfigError(f"Config file must contain a YAML mapping: {path}")

        result = ConfigResult(data={}, files=[])
        for parent in _as_list(raw_data.get("extends")):
            parent_path = self._resolve_parent(parent, path.parent)
            result = _merge_results(result, self._load_file(parent_path, [*stack, path]))

        local_data = {key: value for key, value in raw_data.items() if key != "extends"}
        local_result = ConfigResult(data=local_data, files=[path])
        return _merge_results(result, local_result)

    def _resolve_parent(self, value: str, current_dir: Path) -> Path:
        parent = Path(value).expanduser()
        if parent.is_absolute() and parent.is_file():
            return parent.resolve()

        local = current_dir / parent
        if local.is_file():
            return local.resolve()

        for search_path in self.search_paths:
            candidate = search_path / parent
            if candidate.is_file():
                return candidate.resolve()

        raise ConfigNotFoundError(f"Config parent not found: {value}")


def reject_unresolved_placeholders(
    template: str,
    *,
    key          : str = "path_objects",
    allowed      : Sequence[str] = KNOWN_TOKEN_NAMES,
    curly_allowed: Sequence[str] = (),
) -> str:
    """Return ``template``, or raise when it holds a token nothing will resolve.

    A path template is turned straight into folder names, so an unresolved token
    does not fail, it exports into a directory named after the placeholder.
    Old ADT ships ``#path_objects : '{$INFO_SCHEMA}/database/'`` as a commented
    example, so this is the copy-paste every migrating project makes; the
    failure has to be loud rather than a real folder called ``{$INFO_SCHEMA}``
    shadowing the tracked tree (851 files, 2026-08-01).

    The angle-bracket kind is checked the same way and for the same reason. The
    substitution is an exact-match ``str.replace`` per known spelling, so every
    other one reached the filesystem intact: a project trying ``<SCHEMA>`` before
    ADT #411 built a folder literally called ``<SCHEMA>``, and ``export_db`` then
    read the missing ``<schema>`` as a schema-less layout and collapsed every
    schema into one tree. Two failures from one typo, both silent.

    ``curly_allowed`` names the ``{$TOKEN}`` spellings this key DOES resolve, and
    is empty everywhere but ``apex_path_app`` (ADT #474). That key is the one
    written in the old-ADT dialect, and it went through no guard at all: measured
    on ``'{$APP_ID}_{$APP_VERSION}'``, the export created a folder literally
    called ``100_{$APP_VERSION}``, which is this failure in the one dialect that
    was not watched for it.
    """
    old_adt = unsupported_curly_tokens(str(template), curly_allowed)
    unknown = unsupported_tokens(str(template), allowed)
    if not old_adt and not unknown:
        return str(template)

    tokens = ", ".join(sorted(set(old_adt)) + unknown)
    reason = (
        "'{$NAME}' is old ADT syntax and would be written out as a literal folder name."
        if old_adt and not curly_allowed
        else "an unrecognised token is written out as a literal folder name."
    )
    # The casing advice belongs to the angle-bracket dialect, so a key written in
    # the other one is not told about a token it cannot carry.
    casing = (
        "  A schema token carries its own case, so '<schema>' writes 'app_owner/' and "
        "'<SCHEMA>' writes 'APP_OWNER/'. An object type folder is spelled by "
        "object_types in config.yaml, so '<object_type>' has no cased form.\n"
        if "schema" in allowed
        else ""
    )
    example = (
        "'<schema>/database/<object_type>/'"
        if "schema" in allowed
        else "'{$APP_ID}_{$APP_ALIAS}'"
    )
    raise UnresolvedPlaceholderError(
        f"Unresolved placeholder in config {key}: {tokens}\n"
        f"  Value: {template}\n"
        f"  ADT.ai substitutes only {supported_spelling(allowed, curly_allowed)} in "
        f"{key}; {reason}\n"
        f"{casing}"
        f"  Fix {key} in config.yaml (e.g. {example})."
    )


def is_enabled(value: Any, default: bool = False) -> bool:
    """Interpret a config flag: bools pass through, strings accept 1/TRUE/Y/YES/ON."""
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    return str(value).strip().upper() in {"1", "TRUE", "Y", "YES", "ON"}


def _as_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [value]
    if isinstance(value, list) and all(isinstance(item, str) for item in value):
        return value
    raise ConfigError("Config extends must be a string or list of strings")


def _merge_results(base: ConfigResult, overlay: ConfigResult) -> ConfigResult:
    return ConfigResult(
        data    = deep_merge(base.data, overlay.data),
        files   = [*base.files, *overlay.files],
    )
