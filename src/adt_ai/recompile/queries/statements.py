"""Statement builders and selection binds for the recompile module.

The compile/refresh DDL builders (``build_compile_statement``,
``build_refresh_statement``) and the ``-force`` drift-selection binds
(``compile_drift_binds``) live here, split out of :mod:`adt_ai.recompile.queries.objects`
(which holds the read SQL) to keep each file inside the repo's context-size
contract. The package ``__init__`` re-exports all topic modules, so callers keep
importing everything from ``adt_ai.recompile.queries``.
"""

from __future__ import annotations

from adt_ai.shared.object_types import PLSQL_OBJECT_TYPES
from adt_ai.shared.sql_identifiers import safe_identifier, safe_object_type


def _scope_flags(scope: list[str] | None) -> tuple[bool, bool]:
    """Which PL/Scope components a ``-scope`` list asks for: (identifiers, statements).

    ``ALL`` turns both on; the bare component names turn on their own. Shared by
    :func:`build_compile_statement` (which emits the setting) and
    :func:`compile_drift_binds` (which tests for its absence), so the two can never
    disagree on what a ``-scope`` value means.
    """
    if not isinstance(scope, list):
        return (False, False)
    identifiers = "IDENTIFIERS" in scope or "ALL" in scope
    statements  = "STATEMENTS" in scope or "ALL" in scope
    return (identifiers, statements)


def _warning_flags(warnings: list[str] | None) -> tuple[bool, bool, bool]:
    """Which warning groups a ``-warnings`` list asks for: (severe, perf, info).

    Mirrors :func:`build_compile_statement`'s own token tests (including the legacy
    ``PERFORMANE`` misspelling) so drift detection asks for exactly the warnings the
    compile would enable.
    """
    if not isinstance(warnings, list):
        return (False, False, False)
    severe = "SEVERE" in warnings
    perf   = "PERF" in warnings or "PERFORMANE" in warnings
    info   = "INFO" in warnings or "INFORMATIONAL" in warnings
    return (severe, perf, info)


def build_compile_statement(
    object_type: str,
    object_name: str,
    *,
    native: bool = False,
    interpreted: bool = False,
    optimize_level: int | None = None,
    scope: list[str] | None = None,
    warnings: list[str] | None = None,
) -> str:
    """Build the ALTER ... COMPILE statement for one object.

    Faithful port of old ADT ``Recompile.build_query``, including the
    ``'PERFORMANE'`` spelling accepted for the PERFORMANCE warning.

    ``PLSQL_CODE_TYPE`` is stamped **only** when the caller explicitly asks for a
    code type (``-native`` → NATIVE, ``-interpreted`` → INTERPRETED). A plain
    recompile leaves it unset so ``REUSE SETTINGS`` preserves the object's existing
    code type: emitting it unconditionally silently flipped natively-compiled
    objects to INTERPRETED on every recompile, because a named setting overrides
    ``REUSE SETTINGS`` (#146). ``-native`` wins if both flags are passed.
    """
    safe_object_type(object_type, role="object type")
    safe_identifier(object_name, role="object name")
    type_body   = " BODY" if "BODY" in object_type else ""
    type_family = object_type.replace(" BODY", "")
    extras      = ""

    # extra stuff for code objects
    if object_type in PLSQL_OBJECT_TYPES:
        # Only stamp the code type when it was explicitly requested; otherwise let
        # REUSE SETTINGS carry the object's current NATIVE/INTERPRETED state forward.
        if native:
            extras += " PLSQL_CODE_TYPE = NATIVE"
        elif interpreted:
            extras += " PLSQL_CODE_TYPE = INTERPRETED"

        # setup optimize level
        if optimize_level is not None and 1 <= optimize_level <= 3:
            extras += " PLSQL_OPTIMIZE_LEVEL = " + str(optimize_level)

        # setup scope
        if isinstance(scope, list):
            want_identifiers, want_statements = _scope_flags(scope)
            scope_value = ""
            scope_value += "IDENTIFIERS:ALL," if want_identifiers else ""
            scope_value += "STATEMENTS:ALL," if want_statements else ""
            extras += " PLSCOPE_SETTINGS = '" + scope_value.rstrip(",") + "'"

        # setup warnings
        if isinstance(warnings, list):
            want_severe, want_perf, want_info = _warning_flags(warnings)
            warnings_value = ""
            warnings_value += "ENABLE:SEVERE," if want_severe else ""
            warnings_value += "ENABLE:PERFORMANCE," if want_perf else ""
            warnings_value += "ENABLE:INFORMATIONAL," if want_info else ""
            extras += " PLSQL_WARNINGS = '" + warnings_value.strip(",").replace(",", "','") + "'"

        extras += " REUSE SETTINGS"

    return f"ALTER {type_family} {object_name} COMPILE{type_body} {extras}"


def compile_drift_binds(
    *,
    force: bool,
    native: bool = False,
    interpreted: bool = False,
    optimize_level: int | None = None,
    scope: list[str] | None = None,
    warnings: list[str] | None = None,
) -> dict[str, object]:
    """The ``:drift_*`` binds that gate the modifier-narrowed ``-force`` sweep.

    ``-force`` alone recompiles every matching object (``drift_only = 'N'``, today's
    meaning). Combined with a compile modifier it becomes drift-narrowed: only VALID
    PL/SQL objects whose current ``USER_PLSQL_OBJECT_SETTINGS`` differ from the
    requested target state are selected, any single mismatch qualifying (#146).

    A modifier only counts when it names a real target: an out-of-range ``-level`` or
    a ``-scope``/``-warnings`` list naming nothing recognised is inert, exactly as it
    is in :func:`build_compile_statement`. The target values are derived from the same
    helpers the compile uses, so selection and compilation always agree.

    Every bind is returned on every call (neutral when its modifier is off) because
    the query references all of them: python-oracledb rejects a bound-but-unused name,
    and an unbound one, against a real database.
    """
    level_active     = optimize_level is not None and 1 <= optimize_level <= 3
    code_type_active = native or interpreted
    want_identifiers, want_statements = _scope_flags(scope)
    want_severe, want_perf, want_info = _warning_flags(warnings)

    any_modifier = (
        code_type_active
        or level_active
        or want_identifiers
        or want_statements
        or want_severe
        or want_perf
        or want_info
    )

    def yn(active: bool) -> str:
        return "Y" if active else "N"

    target_code_type = ("NATIVE" if native else "INTERPRETED") if code_type_active else ""

    return {
        "drift_only"              : yn(force and any_modifier),
        "drift_code_type"         : yn(code_type_active),
        "target_code_type"        : target_code_type,
        "drift_level"             : yn(level_active),
        "target_level"            : optimize_level if level_active else -1,
        "drift_scope_identifiers" : yn(want_identifiers),
        "drift_scope_statements"  : yn(want_statements),
        "drift_warn_severe"       : yn(want_severe),
        "drift_warn_perf"         : yn(want_perf),
        "drift_warn_info"         : yn(want_info),
    }


def _refresh_method_code(refresh_method: str | None) -> str:
    """Map an MV's configured refresh_method to a DBMS_MVIEW.REFRESH method char.

    COMPLETE → 'C', FAST → 'F'. FORCE, NEVER, anything unknown, and a missing
    method all fall back to '?' (let Oracle decide). The point is to refresh a
    view with the method already attached to it, never silently re-picking and
    flipping a COMPLETE view to FAST.
    """
    method = (refresh_method or "").strip().upper()
    if method == "COMPLETE":
        return "C"
    if method == "FAST":
        return "F"
    return "?"


def mview_type_code(refresh_method: str | None, has_log: bool = False) -> str:
    """Map an MV's configured refresh_method to the F/C TYPE shown in the report.

    Unlike :func:`_refresh_method_code` (which feeds DBMS_MVIEW.REFRESH and leaves
    FORCE as '?' so Oracle decides at runtime), the *display* always resolves to a
    clean letter: COMPLETE → 'C', FAST → 'F'. FORCE resolves to what Oracle would
    actually do, 'F' when a usable MV log exists, 'C' otherwise. NEVER → 'N', and a
    missing method → '' (nothing to show).
    """
    method = (refresh_method or "").strip().upper()
    if method == "COMPLETE":
        return "C"
    if method == "FAST":
        return "F"
    if method == "FORCE":
        return "F" if has_log else "C"
    return method[:1]


def build_refresh_statement(object_name: str, refresh_method: str | None = None) -> str:
    """Build the DBMS_MVIEW.REFRESH call that refreshes one materialized view.

    Staleness is fixed by refreshing (not compiling), using the method the MV is
    configured with (``refresh_method``) so the tool never changes a view's
    refresh type. Unknown/missing methods fall back to '?' (Oracle decides).
    """
    safe_identifier(object_name, role="object name")
    method = _refresh_method_code(refresh_method)
    return f"BEGIN DBMS_MVIEW.REFRESH('{object_name}', '{method}'); END;"
