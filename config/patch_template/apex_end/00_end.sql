-- switch the authentication scheme
/*
BEGIN
    FOR c IN (
        SELECT a.application_id
        FROM apex_applications a
        WHERE a.application_id IN (100)
    ) LOOP
        APEX_APPLICATION_ADMIN.SET_AUTHENTICATION_SCHEME (
            p_application_id    => c.application_id,
            p_name              => 'Application Express Accounts'
        );
    END LOOP;
    COMMIT;
END;
/
*/

-- set the application version
/*
BEGIN
    FOR c IN (
        SELECT a.application_id
        FROM apex_applications a
        WHERE a.application_id IN (100)
    ) LOOP
        APEX_APPLICATION_ADMIN.SET_APPLICATION_VERSION (
            p_application_id    => c.application_id,
            p_version           => TO_CHAR(SYSDATE, 'YYYY-MM-DD')
        );
    END LOOP;
    COMMIT;
END;
/
*/
