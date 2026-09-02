"""Narrowing scanned commit records down to the selection a request asked for.

Split out of `commit_discovery.py` when ADT #467 pushed that module past the
20 KB context guard (`tests/contracts/test_context_file_size.py`), the third cut
this file has taken and the same call `#309` and `#429` made before it: a module
that crosses the guard is split, never registered as debt.

The seam is one the old module's own opening line already named, "scanning git
history into commit records, AND narrowing them to a selection". Scanning is one
job; everything here is the other, which of those records a `PatchRequest` keeps
and what a `-commit` / `-ignore` argument means when it names one. Nothing below
touches git, the store or the filesystem: it turns records plus a request into
fewer records.

`commit_discovery` re-exports every name, so an importer that reached for
`_filter_records` or `commit_ref_matches` there is untouched.
"""

from __future__ import annotations

import re
from datetime import datetime
from typing import TYPE_CHECKING

from adt_ai.shared.dates import within_recent_window
from adt_ai.shared.sql_like import matches_sql_like

if TYPE_CHECKING:  # pragma: no cover - import cycle, annotations only
    # `commit_discovery` imports this module, so importing its types at runtime
    # would close the loop. `from __future__ import annotations` makes every
    # annotation a string, which is the whole reason the split needs no third
    # module to hold the two dataclasses.
    from adt_ai.shared.commit_discovery import CommitRecord, PatchRequest


def _within_window(commit_date: str, recent_days: int | float) -> bool:
    """Is a stored commit date inside a `-recent` window? Unparsable dates stay."""
    try:
        moment = datetime.fromisoformat(commit_date)
    except (TypeError, ValueError):
        return True
    return within_recent_window(moment, recent_days)


def _filter_records(records: list[CommitRecord], request: PatchRequest) -> list[CommitRecord]:
    filtered = records
    if request.ignore_commits:
        ignored = {value.lower() for value in request.ignore_commits}
        filtered = [
            record
            for record in filtered
            if not _matches_any_ref(record, ignored)
        ]
    if request.commit_refs:
        selected = {value.lower() for value in request.commit_refs}
        filtered = [
            record
            for record in filtered
            if _matches_any_ref(record, selected)
        ]
    if request.authors:
        filtered = [
            record
            for record in filtered
            if matches_author(record.author, request.authors)
        ]
    if request.recent is not None:
        # One arithmetic for every `-recent` in the tool (ADT #467). A record
        # whose stored date will not parse is KEPT: a window narrows what is
        # there, and dropping a commit over the spelling of its timestamp would
        # silently shrink a patch. `patch/preview.folders_within_window` makes
        # the same call for a folder whose name carries no parsable day.
        filtered = [
            record
            for record in filtered
            if _within_window(record.date, request.recent)
        ]
    if request.search_terms:
        # No case folding here since ADT #423: `matches_sql_like` folds both
        # sides itself, and lowering a term in advance would leave two places
        # claiming to own case, which is how the pattern language forked in the
        # first place.
        filtered = [
            record
            for record in filtered
            if all(_record_contains(record, term) for term in request.search_terms)
        ]
    elif request.patch_code and not request.commit_refs and not request.hash_mode:
        # With no explicit `-search`, the patch code IS the search term, old ADT
        # patch.py:147, matched against the commit SUMMARY only (patch.py:1005),
        # never the file paths. Without this, `-patch 65` listed every recent
        # commit and left the operator to spot theirs (ADT #257).
        #
        # The one deliberate divergence: an explicit `-commit <n>` bypasses it.
        # Old ADT filtered those too, so a named commit whose subject missed the
        # code was dropped silently, and `-name <name> -create -commit <n>` is a
        # documented build sequence, where that would build an empty patch and
        # still report success.
        #
        # Matched through `_like_pattern` for the same reason `-search` is (ADT
        # #423): `docs/patch.md` says the patch code IS the search term, and two
        # spellings of one concept is how the two drift apart. The summary-only
        # scope is the one thing that stays narrower here, which is old ADT's
        # own split.
        pattern = _like_pattern(request.patch_code)
        filtered = [
            record for record in filtered if matches_sql_like(record.summary, pattern)
        ]
    if request.commit_pattern and not request.search_terms and not request.commit_refs:
        # `patch_commit_pattern`, a project whose commits all carry a ticket
        # reference declares the shape once and gets every stray `wip` commit
        # kept out of every patch (old ADT config.yaml:113, patch.py:1012-1017).
        #
        # Two exemptions, and old ADT only had the first. It skipped the pattern
        # when `-search` was given (patch.py:1013), because an explicit search IS
        # the filter. `-commit` is exempted here as well: old ADT applied the
        # pattern to commits the user had NAMED (the check runs after the
        # `add_commits` gate at :979), so `-commit 12` on a commit whose subject
        # missed the pattern built an empty patch and still reported success.
        # That is the same failure `#257` fixed for the patch-code filter, and it
        # gets the same answer, a commit you named is an instruction.
        expression = re.compile(request.commit_pattern)
        filtered = [record for record in filtered if expression.search(record.summary)]
    if request.files_only:
        filtered = [record for record in filtered if record.usable_files or record.deleted_files]
    return filtered


def matches_author(author: str, authors: list[str]) -> bool:
    """Does a stored commit author match any `-by` / `-my` value? (ADT #467)

    A lowercase substring, which is what `-by` has always been, lifted out of
    `_filter_records` so `patch/preview.folders_for_authors` can ask the identical
    question about a patch FOLDER. One flag must not select two different sets on
    one screen, and two spellings of one comparison is how that starts.

    The value compared against is an EMAIL: the store is written with `%ae`
    (`rebuild/cache.py`), which is the fact `-my` read wrongly until `#467`.
    """
    return any(needle.lower() in author.lower() for needle in authors)


# `12-40`, an inclusive commit-number range. Both sides must be digits: that is
# what keeps a hash prefix from ever being read as a range, whatever characters it
# happens to carry.
_COMMIT_RANGE_RE = re.compile(r"^(?P<start>\d+)-(?P<stop>\d+)$")
# `12+`, that commit and everything newer.
_COMMIT_FROM_RE = re.compile(r"^(?P<start>\d+)\+$")


def commit_ref_matches(number: int, commit_hash: str, ref: str) -> bool:
    """Does one `-commit` / `-ignore` argument select this commit?

    Three spellings, all old ADT's (`util.ranged_str`, util.py:755-767, resolved
    by `get_search_full`, patch.py:1073-1082):

    * `12`     , that commit number, or a hash prefix
    * `12+`    , commit 12 and everything newer
    * `12-40`  , the inclusive span

    Shared by `patch` and `search_repo` rather than written twice: they are one
    concept at two call sites, and `search_repo` already understood `N+` while
    `patch` understood neither, so `docs/patch.md`'s documented "commit numbers
    or ranges" selected nothing at all, silently (ADT #309, was #15).
    """
    value = str(ref).strip().lower()
    if not value:
        return False
    span = _COMMIT_RANGE_RE.match(value)
    if span:
        return int(span.group("start")) <= number <= int(span.group("stop"))
    onward = _COMMIT_FROM_RE.match(value)
    if onward:
        return number >= int(onward.group("start"))
    return value == str(number) or commit_hash.lower().startswith(value)


def _matches_any_ref(record: CommitRecord, refs: set[str]) -> bool:
    return any(
        commit_ref_matches(record.number, record.commit_hash, ref)
        for ref in refs
    )


def _like_pattern(term: str) -> str:
    """One `-search` term as a SQL LIKE pattern (ADT #423).

    Jan, 2026-08-20: *"how can I search for 'any' commits when you treat '%'
    literarly and not as SQL LIKE?"*, and on chips the same day: *"Same way as we
    are using SQL LIKE filters elsewhere, it should be reusable code!"* So the
    matching is `shared/sql_like.matches_sql_like`, already carrying `ut`,
    `export_db` and `patch_folders`; this commit search was the one filter that
    bypassed it, which is why `%` reached the haystack as a literal character and
    `-search %` returned the single commit whose subject spells `%rollback`.

    A parity regression rather than a feature: old ADT short-circuited the whole
    match on `what_words == ['%']` (patch.py:1060-1062).

    **The wrapping is the whole compatibility story.** `matches_sql_like` is
    anchored, and a `-search` term has always been a substring test, so passing a
    bare term straight through would turn every existing search into an equality
    test and match nothing. A term carrying no `%` of its own is searched as
    `%<term>%`, which is how a contains filter is written in SQL; a term that
    brings its own `%` is its own pattern, so bare `%` matches everything and
    `%TEST%` still means contains.

    `_` is left to the helper's own single-character semantics rather than
    special-cased. Forking the pattern language per call site is exactly the
    drift this card exists to end, and the widening is small: `fn_1` still
    matches `fn_1.sql`, it merely also matches `fnX1`.
    """
    return term if "%" in term else f"%{term}%"


def _record_contains(record: CommitRecord, term: str) -> bool:
    """Does `term` match this commit's subject, author, or any of its paths?

    Per FIELD rather than over a joined haystack: the fields used to be glued
    with newlines and substring-searched, which no anchored pattern can stand in
    for, and joining would let one pattern straddle a subject and a file path,
    a match a substring test could never have produced.
    """
    return any(
        matches_sql_like(field, _like_pattern(term))
        for field in (
            record.summary,
            record.author,
            *record.usable_files.keys(),
            *record.deleted_files,
        )
    )
