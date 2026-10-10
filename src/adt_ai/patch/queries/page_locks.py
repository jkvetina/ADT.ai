"""The App Builder's page locks, taken by a deploy below APEX 26.2 (ADT #1059).

`WWV_FLOW_PROPERTY_DEV.LOCK_PAGE` / `UNLOCK_PAGE` are what Page Designer's own
lock button calls. They are undocumented and carry no grant, so a target uses
them only once a DBA has run `GRANT EXECUTE ON <APEX schema>.WWV_FLOW_PROPERTY_DEV
TO <parsing schema>`. Measured on APEX 26.1.0 (2026-10-08):

- the lock is held by `APP_USER`, whoever that is; any name is accepted and the
  match is case-sensitive, so `adt728` and `ADT728` are two holders
- each call answers JSON through the HTP buffer and never raises, which is why it
  needs an OWA context and why the block reads the lock back instead: a page
  another developer holds answers `{"owner": ...}` and is left alone, and an
  unlock can answer `OK` over a row it did not remove
- `APP_USER` left on the connection moves the `CHECKSUM-SH256` the drift gate
  reads, so both blocks hand the session its own user back, on error too
- an import deletes every page lock of the application it lands on
"""

from __future__ import annotations

# The APEX schema whose `WWV_FLOW_PROPERTY_DEV` this schema may execute, through
# the current release's own `APEX_UTIL` synonym so an older APEX schema left
# behind by an upgrade is never the one called. No row is no grant.
APEX_PAGE_LOCK_OWNER_QUERY = """
SELECT o.owner
FROM   all_objects o
JOIN   all_synonyms s
  ON   s.table_owner  = o.owner
WHERE  s.owner        = 'PUBLIC'
AND    s.synonym_name = 'APEX_UTIL'
AND    o.object_name  = 'WWV_FLOW_PROPERTY_DEV'
AND    o.object_type  = 'PACKAGE'
""".strip()

# Locks every page nobody holds, as `{user}`, and answers one `<page>:<state>`
# line per page it tried: `HELD`, `OTHER` (somebody took it first) or `FAILED`.
# A page already locked, by anyone, is never touched, so a developer's own lock
# keeps its comment whatever happens to this deploy.
APEX_LOCK_PAGES_BLOCK = """
DECLARE
    l_names     OWA.VC_ARR;
    l_prior     VARCHAR2(255);
    l_by        VARCHAR2(255);
    l_out       VARCHAR2(32767);
BEGIN
    BEGIN
        APEX_SESSION.DETACH;
    EXCEPTION WHEN OTHERS THEN
        NULL;
    END;
    APEX_UTIL.SET_WORKSPACE (
        p_workspace => '{workspace}'
    );
    l_prior := V('APP_USER');
    APEX_CUSTOM_AUTH.SET_USER (
        p_user => '{user}'
    );
    OWA.INIT_CGI_ENV(0, l_names, l_names);
    FOR p IN (
        SELECT g.page_id
        FROM   apex_application_pages g
        WHERE  g.application_id = {app_id}
        AND    NOT EXISTS (
                   SELECT 1
                   FROM   apex_application_locked_pages k
                   WHERE  k.application_id = g.application_id
                   AND    k.page_id        = g.page_id
               )
        ORDER  BY g.page_id
    ) LOOP
        HTP.INIT;
        {owner}.WWV_FLOW_PROPERTY_DEV.LOCK_PAGE (
            p_application_id    => {app_id},
            p_page_id           => p.page_id,
            p_comment           => '{comment}'
        );
        SELECT MAX(k.locked_by) INTO l_by
        FROM   apex_application_locked_pages k
        WHERE  k.application_id = {app_id}
        AND    k.page_id        = p.page_id;
        l_out := l_out || p.page_id || ':'
            || CASE WHEN l_by = '{user}'   THEN 'HELD'
                    WHEN l_by IS NOT NULL  THEN 'OTHER'
                    ELSE 'FAILED' END
            || CHR(10);
    END LOOP;
    HTP.INIT;
    APEX_CUSTOM_AUTH.SET_USER (p_user => l_prior);
    COMMIT;
    :result := l_out;
EXCEPTION WHEN OTHERS THEN
    APEX_CUSTOM_AUTH.SET_USER (p_user => l_prior);
    RAISE;
END;
""".strip()

# Unlocks exactly the deploy's own locks, `{user}` with the deploy's comment, so
# a page the developer held before the deploy is never released by it. Answers
# how many of them are still there.
APEX_UNLOCK_PAGES_BLOCK = """
DECLARE
    l_names     OWA.VC_ARR;
    l_prior     VARCHAR2(255);
    l_left      PLS_INTEGER;
BEGIN
    BEGIN
        APEX_SESSION.DETACH;
    EXCEPTION WHEN OTHERS THEN
        NULL;
    END;
    APEX_UTIL.SET_WORKSPACE (
        p_workspace => '{workspace}'
    );
    l_prior := V('APP_USER');
    APEX_CUSTOM_AUTH.SET_USER (
        p_user => '{user}'
    );
    OWA.INIT_CGI_ENV(0, l_names, l_names);
    FOR p IN (
        SELECT k.page_id
        FROM   apex_application_locked_pages k
        WHERE  k.application_id = {app_id}
        AND    k.locked_by      = '{user}'
        AND    k.lock_comment = '{comment}'
    ) LOOP
        HTP.INIT;
        {owner}.WWV_FLOW_PROPERTY_DEV.UNLOCK_PAGE (
            p_application_id    => {app_id},
            p_page_id           => p.page_id
        );
    END LOOP;
    HTP.INIT;
    SELECT COUNT(*) INTO l_left
    FROM   apex_application_locked_pages k
    WHERE  k.application_id = {app_id}
    AND    k.locked_by      = '{user}'
    AND    k.lock_comment = '{comment}';
    APEX_CUSTOM_AUTH.SET_USER (p_user => l_prior);
    COMMIT;
    :result := TO_CHAR(l_left);
EXCEPTION WHEN OTHERS THEN
    APEX_CUSTOM_AUTH.SET_USER (p_user => l_prior);
    RAISE;
END;
""".strip()
