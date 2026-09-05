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

# ORA-01418 (index does not exist), ORA-04043 (object does not exist).
DROP_TEMPLATE = """
PROMPT "-- {header}";
DECLARE
    in_object_type      CONSTANT VARCHAR2(256) := '{object_type}';
    in_object_name      CONSTANT VARCHAR2(256) := '{object_name}';
BEGIN
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

# ORA-01430 (column already exists), ORA-02260, ORA-02275.
ADD_COLUMN_TEMPLATE = """
PROMPT "-- {header}";
DECLARE
    in_table_name       CONSTANT VARCHAR2(256) := '{object_name}';
    in_column_name      CONSTANT VARCHAR2(256) := '{cc_name}';
    --
    v_found CHAR;
BEGIN
    SELECT MAX('Y') INTO v_found
    FROM user_tab_columns
    WHERE table_name    = in_table_name
        AND column_name = in_column_name;
    --
    IF v_found IS NULL THEN
        EXECUTE IMMEDIATE
            '{statement}';
    END IF;
END;
/
""".lstrip()

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

# ORA-02260 (one primary key), ORA-02261, ORA-02264, ORA-02275.
ADD_CONSTRAINT_TEMPLATE = """
PROMPT "-- {header}";
DECLARE
    in_table_name           CONSTANT VARCHAR2(256) := '{object_name}';
    in_constraint_name      CONSTANT VARCHAR2(256) := '{cc_name}';
    --
    v_found CHAR;
BEGIN
    SELECT MAX('Y') INTO v_found
    FROM user_constraints
    WHERE table_name        = in_table_name
        AND constraint_name = in_constraint_name;
    --
    IF v_found IS NULL THEN
        EXECUTE IMMEDIATE
            '{statement}';
    END IF;
END;
/
""".lstrip()

# ORA-02443 (nonexistent constraint), ORA-23292.
DROP_CONSTRAINT_TEMPLATE = """
PROMPT "-- {header}";
DECLARE
    in_table_name           CONSTANT VARCHAR2(256) := '{object_name}';
    in_constraint_name      CONSTANT VARCHAR2(256) := '{cc_name}';
    --
    v_found CHAR;
BEGIN
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
