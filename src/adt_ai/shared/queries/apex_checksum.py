from __future__ import annotations

# An application's APEX checksum, read by `export_apex` when it records one and
# by `patch` when it compares the live application against that record. ONE
# block for both (ADT #962): on PLAYGROUND (APEX 26.1.4, app 122, 2026-09-25)
# the export stored `SH256:b400FZS7...` while this block answered
# `SH256:It5s52FW...` for the same untouched application, so a drift check
# comparing one read with the other could never pass. The cause was the APEX
# session the export had open when it read (below), not its collection.
#
# One call to `APEX_EXPORT.GET_APPLICATION` (ADT #592), never the collection
# round trip the export formats use: that helper exists because an export needs
# the file CONTENTS of several members, and a checksum is one short scalar in
# one member, so the collection and the second query buy nothing.
#
# `CHECKSUM-SH256` is documented as "independent of IDs and can be compared
# across instances and workspaces", which is what lets a sandbox import under a
# different application id be compared against the application it came from.
#
# A PL/SQL block read through `fetch_clob`, not a SELECT over the pipelined
# function (ADT #960). Measured on APEX 24.2, Jan's DEV, 2026-09-25: the SELECT
# form raised `ORA-14552: cannot perform a DDL, commit or rollback inside a query
# or DML` out of `WWV_FLOW_SECURITY` / `WWV_FLOW_EXPORT_API`, because APEX
# commits while it sets up its security context and a query may not commit. The
# SELECT had answered on APEX 26.1.0 (SANDBOX, 2026-08-30), which is why it
# shipped. The block answered `SH256:<base64>` on DEV for app 122.
#
# An application id nothing is installed on still RAISES from the block
# (`ORA-20987: APEX - Application 999998 not found ...`, DEV, 2026-09-25) rather
# than answering empty, as it did from the SELECT on SANDBOX.
#
# **It reads with no APEX session open, which is the guarded DETACH first.** An
# open APEX session moves `CHECKSUM-SH256`. Measured on PLAYGROUND (APEX 26.1.4,
# app 122, 2026-09-25), each case on a fresh connection:
#
#   this block, bare                                   SH256:It5s52FW...
#   `EXPORT_START_QUERY`, then the block               SH256:b400FZS7...
#   SET_WORKSPACE + SET_SECURITY_GROUP_ID, then block  SH256:It5s52FW...
#   the block with the guarded DETACH first            SH256:It5s52FW...
#   `EXPORT_START_QUERY`, then block with DETACH first SH256:It5s52FW...
#
# So it is the session `EXPORT_START_QUERY`'s `APEX_SESSION.CREATE_SESSION`
# opens, not the workspace, and every export recorded `b400...` while `patch`
# read `It5s...`. SANDBOX answers the same either way, which is why no story saw
# it. `patch/queries/objects.APEX_SET_BUILD_STATUS_BLOCK` detaches for a sibling
# reason: `run_component_scan`'s `EXPORT_START_QUERY` leaves a session on the
# connection. `DETACH` with no session open is a no-op (measured there), and it
# is guarded the same way: a detach that fails has changed nothing worth losing
# the read over. A caller that still needs its session reads before opening one.
APEX_CHECKSUM_BLOCK = """
DECLARE
    l_files apex_t_export_files;
BEGIN
    BEGIN APEX_SESSION.DETACH; EXCEPTION WHEN OTHERS THEN NULL; END;
    l_files := APEX_EXPORT.GET_APPLICATION(
        p_application_id => :app_id,
        p_type           => 'CHECKSUM-SH256');
    :result := l_files(1).contents;
END;
""".strip()
