"""Row-level security reads: a schema's VPD policies and application contexts (`#1098`).

Measured on the live 26ai fixture (23.26.3, SANDBOX, 2026-10-30), connected as
the owner with no DBA view:

* `GET_DEPENDENT_DDL('RLS_POLICY', <table>)` answers for every table of the
  schema that carries a policy, one `BEGIN ... END;` line per policy, disabled
  policies included, so one call per table is the whole read.
* A context is a SYS-owned object. `all_context` lists only the namespaces set
  in the current session, and `GET_DDL`/`GET_XML` by name raise `ORA-31603` for
  any context another schema's package owns, so a name list from `all_objects`
  cannot be filtered in SQL. `DBMS_METADATA.OPEN('CONTEXT')` with no filter
  returns exactly the caller's own contexts, in 0.05s, which is why the read is
  a block with a CLOB OUT bind rather than a query.
"""

from __future__ import annotations

# One row per table or view of the schema with at least one policy on it.
VPD_POLICIES_QUERY = """
SELECT p.object_name,
       DBMS_METADATA.GET_DEPENDENT_DDL('RLS_POLICY', p.object_name) AS ddl
FROM   (SELECT DISTINCT object_name FROM user_policies) p
ORDER  BY p.object_name
""".strip()

#: Separates one context's statement from the next in the CLOB the block returns.
CONTEXT_SEPARATOR = "--#ADT#--"

CONTEXTS_BLOCK = f"""
DECLARE
    v_handle    NUMBER;
    v_transform NUMBER;
    v_ddl       CLOB;
    v_all       CLOB;
BEGIN
    v_handle    := DBMS_METADATA.OPEN('CONTEXT');
    v_transform := DBMS_METADATA.ADD_TRANSFORM(v_handle, 'DDL');
    LOOP
        v_ddl := DBMS_METADATA.FETCH_CLOB(v_handle);
        EXIT WHEN v_ddl IS NULL;
        v_all := v_all || v_ddl || CHR(10) || '{CONTEXT_SEPARATOR}' || CHR(10);
    END LOOP;
    DBMS_METADATA.CLOSE(v_handle);
    :result := v_all;
END;
""".strip()

__all__ = ["CONTEXTS_BLOCK", "CONTEXT_SEPARATOR", "VPD_POLICIES_QUERY"]
