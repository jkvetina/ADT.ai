from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path

from adt_ai.rebuild.models import RebuildRequest
from adt_ai.rebuild.runner import RebuildRunner
from adt_ai.shared.commit_cache import open_store
from adt_ai.shared.git_files import default_branch_ref, fetch_origin, git_user_email, run_git


class CalendarError(Exception):
    """Calendar failed for a reason worth showing the user verbatim."""


@dataclass(frozen=True)
class CalendarRequest:
    root: Path
    branch: str | None = None
    month: str | None = None
    offset: int = 0
    authors: list[str] = field(default_factory=list)
    jira_prefix: str | None = None
    # `list_mode` sat here until ADT #345. Nothing ever read it, so `calendar
    # -list` filled it and the report rendered the same either way.
    fetch: bool = True
    # Where the rebuild module stores its per-branch commit cache. The calendar
    # reads commit metadata from this cache instead of re-walking every branch
    # live, and tops it up for the default + prefix branches before reading.
    cache_file_template: str = "./config/commits/#BRANCH#.yaml"


@dataclass(frozen=True)
class CalendarAuthor:
    author: str
    commit_count: int
    ticket_count: int
    pr_count: int
    # date -> {ticket/PR label: commit count on that date}. Weekend commits are
    # already folded into the preceding Friday by `_calendar_date`.
    days: dict[str, dict[str, int]]


@dataclass(frozen=True)
class CalendarResult:
    month: str
    authors: list[CalendarAuthor]


@dataclass
class _Commit:
    name: str
    email: str
    date: str
    summary: str
    branches: set[str] = field(default_factory=set)


class CalendarRunner:
    def run(self, request: CalendarRequest) -> CalendarResult:
        root = request.root.resolve()
        if request.fetch:
            fetch_origin(root)

        refs = _branch_refs(root, request.branch)
        if not refs:
            raise CalendarError("no branches found to build the calendar from")
        default_ref, default_short = default_branch_ref(root)

        month = request.month or _offset_month(date.today(), request.offset)
        prefix = (request.jira_prefix or "").strip() or None
        # Default author is the configured git user, "my commits" is the baseline,
        # so the calendar shows your own activity unless `-by` overrides it.
        terms = [t for t in (request.authors or [git_user_email(root)]) if t]

        # Source commits from the rebuild module's commit cache instead of walking
        # every branch live. Only the default branch and the prefix-named branches
        # are worth caching, so scope the (re)build to those, top the cache up, and
        # read commit metadata back out of it, the same data, far less git work.
        selected = _select_branches(refs, default_short, prefix)
        _ensure_cache(root, selected, request.cache_file_template)
        commits = _commits_from_cache(
            root, selected, default_ref, default_short, request.cache_file_template
        )

        by_author: dict[str, dict[str, dict[str, int]]] = defaultdict(
            lambda: defaultdict(lambda: defaultdict(int))
        )
        commit_counts: dict[str, int] = defaultdict(int)
        ticket_sets: dict[str, set[str]] = defaultdict(set)
        pr_sets: dict[str, set[int]] = defaultdict(set)

        for sha, commit in commits.items():
            if not _contains_any(f"{commit.name} {commit.email}", terms):
                continue
            commit_date = _calendar_date(commit.date)
            if commit_date.strftime("%Y-%m") != month:
                continue

            ticket = extract_ticket(commit.summary, prefix)
            pr = extract_pr(commit.summary)
            branch_prefixed = any(_branch_has_prefix(b, prefix) for b in commit.branches)
            # With a prefix configured, a commit counts only when it carries a
            # matching ticket, lives on a prefix-named branch (whole-branch rule),
            # or is a pull request (PRs always get special attention).
            if prefix and ticket is None and pr is None and not branch_prefixed:
                continue

            author = commit.email or commit.name
            label = _commit_label(ticket, pr, commit.branches, prefix, sha)
            key = commit_date.isoformat()
            by_author[author][key][label] += 1
            commit_counts[author] += 1
            if ticket:
                ticket_sets[author].add(ticket)
            if pr is not None:
                pr_sets[author].add(pr)

        authors = [
            CalendarAuthor(
                author       = author,
                commit_count = commit_counts[author],
                ticket_count = len(ticket_sets[author]),
                pr_count     = len(pr_sets[author]),
                days         = {
                    day: dict(sorted(labels.items()))
                    for day, labels in sorted(days.items())
                },
            )
            for author, days in sorted(by_author.items())
        ]
        return CalendarResult(month=month, authors=authors)


def _select_branches(
    refs: list[tuple[str, str]], default_short: str, prefix: str | None
) -> list[tuple[str, str]]:
    """The branches the calendar caches and reads from.

    Always the default branch; every branch when no prefix is configured; and
    only the prefix-named branches once a prefix is set, honoring "pull branches
    matching jira_prefix and store them there" without caching dead branches.
    """
    return [
        (short, ref)
        for short, ref in refs
        if short == default_short or prefix is None or _branch_has_prefix(short, prefix)
    ]


def _ensure_cache(
    root: Path, selected: list[tuple[str, str]], template: str
) -> None:
    # Top up the rebuild commit cache for exactly the selected branches. `update`
    # mode resumes each branch from its cached tip, so steady-state runs only read
    # the handful of new commits since last time instead of the whole history.
    if not selected:
        return
    RebuildRunner().run(
        RebuildRequest(
            root                = root,
            branches            = [ref for _, ref in selected],
            cache_file_template = template,
            update_only         = True,
        )
    )


def _commits_from_cache(
    root: Path,
    selected: list[tuple[str, str]],
    default_ref: str,
    default_short: str,
    template: str,
) -> dict[str, _Commit]:
    # Reconstruct the `default..feature` attribution from the cache: a commit that
    # is already on the default branch belongs to the default branch, not to the
    # feature branch that inherited it. Build the default's commit set first, then
    # skip those shas when reading the feature branches.
    default_shas: set[str] = set()
    if default_ref:
        default_shas = {record.id for record in _branch_records(root, default_ref, template)}

    commits: dict[str, _Commit] = {}
    for short, ref in selected:
        is_default = short == default_short and ref == default_ref
        for record in _branch_records(root, ref, template):
            if not is_default and record.id in default_shas:
                continue
            commit = commits.get(record.id)
            if commit is None:
                # The cache stores the author email only (`%ae`); name is unused
                # for filtering, which matches on email, so leave it blank.
                commit = _Commit(
                    name    = "",
                    email   = record.author,
                    date    = record.date,
                    summary = record.summary,
                )
                commits[record.id] = commit
            commit.branches.add(short)
    return commits


def _branch_records(root: Path, branch: str, template: str) -> list:
    with open_store(root, branch, template) as store:
        return store.records(branch)


def extract_ticket(text: str, prefix: str | None) -> str | None:
    """Canonical `PREFIX-<n>` ticket id in `text`, or None.

    Matches the prefix case-insensitively with an optional dash before the
    number (`PROJ-100`, `proj100`), and normalizes to upper-case + dash.
    Returns None when no prefix is configured.
    """
    if not prefix:
        return None
    match = re.search(rf"{re.escape(prefix)}-?(\d+)", text, re.IGNORECASE)
    if not match:
        return None
    return f"{prefix.upper()}-{match.group(1)}"


def extract_pr(summary: str) -> int | None:
    """Pull-request number from a commit subject, or None.

    Handles both the merge-commit form (`Merge pull request #42 from ...`) and
    the squash form GitHub writes (`task: ship it (#29)`).
    """
    match = re.search(r"merge pull request #(\d+)", summary, re.IGNORECASE)
    if match:
        return int(match.group(1))
    match = re.search(r"\(#(\d+)\)\s*$", summary)
    if match:
        return int(match.group(1))
    return None


def _commit_label(
    ticket: str | None,
    pr: int | None,
    branches: set[str],
    prefix: str | None,
    sha: str,
) -> str:
    # PRs lead the label so they stay visible; a ticket rides along when present.
    if pr is not None:
        return f"PR#{pr} {ticket}" if ticket else f"PR#{pr}"
    if ticket:
        return ticket
    branch_label = _branch_prefix_label(sorted(branches), prefix)
    if branch_label:
        return branch_label
    return sha[:7]


def _branch_has_prefix(name: str, prefix: str | None) -> bool:
    return bool(prefix) and prefix.lower() in name.lower()


def _branch_prefix_label(branches: list[str], prefix: str | None) -> str | None:
    for name in branches:
        if _branch_has_prefix(name, prefix):
            return extract_ticket(name, prefix) or prefix.upper()
    return None


def _branch_refs(root: Path, only: str | None = None) -> list[tuple[str, str]]:
    """`(short_name, ref)` for every branch, preferring `origin/*` over locals.

    Remote-tracking refs reflect the server regardless of the checked-out
    branch, so they win when both exist. `only` restricts to a single branch,
    matched by short name or full ref.
    """
    refs: dict[str, str] = {}
    heads = run_git(root, ["for-each-ref", "--format=%(refname:short)", "refs/heads"])
    for line in heads.splitlines():
        name = line.strip()
        if name:
            refs.setdefault(name, name)
    for line in run_git(
        root, ["for-each-ref", "--format=%(refname:short)", "refs/remotes/origin"]
    ).splitlines():
        name = line.strip()
        if not name or name in {"origin", "origin/HEAD"}:
            continue
        short = name[len("origin/"):] if name.startswith("origin/") else name
        refs[short] = name
    pairs = sorted(refs.items())
    if only:
        short_only = only[len("origin/"):] if only.startswith("origin/") else only
        pairs = [(s, r) for s, r in pairs if s == short_only or r == only]
    return pairs


def _calendar_date(value: str) -> date:
    result = datetime.fromisoformat(value).date()
    if result.weekday() < 5:
        return result
    # Weekend commits fold into the preceding Friday so they land on a weekday
    # column. At a month start (Sat the 1st, Sun the 1st/2nd) that Friday is in
    # the previous month and the commit would vanish from the requested grid,
    # fold forward to Monday instead, which stays inside the month.
    friday = result - timedelta(days=result.weekday() - 4)
    if friday.month == result.month:
        return friday
    return result + timedelta(days=7 - result.weekday())


def _offset_month(today: date, offset: int) -> str:
    # Step back whole calendar months, not 30-day chunks: a 30-day step drifts
    # past short months (March 1 - 30 days lands in January, skipping February).
    # Work in absolute month indices so year boundaries and >12-month offsets
    # fall out of plain divmod arithmetic.
    month_index = today.year * 12 + (today.month - 1) - offset
    year, month = divmod(month_index, 12)
    return f"{year:04d}-{month + 1:02d}"


def _contains_any(value: str, terms: list[str]) -> bool:
    haystack = value.lower()
    return any(str(term).lower() in haystack for term in terms)
