"""`adtai search TERM`: where a piece of text lives, and what it touches (ADT #895).

The routing and the report. Every flag was already proven readable before the
banner by `commands_search_graph._term_argument_error`; this module turns them
into a `TermRequest`, runs it offline, and renders the answer in the sections
Jan approved on 2026-09-19: the hits, a table per source since ADT #905
(`APEX HITS`, `DB HITS`, ...), then `USES / USED BY` for the database
objects hit, `PAGE LINKS` for the pages hit, and `NOT SEARCHED` last, naming a
layer nobody refreshed so it never reads as a layer with no hits.

Every table is fitted to the 78-column line by the `diff` screen's own fitter:
its cells are somebody else's text, a component name or a file path, and one
long value must not push every row past the terminal. The matching line is
not a cell at all; `_print_hits` says why.
"""

from __future__ import annotations

import argparse
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from adt_ai.cli.constants import (
    ConfigError,
    ConfigLoader,
    GatewayFactory,
    print_adt_header,
    print_adt_table,
    print_module_banner,
)
from adt_ai.cli.context import (
    ApexAppSelection,
    _config_search_paths,
    _flatten_arg_groups,
    _load_startup_context,
    _parse_apex_app_selection,
    _parse_apex_page_selection,
    _repo_root,
)
from adt_ai.cli.diff_reporters import (
    _ELLIPSIS,
    MAX_LINE,
    _fit,
    _line_width,
    _natural_width,
    _trim,
)
from adt_ai.cli.flow_reporters import _component_label, _src_page_label
from adt_ai.cli.refresh_connect import _resolve_refresh_names
from adt_ai.cli.search_autobuild import _refresh_namespace
from adt_ai.cli.search_term_autobuild import level_commit_store, prepare_term_stores
from adt_ai.flow.model import FlowEdge
from adt_ai.search.term import run_term_search
from adt_ai.search.term_model import Hit, Relation, TermRequest, TermResult, parse_layers
from adt_ai.shared import connection_errors
from adt_ai.shared.commit_cache import DEFAULT_COMMITS_TEMPLATE
from adt_ai.shared.error_screen import exit_code_for, print_adt_error
from adt_ai.shared.tables import adt_table_lines, close_adt_table

#: Each source's own hits table: the `Hit` field behind every column, in order.
#: A source lists only what it can fill, so a file hit never carries an empty
#: `APP` and `PAGE` beside it (ADT #905), and every column is named for what it
#: holds there: a DB `COMPONENT` is an object, a GIT one is a commit number.
HIT_TABLES: dict[str, tuple[tuple[str, str], ...]] = {
    "APEX": (
        ("app", "app"), ("page", "page"), ("component", "component"),
        ("line", "line"), ("flag", "flag"),
    ),
    "STATIC": (
        ("app", "app"), ("file", "component"), ("scope", "prop"),
        ("line", "line"), ("flag", "flag"),
    ),
    "DB":    (("object", "component"), ("type", "prop"), ("line", "line"), ("flag", "flag")),
    "GIT":   (("commit", "component"), ("found_in", "prop")),
    "FILES": (("file", "component"), ("line", "line"), ("flag", "flag")),
}

HIT_NUMERIC = ("app", "page", "line", "commit")

#: The columns of each table that give way, and only when their full values
#: would carry the row past `MAX_LINE`; every other column is a number or a
#: short word. Two share the room left as `_fit_names` decides.
HIT_NAMES: dict[str, tuple[str, ...]] = {
    "APEX":   ("component",),
    "STATIC": ("file", "scope"),
    "DB":     ("object", "type"),
    "GIT":    (),
    "FILES":  ("file",),
}

#: Columns holding a path, cut from the left: the file name at the end is the
#: part a reader needs, and `apex_deployment/apex/430_O...` names no file.
HIT_PATHS = ("file",)

#: Sources whose property labels the matched line instead of taking a column.
#: An APEX component name and its property run to 30 characters each, and the
#: 43 columns the rest of the row leaves hold both whole on no real page, so
#: one of them was always cut: `JAVASCRIPT...` and three `ATTRIBUTES...` rows
#: that read the same. In front of the line it names, it is never cut.
HIT_LABELLED = ("APEX",)

#: The excerpt prints on a line of its own under its row, two columns inside
#: the table, and takes the whole line to `MAX_LINE`.
EXCERPT_INDENT = "    "

#: `USES / USED BY` gives the related object's name first, the hit's second.
RELATION_TIERS = (("name",), ("object",))

LINK_TIERS = (("component",),)


def _run_search_term(
    args: argparse.Namespace, gateway_factory: GatewayFactory | None = None
) -> int:
    """Search every requested layer for TERM, exit 1 when none could be read."""
    print_module_banner("SEARCH")
    root = Path(args.root).expanduser().resolve()
    layers = parse_layers(_flatten_arg_groups(args.layer))
    config: dict[str, Any] = {}
    # GIT reads where the commit store lives and DB where `export_db` wrote the
    # object files (ADT #904); the other three read no configuration at all.
    if "GIT" in layers or "DB" in layers:
        try:
            config = ConfigLoader(
                _config_search_paths(getattr(args, "config_dir", None), root, _repo_root())
            ).load().data
        except ConfigError as error:
            print_adt_error("CONFIGURATION INVALID", str(error))
            return exit_code_for("CONFIGURATION INVALID")
    template = str(config.get("repo_commits_file") or DEFAULT_COMMITS_TEMPLATE)
    if "GIT" in layers:
        # Levelled with git the way `patch` levels it, so GIT never names a
        # `rebuild -branch` to run first (ADT #900).
        level_commit_store(args, root, config)
    # Already proven readable by `_term_argument_error`, before the banner.
    apps = _parse_apex_app_selection(_flatten_arg_groups(args.app)) or ApexAppSelection()
    pages = _parse_apex_page_selection(_flatten_arg_groups(args.page))
    # A layer nobody refreshed, or one refreshed before the database changed,
    # is refreshed here rather than named as a rebuild to run (ADT #900).
    prepare_term_stores(args, root, layers, gateway_factory)
    result = run_term_search(
        TermRequest(
            root                = root,
            term                = args.term,
            layers              = layers,
            app_ids             = tuple(int(app_id) for app_id in apps.explicit_ids),
            app_ranges          = apps.ranges,
            page_ids            = pages.explicit_ids if pages else (),
            page_ranges         = pages.ranges if pages else (),
            owners              = _term_owners(args, root) if "DB" in layers else (),
            branch              = args.branch,
            cache_file_template = template,
            config              = config,
        )
    )
    _print_term_result(result, args.term)
    return 0 if result.searched else 1


def _term_owners(args: argparse.Namespace, root: Path) -> tuple[str, ...]:
    """The schemas DB reads: the ones `-schema` names, else the configured default.

    The default is the one a bare `rebuild` and `export_db` resolve, the
    connection file's `schema_db` for its first environment, read from the file
    and never connected through. A project with no such file, or one naming no
    default, has no schema to read, and the layer says so under `NOT SEARCHED`.
    """
    named = _resolve_refresh_names(_flatten_arg_groups(args.schema))
    if named:
        return tuple(named)
    try:
        connections = _load_startup_context(_refresh_namespace(root)).connections
        return tuple(connections.default_schemas(connections.default_environment))
    except (connection_errors.ConnectionError, ConfigError):
        return ()


def _print_term_result(result: TermResult, term: str) -> None:
    """The report, a section per thing worth saying and none for the rest.

    The hits print as one `<SOURCE> HITS (n):` section per source that has any,
    in layer order. With no hit at all, `HITS (0):` and `(none)` still print
    whenever a layer was read, because an empty answer from a searched layer is
    an answer. Nothing prints for them only when nothing was read at all, and
    then `NOT SEARCHED` is the whole report.
    """
    if result.searched:
        by_source: dict[str, list[Hit]] = {}
        for hit in result.hits:
            by_source.setdefault(hit.source, []).append(hit)
        for source, hits in by_source.items():
            print_adt_header(f"{source} HITS ({len(hits)}):")
            _print_hits(source, hits, term)
        if not result.hits:
            print_adt_header("HITS (0):")
            print("  (none)")
    if result.relations:
        print_adt_header("USES / USED BY:")
        _print_fitted(_relation_rows(result.relations), RELATION_TIERS)
    if result.links:
        print_adt_header("PAGE LINKS:")
        _print_fitted(_link_rows(result.links), LINK_TIERS)
    if result.not_searched:
        print_adt_header("NOT SEARCHED:")
        for layers, reason in result.not_searched:
            print(f"  {layers}: {reason}")
        print()


def _print_fitted(rows: list[dict[str, object]], tiers: Sequence[Sequence[str]]) -> None:
    print_adt_table(_fit(rows, tiers))


def _print_hits(source: str, hits: Sequence[Hit], term: str) -> None:
    """One source's hits: a row per hit, and its excerpt on a line of its own under it.

    Every column is as wide as its longest value, against the `MAX_LINE` cap
    the `diff` screen holds its tables to, and the source's names give way
    only when their full values would pass it, as `_hit_table` decides. The
    excerpt is not a column: a path or a component name leaves it too few
    columns to show a line of code with the term in it. Under its row it gets
    the whole line, windowed on TERM, and every row has the same shape.
    """
    # The shared renderer's own lines, so the columns are measured and aligned
    # exactly as every other table's; only the excerpt lines go in between.
    header, separator, *lines = adt_table_lines(_hit_table(source, hits), numeric=HIT_NUMERIC)
    print()
    print(header)
    print(separator)
    for line, hit in zip(lines, hits, strict=True):
        print(line)
        label = f"{hit.prop}: " if source in HIT_LABELLED and hit.prop else ""
        if hit.excerpt:
            width = MAX_LINE - len(EXCERPT_INDENT) - len(label)
            print(EXCERPT_INDENT + label + _window(hit.excerpt, term, width))
        elif label:
            print(EXCERPT_INDENT + label.rstrip(": "))
    close_adt_table()


def _hit_table(source: str, hits: Sequence[Hit]) -> list[dict[str, object]]:
    """The rows of one source's table, empty columns dropped, fitted to the line.

    A column no hit fills goes, so `FLAG` shows only when a line defines TERM
    and `LINE` only when a hit is a line rather than a whole file.
    """
    table = HIT_TABLES[source]
    rows = [{column: getattr(hit, field) for column, field in table} for hit in hits]
    kept = [column for column, _field in table if any(_filled(row[column]) for row in rows)]
    rows = [{column: row[column] for column in kept} for row in rows]
    names = [column for column in HIT_NAMES[source] if column in kept]
    return _fit_names(rows, kept, names)


def _filled(value: object) -> bool:
    return value is not None and value != ""


def _fit_names(
    rows: list[dict[str, object]], columns: Sequence[str], names: Sequence[str]
) -> list[dict[str, object]]:
    """`rows` with as few name values cut as the line allows.

    One name takes whatever the other columns leave. Two share it, and
    shrinking both evenly, as `_fit` does, cuts nearly every row of a real
    application; so the split is the one that leaves the most values whole,
    the first name wider on a tie, and only a value longer than its column is
    cut. A path is cut from the left, so its file name stays.
    """
    widths = {column: _natural_width(rows, column) for column in columns}
    overflow = _line_width(columns, widths) - MAX_LINE
    if overflow <= 0 or not names:
        return rows
    budget = sum(widths[name] for name in names) - overflow
    if len(names) == 1:
        [name] = names
        return [{**row, name: _cut(name, row[name], max(budget, len(name)))} for row in rows]
    first, second = names
    # Both columns keep at least their header: the others are numbers and
    # short words, so the budget never drops under the two headers together.
    widest = min(widths[second], budget - len(first))
    narrowest = max(len(second), budget - widths[first])

    def cuts(second_width: int) -> tuple[int, int]:
        cut = sum(len(str(row[first])) > budget - second_width for row in rows)
        cut += sum(len(str(row[second])) > second_width for row in rows)
        return cut, second_width

    second_width = min(range(narrowest, widest + 1), key=cuts)
    return [
        {
            **row,
            first:  _cut(first, row[first], budget - second_width),
            second: _cut(second, row[second], second_width),
        }
        for row in rows
    ]


def _cut(column: str, value: object, width: int) -> object:
    """`value` cut to `width`, from the left for a path and from the right otherwise."""
    text = "" if value is None else str(value)
    if column not in HIT_PATHS or len(text) <= width:
        return _trim(value, width)
    return _ELLIPSIS + text[len(text) - max(0, width - len(_ELLIPSIS)):]


def _window(text: str, term: str, width: int) -> str:
    """`text` cut to `width` with TERM whole and centred in what is shown.

    Cut from the end alone, a hit near the end of a long line shows the start
    of the line and none of the reason it is listed. So the window follows the
    match: the head of the line when the match is in it, the tail when it is
    there, and otherwise the match in the middle with `...` on both sides. A
    line with no match to find, one `git grep` matched and Python's case fold
    did not, is cut from the end.
    """
    if len(text) <= width:
        return text
    at = text.lower().find(term.lower())
    cut = width - len(_ELLIPSIS)
    if at < 0 or at + len(term) <= cut:
        return f"{text[:cut]}{_ELLIPSIS}"
    if len(text) - at <= cut:
        return f"{_ELLIPSIS}{text[-cut:]}"
    room = cut - len(_ELLIPSIS)
    start = at - max(0, room - len(term)) // 2
    return f"{_ELLIPSIS}{text[start:start + room]}{_ELLIPSIS}"


def _relation_rows(relations: Sequence[Relation]) -> list[dict[str, object]]:
    return [
        {
            "object":   relation.obj,
            "relation": relation.relation,
            "type":     relation.kind,
            "name":     relation.name,
        }
        for relation in relations
    ]


def _link_rows(links: Sequence[FlowEdge]) -> list[dict[str, object]]:
    """One row per link, both ends written `APP.PAGE` (`shared` for a list entry)."""
    return [
        {
            "from":      f"{edge.app_id}.{_src_page_label(edge)}",
            "to":        f"{edge.target_app_id}.{edge.target_page}",
            "src_type":  edge.src_type,
            "component": _component_label(edge),
            "flag":      edge.flag,
        }
        for edge in links
    ]


__all__ = [name for name in globals() if not name.startswith("__")]
