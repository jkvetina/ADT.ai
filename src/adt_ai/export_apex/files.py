from __future__ import annotations

import re
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

from adt_ai.export_apex.inventory import ApexApplication
from adt_ai.shared.config import reject_unresolved_placeholders
from adt_ai.shared.path_template import (
    APEX_APP_TOKEN_NAMES,
    DEFAULT_PATH_APP,
    contains_run,
    render_path_template,
)


@dataclass(frozen=True)
class ApexFileResolver:
    root          : Path
    path_apex     : str = "apex/"
    path_app      : str = DEFAULT_PATH_APP
    path_files    : str = "files/"
    path_rest     : str = "workspace/rest/"
    workspace_dir : str = "workspace/"
    # Schema bound at runtime (per export loop); substitutes <schema> in
    # path_apex, or <SCHEMA> when the folders are spelled uppercase.
    schema        : str = ""

    @classmethod
    def from_config(cls, root: Path, config: dict[str, Any]) -> ApexFileResolver:
        return cls(
            root          = root,
            # `path_apex` resolves only the schema token, so every other one is a
            # folder name waiting to be written; reject it here, before the
            # export builds it (ADT #411).
            path_apex     = reject_unresolved_placeholders(
                str(config.get("path_apex") or "apex/"),
                key     = "path_apex",
                allowed = ("schema",),
            ),
            # `apex_path_app` is the one key written in the old-ADT `{$NAME}`
            # dialect, and it went through no guard at all until ADT #474:
            # measured on `'{$APP_ID}_{$APP_VERSION}'`, the export built a folder
            # literally called `100_{$APP_VERSION}`, which is exactly what the
            # other two keys have been refusing since #411. Angle brackets
            # resolve nothing here, so `allowed` is empty and one reaching this
            # template is a typo rather than a token.
            path_app      = reject_unresolved_placeholders(
                str(config.get("apex_path_app") or DEFAULT_PATH_APP),
                key           = "apex_path_app",
                allowed       = (),
                curly_allowed = APEX_APP_TOKEN_NAMES,
            ),
            path_files    = str(config.get("apex_path_files") or "files/"),
            path_rest     = str(config.get("apex_path_rest") or "workspace/rest/"),
            workspace_dir = str(config.get("apex_workspace_dir") or "workspace/"),
        )

    def for_schema(self, schema: str) -> ApexFileResolver:
        return replace(self, schema=schema or "")

    def apex_root(self) -> Path:
        rendered = render_path_template(self.path_apex, schema=self.schema or "")
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

    def apexlang_root(self, application: ApexApplication) -> Path:
        return self.app_root(application) / "apexlang"

    def apexlang_export(self, application: ApexApplication, relative_path: str) -> Path:
        # APEXlang member names are already the folder tree APEX wants
        # (`application.apx`, `pages/…`, `.apex/apexlang.json`), so the layout is
        # copied verbatim under one `apexlang/` root beside `readable/` and
        # `embedded_code/`.
        return self.apexlang_root(application) / _clean_relative(relative_path)

    def stale_checksum_files(self) -> list[Path]:
        """Every `checksum.txt` this schema's apex root still carries.

        `-checksum` wrote one per application folder while it was an export
        format (ADT #28). The fingerprint moved into
        `config/internal/apex_apps.yaml` (ADT #343), so those files are stale
        wherever they sit, including under applications the current run does
        not touch and in a repository a colleague exported. That is why the
        search covers the whole tree rather than the run's own app list.

        Static files are the one channel that can legitimately produce a file
        by that name, `-files` writes whatever the application stores, so
        anything inside the configured static-files folder is left alone.
        """
        root = self.apex_root()
        if not root.is_dir():
            return []
        files_parts = _clean_relative(self.path_files).parts
        return [
            path
            for path in sorted(root.rglob("checksum.txt"))
            if not contains_run(path.parent.relative_to(root).parts, files_parts)
        ]

    def rest_export(self, module_name: str) -> Path:
        name = module_name.strip("/")
        if not name.endswith(".sql"):
            name = f"{name}.sql"
        return self.apex_root() / _clean_relative(self.path_rest) / _clean_relative(name)

    def application_file(self, application: ApexApplication, relative_path: str) -> Path:
        return (
            self.app_root(application)
            / _clean_relative(self.path_files)
            / _clean_relative(relative_path)
        )

    def workspace_file(self, relative_path: str) -> Path:
        return (
            self.workspace_root()
            / _clean_relative(self.path_files)
            / _clean_relative(relative_path)
        )


def _render_app_folder(template: str, application: ApexApplication) -> Path:
    # Keyed on `APEX_APP_TOKEN_NAMES` so the vocabulary this writer substitutes,
    # the one the guard accepts and the one `patch/layout.py` reads back are one
    # list rather than three (ADT #474).
    values = {
        "APP_ID"   : str(application.app_id),
        "APP_ALIAS": application.app_alias,
        "APP_NAME" : application.app_name,
        "APP_GROUP": application.app_group,
    }
    rendered = template
    for name in APEX_APP_TOKEN_NAMES:
        rendered = rendered.replace(f"{{${name}}}", values[name] or "")
    return _clean_relative(rendered)


def _clean_relative(path: str | Path) -> Path:
    text = str(path).replace("\\", "/").strip("/")
    if not text:
        return Path()
    parts = [part for part in text.split("/") if part and part != "."]
    if any(part == ".." for part in parts):
        raise ValueError(f"Path must stay under the APEX export root: {path}")
    return Path(*parts)
