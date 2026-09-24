"""The comparison, driven through SQLcl's own ``project`` commands (ADT #780).

``DIFF -source A -target B`` refuses to run whenever the two sides' current
schema matches AND their database identity matches, and it reads that identity
from ``SYS_CONTEXT('USERENV','DB_UNIQUE_NAME')``, which is **CDB-scoped**. Two
PDBs of one container database therefore answer the same value, and
``CON_NAME`` -- the only thing that separates them -- is never consulted. So
``diff -schema APP -target TEST`` between two PDBs of one CDB aborts with
``Source and target connections are the same. Nothing to diff.`` even though the
two schemas are genuinely different objects in genuinely different databases.

Measured 2026-09-11 against a real multitenant deployment: two environments were
two PDBs of one CDB, both reporting the same ``DB_UNIQUE_NAME``, each carrying
its own copy of the schema under comparison. Reproduced locally against the two
PDBs in ``tests/fixtures`` terms: ``FREEPDB1`` and ``FREEPDB2`` both answer
``DB_UNIQUE_NAME = FREE``. ``dbtools-diff.jar`` was ``26.2.1.209.2118``, the
current release, so no SQLcl upgrade avoids it. The guard lives in
``oracle.dbtools.extension.diff.utils.SqlclUtils.isSameConnection``.

Nothing ADT sends is wrong, and nothing ADT can send makes the verdict false:
the schema read per side is the one being compared, and the database identity is
not ours to change. The way out is that ``DIFF`` is a thin wrapper. Underneath it
drives four ordinary ``project`` commands, each against the *current* session
with no identity check anywhere:

    project init -name <name> -schemas <schemas>
    project export -schemas <schemas>
    project stage -branch-name <branch>
    project gen-artifact -name <name> -format <format>

This module drives those four directly, owning the git scaffolding that
``SchemaDiffProcessor`` performs with jgit: a throwaway repository, one branch
per side, the target staged against the source, and the project directory
removed on **every** exit path, which is the contract ``DIFF`` itself keeps
(``DIFF_INFO_CLEANUP``, four call sites, one per exit).

Two deliberate differences from what ``DIFF`` does:

* **No throwaway aliases are saved.** ``DIFF`` addresses its two sides by name,
  so ADT had to save each side in the SQLcl store under ``__adt_diff_src`` /
  ``__adt_diff_tgt`` and drop them afterwards, which is what put the
  ``CONNMGR DELETE`` / ``Connection ... has been deleted`` noise at the top of a
  failing transcript. A ``project`` command runs against whatever session is
  open, so no phase here saves an alias. A connection the project
  YAML already names in the store is still used by name -- that path keeps
  credentials out of the generated script and is the only way a file with no
  ``pwd:`` connects at all (ADT #396).
* **The two exports run side by side, in project directories of their own.**
  ``-parallel-exports`` is a flag on ``DIFF`` with no counterpart on ``project
  export``, and the first cut of this module gave the parallelism up: one project
  directory cannot hold two exports at once, and a branch has to be committed
  between them. Jan had asked for it outright (`#773`: *"Are you using
  -parallel-exports ??? If not, you should."*), so it is back by moving the
  constraint instead of accepting it. ``project init`` runs once, the initialised
  project is COPIED per side, the two exports run concurrently in their copies,
  and each tree is adopted onto its branch afterwards. Git never sees two
  writers, because git is not what runs in parallel.
* **``-type`` and ``-name`` reach the export.** The patterns are written into the
  project's own filters file before the copies are taken, so both sides export
  the same narrowed set and the artifact holds what was asked for rather than the
  whole schema (`diff/export_filters.py`, Jan on `#780`).
"""

from __future__ import annotations

import shutil
from dataclasses import dataclass
from pathlib import Path

from adt_ai.shared.connections import Connection
from adt_ai.shared.git_files import run_git
from adt_ai.shared.sqlcl_connect import SqlclConnect, sqlcl_connect
from adt_ai.shared.sqlcl_quoting import reject_unquotable

#: The project name handed to `project init` / `project gen-artifact`. It never
#: reaches a user-visible path -- the artifact is renamed by `runner.py` -- so it
#: is a constant rather than derived from the environments being compared.
PROJECT_NAME = "adt_diff"

#: One branch per side. `project stage` runs on the target branch and names the
#: source branch, which is the direction `DIFF_INFO_STAGE_ON` reports as
#: "Staging target against source".
SOURCE_BRANCH = "adt_diff_source"
TARGET_BRANCH = "adt_diff_target"

#: The branch holding the scaffolding both sides fork from. Named rather than
#: left to git, because `init.defaultBranch` is a user setting: a repository ADT
#: creates and deletes within one command must not answer to it.
BASE_BRANCH = "adt_diff_base"

#: SQLcl writes the artifact under the project's own `dist/` folder; this is
#: where `gen-artifact -format zip` leaves it.
_ARTIFACT_GLOB = "*.zip"


@dataclass(frozen=True)
class ProjectPhase:
    """One SQLcl invocation: a connect block plus the `project` commands after it."""

    label  : str
    script : str


@dataclass(frozen=True)
class ProjectPlan:
    """The four phases plus the connect plan each side was built from.

    The plans ride along because the caller owns two things this module does
    not: writing a fresh registration back into the connection YAML, and
    deciding whether a run that produced nothing is worth retrying with the
    names re-registered. Both questions are answered by ``SqlclConnect``.
    """

    phases : tuple[ProjectPhase, ...]
    source : SqlclConnect
    target : SqlclConnect

    @property
    def by_name(self) -> bool:
        """True when either side connected through the SQLcl store by name.

        That is the one path whose failure mode is "the store never held this
        entry": the connection YAML travels with the project and the store does
        not, so a fresh fingerprint can still name nothing locally.
        """
        return self.source.by_name or self.target.by_name


def build_phases(
    *,
    source: Connection,
    target: Connection,
    source_schema: str,
    target_schema: str,
    startup_sql: str | None,
    project_root: Path | None,
    named_connections: bool,
    artifact_format: str = "zip",
    debug: bool = False,
    force_register: bool = False,
) -> ProjectPlan:
    """The four phases, in the order they must run.

    ``init`` and the source export run against the source session; the target
    export and the staging run against the target. `project stage` has to see
    both sides' committed trees, which is why it runs last and on the target
    branch.
    """
    # `-debug` is `project export`'s own flag and it is the only phase that
    # takes one; `project init`/`stage`/`gen-artifact` have no such option, so
    # the flag is appended to the exports alone rather than to every command.
    export_tail = " -debug" if debug else ""
    source_plan = _connect_plan(
        source, startup_sql, project_root, named_connections, force_register
    )
    target_plan = _connect_plan(
        target, startup_sql, project_root, named_connections, force_register
    )
    phases = (
        _phase(
            "init",
            source_plan,
            f'project init -name "{PROJECT_NAME}" -schemas "{source_schema}"',
        ),
        _phase(
            "export source",
            source_plan,
            f"project export -schemas {source_schema}{export_tail}",
        ),
        _phase(
            "export target",
            target_plan,
            f"project export -schemas {target_schema}{export_tail}",
        ),
        _phase(
            "stage",
            target_plan,
            f'project stage -branch-name "{SOURCE_BRANCH}"',
            f'project gen-artifact -name "{PROJECT_NAME}" -format "{artifact_format}"',
        ),
    )
    return ProjectPlan(phases=phases, source=source_plan, target=target_plan)


def _connect_plan(
    connection: Connection,
    startup_sql: str | None,
    project_root: Path | None,
    named_connections: bool,
    force_register: bool,
) -> SqlclConnect:
    """One side's connect block, built once and reused by both of its phases.

    No throwaway alias is saved, and that is the whole of ADT #780's cleanup: a
    `project` command addresses no connection by name, so nothing has to be
    saved in the SQLcl store and nothing has to be dropped afterwards.
    """
    return sqlcl_connect(
        connection,
        startup_sql       = startup_sql,
        project_root      = project_root,
        named_connections = named_connections,
        force_register    = force_register,
    )


def _phase(label: str, plan: SqlclConnect, *commands: str) -> ProjectPhase:
    """One connect block plus ``commands``, closed with ``exit;``."""
    return ProjectPhase(label=label, script="\n".join([*plan.lines, *commands, "exit;"]))


def prepare_repository(project_dir: Path) -> None:
    """A throwaway git repository with one empty commit, the way `DIFF` starts.

    `project stage` compares committed trees, so the project has to live in a
    repository before anything is exported into it. The empty initial commit is
    the base both side branches fork from.
    """
    if project_dir.exists():
        remove_project(project_dir)
    project_dir.mkdir(parents=True, exist_ok=True)
    git(project_dir, "init", "--quiet", f"--initial-branch={BASE_BRANCH}")
    # Identity is set locally so the commits below cannot fail on a machine with
    # no global git identity -- the repository is deleted minutes later and its
    # history never leaves this folder.
    git(project_dir, "config", "user.email", "adt@localhost")
    git(project_dir, "config", "user.name", "ADT.ai")
    git(project_dir, "commit", "--quiet", "--allow-empty", "-m", "chore: init repo")


def commit_all(project_dir: Path, message: str) -> None:
    git(project_dir, "add", "-A")
    git(project_dir, "commit", "--quiet", "--allow-empty", "-m", message)


def base_commit(project_dir: Path) -> str:
    return git(project_dir, "rev-parse", "HEAD").strip()


def checkout_new_branch(project_dir: Path, branch: str, start_point: str) -> None:
    git(project_dir, "checkout", "--quiet", "-B", branch, start_point)


def git(project_dir: Path, *args: str) -> str:
    """Run one git command in ``project_dir`` and return its stdout.

    Through ``shared/git_files.run_git``, the tree's git adapter, rather than a
    ``subprocess`` call of its own: that is where the safe child environment, the
    UTF-8 decoding and ``check=True`` already live, and a second spelling of them
    is the shape ``tests/contracts/test_subprocess_home`` exists to stop. The
    raise matters here -- a scaffolding step that failed silently would surface
    later as an empty artifact, which is exactly what `#780` exists to stop
    reporting as success.
    """
    return run_git(project_dir, list(args))


def find_artifact(project_dir: Path) -> Path | None:
    """The newest zip `gen-artifact` left anywhere under the project."""
    candidates = sorted(
        project_dir.rglob(_ARTIFACT_GLOB),
        key = lambda path: (path.stat().st_mtime_ns, path.name),
    )
    return candidates[-1] if candidates else None


def remove_project(project_dir: Path) -> None:
    """Delete the project directory, on success and on failure alike.

    This is the contract `DIFF` keeps through ``DIFF_INFO_CLEANUP`` at four exit
    paths, and Jan asked for it explicitly (`#780`): the project does not outlive
    the comparison. Best effort -- a directory that cannot be removed must not
    turn a finished comparison into a failed one.
    """
    shutil.rmtree(project_dir, ignore_errors=True)


def remove_projects(dirs: ProjectDirs) -> None:
    """The repository and both export copies, on every exit path.

    One call so a new directory cannot be added to the comparison and forgotten
    by the cleanup, which is the whole of what `DIFF`'s four `DIFF_INFO_CLEANUP`
    sites are guarding against.
    """
    for path in dirs.all:
        remove_project(path)


#: The throwaway project's folder name, under `-out`. Not hidden: a dot-folder
#: ADT joins onto a path it did not define is what
#: `tests/contracts/test_generated_data_location` refuses, and hiding a directory
#: that exists for the length of one command and is deleted on every exit path
#: buys nothing anyway.
PROJECT_DIR_NAME = "adt_diff_project"

#: The two export copies, named after the side they carry so a run interrupted
#: hard enough to leave them behind says which is which.
EXPORT_DIR_NAMES = (f"{PROJECT_DIR_NAME}_source", f"{PROJECT_DIR_NAME}_target")


@dataclass(frozen=True)
class ProjectDirs:
    """The three directories one comparison uses, and what each is for.

    ``repo`` is the git repository `project stage` reads: it holds the scaffolding
    `project init` wrote and, one branch at a time, the tree a side exported.
    ``source`` and ``target`` are plain copies of that scaffolding with no git in
    them at all, which is what lets the two exports run at once.
    """

    repo   : Path
    source : Path
    target : Path

    @property
    def all(self) -> tuple[Path, ...]:
        """Every directory this comparison created, for the cleanup."""
        return (self.repo, self.source, self.target)


def project_dirs_for(artifact_out: Path) -> ProjectDirs:
    """Where the throwaway project and its two export copies live.

    Beside the artifact rather than in a system temp dir: `-out` is already a
    folder ADT owns and git-ignores, and a system temp path is not an approved
    write root for every caller.
    """
    repo = project_dir_for(artifact_out)
    source, target = (artifact_out / name for name in EXPORT_DIR_NAMES)
    for path in (source, target):
        reject_unquotable(path.as_posix(), role="DIFF project path")
    return ProjectDirs(repo=repo, source=source, target=target)


def project_dir_for(artifact_out: Path) -> Path:
    """Where the throwaway project's git repository lives."""
    path = artifact_out / PROJECT_DIR_NAME
    reject_unquotable(path.as_posix(), role="DIFF project path")
    return path


def copy_project(project_dir: Path, destination: Path) -> None:
    """Copy the initialised project to ``destination``, leaving its git behind.

    An export needs the project's config and filters and nothing else; carrying
    `.git` across would give the copy a second working tree pointing at the same
    history, and the copy is deleted minutes later regardless.
    """
    remove_project(destination)
    shutil.copytree(project_dir, destination, ignore=shutil.ignore_patterns(".git"))


def adopt_export(export_dir: Path, project_dir: Path) -> None:
    """Lay an export copy's tree over the repository's current branch.

    The branch was just reset to the base commit, so everything landing here is
    this side's export plus the scaffolding it was copied from, which is already
    identical and commits as nothing. `.git` is excluded for the same reason it
    was never copied out.
    """
    shutil.copytree(
        export_dir,
        project_dir,
        ignore         = shutil.ignore_patterns(".git"),
        dirs_exist_ok  = True,
    )
