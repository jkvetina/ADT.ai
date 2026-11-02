"""The probe that asks a masked APEX refusal again (ADT #1093).

Its own topic module because `objects.py` sits at the 24 KB context cap.
"""

from __future__ import annotations

# A masked refusal asked again, outside the APEX session (ADT #1093). The
# export opens that session as the application it exports, so the
# application's own error-handling function sees the refusal first, and the
# template the APEX docs ship rewrites it into "An unexpected internal
# application error has occurred". `{call}` is the format's own
# `GET_APPLICATION` statement, lifted out of its export block by
# `failures.refusal_probe`, so the probe cannot ask a different question.
#
# The session is left and rejoined in one block, so the slice after this one
# still finds the session and its collection where they were (measured on
# SANDBOX, 2026-10-28: the next `-split` slice wrote its 37 members). The
# workspace is set again because detaching takes it with the session, and for
# an `ords:` connection, whose every request is a session that never had one.
# Nothing here names the collection, which is what lets
# `one_request.OneRequestCollection` send it as a request of its own.
EXPORT_REFUSAL_PROBE = """
DECLARE
    l_files     apex_t_export_files;
    l_app_id    NUMBER := APEX_APPLICATION.G_FLOW_ID;
    l_session   NUMBER := APEX_APPLICATION.G_INSTANCE;
BEGIN
    IF l_session IS NOT NULL THEN
        APEX_SESSION.DETACH;
    END IF;
    FOR c IN (
        SELECT a.workspace
        FROM apex_applications a
        WHERE a.application_id = :app_id
    ) LOOP
        APEX_UTIL.SET_WORKSPACE (
            p_workspace => c.workspace
        );
    END LOOP;
    BEGIN
        {call}
    EXCEPTION
    WHEN OTHERS THEN
        IF l_session IS NOT NULL THEN
            APEX_SESSION.ATTACH (
                p_app_id        => l_app_id,
                p_page_id       => 0,
                p_session_id    => l_session
            );
        END IF;
        RAISE;
    END;
    IF l_session IS NOT NULL THEN
        APEX_SESSION.ATTACH (
            p_app_id        => l_app_id,
            p_page_id       => 0,
            p_session_id    => l_session
        );
    END IF;
END;
""".strip()
