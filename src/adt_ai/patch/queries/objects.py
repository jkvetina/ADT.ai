from __future__ import annotations

INVALID_OBJECTS_QUERY = "SELECT object_type, object_name FROM user_objects WHERE status = 'INVALID'"

# The signature `-app` reads off a live application before it imports anything
# over it (ADT #592). One SELECT over the pipelined function, never
# `export_apex`'s collection round trip: that helper exists because an export
# needs the file CONTENTS of several members, and a checksum is one short scalar
# in one member, so the collection, the COMMIT and the second query buy nothing.
#
# `CHECKSUM-SH256` is documented as "independent of IDs and can be compared
# across instances and workspaces", which is what lets a sandbox import under a
# different application id be compared against the application it came from.
#
# Measured against APEX 26.1.0 on SANDBOX, 2026-08-30: it answers
# `SH256:<base64>` from a plain connection with no workspace security context
# set up first, and an application id nothing is installed on RAISES
# (`ORA-20987 ... not found`) rather than answering no rows.
APEX_CHECKSUM_QUERY = """
SELECT DBMS_LOB.SUBSTR(contents, 4000, 1) AS checksum
FROM   TABLE(APEX_EXPORT.GET_APPLICATION(
           p_application_id => :app_id,
           p_type           => 'CHECKSUM-SH256'))
""".strip()

# What `-drop` reads before it removes anything (ADT #592). Two facts per
# application, and both halves of the rail rest on them: the alias, because a
# derived sandbox carries `<SOURCE_ALIAS>_<task>` and nothing else proves an id
# was minted by a `-app` deploy, and the workspace, because an alias is unique
# per workspace, so a source in another one is not a source at all.
#
# Unfiltered by design: the rail needs every application the connected schema can
# see, since the SOURCE is what authorizes dropping the target and narrowing to
# the target's own row would leave nothing to authorize it with.
#
# `created_by` is the ownership check's half (ADT #639), read live off the
# target rather than off any store: Jan, 2026-09-01, *"verify the app owner
# (create by) on the fly from the target env and compare with user IDENTITY"*.
APEX_WORKSPACE_APPS_QUERY = """
SELECT
    a.application_id    AS app_id,
    a.alias             AS app_alias,
    a.owner,
    a.workspace,
    a.workspace_id,
    a.created_by
FROM apex_applications a
ORDER BY
    a.application_id
""".strip()

# The two values `wwv_flow_imp.import_begin` is given, read live rather than
# carried in a constant (ADT #592). Measured on SANDBOX, 2026-08-30:
# `api_compatibility` answers `2026.03.30` and `version_no` `26.1.0`, which is
# byte for byte what the shipped `sandbox/apex/100_ORDERS/f100.sql` export passes
# to `import_begin`. Captured rather than remembered, the rule `#473` was filed
# on: a value this run matches against another tool's output is read from the
# tool.
APEX_RELEASE_QUERY = """
SELECT
    r.version_no,
    r.api_compatibility
FROM apex_release r
""".strip()

# Jan's transport, 2026-08-30: *"Test this approach from legacy .sql full app
# file, they are dropping the app before the import start."* Every legacy full
# export opens on `import_begin` and then `remove_flow`, and it runs as the
# PARSING SCHEMA, `import_begin` having set the workspace context, so no
# instance-admin grant is involved anywhere. SQLcl's own `apex` command has no
# drop verb at all (measured 2026-08-29), which is why the transport is this
# block rather than a flag.
#
# `p_default_application_id` is the id being REMOVED. `remove_flow` reads
# `wwv_flow.g_flow_id`, which is what `import_begin` just set from it, so the
# block is the whole of the addressing and there is no second place a wrong id
# could enter.
APEX_DROP_BLOCK = """
BEGIN
    wwv_flow_imp.import_begin (
        p_version_yyyy_mm_dd     => '{version}'
       ,p_release                => '{release}'
       ,p_default_workspace_id   => {workspace_id}
       ,p_default_application_id => {app_id}
       ,p_default_id_offset      => 0
       ,p_default_owner          => '{owner}'
    );
    wwv_flow_imp.remove_flow(wwv_flow.g_flow_id);
    wwv_flow_imp.import_end(
        p_auto_install_sup_obj => NVL(wwv_flow_application_install.get_auto_install_sup_obj, FALSE)
    );
    COMMIT;
END;
/
""".strip()

# `DIFF_TABLES_QUERY` lived here until ADT #356 moved the sweep to
# `shared/queries/diff_tables.py`. Three commands run it now, the `diff`
# producer, `export_db`, and the connecting `patch` run, so it belongs where all
# three read the one copy rather than in the module of the flag that used to own
# it. `DROP_TABLE_STATEMENT` went with it: the comment left here claimed the
# generated helpers still used it, and they never did, `helpers.py` builds its
# `DROP <type> <name>` inline off the parsed identity (ADT #554).

VIEW_COLUMNS_QUERY = """
SELECT
    c.column_name,
    c.column_id
FROM user_tab_cols c
WHERE c.table_name = :view_name
""".strip()

ALTER_TABLE_ADD_STATEMENT         = "ALTER TABLE {table_name} ADD {definition};"
ALTER_TABLE_MODIFY_STATEMENT      = "ALTER TABLE {table_name} MODIFY {definition};"
ALTER_TABLE_DROP_COLUMN_STATEMENT = "ALTER TABLE {table_name} DROP COLUMN {column};"

SQLERROR_CONTINUE_DIRECTIVE      = "WHENEVER SQLERROR CONTINUE;"
SQLERROR_EXIT_ROLLBACK_DIRECTIVE = "WHENEVER SQLERROR EXIT ROLLBACK;"

# Old ADT spooled quoted, with APPEND, from a `./`-anchored path (patch.py:1471)
# and then moved the file under `logs_<ENV>/` once the run finished
# (patch.py:577-586). ADT.ai spools straight into that folder instead: the
# script is generated per target, so the destination is known at create time and
# a hand-run in SQLcl lands where an `adtai patch -deploy` does (ADT #260).
SPOOL_START_DIRECTIVE            = 'SPOOL "./{folder}/{schema}.log" APPEND;'
SPOOL_OFF_DIRECTIVE              = "SPOOL OFF;"

# `patch_rollback` in old ADT (patch.py:153, emitted at patch.py:1466-1467). The
# generated script carries the safe default; `-deploy -continue` strips these two
# lines back out so the deploy-time directive is the one that stands.
WHENEVER_DIRECTIVES              = (
    "WHENEVER OSERROR  EXIT ROLLBACK;",
    "WHENEVER SQLERROR EXIT ROLLBACK;",
)
WHENEVER_PREFIX                  = "WHENEVER "

# Old ADT set these on every generated install script before anything else ran
# (patch.py:1461-1469). DEFINE OFF is the load-bearing one: SQLcl reads `&` as a
# substitution prompt, so a package body holding a literal '&APP_ID.' stops a
# terminal-less deploy dead with `Substitution cancelled` (ADT #254).
# They are emitted before the db_init/apex_init templates precisely so a
# project's own template can still override any of them.
SESSION_DEFAULT_DIRECTIVES       = ("SET DEFINE OFF", "SET TIMING OFF", "SET SQLBLANKLINES ON")

DROP_HELPER_TEMPLATE = """
PROMPT "-- {statement}";
DECLARE
    in_object_type CONSTANT VARCHAR2(256) := '{object_type}';
    in_object_name CONSTANT VARCHAR2(256) := '{object_name}';
BEGIN
    FOR c IN (
        SELECT object_type, object_name
        FROM user_objects
        WHERE object_type = in_object_type
            AND object_name = in_object_name
    ) LOOP
        EXECUTE IMMEDIATE '{statement}';
    END LOOP;
END;
/
""".lstrip()

# ADT emits the APEX environment itself, with the workspace resolved from the
# cached `config/internal/apex_apps.yaml` (ADT #298). These are the same two APEX_UTIL /
# APEX_APPLICATION_INSTALL calls the shipped scaffold's `apex_init/00_init.sql`
# carried; what changed is where the value comes from, a hand-edited
# `<APEX_WORKSPACE>` placeholder before, `export_apex`'s own metadata now.
#
# It has to be emitted rather than substituted into the template: since ADT #288
# a slot file is LINKED where it lives, so there is no per-patch copy to
# substitute into. Old ADT could only substitute because `create_file_snapshot`
# (patch.py:1948) wrote a copy first and resolved tokens into that copy.
#
# Emitted BEFORE the apex_init templates, for the same reason the session
# defaults are: a project's own template can then override any of it.
APEX_ENVIRONMENT_BLOCK = """
PROMPT --;
PROMPT -- APEX ENVIRONMENT
PROMPT --;
BEGIN
    APEX_UTIL.SET_WORKSPACE (
        p_workspace => '{workspace}'
    );

    -- keep sessions alive
    APEX_APPLICATION_INSTALL.SET_KEEP_SESSIONS(p_keep_sessions => TRUE);
    COMMIT;
END;
/
""".strip()

# Old ADT spelled the production lock as a `.[PROD].`-tagged template carrying
# `{$APEX_APP_ID}`. ADT knows the app id, so the project names the status per
# environment (`patch_apex_build_status`) and ADT emits the call resolved. The
# application VERSION old ADT stamped alongside it is deliberately dropped
# (Jan, 2026-08-12). Emitted last, locking the app is a terminal action.
APEX_BUILD_STATUS_BLOCK = """
PROMPT --;
PROMPT -- APEX BUILD STATUS
PROMPT --;
BEGIN
    APEX_UTIL.SET_APP_BUILD_STATUS (
        p_application_id    => {app_id},
        p_build_status      => '{build_status}'
    );
    COMMIT;
END;
/
""".strip()

APEX_MODE_REPLACE_BLOCK = "BEGIN wwv_flow_imp.g_mode := 'REPLACE'; END;\n/"

APEX_REMOVE_PAGE_STATEMENT = (
    "    wwv_flow_imp_page.remove_page(p_flow_id => wwv_flow.g_flow_id, p_page_id => {page_id});"
)

APEX_STATIC_FILE_HEADER = "wwv_flow_imp.g_varchar2_table := wwv_flow_imp.empty_varchar2_table;"
APEX_STATIC_FILE_ROW    = "wwv_flow_imp.g_varchar2_table({index}) := '{row}';"
APEX_STATIC_FILE_FOOTER = """
wwv_flow_imp_shared.create_app_static_file(
 p_id=>wwv_flow_id.next_val
,p_file_name=>'{file_name}'
,p_mime_type=>'{mime_type}'
,p_file_charset=>'utf-8'
,p_file_content => wwv_flow_imp.varchar2_to_blob(wwv_flow_imp.g_varchar2_table)
);
wwv_flow_imp.component_end;
end;
/
""".strip()
