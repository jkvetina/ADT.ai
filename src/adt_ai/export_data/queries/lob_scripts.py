from __future__ import annotations

# The two PL/SQL blocks a LOB update script is made of, one per LOB value that
# is too long for its MERGE (export_data/lob_update_scripts.py). They are the
# output a user replays, not SQL ADT.ai runs, and they live in the module's SQL
# home all the same so no block hides outside it (ADT #923).
#
# Formatted with `str.format`: `writes` is the chunked `WRITEAPPEND` body, and
# `table`, `column`, `value` and `where` arrive already quoted and escaped. No
# other brace may appear in either template.
BLOB_UPDATE_BLOCK = """DECLARE
    v_blob  BLOB;
    v_raw   RAW(32767);
BEGIN
    DBMS_LOB.CREATETEMPORARY(v_blob, TRUE);
{writes}
    --
    UPDATE {table}
    SET {column} = v_blob
    WHERE {where};
    --
    DBMS_LOB.FREETEMPORARY(v_blob);
END;
/
"""

CLOB_UPDATE_BLOCK = """DECLARE
    v_clob  CLOB;
    v_raw   RAW(32767);
    v_text  VARCHAR2(32767);
BEGIN
    DBMS_LOB.CREATETEMPORARY(v_clob, TRUE);
{writes}
    --
    UPDATE {table}
    SET {value}
    WHERE {where};
    --
    DBMS_LOB.FREETEMPORARY(v_clob);
END;
/
"""
