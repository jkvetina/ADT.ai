"""SQL for the Oracle table diff `patch -create` generates ALTERs from (ADT #753).

Old ADT asked the database what changed between two versions of a table
(`ADT--OLD/lib/queries_patch.py:218`) and ADT.ai replaced it with a Python
parser over the two `CREATE TABLE` texts. The parser only ever covered columns,
so a PK, UNIQUE, FK, CHECK or index change generated nothing at all, and its
comparison was string equality of a definition rather than a datatype
comparison. Jan, settling `#753`: *"I want the Oracle DIFF"*.

**Why the multi-step API and not `COMPARE_ALTER`.** `DBMS_METADATA_DIFF` offers
a one-call `COMPARE_ALTER(object_type, name1, name2)` that reads both objects
out of the dictionary itself. It cannot be used here: the two versions are two
tables in ONE schema, so they must carry different names, and every constraint
on them must too. `COMPARE_ALTER` would then report each of those spellings as a
rename and bury the real change. The multi-step form is what makes the suffix
removable -- `GET_SXML` hands back a document, `REPLACE` takes the marker back
out of it, and the two documents Oracle diffs are the same object under the same
name. That is old ADT's reason for the shape and it still holds.
"""

from __future__ import annotations

#: Physical storage is not a schema change. Old ADT suppressed exactly these
#: five before reading the SXML (`ADT--OLD/lib/queries_patch.py:231-235`), and
#: without them a table whose tablespace or segment attributes differ between
#: two shadow copies answers with an ALTER nobody asked for.
SUPPRESSED_TRANSFORMS = (
    "PARTITIONING",
    "PHYSICAL_PROPERTIES",
    "SEGMENT_ATTRIBUTES",
    "STORAGE",
    "TABLESPACE",
)

#: Bound one parameter at a time rather than five statements inline, so the list
#: above is the single place a transform is added or removed.
SET_TRANSFORM_PARAM_BLOCK = (
    "BEGIN DBMS_METADATA.SET_TRANSFORM_PARAM("
    "DBMS_METADATA.SESSION_TRANSFORM, :parameter, FALSE); END;"
)

#: The comparison itself. `GET_SXML` renders each shadow table as Simple XML,
#: `REPLACE` removes the `$1` / `$2` marker so both documents name one object,
#: `DBMS_METADATA_DIFF` produces the ALTERXML, and the `ALTERXML` + `ALTERDDL`
#: transforms render it as the DDL that gets written into the patch.
#:
#: `:source_marker` and `:target_marker` are bound rather than spelled `'$1'` /
#: `'$2'`, because the marker is `table_diff.shadow_table_names`' answer and a
#: second copy of it here is a second place for it to drift.
TABLE_DIFF_BLOCK = """
DECLARE
    in_source_table CONSTANT VARCHAR2(128) := :source_table;
    in_target_table CONSTANT VARCHAR2(128) := :target_table;
    in_source_mark  CONSTANT VARCHAR2(8)   := :source_marker;
    in_target_mark  CONSTANT VARCHAR2(8)   := :target_marker;
    --
    v_xml_source    CLOB;
    v_xml_target    CLOB;
    v_alter_xml     CLOB;
    v_diff          CLOB;
    v_handler       NUMBER;
    v_transform     NUMBER;
BEGIN
    -- Both sides render under the same suppressed transforms, so a difference
    -- in physical storage cannot reach the comparison at all.
    v_xml_source    := DBMS_METADATA.GET_SXML('TABLE', in_source_table);
    v_xml_target    := DBMS_METADATA.GET_SXML('TABLE', in_target_table);
    v_xml_source    := REPLACE(v_xml_source, in_source_mark, '');
    v_xml_target    := REPLACE(v_xml_target, in_target_mark, '');
    --
    v_handler       := DBMS_METADATA_DIFF.OPENC('TABLE');
    DBMS_METADATA_DIFF.ADD_DOCUMENT(handle => v_handler, document => v_xml_source);
    DBMS_METADATA_DIFF.ADD_DOCUMENT(handle => v_handler, document => v_xml_target);
    v_alter_xml     := DBMS_METADATA_DIFF.FETCH_CLOB(v_handler);
    DBMS_METADATA_DIFF.CLOSE(v_handler);
    --
    v_handler       := DBMS_METADATA.OPENW('TABLE');
    v_transform     := DBMS_METADATA.ADD_TRANSFORM(v_handler, 'ALTERXML');
    v_transform     := DBMS_METADATA.ADD_TRANSFORM(v_handler, 'ALTERDDL');
    --
    DBMS_LOB.CREATETEMPORARY(v_diff, TRUE);
    DBMS_METADATA.CONVERT(v_handler, v_alter_xml, v_diff);
    DBMS_METADATA.CLOSE(v_handler);
    --
    :result := v_diff;
END;
""".strip()

#: The one-call form, kept for `tests/tools/table_diff_probe.py` to measure the
#: two against each other on a live database. Never used by the generator, for
#: the reason in the module docstring.
COMPARE_ALTER_QUERY = (
    "SELECT DBMS_METADATA_DIFF.COMPARE_ALTER('TABLE', :source, :target) AS diff FROM dual"
)

#: `PURGE` for the reason `shared/queries/diff_tables.py` gives: a plain drop
#: leaves `BIN$...` objects `export_db` then has to filter. `CASCADE
#: CONSTRAINTS` because a shadow table carries the original's foreign keys.
DROP_SHADOW_TABLE_STATEMENT = "DROP TABLE {table_name} CASCADE CONSTRAINTS PURGE"
