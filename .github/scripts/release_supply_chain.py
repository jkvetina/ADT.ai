#!/usr/bin/env python3
"""Carry one immutable ADT.ai artifact set through every release stage.

The release workflow builds the wheel and sdist once. Five stages then handle
those same two files — record, attest, assets, publish, downloaded — and GitHub
Actions has no opinion about whether a later job downloads that upload or
quietly rebuilds from its own checkout.

Rebuilding is not automatically a difference: measured on 2026-09-01 against
`adt_ai-0.9.5`, hatchling produced a byte-identical wheel from the same tree,
`800982f3c2f0…` twice. That is what makes the ledger necessary rather than
redundant. Reproducibility means a rebuild is *invisible*, so the artifact a
later job publishes is unchecked whenever its checkout differs at all from the
one the build and the attestation used — a moved tag, a different ref, a file
added between jobs. The attestation covers what the build job held; nothing
downstream previously proved it still held it.

This script is the ledger that closes that gap. `record` writes the digests of
the built pair once, beside the checksums and the SBOM; every later stage
re-derives the digests from the files it is actually holding, and compares the
whole set, so an added artifact fails as loudly as a changed one. `dry-run`
walks all five stages locally, so the path can be proven without publishing.

It deliberately cannot publish. There is no upload, no `gh` call, no `twine`,
and no subprocess: this measures, the workflow ships. That boundary is what
stops a locally built artifact from ever becoming a public release asset — the
local publish contract has no route to attach files at all, and the workflow
attaches only what it built and this ledger accepted.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import tarfile
import tomllib
import zipfile
from email.parser import Parser
from pathlib import Path, PurePosixPath
from typing import NamedTuple

PACKAGE_NAME = "adt-ai"
DISTRIBUTION = "adt_ai"
CHECKSUMS_NAME = "SHA256SUMS"
SBOM_NAME = f"{PACKAGE_NAME}-sbom.cdx.json"
MANIFEST_NAME = "release-manifest.json"

# The stages the workflow runs, in order. `dry-run` walks exactly this list, and
# the workflow names each stage when it calls `verify`, so a stage added to one
# and forgotten in the other shows up as an unknown stage rather than silently
# skipping the identity check.
RELEASE_STAGES = ("record", "attest", "assets", "publish", "downloaded")

# `v1.2.3` and nothing else. A release is MAJOR.MINOR.PATCH by the publish
# contract — no beta suffix, no fourth digit — so a candidate tag has no path
# into the publish job rather than one that fails halfway.
TAG_RE = re.compile(r"^(?:refs/tags/)?v(\d+\.\d+\.\d+)$")
CHANGELOG_HEADING_RE = re.compile(r"^## (\d+\.\d+\.\d+) - (\d{4}-\d{2}-\d{2})\s*$")
REQUIREMENT_NAME_RE = re.compile(r"^([A-Za-z0-9][A-Za-z0-9._-]*)")


class ArtifactDigest(NamedTuple):
    name: str
    sha256: str
    size: int


class ReleaseManifest(NamedTuple):
    package: str
    version: str
    tag: str
    files: tuple[ArtifactDigest, ...]

    def as_dict(self) -> dict:
        return {
            "package": self.package,
            "version": self.version,
            "tag": self.tag,
            "files": [entry._asdict() for entry in self.files],
        }

    @classmethod
    def from_dict(cls, payload: dict) -> ReleaseManifest:
        return cls(
            package=payload["package"],
            version=payload["version"],
            tag=payload["tag"],
            files=tuple(ArtifactDigest(**entry) for entry in payload["files"]),
        )


def _only(paths: list[Path], label: str) -> Path:
    if len(paths) != 1:
        names = ", ".join(path.name for path in paths) or "none"
        raise ValueError(f"expected exactly one {label}, found {names}")
    return paths[0]


def artifact_pair(dist: Path) -> tuple[Path, Path]:
    """The one wheel and one sdist the build produced, in asset order."""
    dist = dist.resolve()
    if not dist.is_dir():
        raise ValueError(f"artifact directory does not exist: {dist}")
    wheel = _only(sorted(dist.glob("*.whl")), "wheel")
    sdist = _only(sorted(dist.glob("*.tar.gz")), "sdist")
    return wheel, sdist


def _digest(path: Path) -> ArtifactDigest:
    payload = path.read_bytes()
    return ArtifactDigest(
        name=path.name, sha256=hashlib.sha256(payload).hexdigest(), size=len(payload)
    )


def artifact_digests(dist: Path) -> tuple[ArtifactDigest, ...]:
    """Digest every distributable file present, not only the expected two.

    Reading the directory rather than the manifest is the point: an extra wheel
    smuggled into `dist/` between stages has to change this tuple, and the
    comparison in `verify_stage` is what refuses it.
    """
    dist = dist.resolve()
    if not dist.is_dir():
        raise ValueError(f"artifact directory does not exist: {dist}")
    found = sorted(
        path
        for path in dist.iterdir()
        if path.is_file() and (path.suffix == ".whl" or path.name.endswith(".tar.gz"))
    )
    return tuple(_digest(path) for path in found)


def tag_version(tag: str) -> str:
    match = TAG_RE.match(tag.strip())
    if not match:
        raise ValueError(f"not a release tag: {tag!r}; expected refs/tags/vMAJOR.MINOR.PATCH")
    return match.group(1)


def _wheel_metadata(wheel: Path) -> Parser:
    with zipfile.ZipFile(wheel) as archive:
        names = [name for name in archive.namelist() if name.endswith(".dist-info/METADATA")]
        name = _only([Path(entry) for entry in names], "wheel METADATA")
        return Parser().parsestr(archive.read(name.as_posix()).decode("utf-8"))


def _sdist_member(sdist: Path, relative: str) -> bytes:
    with tarfile.open(sdist, "r:gz") as archive:
        for member in archive.getmembers():
            if not member.isfile():
                continue
            parts = PurePosixPath(member.name).parts[1:]
            if parts and PurePosixPath(*parts).as_posix() == relative:
                extracted = archive.extractfile(member)
                if extracted is not None:
                    return extracted.read()
    raise ValueError(f"sdist has no {relative}")


def artifact_version(dist: Path) -> str:
    """The version the built pair declares, with wheel and sdist cross-checked."""
    wheel, sdist = artifact_pair(dist)
    metadata = _wheel_metadata(wheel)
    declared_name = metadata.get("Name")
    if declared_name != PACKAGE_NAME:
        raise ValueError(f"wheel declares package {declared_name!r}, expected {PACKAGE_NAME}")
    version = metadata.get("Version")
    if not version:
        raise ValueError("wheel METADATA carries no Version")

    try:
        pyproject = tomllib.loads(_sdist_member(sdist, "pyproject.toml").decode("utf-8"))
        declared = pyproject["project"]["version"]
    except (KeyError, tomllib.TOMLDecodeError) as error:
        raise ValueError("sdist has no readable project.version") from error
    if declared != version:
        raise ValueError(f"wheel declares {version}, sdist declares {declared}")
    return version


def changelog_release(changelog: Path, version: str) -> str:
    """The dated heading the curated changelog carries for this version."""
    for line in changelog.read_text(encoding="utf-8").splitlines():
        match = CHANGELOG_HEADING_RE.match(line)
        if match and match.group(1) == version:
            return match.group(2)
    raise ValueError(f"changelog {changelog.name} has no '## {version} - YYYY-MM-DD' section")


def verify_consistency(*, dist: Path, tag: str, changelog: Path) -> tuple[str, str]:
    """Tag, wheel, sdist and changelog agree, or the release does not start."""
    version = artifact_version(dist)
    tagged = tag_version(tag)
    if tagged != version:
        raise ValueError(f"tag v{tagged} does not match the built artifact version {version}")
    return version, changelog_release(changelog, version)


def _requirements(wheel: Path) -> list[str]:
    return [
        value.strip()
        for value in _wheel_metadata(wheel).get_all("Requires-Dist", [])
        if value.strip()
    ]


def build_sbom(*, dist: Path, manifest: ReleaseManifest) -> dict:
    """A dependency-free CycloneDX 1.6 document for the built pair.

    Generated here rather than by `cyclonedx-py` on purpose. The release job
    pins its build tooling, and a generator that has to be installed is one more
    pinned input plus a network fetch inside the job that produces the evidence.
    Everything this document asserts is read out of the wheel's own METADATA and
    the ledger above, so it is reproducible offline and testable without a
    network.
    """
    wheel, _ = artifact_pair(dist)
    metadata = _wheel_metadata(wheel)
    components = []
    for requirement in _requirements(wheel):
        name_match = REQUIREMENT_NAME_RE.match(requirement)
        if not name_match:
            raise ValueError(f"unreadable requirement in wheel METADATA: {requirement!r}")
        name = name_match.group(1)
        components.append(
            {
                "type": "library",
                "name": name,
                "purl": f"pkg:pypi/{name}",
                "scope": "required",
                "properties": [{"name": f"{PACKAGE_NAME}:requirement", "value": requirement}],
            }
        )
    return {
        "bomFormat": "CycloneDX",
        "specVersion": "1.6",
        "version": 1,
        "metadata": {
            "component": {
                "type": "application",
                "name": PACKAGE_NAME,
                "version": manifest.version,
                "purl": f"pkg:pypi/{PACKAGE_NAME}@{manifest.version}",
                "licenses": [{"license": {"id": metadata.get("License") or "MIT"}}],
                "properties:artifacts": [
                    {
                        "name": entry.name,
                        "size": entry.size,
                        "hashes": [{"alg": "SHA-256", "content": entry.sha256}],
                    }
                    for entry in manifest.files
                ],
            },
            "properties": [
                {"name": f"{PACKAGE_NAME}:tag", "value": manifest.tag},
                {
                    "name": f"{PACKAGE_NAME}:requires-python",
                    "value": metadata.get("Requires-Python"),
                },
            ],
        },
        "components": components,
    }


def release_assets(*, dist: Path, out_dir: Path) -> list[Path]:
    """Exactly what the workflow attaches to the GitHub Release, in order."""
    wheel, sdist = artifact_pair(dist)
    return [
        wheel,
        sdist,
        out_dir / CHECKSUMS_NAME,
        out_dir / SBOM_NAME,
        out_dir / MANIFEST_NAME,
    ]


def record_release_inputs(
    *, dist: Path, tag: str, changelog: Path, out_dir: Path
) -> ReleaseManifest:
    """Stage one: prove consistency, then ledger the artifact set once."""
    version, _ = verify_consistency(dist=dist, tag=tag, changelog=changelog)
    manifest = ReleaseManifest(
        package=PACKAGE_NAME,
        version=version,
        tag=f"v{version}",
        files=artifact_digests(dist),
    )
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / CHECKSUMS_NAME).write_text(
        "".join(f"{entry.sha256}  {entry.name}\n" for entry in manifest.files), encoding="utf-8"
    )
    (out_dir / SBOM_NAME).write_text(
        json.dumps(build_sbom(dist=dist, manifest=manifest), indent=2) + "\n", encoding="utf-8"
    )
    (out_dir / MANIFEST_NAME).write_text(
        json.dumps(manifest.as_dict(), indent=2) + "\n", encoding="utf-8"
    )
    return manifest


def load_manifest(manifest: Path) -> ReleaseManifest:
    return ReleaseManifest.from_dict(json.loads(manifest.read_text(encoding="utf-8")))


def _describe(files: tuple[ArtifactDigest, ...]) -> str:
    return ", ".join(f"{entry.name}@{entry.sha256[:12]}" for entry in files) or "none"


def verify_stage(*, dist: Path, manifest: Path, stage: str) -> ReleaseManifest:
    """Every stage after the first re-derives the digests it actually holds."""
    if stage not in RELEASE_STAGES:
        raise ValueError(f"unknown release stage: {stage!r}; expected one of {RELEASE_STAGES}")
    recorded = load_manifest(manifest)
    found = artifact_digests(dist)
    if found != recorded.files:
        raise ValueError(
            f"stage {stage} differs from the recorded release manifest: "
            f"found {_describe(found)}, recorded {_describe(recorded.files)}"
        )
    return recorded


def verify_downloaded(*, manifest: Path, downloaded: Path) -> ReleaseManifest:
    """The last stage: what PyPI served back is what CI built and attested."""
    recorded = load_manifest(manifest)
    downloaded = downloaded.resolve()
    for entry in recorded.files:
        path = downloaded / entry.name
        if not path.is_file():
            raise ValueError(f"published artifact is missing from the download: {entry.name}")
        actual = _digest(path)
        if actual.sha256 != entry.sha256:
            raise ValueError(
                f"published artifact {entry.name} has digest {actual.sha256}, "
                f"the release recorded {entry.sha256}"
            )
    return recorded


def dry_run(*, dist: Path, tag: str, changelog: Path, out_dir: Path) -> Path:
    """Walk all five stages without publishing, and write the evidence.

    The publishing stages are exercised against the local artifact set rather
    than PyPI, which is the whole point of the rehearsal: the identity check
    each stage runs in CI is the same call, so a set that survives here is one
    the real run cannot silently swap.
    """
    manifest = record_release_inputs(dist=dist, tag=tag, changelog=changelog, out_dir=out_dir)
    rows = [("record", f"{len(manifest.files)} artifacts ledgered")]
    for stage in RELEASE_STAGES[1:-1]:
        verify_stage(dist=dist, manifest=out_dir / MANIFEST_NAME, stage=stage)
        rows.append((stage, "artifact set unchanged"))
    verify_downloaded(manifest=out_dir / MANIFEST_NAME, downloaded=dist)
    rows.append(("downloaded", "digests match the ledger"))

    assets = release_assets(dist=dist, out_dir=out_dir)
    missing = [asset.name for asset in assets if not asset.is_file()]
    if missing:
        raise ValueError(f"release assets are missing: {', '.join(missing)}")

    report = out_dir / "release-dry-run.md"
    lines = [
        f"# Release supply-chain rehearsal — {PACKAGE_NAME} {manifest.version}",
        "",
        f"TAG: {manifest.tag}",
        "PUBLISHED: no",
        "",
        "| Stage | Outcome |",
        "| --- | --- |",
        *(f"| {stage} | {outcome} |" for stage, outcome in rows),
        "",
        "## Artifact set",
        "",
        "| File | SHA-256 | Bytes |",
        "| --- | --- | --- |",
        *(f"| `{e.name}` | `{e.sha256}` | {e.size} |" for e in manifest.files),
        "",
        "## Release assets",
        "",
        *(f"- `{asset.name}`" for asset in assets),
        "",
    ]
    report.write_text("\n".join(lines), encoding="utf-8")
    return report


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    record = subparsers.add_parser("record", help="ledger the built artifact set once")
    record.add_argument("--dist", type=Path, required=True)
    record.add_argument("--tag", required=True)
    record.add_argument("--changelog", type=Path, required=True)
    record.add_argument("--out-dir", type=Path, required=True)

    verify = subparsers.add_parser("verify", help="re-derive digests at a later stage")
    verify.add_argument("--dist", type=Path, required=True)
    verify.add_argument("--manifest", type=Path, required=True)
    verify.add_argument("--stage", required=True, choices=RELEASE_STAGES)

    version = subparsers.add_parser("version", help="print the ledgered version for GITHUB_OUTPUT")
    version.add_argument("--manifest", type=Path, required=True)

    downloaded = subparsers.add_parser("downloaded", help="check what PyPI served back")
    downloaded.add_argument("--manifest", type=Path, required=True)
    downloaded.add_argument("--downloaded", type=Path, required=True)

    rehearse = subparsers.add_parser("dry-run", help="walk every stage without publishing")
    rehearse.add_argument("--dist", type=Path, required=True)
    rehearse.add_argument("--tag", required=True)
    rehearse.add_argument("--changelog", type=Path, required=True)
    rehearse.add_argument("--out-dir", type=Path, required=True)

    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    """Every stage is a gate, so a mismatch is a reason, never a traceback."""
    try:
        return _dispatch(parse_args(argv))
    except ValueError as error:
        print(f"release supply chain: {error}", file=sys.stderr)
        return 1


def _dispatch(args: argparse.Namespace) -> int:
    if args.command == "record":
        manifest = record_release_inputs(
            dist=args.dist, tag=args.tag, changelog=args.changelog, out_dir=args.out_dir
        )
        print(f"recorded {manifest.package} {manifest.version}: {_describe(manifest.files)}")
    elif args.command == "verify":
        manifest = verify_stage(dist=args.dist, manifest=args.manifest, stage=args.stage)
        print(f"stage {args.stage} holds the recorded set: {_describe(manifest.files)}")
    elif args.command == "version":
        # Shaped for `>> "$GITHUB_OUTPUT"`: the later jobs name the wheel and
        # sdist by version, so it leaves the build job as a recorded value
        # rather than as a directory listing a rebuild could change.
        print(f"version={load_manifest(args.manifest).version}")
    elif args.command == "downloaded":
        manifest = verify_downloaded(manifest=args.manifest, downloaded=args.downloaded)
        print(f"published artifacts match the release ledger: {_describe(manifest.files)}")
    else:
        report = dry_run(
            dist=args.dist, tag=args.tag, changelog=args.changelog, out_dir=args.out_dir
        )
        print(f"release rehearsal complete, nothing published: {report}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
