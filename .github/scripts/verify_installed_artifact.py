#!/usr/bin/env python3
"""Verify and smoke-test the exact ADT.ai wheel produced by CI."""

from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
import tarfile
import tomllib
import zipfile
from email.parser import Parser
from pathlib import Path, PurePosixPath
from typing import NamedTuple

PYTHON_FLOOR = ">=3.14"
PATCH_TEMPLATE_FILES = (
    "config/patch_template/README.md",
    "config/patch_template/apex_init/00_init.sql",
    "config/patch_template/db_end/70_mviews.sql",
    "config/patch_template/db_end/80_jobs.sql",
    "config/patch_template/db_end/90_checks.sql",
    "config/patch_template/db_init/00_init.sql",
)
SCAFFOLD_FILES = (
    ".gitignore",
    "config/IDENTITY.yaml",
    "config/config.yaml",
    *PATCH_TEMPLATE_FILES,
    "connections/.gitkeep",
    "connections/wallets/.gitkeep",
)
RESOURCE_ROOT = "adt_ai/doctor/resources/"


class ArtifactPair(NamedTuple):
    wheel: Path
    sdist: Path


def _only(paths: list[Path], label: str) -> Path:
    if len(paths) != 1:
        names = ", ".join(path.name for path in paths) or "none"
        raise ValueError(f"expected exactly one {label}, found {names}")
    return paths[0]


def artifact_pair(dist: Path) -> ArtifactPair:
    dist = dist.resolve()
    if not dist.is_dir():
        raise ValueError(f"artifact directory does not exist: {dist}")
    wheel = _only(sorted(dist.glob("*.whl")), "wheel")
    sdist = _only(sorted(dist.glob("*.tar.gz")), "sdist")
    return ArtifactPair(wheel=wheel, sdist=sdist)


def _wheel_files(wheel: Path) -> dict[str, bytes]:
    with zipfile.ZipFile(wheel) as archive:
        return {
            name: archive.read(name)
            for name in archive.namelist()
            if not name.endswith("/")
        }


def _sdist_files(sdist: Path) -> dict[str, bytes]:
    with tarfile.open(sdist, "r:gz") as archive:
        members = [member for member in archive.getmembers() if member.isfile()]
        roots = {PurePosixPath(member.name).parts[0] for member in members}
        if len(roots) != 1:
            raise ValueError(f"sdist must have one top-level directory, found {sorted(roots)}")
        files: dict[str, bytes] = {}
        for member in members:
            parts = PurePosixPath(member.name).parts[1:]
            extracted = archive.extractfile(member)
            if parts and extracted is not None:
                files[PurePosixPath(*parts).as_posix()] = extracted.read()
        return files


def _source_path(wheel_path: str) -> str | None:
    if ".dist-info/" in wheel_path:
        return None
    if wheel_path == f"{RESOURCE_ROOT}.gitignore":
        return ".gitignore"
    if wheel_path == f"{RESOURCE_ROOT}requirements.txt":
        return "requirements.txt"
    patch_prefix = f"{RESOURCE_ROOT}config/patch_template/"
    if wheel_path.startswith(patch_prefix):
        return wheel_path.removeprefix(RESOURCE_ROOT)
    if wheel_path.startswith("adt_ai/"):
        return f"src/{wheel_path}"
    raise ValueError(f"wheel contains unexpected payload outside adt_ai: {wheel_path}")


def _verify_python_floor(wheel_files: dict[str, bytes], sdist_files: dict[str, bytes]) -> None:
    metadata_names = [name for name in wheel_files if name.endswith(".dist-info/METADATA")]
    metadata_name = _only([Path(name) for name in metadata_names], "wheel METADATA")
    metadata = Parser().parsestr(wheel_files[metadata_name.as_posix()].decode("utf-8"))
    if metadata.get("Requires-Python") != PYTHON_FLOOR:
        raise ValueError(
            f"wheel Requires-Python is {metadata.get('Requires-Python')!r}, expected {PYTHON_FLOOR}"
        )

    try:
        pyproject = tomllib.loads(sdist_files["pyproject.toml"].decode("utf-8"))
        declared = pyproject["project"]["requires-python"]
    except (KeyError, tomllib.TOMLDecodeError) as error:
        raise ValueError("sdist has no readable project.requires-python") from error
    if declared != PYTHON_FLOOR:
        raise ValueError(f"sdist requires-python is {declared!r}, expected {PYTHON_FLOOR}")


def _verify_single_version_source(
    wheel_files: dict[str, bytes], sdist_files: dict[str, bytes]
) -> None:
    """The built metadata version is the packaged `__version__`, not a copy.

    `pyproject.toml` used to restate the number `src/adt_ai/__init__.py`
    declares, and the two were kept in step by a test. This is the same
    property asserted where it actually matters: on the artifact a user
    installs, after the backend has resolved it.
    """
    metadata_name = _only(
        [Path(name) for name in wheel_files if name.endswith(".dist-info/METADATA")],
        "wheel METADATA",
    )
    metadata = Parser().parsestr(wheel_files[metadata_name.as_posix()].decode("utf-8"))
    built = metadata.get("Version")

    init = wheel_files.get("adt_ai/__init__.py")
    if init is None:
        raise ValueError("wheel carries no adt_ai/__init__.py to read a version from")
    match = re.search(r"""^__version__\s*=\s*["']([^"']+)["']""", init.decode("utf-8"), re.M)
    if match is None:
        raise ValueError("packaged adt_ai/__init__.py declares no __version__")
    declared = match.group(1)

    if built != declared:
        raise ValueError(
            f"built metadata version is {built!r}, but the package declares {declared!r}"
        )

    try:
        pyproject = tomllib.loads(sdist_files["pyproject.toml"].decode("utf-8"))
    except (KeyError, tomllib.TOMLDecodeError) as error:
        raise ValueError("sdist has no readable pyproject.toml") from error
    project = pyproject.get("project", {})
    if "version" in project:
        raise ValueError("pyproject.toml restates the version; it must stay dynamic")
    if project.get("dynamic") != ["version"]:
        raise ValueError(f"pyproject.toml dynamic fields are {project.get('dynamic')!r}")


def _verify_patch_resources(wheel_files: dict[str, bytes]) -> None:
    prefix = f"{RESOURCE_ROOT}config/patch_template/"
    actual = {
        f"config/patch_template/{name.removeprefix(prefix)}"
        for name in wheel_files
        if name.startswith(prefix)
    }
    expected = set(PATCH_TEMPLATE_FILES)
    if actual != expected:
        raise ValueError(
            "wheel patch-template resources differ: "
            f"missing={sorted(expected - actual)}, extra={sorted(actual - expected)}"
        )


def verify_archives(dist: Path) -> ArtifactPair:
    pair = artifact_pair(dist)
    wheel_files = _wheel_files(pair.wheel)
    sdist_files = _sdist_files(pair.sdist)
    _verify_python_floor(wheel_files, sdist_files)
    _verify_single_version_source(wheel_files, sdist_files)
    _verify_patch_resources(wheel_files)

    for wheel_path, wheel_payload in wheel_files.items():
        source_path = _source_path(wheel_path)
        if source_path is None:
            continue
        source_payload = sdist_files.get(source_path)
        if source_payload is None:
            raise ValueError(f"wheel payload is absent from sdist: {source_path}")
        if source_payload != wheel_payload:
            raise ValueError(f"{source_path} differs between sdist and wheel")
    return pair


def verify_scaffold(project: Path) -> None:
    actual = {
        path.relative_to(project).as_posix()
        for path in project.rglob("*")
        if path.is_file()
    }
    expected = set(SCAFFOLD_FILES)
    if actual != expected:
        raise ValueError(
            "installed scaffold differs: "
            f"missing={sorted(expected - actual)}, extra={sorted(actual - expected)}"
        )


def _verify_scaffold_payloads(project: Path, wheel: Path) -> None:
    wheel_files = _wheel_files(wheel)
    for relative in PATCH_TEMPLATE_FILES:
        packaged = wheel_files[f"{RESOURCE_ROOT}{relative}"]
        scaffolded = (project / relative).read_bytes()
        if scaffolded != packaged:
            raise ValueError(f"scaffolded resource differs from installed wheel: {relative}")


def _venv_python(venv: Path) -> Path:
    return venv / ("Scripts/python.exe" if os.name == "nt" else "bin/python")


def _run(command: list[str], *, cwd: Path, env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    print("+ " + " ".join(command), flush=True)
    result = subprocess.run(command, cwd=cwd, env=env, capture_output=True, text=True)
    if result.stdout:
        print(result.stdout, end="" if result.stdout.endswith("\n") else "\n")
    if result.stderr:
        print(result.stderr, file=sys.stderr, end="" if result.stderr.endswith("\n") else "\n")
    if result.returncode:
        raise RuntimeError(f"command exited {result.returncode}: {' '.join(command)}")
    return result


def smoke_installed_artifact(dist: Path, work_root: Path) -> None:
    pair = verify_archives(dist)
    source_root = Path(__file__).resolve().parents[2]
    work_root = work_root.resolve()
    if work_root == source_root or source_root in work_root.parents:
        raise ValueError(f"clean-room work root must be outside the source checkout: {work_root}")
    work_root.mkdir(parents=True, exist_ok=False)

    env = os.environ.copy()
    env.pop("PYTHONPATH", None)
    env.update({"CI": "1", "GITHUB_ACTIONS": "1", "PYTHONDONTWRITEBYTECODE": "1"})
    venv = work_root / "venv"
    _run([sys.executable, "-m", "venv", str(venv)], cwd=work_root, env=env)
    python = _venv_python(venv)
    _run(
        [str(python), "-m", "pip", "install", "--disable-pip-version-check", str(pair.wheel)],
        cwd=work_root,
        env=env,
    )
    _run([str(python), "-m", "pip", "check"], cwd=work_root, env=env)

    imported = _run(
        [
            str(python),
            "-c",
            "from pathlib import Path; import adt_ai; print(Path(adt_ai.__file__).resolve())",
        ],
        cwd=work_root,
        env=env,
    )
    installed_path = Path(imported.stdout.strip())
    if venv not in installed_path.parents:
        raise ValueError(f"adt_ai imported outside the clean venv: {installed_path}")

    project = work_root / "project"
    _run(
        [str(python), "-m", "adt_ai", "doctor", "-init", "-root", str(project), "-nobeep"],
        cwd=work_root,
        env=env,
    )
    verify_scaffold(project)
    _verify_scaffold_payloads(project, pair.wheel)
    _run(
        [str(python), "-m", "adt_ai", "doctor", "-offline", "-root", str(project), "-nobeep"],
        cwd=work_root,
        env=env,
    )


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    archives = subparsers.add_parser("archives", help="verify the wheel/sdist pair")
    archives.add_argument("--dist", type=Path, required=True)
    smoke = subparsers.add_parser("smoke", help="install and exercise the exact wheel")
    smoke.add_argument("--dist", type=Path, required=True)
    smoke.add_argument("--work-root", type=Path, required=True)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    if args.command == "archives":
        pair = verify_archives(args.dist)
        print(f"verified archives: {pair.wheel.name}, {pair.sdist.name}")
    else:
        smoke_installed_artifact(args.dist, args.work_root)
        print("installed artifact smoke: OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
