from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from adt_ai.dict_merge import deep_merge


class ConfigError(Exception):
    """Base error for configuration loading failures."""


class ConfigNotFoundError(ConfigError):
    """Raised when a requested configuration file cannot be found."""


class ConfigCycleError(ConfigError):
    """Raised when explicit configuration inheritance contains a cycle."""


@dataclass(frozen=True)
class ConfigResult:
    data    : dict[str, Any]
    files   : list[Path]
    sources : dict[tuple[str, ...], Path]

    def source_for(self, key_path: str | tuple[str, ...]) -> Path | None:
        if isinstance(key_path, str):
            key_path = tuple(part for part in key_path.split(".") if part)
        return self.sources.get(key_path)


class ConfigLoader:
    def __init__(self, search_paths: list[Path] | tuple[Path, ...]) -> None:
        self.search_paths = [Path(path) for path in search_paths]

    def load(self, filename: str = "config.yaml") -> ConfigResult:
        candidates = [path / filename for path in self.search_paths if (path / filename).is_file()]
        if not candidates:
            raise ConfigNotFoundError(f"Config file not found in search paths: {filename}")

        result = ConfigResult(data={}, files=[], sources={})
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

        result = ConfigResult(data={}, files=[], sources={})
        for parent in _as_list(raw_data.get("extends")):
            parent_path = self._resolve_parent(parent, path.parent)
            result = _merge_results(result, self._load_file(parent_path, [*stack, path]))

        local_data = {key: value for key, value in raw_data.items() if key != "extends"}
        local_result = ConfigResult(
            data    = local_data,
            files   = [path],
            sources = _leaf_sources(local_data, path),
        )
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
        sources = {**base.sources, **overlay.sources},
    )


def _leaf_sources(
    data: dict[str, Any],
    path: Path,
    prefix: tuple[str, ...] = (),
) -> dict[tuple[str, ...], Path]:
    sources: dict[tuple[str, ...], Path] = {}
    for key, value in data.items():
        key_path = (*prefix, str(key))
        if isinstance(value, dict):
            sources.update(_leaf_sources(value, path, key_path))
        else:
            sources[key_path] = path
    return sources
