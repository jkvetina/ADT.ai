BEGIN
    -- run all daily jobs
    FOR c IN (
        SELECT
            r.job_name
        FROM user_scheduler_jobs r
        WHERE r.job_style           = 'REGULAR'
            AND r.repeat_interval   LIKE 'FREQ=DAILY;%'
            AND r.enabled           = 'TRUE'
            AND r.state             = 'SCHEDULED'
            AND r.job_name          NOT LIKE '%LOGGER%'
        ORDER BY 1
    ) LOOP
        DBMS_OUTPUT.PUT_LINE('-- RUNNING JOB: ' || c.job_name);
        DBMS_SCHEDULER.RUN_JOB (
            job_name                => c.job_name,
            use_current_session     => FALSE
        );
    END LOOP;

    -- uncomment to wait for the jobs to finish before the deploy moves on.
    -- they are launched in their own sessions, so nothing above waits on them
    -- and the query below reports whatever their previous run left behind.
    -- the cost is a flat minute on every deploy whatever its size, so the wait
    -- is off here and each project decides for itself whether to pay it
    --DBMS_SESSION.SLEEP(60);
END;
/

-- check the job results
SELECT
    r.log_id,
    r.job_name,
    r.status,
    TO_CHAR(r.log_date, 'YYYY-MM-DD HH24:MI') AS log_date,
    LTRIM(REPLACE(REGEXP_REPLACE(r.run_duration, '^[^\s]\s+', ''), '+000', '')) AS run_duration,
    r.additional_info,
    r.output
FROM user_scheduler_job_run_details r
WHERE r.log_id IN (
    SELECT
        MAX(r.log_id) AS log_id
    FROM user_scheduler_job_run_details r
    WHERE r.log_date >= SYSDATE - 1/1      -- in past 1 hour
    GROUP BY
        r.job_name
)
ORDER BY
    r.job_name;
