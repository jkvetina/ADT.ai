from __future__ import annotations

import math
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


class InvalidConfigValueError(ConfigError):
    """A config file that was found and read, but holds a value ADT cannot use.

    Separated from the not-found failures because the remedies have nothing in
    common: "run from a project folder" is noise above a malformed value, and
    `CONFIGURATION NOT FOUND` above one sends the reader hunting for a file that
    is sitting right there. The CLI branches its error banner on this class, so
    a new invalid-value error only has to inherit it to be reported correctly.
    """


class ConfigCycleError(InvalidConfigValueError):
    """Raised when explicit configuration inheritance contains a cycle.

    Every file in the cycle exists, so it is an invalid configuration: as a bare
    `ConfigError` it took `CONFIGURATION NOT FOUND` and its create-a-folder
    remedy (ADT #923).
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
            raise ConfigNotFoundError(f"CONFIG FILE NOT FOUND: {filename}")

        result = ConfigResult(data={}, files=[])
        for candidate in candidates:
            loaded = self._load_file(candidate.resolve(), stack=[])
            result = _merge_results(result, loaded)
        return result

    def _load_file(self, path: Path, stack: list[Path]) -> ConfigResult:
        if path in stack:
            raise ConfigCycleError(f"CONFIG INHERITANCE CYCLE: {path}")

        # `ValueError` is two failures that are not a `YAMLError`, both measured
        # as `UNEXPECTED ERROR` naming no file (ADT #923): a file that is not
        # UTF-8 (a cp1250 Czech comment) and a value YAML builds and rejects
        # itself (`2026-13-45` has a date's shape and no date's month).
        try:
            raw_data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        except (OSError, ValueError, yaml.YAMLError) as error:
            raise InvalidConfigValueError(
                f"COULD NOT READ CONFIG FILE: {path}\n\n{error}"
            ) from error
        if not isinstance(raw_data, dict):
            raise InvalidConfigValueError(
                f"CONFIG FILE IS NOT A YAML MAPPING: {path}"
            )

        result = ConfigResult(data={}, files=[])
        for parent in _as_list(raw_data.get("extends"), path):
            parent_path = self._resolve_parent(parent, path.parent)
            result = _merge_results(result, self._load_file(parent_path, [*stack, path]))

        local_data = {key: value for key, value in raw_data.items() if key != "extends"}
        _reject_invalid_timeouts(local_data, path)
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

        raise ConfigNotFoundError(f"CONFIG PARENT NOT FOUND: {value}")


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
        f"UNRESOLVED PLACEHOLDER IN CONFIG {key}: {tokens}\n\n"
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


def as_int(value: Any) -> int:
    """``int(value)``, with the argument narrowed so a checker can read it.

    A config value arrives as whatever YAML made of it, which is ``object`` to
    a reader, and ``int(object)`` is not something a type checker accepts. This
    is the one place that narrowing is written down, beside ``is_enabled``.

    **It changes nothing about what a value MEANS**, failures included: a
    number or a numeric string converts, and anything else raises, exactly as
    the inline ``int()`` calls it replaces did. Whether a malformed key is
    fatal stays each caller's own decision, and each one already had it.
    """
    if not isinstance(value, int | float | str):
        raise TypeError(f"expected a number, got {type(value).__name__}")
    return int(value)


#: The driver timeouts `timeout_or_default` reads. `rest_timeout_seconds` is not
#: one: it keeps its own rule in `export_apex/rest.py`, where 0 is the default.
TIMEOUT_KEYS = ("connect_timeout_seconds", "query_timeout_seconds")


def timeout_or_default(value: Any, default: int, *, key: str) -> int | None:
    """A timeout key's seconds: ``None`` for no timeout, ``default`` when unset.

    The rule ``docs/config.md`` states for the two driver timeouts:

    - absent or not a number takes the default. The gateway read them through a
      bare ``int()``, so ``query_timeout_seconds: 20m`` ended every database
      command on ``UNEXPECTED ERROR`` naming neither the key nor the file (#923);
    - ``0`` means no timeout at all (#924 F61, Jan 2026-09-23). It used to reach
      the driver as itself, which was "no timeout" for ``call_timeout`` and a
      connect that fails at once for ``tcp_connect_timeout``;
    - below 0 raises ``InvalidConfigValueError`` naming the key. ``ConfigLoader``
      calls this per file, so the screen names the file as well;
    - a fraction of a second rounds UP to 1, because ``int(0.5)`` is 0 and would
      read as no timeout.
    """
    if value is None or not isinstance(value, int | float | str):
        return default
    try:
        seconds = float(value)
    except (ValueError, OverflowError):
        return default
    if not math.isfinite(seconds):
        return default
    if seconds < 0:
        raise InvalidConfigValueError(
            f"{key} IS {value!r}\n\n"
            "Use 0 for no timeout or a positive number of seconds."
        )
    if seconds == 0:
        return None
    return max(int(seconds), 1)


def _reject_invalid_timeouts(data: dict[str, Any], path: Path) -> None:
    """Refuse a negative timeout while the file holding it is still known (#924 F61)."""
    for key in TIMEOUT_KEYS:
        try:
            timeout_or_default(data.get(key), 0, key=key)
        except InvalidConfigValueError as error:
            # The key's own headline leads and the file joins the detail under
            # it (ADT #934).
            headline, _, detail = str(error).partition("\n\n")
            raise InvalidConfigValueError(
                f"{headline}\n\nConfig file: {path}\n{detail}"
            ) from error


def _as_list(value: Any, path: Path) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [value]
    if isinstance(value, list) and all(isinstance(item, str) for item in value):
        return value
    # The file was found and read, so this is its value being wrong, never a
    # missing configuration (ADT #923).
    raise InvalidConfigValueError(
        f"'extends' MUST BE A STRING OR A LIST OF STRINGS\n\nConfig file: {path}"
    )


def _merge_results(base: ConfigResult, overlay: ConfigResult) -> ConfigResult:
    return ConfigResult(
        data    = deep_merge(base.data, overlay.data),
        files   = [*base.files, *overlay.files],
    )
