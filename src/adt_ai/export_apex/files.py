from __future__ import annotations

import re
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

from adt_ai.export_apex.inventory import ApexApplication


@dataclass(frozen=True)
class ApexFileResolver:
    root          : Path
    path_apex     : str = "apex/"
    path_app      : str = "{$APP_ID}_{$APP_ALIAS}"
    path_files    : str = "files/"
    path_rest     : str = "workspace/rest/"
    workspace_dir : str = "workspace/"
    # Schema bound at runtime (per export loop); substitutes <schema> in path_apex.
    schema        : str = ""

    @classmethod
    def from_config(cls, root: Path, config: dict[str, Any]) -> ApexFileResolver:
        return cls(
            root          = root,
            path_apex     = str(config.get("path_apex") or "apex/"),
            path_app      = str(config.get("apex_path_app") or "{$APP_ID}_{$APP_ALIAS}"),
            path_files    = str(config.get("apex_path_files") or "files/"),
            path_rest     = str(config.get("apex_path_rest") or "workspace/rest/"),
            workspace_dir = str(config.get("apex_workspace_dir") or "workspace/"),
        )

    def for_schema(self, schema: str) -> ApexFileResolver:
        return replace(self, schema=schema or "")

    def apex_root(self) -> Path:
        rendered = self.path_apex.replace("<schema>", (self.schema or "").lower())
        return self.root / _clean_relative(rendered)

    def app_root(self, application: ApexApplication) -> Path:
        return self.apex_root() / _render_app_folder(self.path_app, application)

    def workspace_root(self) -> Path:
        return self.apex_root() / _clean_relative(self.workspace_dir)

    def full_export(self, application: ApexApplication) -> Path:
        return self.app_root(application) / f"f{application.app_id}.sql"

    def split_export(self, application: ApexApplication, relative_path: str) -> Path:
        return self.app_root(application) / _clean_relative(relative_path)

    def readable_export(self, application: ApexApplication, source_path: str) -> Path:
        path = _clean_relative(source_path)
        text = path.as_posix()
        prefix = "readable/"
        if text.startswith(prefix):
            text = text[len(prefix):]
        if text.startswith("application/"):
            text = text[len("application/"):]
            if text == f"f{application.app_id}.yaml":
                return self.app_root(application) / text
            if text == "page_groups.yaml":
                return self.app_root(application) / "application" / "pages" / text
            if re.match(r"^pages/p\d", text):
                text = re.sub(r"^pages/p", "application/pages/page_", text)
                return self.app_root(application) / text
            return self.app_root(application) / "application" / text
        if text.startswith("workspace/"):
            return self.workspace_root() / text[len("workspace/"):]
        return self.app_root(application) / text

    def embedded_export(self, application: ApexApplication, relative_path: str) -> Path:
        return self.app_root(application) / "embedded_code" / _clean_relative(relative_path)

    def rest_export(self, module_name: str) -> Path:
        name = module_name.strip("/")
        if not name.endswith(".sql"):
            name = f"{name}.sql"
        return self.apex_root() / _clean_relative(self.path_rest) / _clean_relative(name)

    def application_file(self, application: ApexApplication, relative_path: str) -> Path:
        return self.app_root(application) / _clean_relative(self.path_files) / _clean_relative(relative_path)

    def workspace_file(self, relative_path: str) -> Path:
        return self.workspace_root() / _clean_relative(self.path_files) / _clean_relative(relative_path)


def _render_app_folder(template: str, application: ApexApplication) -> Path:
    rendered = template
    replacements = {
        "{$APP_ID}": str(application.app_id),
        "{$APP_ALIAS}": application.app_alias,
        "{$APP_NAME}": application.app_name,
        "{$APP_GROUP}": application.app_group,
    }
    for token, value in replacements.items():
        rendered = rendered.replace(token, value or "")
    return _clean_relative(rendered)


def _clean_relative(path: str | Path) -> Path:
    text = str(path).replace("\\", "/").strip("/")
    if not text:
        return Path()
    parts = [part for part in text.split("/") if part and part != "."]
    if any(part == ".." for part in parts):
        raise ValueError(f"Path must stay under the APEX export root: {path}")
    return Path(*parts)
