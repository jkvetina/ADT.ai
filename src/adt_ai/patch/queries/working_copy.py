"""The SQL behind `patch -deploy -app 0`'s working copy (ADT #1069)."""

from __future__ import annotations

# The copy, found by the application it copies and the name the deploy gave
# it, so a redeploy of the same patch lands on the copy its first run made.
# APEX 26.2+ only: `main_application_id` and `working_copy_name` are not
# columns below it.
APEX_WORKING_COPY_QUERY = """
SELECT application_id AS app_id, alias AS app_alias
FROM   apex_applications
WHERE  main_application_id = :app_id
AND    working_copy_name   = :name
ORDER  BY application_id
FETCH  FIRST 1 ROW ONLY
""".strip()

# Measured on APEX 26.2.0 (`tests/tools/working_copy_probe.py`, 2026-10-09):
# the function takes the application, a required name and an optional
# description, returns the new id and takes none, so APEX picks it (`101` for
# app 100, alias `ORDERS101`). It runs as the parsing schema once a workspace is
# set, like `APEX_LOCK_APPLICATION_BLOCK`, and the id is read back through
# `APEX_WORKING_COPY_QUERY` rather than an OUT bind.
APEX_CREATE_WORKING_COPY_BLOCK = """
DECLARE
    l_app_id NUMBER;
BEGIN
    BEGIN
        APEX_SESSION.DETACH;
    EXCEPTION WHEN OTHERS THEN
        NULL;
    END;
    APEX_UTIL.SET_WORKSPACE (
        p_workspace => '{workspace}'
    );
    l_app_id := APEX_APPLICATION_ADMIN.CREATE_WORKING_COPY (
        p_application_id           => {app_id},
        p_working_copy_name        => '{name}',
        p_working_copy_description => '{description}'
    );
    COMMIT;
END;
""".strip()

# The working copies a `-deploy -app 0` made, which `-drop` may remove: the
# description the create block writes is the fingerprint, the way the derived
# alias is for a derived id. Dropping the main application leaves its copies
# standing (measured, `working_copy_probe.py --cascade`), so nothing else
# removes them. APEX 26.2+ only, like the query above.
APEX_DEPLOYED_WORKING_COPIES_QUERY = """
SELECT application_id AS app_id, main_application_id AS main_id
FROM   apex_applications
WHERE  is_working_copy          = 'Yes'
AND    working_copy_description = :description
""".strip()
