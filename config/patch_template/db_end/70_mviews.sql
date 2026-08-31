DECLARE
    v_start_at      DATE;
    v_count         PLS_INTEGER;
BEGIN
    DBMS_OUTPUT.PUT_LINE('--');
    DBMS_OUTPUT.PUT_LINE('-- REFRESHING MATERIALIZED VIEWS');
    DBMS_OUTPUT.PUT_LINE('--');
    --
    FOR c IN (
        SELECT
            m.mview_name,
            TO_CHAR(m.last_refresh_date, 'YYYY-MM-DD HH24:MI') AS last_refresh_date,
            m.compile_state
        FROM user_mviews m
        WHERE m.mview_name LIKE '%' ESCAPE '\'
        ORDER BY 1
    ) LOOP
        DBMS_OUTPUT.PUT('--   ' || RPAD(c.mview_name || ' ', 40, '.') || ' ' || c.last_refresh_date || ' ' || c.compile_state);
        --
        v_start_at := SYSDATE;
        --
        DBMS_MVIEW.REFRESH (
            list            => c.mview_name,
            method          => 'C',
            atomic_refresh  => FALSE
        );
        --
        EXECUTE IMMEDIATE
            'SELECT COUNT(*) FROM ' || c.mview_name
            INTO v_count;
        --
        DBMS_OUTPUT.PUT_LINE(' -> ' || CEIL((SYSDATE - v_start_at) * 86400) || 's, ' || v_count || ' rows');
    END LOOP;
    --
    DBMS_OUTPUT.PUT_LINE('--');
END;
/



BEGIN
    DBMS_STATS.GATHER_SCHEMA_STATS(USER);
END;
/
