from __future__ import annotations

from pathlib import Path

from adt_ai.doctor._base import (
    PROJECT_CONFIG_TEMPLATE,
    DoctorResult,
    _init_group_lines,
)
from adt_ai.shared import text_files

# The patch template scaffold ships with ADT.ai but is read from the *project*
# root, so `-init` is what actually puts it where `patch -create` looks (ADT #256).
# `#254` shipped it as a reference copy the tool never reads, which left every
# project that did not copy it in by hand with six silently empty template slots.
# Copied verbatim from the package, never re-authored here -- the same way the
# root `.gitignore` above is copied rather than templated.
PATCH_TEMPLATE_DIR = Path("config/patch_template")


def _patch_template_files(package_root: Path) -> list[tuple[Path, str]]:
    source_root = package_root / PATCH_TEMPLATE_DIR
    if not source_root.is_dir():
        return []
    return [
        (PATCH_TEMPLATE_DIR / path.relative_to(source_root), path.read_text(encoding="utf-8"))
        for path in sorted(source_root.rglob("*"))
        if path.is_file() and path.name != ".DS_Store"
    ]


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

        scaffold: list[tuple[Path, str]] = [
            (Path("config/config.yaml"), PROJECT_CONFIG_TEMPLATE),
            (Path(".gitignore"), source_gitignore),
            (Path("connections/.gitkeep"), ""),
            (Path("connections/wallets/.gitkeep"), ""),
            *_patch_template_files(self.package_root),  # type: ignore[attr-defined]
        ]

        for relative_path, content in scaffold:
            path = root / relative_path
            if path.exists() and not request.force:  # type: ignore[union-attr]
                skipped.append(relative_path)
                continue
            path.parent.mkdir(parents=True, exist_ok=True)
            text_files.write_text(path, content)
            created.append(relative_path)

        self._extend(lines, _init_group_lines("CREATED", root, created))  # type: ignore[attr-defined]
        self._extend(lines, _init_group_lines("SKIPPED", root, skipped))  # type: ignore[attr-defined]

        if skipped:
            self._add(lines, "")  # type: ignore[attr-defined]
            self._add(lines, "Use `adtai doctor -init -force` to overwrite generated files.")  # type: ignore[attr-defined]

        return DoctorResult(lines, performed_actions=[], exit_code=0)
