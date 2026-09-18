from __future__ import annotations

import re
import shutil
from collections.abc import Callable
from concurrent.futures import Future, ThreadPoolExecutor, wait
from dataclasses import dataclass
from pathlib import Path

from adt_ai.diff import export_filters, inventory, project_pipeline
from adt_ai.shared import text_files
from adt_ai.shared.connections import Connection
from adt_ai.shared.db import run_sqlcl_script
from adt_ai.shared.sqlcl_connect import SqlclConnect
from adt_ai.shared.sqlcl_errors import SqlclNotConnectedError, SqlclScriptError
from adt_ai.shared.sqlcl_names import credential_fingerprint, record_sqlcl_registration

SqlclRequest = Callable[..., str]

#: `diff`'s default `-out`, relative to the project root and spelled exactly as
#: the `.gitignore` this repo ships spells it, that being the same file
#: `doctor -init` copies into a new project.
GITIGNORE_ENTRY = "config/diff/"

#: Anything outside this set is replaced in a filename component. An environment
#: and a schema are both config keys, so neither is guaranteed to be a safe path
#: segment, and a `/` in one would write the zip outside `-out` altogether.
_UNSAFE_IN_NAME = re.compile(r"[^A-Za-z0-9_.-]+")


@dataclass(frozen=True)
class DiffRequest:
    source            : Connection
    target            : Connection
    artifact_out      : Path
    # Required, not defaulted: diff opens two real SQLcl sessions, and a
    # defaulted session setup is exactly what let ADT #177 happen on the
    # oracledb side. The CLI reads it from the shared ``StartupContext``.
    startup_sql       : str | None
    debug             : bool = False
    project_root      : Path | None = None
    named_connections : bool = True
    #: What to call the zip, when `-out` named a file rather than a folder
    #: (`#773`). `None` keeps the derived `<env>_<schema>---<env>_<schema>.zip`,
    #: which is what makes a folder of comparisons readable; a person who typed a
    #: file path has already answered that question for this one.
    artifact_name     : str | None = None
    #: `-name` / `-type`, the same patterns the screen filter reads. Here they
    #: narrow the EXPORT, so the artifact carries only what was asked for (`#780`,
    #: overruling `#773`'s screen-only reading); `diff/export_filters.py` owns
    #: what they become. Empty means export everything, the ordinary case.
    object_names      : tuple[str, ...] = ()
    object_types      : tuple[str, ...] = ()


@dataclass(frozen=True)
class DiffResult:
    artifact_path : Path | None
    output        : str
    success       : bool
    #: What the two sides actually exported, compared object by object. The
    #: screen is drawn from this rather than from the artifact (`#790`): the
    #: trees are what SQLcl compared, so they can say which side each object is
    #: on, which a generated DDL script cannot.
    inventory     : inventory.Inventory | None = None


@dataclass(frozen=True)
class _Attempt:
    """One full pass of the four phases: what it produced and what it printed."""

    artifact  : Path | None
    output    : str
    inventory : inventory.Inventory | None = None


class DiffRunner:
    def __init__(self, sqlcl_request: SqlclRequest | None = None) -> None:
        self.sqlcl_request = sqlcl_request or run_sqlcl_script

    def run(self, request: DiffRequest) -> DiffResult:
        """Compare the two schemas through SQLcl's `project` commands (`#780`).

        The single `DIFF` line this used to send refused outright whenever the two
        sides shared a current schema and a CDB-scoped `DB_UNIQUE_NAME`, which is
        every comparison of one schema across two PDBs of one container database.
        `project init` / `export` / `stage` / `gen-artifact` consult no connection
        identity at all, so the comparison runs; `project_pipeline` owns the git
        scaffolding and the cleanup that `DIFF` did internally.
        """
        request.artifact_out.mkdir(parents=True, exist_ok=True)
        # Before the run, not after: the folder now exists, so a run that fails
        # to produce an artifact still leaves an ignored folder rather than an
        # untracked one. A DIFF artifact is a zip of two schemas' DDL, which is
        # the whole reason `#763` put the entry in the shipped `.gitignore`; a
        # project scaffolded before that carries an older copy of the file and
        # would never earn the entry from `doctor -init` again.
        _ensure_diff_ignored(request)
        dirs = project_pipeline.project_dirs_for(request.artifact_out)
        plan = self._plan(request, force_register=False)
        try:
            attempt = self._compare(plan, request, dirs)
        except (SqlclScriptError, SqlclNotConnectedError):
            # A side that connected by name has one failure mode of its own: the
            # SQLcl store never held the entry. The connection YAML travels with
            # the project and the store does not, so a fresh fingerprint on a new
            # machine names nothing locally and the guarded connect exits failure.
            # Re-register both sides and retry once; a second failure is real and
            # propagates to the CLI, which prints it under `DIFF FAILED:`.
            if not plan.by_name:
                raise
            attempt = self._compare(
                self._plan(request, force_register=True), request, dirs
            )
        else:
            if attempt.artifact is None and plan.by_name:
                attempt = self._compare(
                    self._plan(request, force_register=True), request, dirs
                )
        return DiffResult(
            artifact_path = attempt.artifact,
            output        = attempt.output,
            success       = attempt.artifact is not None,
            inventory     = attempt.inventory,
        )

    def _plan(
        self, request: DiffRequest, *, force_register: bool
    ) -> project_pipeline.ProjectPlan:
        return project_pipeline.build_phases(
            source            = request.source,
            target            = request.target,
            source_schema     = request.source.schema,
            target_schema     = request.target.schema,
            startup_sql       = request.startup_sql,
            project_root      = request.project_root,
            named_connections = request.named_connections,
            debug             = request.debug,
            force_register    = force_register,
        )

    def _compare(
        self,
        plan: project_pipeline.ProjectPlan,
        request: DiffRequest,
        dirs: project_pipeline.ProjectDirs,
    ) -> _Attempt:
        """One pass of the four phases, with the throwaway project around them."""
        init, export_source, export_target, stage = plan.phases
        transcript: list[str] = []
        artifact: Path | None = None
        found_inventory: inventory.Inventory | None = None
        try:
            project_pipeline.prepare_repository(dirs.repo)
            # The scaffolding is committed on the base commit so BOTH side
            # branches carry it; each side then exports onto its own branch, and
            # `project stage` runs on the target branch naming the source one.
            transcript.append(self._run_phase(init, dirs.repo, request))
            # Before the copies, so both sides export through the same filters
            # and the two trees `stage` compares are narrowed the same way. A
            # filter on one side only would report every object the other side
            # still exported as a difference.
            export_filters.apply(
                dirs.repo, names=request.object_names, types=request.object_types
            )
            project_pipeline.commit_all(dirs.repo, "chore: project init")
            base = project_pipeline.base_commit(dirs.repo)
            project_pipeline.copy_project(dirs.repo, dirs.source)
            project_pipeline.copy_project(dirs.repo, dirs.target)
            transcript.extend(
                self._run_exports(
                    (export_source, dirs.source),
                    (export_target, dirs.target),
                    request,
                )
            )
            # Read while both trees still stand, and before either is adopted
            # onto a branch: this IS the comparison the screen reports, one file
            # per object per side, and the `finally` below deletes both (`#790`).
            found_inventory = inventory.compare(
                dirs.source,
                dirs.target,
                schema        = request.source.schema,
                target_schema = request.target.schema,
            )
            # Serial again from here, and cheap: adopting a tree is a file copy
            # and a commit, with no database on the other end of it.
            for branch, export_dir, message in (
                (project_pipeline.SOURCE_BRANCH, dirs.source, "export: source"),
                (project_pipeline.TARGET_BRANCH, dirs.target, "export: target"),
            ):
                project_pipeline.checkout_new_branch(dirs.repo, branch, base)
                project_pipeline.adopt_export(export_dir, dirs.repo)
                project_pipeline.commit_all(dirs.repo, message)
            transcript.append(self._run_phase(stage, dirs.repo, request))
            # Moved out BEFORE the cleanup below deletes the project it sits in.
            found = project_pipeline.find_artifact(dirs.repo)
            if found is not None:
                artifact = _move_artifact(found, request)
        finally:
            # Every exit path, which is the contract `DIFF` keeps through
            # `DIFF_INFO_CLEANUP` and what Jan asked for on `#780`.
            project_pipeline.remove_projects(dirs)
        # Only on the path that raised nothing. A fingerprint written for a store
        # entry whose registering script failed is the phantom of ADT #188: it
        # survives every later run and the connection is still not there.
        _record_registrations((plan.source, plan.target))
        return _Attempt(
            artifact  = artifact,
            output    = "\n".join(transcript),
            inventory = found_inventory,
        )

    def _run_exports(
        self,
        source: tuple[project_pipeline.ProjectPhase, Path],
        target: tuple[project_pipeline.ProjectPhase, Path],
        request: DiffRequest,
    ) -> list[str]:
        """Both exports at once, their transcripts still in source-then-target order.

        This is `-parallel-exports` put back (`#773`, `#780`). The two exports are
        the whole wait of a comparison -- each walks a schema through
        `DBMS_METADATA` -- and nothing makes them wait for each other once they
        have project directories of their own.

        **Both futures are waited on before either is read.** Reading them in
        order would re-raise the source's failure with the target's SQLcl still
        running against a directory `_compare`'s `finally` is about to delete.
        Reading in order AFTER the wait is what keeps the transcript deterministic
        whichever side finishes first.
        """
        with ThreadPoolExecutor(max_workers=2, thread_name_prefix="adt-diff-export") as pool:
            futures: list[Future[str]] = [
                pool.submit(self._run_phase, phase, directory, request)
                for phase, directory in (source, target)
            ]
            wait(futures)
        return [future.result() for future in futures]

    def _run_phase(
        self,
        phase: project_pipeline.ProjectPhase,
        project_dir: Path,
        request: DiffRequest,
    ) -> str:
        """One phase, with the project directory as the working directory.

        `project` resolves its own project from the cwd, so the phase has to run
        inside it rather than in `-out`.
        """
        return self.sqlcl_request(
            phase.script, project_dir, project_root=request.project_root
        )


def _record_registrations(plans: tuple[SqlclConnect, ...]) -> None:
    """Write a fresh `sqlcl`/`sqlcl_sync` pair back into the connection YAML.

    Unchanged from the `DIFF` path: `registers` is set only where the connect
    block actually saved the name with `-savepwd`, so that name is the store
    entry being recorded.
    """
    for plan in plans:
        connection = plan.registers
        if connection is None or not connection.sqlcl_source or plan.name is None:
            continue
        record_sqlcl_registration(
            connection.sqlcl_source,
            connection.environment,
            connection.schema,
            plan.name,
            credential_fingerprint(connection),
        )


#: What `-out` may name, beyond the folder it has always named (`#773`).
_ARTIFACT_SUFFIX = ".zip"


def resolve_out(out: str | None, root: Path) -> tuple[Path, str | None]:
    """`-out` as (folder, artifact name), the file case included.

    Jan, ADT #773: *"-out DIR should also support a specific file: -out FILE"*.
    A `.zip` suffix is what says file, rather than a guess at whether the path
    looks like one: a release folder called `releases/v1.2` carries a suffix too,
    and a folder that does not exist yet cannot be asked what it is.

    The default is under `config/`, beside every other folder ADT.ai generates
    into a project (`commits/`, `discovery/`, `internal/`, `temp/`). It used to
    default to `<root>/diff_output`, a folder invented at the project root, which
    is the one place a user keeps their own tree (Jan, ADT #763).
    """
    if not out:
        return root / "config" / "diff", None
    path = Path(out).expanduser()
    if path.suffix.lower() == _ARTIFACT_SUFFIX:
        return path.parent.resolve(), path.name
    return path.resolve(), None


def artifact_name(request: DiffRequest) -> str:
    """`<source_env>_<source_schema>---<target_env>_<target_schema>.zip`.

    SQLcl names its own output `diff_<SRC>_<TGT>_artifact.zip`, which spells the
    two schemas and neither environment, so a folder holding a DEV/UAT and a
    DEV/PROD comparison of the same schema pair carries two files whose names
    cannot be told apart, and the word `artifact` in a folder of nothing but
    artifacts says nothing at all. Jan, on the first live run of `#769`:
    *"WHAT A STUPID FILENAME!"*

    The `---` separator is what makes the two sides readable at a glance: both
    halves already contain `_`, so a single underscore between them would leave
    a four-token name with no visible seam.

    `-out <file>.zip` overrides it outright (`#773`): the derived name exists to
    tell one comparison from another in a shared folder, and a person naming a
    file has already told them apart.
    """
    if request.artifact_name:
        return request.artifact_name
    return (
        f"{_safe(request.source.environment)}_{_safe(request.source.schema)}"
        f"---{_safe(request.target.environment)}_{_safe(request.target.schema)}.zip"
    )


def data_log_path(out: str, source: Connection, target: Connection) -> Path:
    """Where `diff -data -out` writes its untrimmed rows (`#886`).

    A `.log` or `.txt` path is the file itself; anything else is the folder it
    lands in, named for the two sides the way the object artifact is, so a run
    of the same pair replaces its file rather than adding one beside it.
    """
    path = Path(out).expanduser()
    if path.suffix.lower() in _LOG_SUFFIXES:
        return path.resolve()
    return path.resolve() / (
        f"{_safe(source.environment)}_{_safe(source.schema)}"
        f"---{_safe(target.environment)}_{_safe(target.schema)}_data.log"
    )


#: The suffixes that make a `diff -data -out` path a file rather than a folder.
_LOG_SUFFIXES = (".log", ".txt")


def _safe(part: str) -> str:
    return _UNSAFE_IN_NAME.sub("_", part)


def _move_artifact(artifact: Path, request: DiffRequest) -> Path:
    """Move the generated zip out of the throwaway project into `-out`.

    Under `DIFF` this was an in-place rename, because SQLcl wrote its zip
    straight into `-artifact-out`. `project gen-artifact` writes it inside the
    project instead, so the zip has to LEAVE before `remove_project` deletes the
    folder it sits in, which makes the move load-bearing rather than cosmetic,
    and is why a failure here is reported instead of shrugged off. `replace`
    falls back to a copy for the one ordinary refusal, a `-out` on a different
    device from the project; both failing means the artifact is about to be
    deleted with the project, so the run says so rather than handing back a path
    to nothing.
    """
    target = request.artifact_out / artifact_name(request)
    try:
        artifact.replace(target)
    except OSError:
        try:
            shutil.copy2(artifact, target)
        except OSError as error:
            raise RuntimeError(
                f"The comparison finished but its artifact could not be written to "
                f"{target}: {error}"
            ) from error
    return target


def _ensure_diff_ignored(request: DiffRequest) -> None:
    """Idempotently ensure `config/diff/` is git-ignored in the project root.

    Mirrors `shared/internal_paths.ensure_internal_ignored` and
    `discovery/report.ensure_discovery_ignored`. Scoped to the DEFAULT `-out`:
    the entry names `config/diff/` literally, so it says nothing true about a
    folder the user pointed `-out` somewhere else, and writing it there would
    ignore a path this run never touches.
    """
    root = request.project_root
    if root is None or request.artifact_out != root / "config" / "diff":
        return
    gitignore = root / ".gitignore"
    existing = gitignore.read_text(encoding="utf-8") if gitignore.exists() else ""
    if GITIGNORE_ENTRY in {line.strip() for line in existing.splitlines()}:
        return
    prefix = existing
    if prefix and not prefix.endswith("\n"):
        prefix += "\n"
    try:
        text_files.write_text(gitignore, prefix + GITIGNORE_ENTRY + "\n")
    except OSError:
        # Housekeeping never fails a command, the same posture the two sibling
        # helpers take.
        return
