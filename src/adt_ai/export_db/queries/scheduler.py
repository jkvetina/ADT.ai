"""Scheduler reads for `export_db`: the job listing, job arguments, PROCOBJ DDL.

Split out of `objects.py` by `#923`, when the job signature grew to cover every
column a job file can carry. It is a topic of its own: a scheduler object is the
one exported kind the dictionary cannot date, and the one `DBMS_METADATA` reaches
only through the shared `PROCOBJ` token.
"""

from __future__ import annotations

# A scheduler job carries no change timestamp anywhere in the dictionary, so the
# window that narrows every other type cannot narrow this one. `user_objects` does
# hold a JOB row, and its LAST_DDL_TIME is the last RUN rather than the last edit:
# measured on the local container 2026-08-20 with a dummy job, a scheduler run carrying no
# DDL at all moved LAST_DDL_TIME from 11:26:31 to 11:27:31 while CREATED stayed put,
# and an in-place SET_ATTRIBUTE later moved LAST_DDL_TIME again and left CREATED
# alone. CREATED is therefore reliable but only sees a create or a drop+create, and
# LAST_DDL_TIME sees everything and fires for every enabled job on every run.
#
# So the change signal is built rather than found: SIGNATURE hashes every column
# `object_normalizers/job.py` and `content.py` can render into the exported file,
# and a windowed run exports the jobs whose signature moved. That is each
# CREATE_JOB argument, each attribute PROCOBJ writes as a trailing SET_ATTRIBUTE
# once it is off its default (JOB_PRIORITY is the one measured), and the argument
# values the file sets with SET_JOB_ARGUMENT_VALUE, from `user_scheduler_job_args`.
# The last two were missing until `#923`, so a priority or argument change left the
# hash where it was and every `-recent` run skipped the job. Left out on purpose:
# what a run moves (STATE, the run and failure counts, LAST_START_DATE,
# NEXT_RUN_DATE), START_DATE, which the file always rewrites as SYSDATE, and
# NLS_ENV, which it drops. Every column read here is in the 12.1 view too, the
# oldest release STANDARD_HASH runs on.
#
# It is computed IN the database so a 4000-char JOB_ACTION never crosses the wire,
# which is what keeps a 2000-job schema to one fetch of name plus 32 bytes instead of
# 2000 DBMS_METADATA round trips. `||` is capped at 4000 bytes as well, so every
# column the view declares 4000 wide is hashed on its own first (a long JOB_ACTION
# beside a long REPEAT_INTERVAL would otherwise fail the whole listing with
# ORA-01489), and each argument is cut to 15 hex digits of its own hash before the
# per-job LISTAGG, which keeps the 255 arguments a job may declare under that cap.
# CHR(1) separates the fields so two different jobs cannot concatenate to one string,
# and every column takes NVL because `||` swallows a NULL silently and would collide.
# END_DATE takes an explicit format so the session's NLS settings cannot move it.
JOBS_QUERY = """
WITH job_arguments AS (
    SELECT a.job_name,
           RAWTOHEX(STANDARD_HASH(LISTAGG(SUBSTR(RAWTOHEX(STANDARD_HASH(
               TO_CHAR(a.argument_position)                         || CHR(1) ||
               NVL(a.argument_name, '~')                            || CHR(1) ||
               RAWTOHEX(STANDARD_HASH(NVL(a.value, '~'), 'SHA256'))
           , 'SHA256')), 1, 15)) WITHIN GROUP (ORDER BY a.argument_position), 'SHA256'))
               AS signature
    FROM user_scheduler_job_args a
    GROUP BY a.job_name
)
SELECT 'JOB' AS object_type, j.job_name AS object_name, j.schedule_type,
    RAWTOHEX(STANDARD_HASH(
        NVL(j.job_name, '~')                                            || CHR(1) ||
        NVL(j.job_style, '~')                                           || CHR(1) ||
        NVL(j.job_type, '~')                                            || CHR(1) ||
        RAWTOHEX(STANDARD_HASH(NVL(j.job_action, '~'), 'SHA256'))       || CHR(1) ||
        NVL(TO_CHAR(j.number_of_arguments), '~')                        || CHR(1) ||
        NVL(a.signature, '~')                                           || CHR(1) ||
        RAWTOHEX(STANDARD_HASH(NVL(j.program_owner, '~'), 'SHA256'))    || CHR(1) ||
        RAWTOHEX(STANDARD_HASH(NVL(j.program_name, '~'), 'SHA256'))     || CHR(1) ||
        RAWTOHEX(STANDARD_HASH(NVL(j.schedule_owner, '~'), 'SHA256'))   || CHR(1) ||
        RAWTOHEX(STANDARD_HASH(NVL(j.schedule_name, '~'), 'SHA256'))    || CHR(1) ||
        RAWTOHEX(STANDARD_HASH(NVL(j.repeat_interval, '~'), 'SHA256'))  || CHR(1) ||
        NVL(j.event_queue_owner, '~')                                   || CHR(1) ||
        NVL(j.event_queue_name, '~')                                    || CHR(1) ||
        NVL(j.event_queue_agent, '~')                                   || CHR(1) ||
        RAWTOHEX(STANDARD_HASH(NVL(j.event_condition, '~'), 'SHA256'))  || CHR(1) ||
        NVL(j.file_watcher_owner, '~')                                  || CHR(1) ||
        NVL(j.file_watcher_name, '~')                                   || CHR(1) ||
        NVL(TO_CHAR(j.end_date, 'YYYY-MM-DD HH24:MI:SS.FF TZR'), '~')   || CHR(1) ||
        NVL(j.job_class, '~')                                           || CHR(1) ||
        NVL(j.enabled, '~')                                             || CHR(1) ||
        NVL(j.auto_drop, '~')                                           || CHR(1) ||
        RAWTOHEX(STANDARD_HASH(NVL(j.comments, '~'), 'SHA256'))         || CHR(1) ||
        NVL(TO_CHAR(j.job_priority), '~')                               || CHR(1) ||
        NVL(j.logging_level, '~')                                       || CHR(1) ||
        NVL(TO_CHAR(j.max_runs), '~')                                   || CHR(1) ||
        NVL(TO_CHAR(j.max_failures), '~')                               || CHR(1) ||
        NVL(TO_CHAR(j.schedule_limit), '~')                             || CHR(1) ||
        NVL(TO_CHAR(j.max_run_duration), '~')                           || CHR(1) ||
        NVL(j.restartable, '~')                                         || CHR(1) ||
        NVL(j.restart_on_recovery, '~')                                 || CHR(1) ||
        NVL(j.restart_on_failure, '~')                                  || CHR(1) ||
        NVL(j.stop_on_window_close, '~')                                || CHR(1) ||
        NVL(j.instance_stickiness, '~')                                 || CHR(1) ||
        RAWTOHEX(STANDARD_HASH(NVL(j.raise_events, '~'), 'SHA256'))     || CHR(1) ||
        NVL(TO_CHAR(j.job_weight), '~')                                 || CHR(1) ||
        NVL(TO_CHAR(j.instance_id), '~')                                || CHR(1) ||
        NVL(j.store_output, '~')                                        || CHR(1) ||
        NVL(j.allow_runs_in_restricted_mode, '~')                       || CHR(1) ||
        NVL(j.credential_owner, '~')                                    || CHR(1) ||
        NVL(j.credential_name, '~')                                     || CHR(1) ||
        NVL(j.destination_owner, '~')                                   || CHR(1) ||
        NVL(j.destination, '~')                                         || CHR(1) ||
        NVL(j.connect_credential_owner, '~')                            || CHR(1) ||
        NVL(j.connect_credential_name, '~')
    , 'SHA256')) AS signature
FROM user_scheduler_jobs j
LEFT JOIN job_arguments a
    ON a.job_name = j.job_name
WHERE (:schema IS NOT NULL)
AND j.schedule_type != 'IMMEDIATE'
ORDER BY j.job_name
""".strip()

JOB_DDL_QUERY = """
SELECT DBMS_METADATA.GET_DDL('PROCOBJ', job_name) AS ddl
FROM user_scheduler_jobs
WHERE job_name = :object_name
""".strip()

# A SCHEDULE hits the same DBMS_METADATA limitation as a JOB above: GET_DDL
# rejects the literal 'SCHEDULE' object type with ORA-31600, because a
# scheduler SCHEDULE is stored as a procedural object and fetched through the
# shared 'PROCOBJ' token like every other DBMS_SCHEDULER object.
SCHEDULE_DDL_QUERY = """
SELECT DBMS_METADATA.GET_DDL('PROCOBJ', schedule_name) AS ddl
FROM user_scheduler_schedules
WHERE schedule_name = :object_name
""".strip()

JOB_ARGUMENTS_QUERY = """
SELECT argument_name, argument_position, argument_type, value
FROM user_scheduler_job_args
WHERE job_name = :job_name
ORDER BY argument_position
""".strip()
