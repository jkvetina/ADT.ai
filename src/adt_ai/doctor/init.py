from __future__ import annotations

from pathlib import Path

from adt_ai.doctor._base import (
    PROJECT_CONFIG_TEMPLATE,
    DoctorResult,
    _init_group_lines,
)


class DoctorInitMixin:
    def _init_project(self, request: object) -> DoctorResult:  # type: ignore[override]
        # request is DoctorRequest; typed as object to avoid circular import
        root = (request.root or Path(".")).expanduser().resolve()  # type: ignore[union-attr]
        lines: list[str] = []
        self._add(lines, "PROJECT INIT:")  # type: ignore[attr-defined]
        created: list[Path] = []
        skipped: list[Path] = []
        source_gitignore = (self.package_root / ".gitignore").read_text(  # type: ignore[attr-defined]
            encoding="utf-8"
        )

        for relative_path, content in (
            (Path("config/config.yaml"), PROJECT_CONFIG_TEMPLATE),
            (Path(".gitignore"), source_gitignore),
            (Path("connections/.gitkeep"), ""),
            (Path("connections/wallets/.gitkeep"), ""),
        ):
            path = root / relative_path
            if path.exists() and not request.force:  # type: ignore[union-attr]
                skipped.append(relative_path)
                continue
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8")
            created.append(relative_path)

        self._extend(lines, _init_group_lines("CREATED", root, created))  # type: ignore[attr-defined]
        self._extend(lines, _init_group_lines("SKIPPED", root, skipped))  # type: ignore[attr-defined]

        if skipped:
            self._add(lines, "")  # type: ignore[attr-defined]
            self._add(lines, "Use `adtai doctor -init -force` to overwrite generated files.")  # type: ignore[attr-defined]

        return DoctorResult(lines, performed_actions=[], exit_code=0)
