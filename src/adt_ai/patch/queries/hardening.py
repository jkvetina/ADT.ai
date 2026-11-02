"""Existence-check wrappers for a hand-written patch script, ADT #309 (was #17).

A verbatim port of old ADT's ``lib/queries_patch.py`` template set, which
``fix_patch_script`` (patch.py:2211-2312) selected between by statement shape.
Every one turns a statement that fails on its second run into a no-op, and the
ORA code each one dodges is named beside it, that is the whole reason the
wrapper exists, and a template whose guard is written the wrong way round dodges
nothing.

The bodies are old ADT's, whitespace included, because the generated PL/SQL is a
compatibility contract: a project that has reviewed one of these blocks in a
deployed patch should recognise it byte for byte in the next.
"""

from __future__ import annotations

# USER_* is authoritative only for the connected user's objects. ALL_* can
# omit a foreign object even when an ANY privilege permits its DDL.
SCHEMA_GUARD = """    IF {owner_expression} <> USER THEN
        RAISE_APPLICATION_ERROR(-20002,
            'cannot harden DDL for schema ' || {owner_expression} ||
            ' while connected as ' || USER || '; connect as the target schema');
    END IF;
    --
"""

# ORA-01418 (index does not exist), ORA-04043 (object does not exist).
DROP_TEMPLATE = """
PROMPT "-- {header}";
DECLARE
    in_object_type      CONSTANT VARCHAR2(256) := '{object_type}';
    in_object_name      CONSTANT VARCHAR2(256) := '{object_name}';
BEGIN
{schema_guard}\
    FOR c IN (
        SELECT object_type, object_name
        FROM user_objects
        WHERE object_type   = in_object_type
            AND object_name = in_object_name
    ) LOOP
        EXECUTE IMMEDIATE
            '{statement}';
    END LOOP;
END;
/
""".lstrip()

# ORA-00955 (name already used), ORA-01408 (column list already indexed).
CREATE_TEMPLATE = """
PROMPT "-- {header}";
DECLARE
    in_object_type      CONSTANT VARCHAR2(256) := '{object_type}';
    in_object_name      CONSTANT VARCHAR2(256) := '{object_name}';
    --
    v_found CHAR;
BEGIN
{schema_guard}\
    SELECT MAX('Y') INTO v_found
    FROM user_objects
    WHERE object_type   = in_object_type
        AND object_name = in_object_name;
    --
    IF v_found IS NULL THEN
        EXECUTE IMMEDIATE
            '{statement}';
    END IF;
END;
/
""".lstrip()

# ORA-01430 (column already exists), ORA-02260, ORA-02275, and since ADT #753
# ORA-00942 (table or view does not exist).
#
# The table test is the one addition to old ADT's block, and it is what lets a
# generated ALTER run AHEAD of the table's own exported file (`patch/create.py`).
# `#753` skipped a table the target never received and left the `CREATE TABLE IF
# NOT EXISTS` behind this block to create it whole; since `#969` that CREATE is
# linked commented out for every altered table (`create._alter_disabled_link`),
# so the skip left a target behind the source (UAT behind DEV) with neither
# statement and a log ending `-- SUCCESS`. An absent table RAISES now (ADT
# #1094): the `ORA-20001` line fails the deploy, where `ORA-00942` would have
# too, and names the table and why its CREATE is not there to save it.
ADD_COLUMN_TEMPLATE = """
PROMPT "-- {header}";
DECLARE
    in_table_name       CONSTANT VARCHAR2(256) := '{object_name}';
    in_column_name      CONSTANT VARCHAR2(256) := '{cc_name}';
    --
    v_table CHAR;
    v_found CHAR;
BEGIN
{schema_guard}\
    SELECT MAX('Y') INTO v_table
    FROM user_tables
    WHERE table_name    = in_table_name;
    --
    SELECT MAX('Y') INTO v_found
    FROM user_tab_columns
    WHERE table_name    = in_table_name
        AND column_name = in_column_name;
    --
    IF v_table = 'Y' AND v_found IS NULL THEN
        EXECUTE IMMEDIATE
            '{statement}';
    ELSIF v_table IS NULL THEN
        RAISE_APPLICATION_ERROR(-20001, 'table ' || in_table_name || ' does not exist on this target; its CREATE is disabled by the ALTER');
    END IF;
END;
/
""".lstrip()  # noqa: E501 - the raise is one PL/SQL line, as the deploy log shows it

# ORA-00904 (invalid identifier), ORA-01430, ORA-02275. The guard is INVERTED
# against the ADD variant, this one runs only when the column IS there.
DROP_COLUMN_TEMPLATE = """
PROMPT "-- {header}";
DECLARE
    in_table_name       CONSTANT VARCHAR2(256) := '{object_name}';
    in_column_name      CONSTANT VARCHAR2(256) := '{cc_name}';
    --
    v_found CHAR;
BEGIN
{schema_guard}\
    SELECT MAX('Y') INTO v_found
    FROM user_tab_columns
    WHERE table_name    = in_table_name
        AND column_name = in_column_name;
    --
    IF v_found = 'Y' THEN
        EXECUTE IMMEDIATE
            '{statement}';
    END IF;
END;
/
""".lstrip()

# ORA-02260 (one primary key), ORA-02261, ORA-02264, ORA-02275, and ORA-00942
# through the same table test the ADD COLUMN block carries, raising on an absent
# table for the same reason (ADT #1094).
ADD_CONSTRAINT_TEMPLATE = """
PROMPT "-- {header}";
DECLARE
    in_table_name           CONSTANT VARCHAR2(256) := '{object_name}';
    in_constraint_name      CONSTANT VARCHAR2(256) := '{cc_name}';
    --
    v_table CHAR;
    v_found CHAR;
BEGIN
{schema_guard}\
    SELECT MAX('Y') INTO v_table
    FROM user_tables
    WHERE table_name        = in_table_name;
    --
    SELECT MAX('Y') INTO v_found
    FROM user_constraints
    WHERE table_name        = in_table_name
        AND constraint_name = in_constraint_name;
    --
    IF v_table = 'Y' AND v_found IS NULL THEN
        EXECUTE IMMEDIATE
            '{statement}';
    ELSIF v_table IS NULL THEN
        RAISE_APPLICATION_ERROR(-20001, 'table ' || in_table_name || ' does not exist on this target; its CREATE is disabled by the ALTER');
    END IF;
END;
/
""".lstrip()  # noqa: E501 - the raise is one PL/SQL line, as the deploy log shows it

# ORA-02443 (nonexistent constraint), ORA-23292.
DROP_CONSTRAINT_TEMPLATE = """
PROMPT "-- {header}";
DECLARE
    in_table_name           CONSTANT VARCHAR2(256) := '{object_name}';
    in_constraint_name      CONSTANT VARCHAR2(256) := '{cc_name}';
    --
    v_found CHAR;
BEGIN
{schema_guard}\
    SELECT MAX('Y') INTO v_found
    FROM user_constraints
    WHERE table_name        = in_table_name
        AND constraint_name = in_constraint_name;
    --
    IF v_found = 'Y' THEN
        EXECUTE IMMEDIATE
            '{statement}';
    END IF;
END;
/
""".lstrip()

# Keyed exactly as old ADT keyed them, because the LOOKUP ORDER in
# `harden.py` builds these strings from the parsed statement:
# `<TYPE> | <OBJECT_TYPE> | <OPERATION>`, then narrower fallbacks. RENAME reuses
# the DROP-shaped guard for both columns and constraints, renaming needs the
# old name to exist, which is the same test.
HARDENING_TEMPLATES: dict[str, str] = {
    "DROP": DROP_TEMPLATE,
    "CREATE": CREATE_TEMPLATE,
    "ALTER | ADD COLUMN": ADD_COLUMN_TEMPLATE,
    "ALTER | DROP COLUMN": DROP_COLUMN_TEMPLATE,
    "ALTER | MODIFY COLUMN": DROP_COLUMN_TEMPLATE,
    "ALTER | RENAME COLUMN": DROP_COLUMN_TEMPLATE,
    "ALTER | ADD CONSTRAINT": ADD_CONSTRAINT_TEMPLATE,
    "ALTER | DROP CONSTRAINT": DROP_CONSTRAINT_TEMPLATE,
    "ALTER | RENAME CONSTRAINT": DROP_CONSTRAINT_TEMPLATE,
}

# The statement a generated DROP helper (`patch/drop_helpers.py`) runs for an
# object type Oracle drops with something other than `DROP <type> <name>` (ADT
# #1094). `DROP JOB` is ORA-00950 and ended the deploy in ERROR once the guard
# had found the job: the scheduler objects go through `DBMS_SCHEDULER`, an MLE
# environment through `DROP MLE ENV`. Each sits inside the single-quoted literal
# `DROP_HELPER_TEMPLATE` runs, so its own quotes are doubled. `{name}` is the
# object name, `{type}` the object type, both validated before they are rendered.
DROP_STATEMENTS: dict[str, str] = {
    "JOB": "BEGIN DBMS_SCHEDULER.DROP_JOB(''{name}''); END;",
    "SCHEDULE": "BEGIN DBMS_SCHEDULER.DROP_SCHEDULE(''{name}''); END;",
    "PROGRAM": "BEGIN DBMS_SCHEDULER.DROP_PROGRAM(''{name}''); END;",
    "MLE ENVIRONMENT": "DROP MLE ENV {name}",
}
DROP_STATEMENT_DEFAULT = "DROP {type} {name}"
