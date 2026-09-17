SET SERVEROUTPUT OFF

-- install or upgrade supporting objects during the import
/*
BEGIN
    APEX_APPLICATION_INSTALL.SET_AUTO_INSTALL_SUP_OBJ(p_auto_install_sup_obj => TRUE);
END;
/
*/
