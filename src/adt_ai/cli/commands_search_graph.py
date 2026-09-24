"""`search`'s graph questions: what an object uses and is used by, and page links (`#30`).

`search` answers from ADT.ai's local stores. History is the mode a
run gets when it asks nothing else; `-from`, `-to`, `-impact` and `-constraint`
ask the object graph in `dependencies.db` or the page graph in `flow.db`, and
a store that lacks what was asked, or is older than the application's last
change, is refreshed first rather than named as a rebuild to run (ADT #900),
with
the answers the retired `dependencies` and `flow` commands printed, rendered by
the same code. `-app` asks the mirror the application's side of that graph:
every object the application uses, narrowed by `-page`, `-type`, `-name` and
`-schema`, the two pattern flags reading the way they read on history.

**One flag pair, told apart by the value.** A `-from`/`-to` value whose first
character is a digit is a page, written `APP.PAGE`; Oracle identifiers cannot
open on a digit, so nothing that names an object is read as a page. Anything
else is an object, `TYPE.NAME` or a bare name, exactly as the dependency
mirror names it. That keeps one string shape on both flags and needs no `-app` beside a page.

**A flag the chosen mode would ignore is refused**, never ignored: a history
filter beside a graph question, `-schema` or a machine `-format` beside a
history search, two graph questions in one run, and `-schema` beside a page or
a constraint it cannot narrow. `console.md` puts the rule as a flag a command
does not take being a parser error rather than a flag it ignores, and a mode is
where one command stops taking a flag.

A TERM (ADT #895) is a mode of its own, `commands_search_term`, and follows the
same rule: `-layer`, `-app`, `-page`, `-schema` and `-branch` narrow it, and
every other flag of the two tables below is refused beside it.
"""

from __future__ import annotations

import argparse
import re
import sys
from collections.abc import Iterator
from contextlib import contextmanager, redirect_stdout
from pathlib import Path

from adt_ai.cli.constants import (
    ConfigLoader,
    ConnectionConfigError,
    DependencyStore,
    GatewayFactory,
    print_module_banner,
)
from adt_ai.cli.context import (
    ApexAppSelection,
    _app_in_selection,
    _config_search_paths,
    _flatten_arg_groups,
    _parse_apex_app_selection,
    _parse_apex_page_selection,
    _repo_root,
)
from adt_ai.cli.dependencies_reporters import (
    _print_app_inventories,
    _print_dependency_impact,
    _print_dependency_list,
    _print_foreign_key_tree,
)
from adt_ai.cli.flow_reporters import _print_not_loaded, _print_page_links
from adt_ai.cli.refresh_connect import (
    _app_selection_error,
    _page_selection_error,
    _resolve_refresh_names,
)
from adt_ai.cli.search_autobuild import changed_apps, refresh_stores, stamped_apps
from adt_ai.dependencies.store import DEFAULT_MAX_DEPTH
from adt_ai.flow.store import ApexFlowStore
from adt_ai.search.term_model import parse_layers
from adt_ai.shared.error_screen import exit_code_for, print_adt_error
from adt_ai.shared.internal_paths import internal_path

#: Each graph question by the dest it parses into, in the order the refusals
#: name them.
GRAPH_FLAGS = (
    ("graph_from", "-from"),
    ("graph_to", "-to"),
    ("impact", "-impact"),
    ("constraint", "-constraint"),
    ("app", "-app"),
)

#: The history flags `-app` reads too: which objects to list, matched the way a
#: history search matches them, as SQL LIKE patterns.
INVENTORY_FILTERS = frozenset({"type", "name"})

#: Every flag only a history search reads, by dest. Absence is `None` or
#: `False` for all of them, which is why `-limit` defaults to `None`.
HISTORY_FLAGS = (
    ("branch", "-branch"),
    ("limit", "-limit"),
    ("files", "-files"),
    ("summary", "-summary"),
    ("file", "-file"),
    ("type", "-type"),
    ("name", "-name"),
    ("by", "-by"),
    ("my", "-my"),
    ("commit_refs", "-commit"),
    ("hash", "-hash"),
    ("recent", "-recent"),
    ("since", "-since"),
    ("until", "-until"),
    ("restore", "-restore"),
)

_PAGE_SHAPE = re.compile(r"(\d+)\.(\d+)")


@contextmanager
def _chrome_stream(machine_format: bool) -> Iterator[None]:
    """Where a warning goes: stdout beside a table, stderr beside a document.

    Under `-format yaml`/`md` stdout carries the document and nothing else, so
    a pasted answer never opens on chrome; the banner and the timer are already
    on stderr, and a warning is chrome to the last byte.
    """
    if not machine_format:
        yield
        return
    with redirect_stdout(sys.stderr):
        yield


def _present(args: argparse.Namespace, dest: str) -> bool:
    value = getattr(args, dest, None)
    return value is not None and value is not False


def _graph_flags(args: argparse.Namespace) -> list[str]:
    return [flag for dest, flag in GRAPH_FLAGS if _present(args, dest)]


def _graph_requested(args: argparse.Namespace) -> bool:
    """True when the run asks the object or page graph rather than history."""
    return bool(_graph_flags(args))


def _parse_page(value: str) -> tuple[int, int] | None:
    """`APP.PAGE` as `(app, page)`, `None` for an object, `ValueError` for neither.

    The first character decides, so `100` is a malformed page rather than an
    object named `100`: nothing that opens on a digit can be an Oracle object,
    and reading it as one would answer `(none)` to a question that was really a
    typo.
    """
    if not value[:1].isdigit():
        return None
    match = _PAGE_SHAPE.fullmatch(value)
    if match is None:
        raise ValueError("PAGE MUST BE APP.PAGE, LIKE 122.50")
    return int(match.group(1)), int(match.group(2))


def _search_argument_error(args: argparse.Namespace) -> str | None:
    """Refuse a flag the chosen mode would ignore, before the banner prints."""
    graph = _graph_flags(args)
    if getattr(args, "data", False) and getattr(args, "term", None) is None:
        return "-data NEEDS A TERM\n\n-data searches table rows for a TERM, written first."
    if args.page and "-app" not in graph and not getattr(args, "data", False):
        return "-page NEEDS -app\n\n-page narrows -app."
    if getattr(args, "term", None) is not None:
        return _term_argument_error(args)
    if getattr(args, "layer", None):
        return "-layer NEEDS A TERM\n\n-layer narrows a TERM search, and the TERM is written first."
    if not graph:
        offenders = [
            flag
            for flag, present in (
                ("-schema", bool(args.schema)),
                ("-format", args.format != "table"),
            )
            if present
        ]
        if offenders:
            return (
                f"{' / '.join(offenders)} NEEDS A GRAPH QUERY\n\n"
                "Add -from, -to, -impact, -constraint or -app."
            )
        return None
    if len(graph) > 1:
        return (
            f"{' / '.join(graph)} CANNOT BE COMBINED\n\n"
            "They are separate actions; pass one per run."
        )
    inventory = graph == ["-app"]
    history = [
        flag
        for dest, flag in HISTORY_FLAGS
        if _present(args, dest) and not (inventory and dest in INVENTORY_FILTERS)
    ]
    if history:
        return (
            f"{' / '.join(history)} CANNOT BE COMBINED WITH {graph[0]}\n\n"
            f"{' / '.join(history)} searches commit history."
        )
    if inventory:
        return _app_selection_error(args.app) or _page_selection_error(args.page)
    for dest, flag in GRAPH_FLAGS[:2]:
        value = getattr(args, dest)
        if value is None:
            continue
        try:
            page = _parse_page(value)
        except ValueError as exc:
            return f"{flag} {value}: {exc}"
        if page is not None and args.schema:
            return (
                f"-schema CANNOT BE COMBINED WITH PAGE {value}\n\n"
                "-schema narrows an object's owner."
            )
    if args.constraint is not None and args.schema:
        return "-schema CANNOT BE COMBINED WITH -constraint\n\n-schema narrows an object's owner."
    return None


#: The flags from the two tables above that narrow a TERM search too (ADT
#: #895). `-page` and `-schema` are in neither table and narrow it as well;
#: every other flag in them is refused beside TERM, so a flag either table
#: gains or loses is refused or allowed with no edit here.
TERM_FILTERS = frozenset({"app", "branch"})

#: Which layers each narrowing flag reads, so one `-layer` leaves out is
#: refused rather than silently narrowing nothing.
_TERM_FILTER_LAYERS = (
    ("app", "-app", ("APEX", "STATIC")),
    ("page", "-page", ("APEX",)),
    ("schema", "-schema", ("DB",)),
    ("branch", "-branch", ("GIT",)),
)


def _term_argument_error(args: argparse.Namespace) -> str | None:
    """Refuse what a TERM search would ignore, before the banner (ADT #895)."""
    if not args.term.strip():
        return "TERM IS EMPTY\n\nWrite the text to find first: adtai search TERM"
    if getattr(args, "data", False):
        return _data_argument_error(args)
    refused = [
        flag
        for dest, flag in (*GRAPH_FLAGS, *HISTORY_FLAGS)
        if dest not in TERM_FILTERS and _present(args, dest)
    ]
    if args.format != "table":
        refused.append("-format")
    if refused:
        return (
            f"{' / '.join(refused)} CANNOT BE COMBINED WITH TERM\n\n"
            "Only -layer, -app, -page, -schema and -branch narrow it."
        )
    try:
        layers = parse_layers(_flatten_arg_groups(args.layer))
    except ValueError as exc:
        return str(exc)
    for dest, flag, reads in _TERM_FILTER_LAYERS:
        if _present(args, dest) and not set(reads) & set(layers):
            return (
                f"-layer LEAVES OUT WHAT {flag} NARROWS\n\n"
                f"{flag} narrows {' and '.join(reads)}."
            )
    return _app_selection_error(args.app) or _page_selection_error(args.page)


#: What narrows `TERM -data` (ADT #879), Jan's pick: `-schema` the owners,
#: `-name` the tables and `-limit` the rows each table returns. `-schema` is in
#: neither table below, so only these two are let through them.
DATA_FILTERS = frozenset({"name", "limit"})


def _data_argument_error(args: argparse.Namespace) -> str | None:
    """Refuse what a table-row search would ignore: every offline-layer flag too."""
    refused = [
        flag
        for flag, present in (("-layer", bool(args.layer)), ("-page", bool(args.page)))
        if present
    ]
    refused += [
        flag
        for dest, flag in (*GRAPH_FLAGS, *HISTORY_FLAGS)
        if dest not in DATA_FILTERS and _present(args, dest)
    ]
    if args.format != "table":
        refused.append("-format")
    if refused:
        return (
            f"{' / '.join(refused)} CANNOT BE COMBINED WITH -data\n\n"
            "Only -schema, -name and -limit narrow it."
        )
    if args.limit is not None and args.limit < 0:
        return "-limit CANNOT BE NEGATIVE\n\n-limit takes 0 or more rows per object."
    return None


def _run_search_graph(
    args: argparse.Namespace, gateway_factory: GatewayFactory | None = None
) -> int:
    """Answer one graph question from the local stores, building them first.

    A store that does not hold what was asked, or holds an application from
    before its last change, is refreshed the way `rebuild` refreshes it, and
    the answer follows on the same screen (ADT #900).
    """
    # Machine output keeps stdout pure data, the banner and every warning on
    # stderr beside the timer, the one routing `_command_timer_stdout` also reads.
    chrome = sys.stdout if args.format == "table" else sys.stderr
    print_module_banner("SEARCH", file=chrome)
    root = Path(args.root).expanduser().resolve()

    if args.app:
        return _answer_inventory(root, args, gateway_factory)
    for dest, _flag in GRAPH_FLAGS[:2]:
        value = getattr(args, dest)
        page = None if value is None else _parse_page(value)
        if page is not None:
            return _answer_page(
                root, "into" if dest == "graph_to" else "from", *page, args, gateway_factory
            )
    return _answer_object(root, args, gateway_factory)


def _flow_apps(root: Path) -> set[int]:
    db_path = internal_path(root, "flow.db")
    if not db_path.exists():
        return set()
    with ApexFlowStore.open(db_path) as store:
        return set(store.all_app_ids())


def _bring_apps_up_to_date(
    root: Path,
    app_ids: list[int],
    held: set[int],
    args: argparse.Namespace,
    gateway_factory: GatewayFactory | None,
) -> int:
    """Refresh every application the store lacks or holds from before its last change.

    With no connection to refresh through, a question the store can still
    partly answer is answered, and the applications it lacks are named under
    `APP NOT LOADED` by the caller; one it cannot answer at all stops on the
    configuration screen.
    """
    missing = [app for app in app_ids if app not in held]
    stale = changed_apps(root, [app for app in app_ids if app in held], gateway_factory)
    wanted = sorted({*missing, *stale})
    if not wanted:
        return 0
    try:
        return refresh_stores(
            root,
            gateway_factory,
            machine_format = args.format != "table",
            apps           = [str(app) for app in wanted],
        )
    except ConnectionConfigError:
        if any(app in held for app in app_ids):
            return 0
        raise


def _answer_page(
    root: Path,
    direction: str,
    app_id: int,
    page: int,
    args: argparse.Namespace,
    gateway_factory: GatewayFactory | None = None,
) -> int:
    # Past this point the store holds the application: a refresh that could
    # not load it failed with its own warning, and one that could not connect
    # raised, since a store lacking the application has nothing to answer.
    code = _bring_apps_up_to_date(root, [app_id], _flow_apps(root), args, gateway_factory)
    if code:
        return code
    with ApexFlowStore.open(internal_path(root, "flow.db")) as store:
        edges = store.incoming(app_id, page) if direction == "into" else store.outgoing(
            app_id, page
        )
    _print_page_links(direction, app_id, page, edges, args.format)
    return 0


def _answer_inventory(
    root: Path, args: argparse.Namespace, gateway_factory: GatewayFactory | None = None
) -> int:
    """Every object the named applications use, one section per application.

    An application counts as loaded once `rebuild -app` stamped it in the
    mirror. An explicit id the mirror lacks, or holds from before its last
    change, is scanned first (ADT #900); one the refresh could not reach is
    still named under `APP NOT LOADED` rather than answered `(none)`. A range
    resolves against the applications the mirror holds, and with no mirror at
    all the range itself is refreshed; one matching none of them is the
    refusal `rebuild -app` prints for a range matching nothing.
    """
    tokens = _flatten_arg_groups(args.app) or []
    # Already proven readable by `_search_argument_error`, before the banner.
    selection = _parse_apex_app_selection(tokens) or ApexAppSelection()
    db_path = internal_path(root, "dependencies.db")
    if not db_path.exists() and selection.has_ranges:
        code = refresh_stores(
            root, gateway_factory, machine_format=args.format != "table", apps=tokens
        )
    else:
        code = _bring_apps_up_to_date(
            root,
            sorted(int(app_id) for app_id in selection.explicit_ids),
            stamped_apps(root),
            args,
            gateway_factory,
        )
    if code and not db_path.exists():
        return code
    # A refresh that failed one application still stored the others: they are
    # answered, and the one it failed is named under `APP NOT LOADED` below.
    pages = _parse_apex_page_selection(_flatten_arg_groups(args.page))
    with DependencyStore.open(db_path) as store:
        loaded = {
            int(row["scope"])
            for row in store.last_refreshes()
            if row["type"] == "app" and row["scope"].isdigit()
        }
        in_ranges = ApexAppSelection(ranges=selection.ranges)
        ranged = {app for app in loaded if _app_in_selection(app, in_ranges)}
        if selection.has_ranges and not ranged:
            print_adt_error("INPUT NOT FOUND", "-app RANGE MATCHED NO APPLICATIONS")
            return exit_code_for("INPUT NOT FOUND")
        named = {int(app_id) for app_id in selection.explicit_ids}
        answers = [
            (
                app,
                store.apex_app_inventory(
                    [app],
                    page_ids    = pages.explicit_ids if pages else (),
                    page_ranges = pages.ranges if pages else (),
                    types       = _flatten_arg_groups(args.type),
                    names       = _flatten_arg_groups(args.name),
                    owners      = _resolve_refresh_names(_flatten_arg_groups(args.schema)),
                ),
            )
            for app in sorted((named & loaded) | ranged)
        ]
    missing = sorted(named - loaded)
    with _chrome_stream(args.format != "table"):
        _print_not_loaded(
            f"APP {app} is not loaded, and could not be refreshed" for app in missing
        )
    if answers:
        _print_app_inventories(answers, args.format)
    return 1 if missing else 0


def _answer_object(
    root: Path, args: argparse.Namespace, gateway_factory: GatewayFactory | None = None
) -> int:
    db_path = internal_path(root, "dependencies.db")
    if not db_path.exists():
        # No mirror at all: build it for the schemas `-schema` names, or the
        # connection's default ones, the way a bare `rebuild` does (ADT #900).
        # A schema refresh fails by raising onto the shared error screens; it
        # has no failing exit code of its own, so nothing is read back here.
        refresh_stores(
            root,
            gateway_factory,
            machine_format = args.format != "table",
            schemas        = _flatten_arg_groups(args.schema),
        )
    owners = _resolve_refresh_names(_flatten_arg_groups(args.schema))
    with DependencyStore.open(db_path) as store:
        if args.graph_from is not None:
            return _print_dependency_list(
                args.graph_from, store.uses(args.graph_from, owners=owners), "uses", args.format
            )
        if args.graph_to is not None:
            return _print_dependency_list(
                args.graph_to,
                store.used_by(args.graph_to, owners=owners),
                "used_by",
                args.format,
            )
        if args.impact is not None:
            config = ConfigLoader(
                _config_search_paths(None, root, _repo_root())
            ).load().data
            max_depth = int(config.get("dependencies_max_depth") or DEFAULT_MAX_DEPTH)
            return _print_dependency_impact(
                args.impact,
                store.impact(args.impact, max_depth=max_depth, owners=owners),
                args.format,
                store.affected_columns(args.impact),
                store.apex_callers(args.impact),
            )
        return _print_foreign_key_tree(
            args.constraint, store.foreign_key_tree(args.constraint), args.format
        )


__all__ = [name for name in globals() if not name.startswith("__")]
