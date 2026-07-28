from __future__ import annotations

APPLICATIONS_QUERY = """
SELECT
    a.owner,
    a.workspace,
    a.workspace_id,
    a.application_group     AS app_group,
    a.application_id        AS app_id,
    a.alias                 AS app_alias,
    a.application_name      AS app_name,
    a.pages,
    TO_CHAR(a.last_updated_on, 'YYYY-MM-DD HH24:MI') AS updated_at
FROM apex_applications a
WHERE 1 = 1
    AND a.owner                 = :owner
    AND (a.workspace            = :workspace    OR :workspace IS NULL)
    AND (a.application_group    = :group_id     OR :group_id IS NULL)
    AND ('|' || :app_id || '|' LIKE '%|' || a.application_id || '|%' OR :app_id IS NULL)
    AND (a.application_id < :max_app_id OR :max_app_id IS NULL)
    AND (:recent IS NULL OR a.last_updated_on >= TRUNC(SYSDATE) + 1 - :recent)
ORDER BY
    a.application_id
""".strip()

APPLICATION_OWNER_QUERY = """
SELECT
    a.owner
FROM apex_applications a
WHERE a.application_id = :app_id
""".strip()

OWNER_APP_COUNTS_QUERY = """
SELECT
    t.owner,
    COUNT(*)    AS app_count
FROM apex_applications t
WHERE t.is_working_copy = 'No'
    AND ('|' || :owners || '|' LIKE '%|' || t.owner || '|%' OR :owners IS NULL)
    AND (t.application_id < :max_app_id OR :max_app_id IS NULL)
GROUP BY t.owner
ORDER BY t.owner
""".strip()

WORKSPACES_QUERY = """
SELECT
    t.workspace,
    t.workspace_id,
    (
        SELECT COUNT(DISTINCT a.owner)
        FROM apex_applications a
        WHERE a.workspace = t.workspace
            AND (a.application_id < :max_app_id OR :max_app_id IS NULL)
    )                   AS owners,
    (
        SELECT COUNT(*)
        FROM apex_applications a
        WHERE a.workspace = t.workspace
            AND (a.application_id < :max_app_id OR :max_app_id IS NULL)
    )                   AS applications,
    t.apex_developers   AS developers
FROM apex_workspaces t
WHERE 1 = 1
    AND t.workspace     NOT IN ('INTERNAL')
    AND t.workspace     NOT LIKE 'COM.ORACLE.%'
    AND (t.workspace    = :workspace    OR :workspace IS NULL)
    AND (:schemas IS NULL OR EXISTS (
        SELECT 1 FROM apex_workspace_schemas s
        WHERE  s.workspace_id = t.workspace_id
            AND '|' || :schemas || '|' LIKE '%|' || s.schema || '|%'
    ))
ORDER BY
    t.workspace
""".strip()

EXPORT_START_QUERY = """
BEGIN
    FOR c IN (
        SELECT
            a.workspace,
            a.application_id AS app_id
        FROM apex_applications a
        WHERE a.application_id = :app_id
    ) LOOP
        APEX_UTIL.SET_WORKSPACE (
            p_workspace => c.workspace
        );
        APEX_UTIL.SET_SECURITY_GROUP_ID (
            p_security_group_id => APEX_UTIL.FIND_SECURITY_GROUP_ID(p_workspace => c.workspace)
        );
        BEGIN
            APEX_SESSION.CREATE_SESSION (
                p_app_id                    => c.app_id,
                p_page_id                   => 0,
                p_username                  => c.workspace,
                p_call_post_authentication  => FALSE
            );
        EXCEPTION
        WHEN OTHERS THEN
            NULL;
        END;
        APEX_COLLECTION.CREATE_COLLECTION (
            p_collection_name       => 'ADT_APEX_EXPORT',
            p_truncate_if_exists    => 'YES'
        );
        COMMIT;
    END LOOP;
END;
""".strip()

EXPORT_FULL_QUERY = """
DECLARE
    l_files apex_t_export_files;
BEGIN
    l_files := APEX_EXPORT.GET_APPLICATION (
        p_application_id            => :app_id,
        p_split                     => FALSE,
        p_with_date                 => (:with_date = 'Y'),
        p_with_ir_public_reports    => (:with_ir_public_reports = 'Y'),
        p_with_ir_private_reports   => (:with_ir_private_reports = 'Y'),
        p_with_ir_notifications     => (:with_ir_notifications = 'Y'),
        p_with_translations         => (:with_translations = 'Y'),
        p_with_original_ids         => (:originals = 'Y'),
        p_with_no_subscriptions     => (:with_no_subscriptions = 'Y'),
        p_with_comments             => (:with_comments = 'Y'),
        p_with_acl_assignments      => (:with_acl_assignments = 'Y'),
        p_with_audit_info           => :with_audit_info,
        p_with_runtime_instances    => NULL
    );
    APEX_COLLECTION.CREATE_COLLECTION (
        p_collection_name       => 'ADT_APEX_EXPORT',
        p_truncate_if_exists    => 'YES'
    );
    FOR i IN l_files.FIRST .. l_files.LAST LOOP
        APEX_COLLECTION.ADD_MEMBER (
            p_collection_name   => 'ADT_APEX_EXPORT',
            p_c001              => l_files(i).name,
            p_clob001           => l_files(i).contents
        );
    END LOOP;
    COMMIT;
END;
""".strip()

EXPORT_SPLIT_QUERY = """
DECLARE
    l_files apex_t_export_files;
BEGIN
    l_files := APEX_EXPORT.GET_APPLICATION (
        p_application_id            => :app_id,
        p_split                     => TRUE,
        p_type                      => 'APPLICATION_SOURCE',
        p_with_date                 => (:with_date = 'Y'),
        p_with_ir_public_reports    => (:with_ir_public_reports = 'Y'),
        p_with_ir_private_reports   => (:with_ir_private_reports = 'Y'),
        p_with_ir_notifications     => (:with_ir_notifications = 'Y'),
        p_with_translations         => (:with_translations = 'Y'),
        p_with_original_ids         => (:originals = 'Y'),
        p_with_no_subscriptions     => (:with_no_subscriptions = 'Y'),
        p_with_comments             => (:with_comments = 'Y'),
        p_with_acl_assignments      => (:with_acl_assignments = 'Y'),
        p_with_audit_info           => :with_audit_info,
        p_with_runtime_instances    => NULL
    );
    APEX_COLLECTION.CREATE_COLLECTION (
        p_collection_name       => 'ADT_APEX_EXPORT',
        p_truncate_if_exists    => 'YES'
    );
    FOR i IN l_files.FIRST .. l_files.LAST LOOP
        IF (l_files(i).name LIKE '%/files/%' OR l_files(i).name LIKE '%/app_static_files/%') THEN
            CONTINUE;
        END IF;
        APEX_COLLECTION.ADD_MEMBER (
            p_collection_name   => 'ADT_APEX_EXPORT',
            p_c001              => l_files(i).name,
            p_clob001           => l_files(i).contents
        );
    END LOOP;
    COMMIT;
END;
""".strip()

EXPORT_READABLE_QUERY = """
DECLARE
    l_files apex_t_export_files;
BEGIN
    l_files := APEX_EXPORT.GET_APPLICATION (
        p_application_id        => :app_id,
        p_split                 => TRUE,
        p_type                  => 'READABLE_YAML',
        p_with_date             => FALSE,
        p_with_translations     => TRUE,
        p_with_original_ids     => (:originals = 'Y'),
        p_with_comments         => FALSE
    );
    APEX_COLLECTION.CREATE_COLLECTION (
        p_collection_name       => 'ADT_APEX_EXPORT',
        p_truncate_if_exists    => 'YES'
    );
    FOR i IN l_files.FIRST .. l_files.LAST LOOP
        IF (l_files(i).name LIKE '%/files/%' OR l_files(i).name LIKE '%/app_static_files/%') THEN
            CONTINUE;
        END IF;
        APEX_COLLECTION.ADD_MEMBER (
            p_collection_name   => 'ADT_APEX_EXPORT',
            p_c001              => l_files(i).name,
            p_clob001           => l_files(i).contents
        );
    END LOOP;
    COMMIT;
END;
""".strip()

EXPORT_EMBEDDED_QUERY = """
DECLARE
    l_files apex_t_export_files;
BEGIN
    l_files := APEX_EXPORT.GET_APPLICATION (
        p_application_id        => :app_id,
        p_split                 => TRUE,
        p_type                  => 'EMBEDDED_CODE',
        p_with_date             => FALSE,
        p_with_translations     => TRUE,
        p_with_original_ids     => (:originals = 'Y'),
        p_with_comments         => FALSE
    );
    APEX_COLLECTION.CREATE_COLLECTION (
        p_collection_name       => 'ADT_APEX_EXPORT',
        p_truncate_if_exists    => 'YES'
    );
    FOR i IN l_files.FIRST .. l_files.LAST LOOP
        IF (l_files(i).name LIKE '%/files/%' OR l_files(i).name LIKE '%/app_static_files/%') THEN
            CONTINUE;
        END IF;
        APEX_COLLECTION.ADD_MEMBER (
            p_collection_name   => 'ADT_APEX_EXPORT',
            p_c001              => l_files(i).name,
            p_clob001           => l_files(i).contents
        );
    END LOOP;
    COMMIT;
END;
""".strip()

EXPORT_CHECKSUM_QUERY = """
DECLARE
    l_files apex_t_export_files;
BEGIN
    l_files := APEX_EXPORT.GET_APPLICATION (
        p_application_id        => :app_id,
        p_type                  => 'CHECKSUM-SH256'
    );
    APEX_COLLECTION.CREATE_COLLECTION (
        p_collection_name       => 'ADT_APEX_EXPORT',
        p_truncate_if_exists    => 'YES'
    );
    FOR i IN l_files.FIRST .. l_files.LAST LOOP
        APEX_COLLECTION.ADD_MEMBER (
            p_collection_name   => 'ADT_APEX_EXPORT',
            p_c001              => l_files(i).name,
            p_clob001           => l_files(i).contents
        );
    END LOOP;
    COMMIT;
END;
""".strip()

# APEXLANG (APEX 26.1+) is a whole-app folder tree, not a component slice: member
# names already arrive relative (`application.apx`, `pages/pNNNNN-<alias>.apx`)
# with no `f<id>/` root prefix, and `p_split` does not change that. Static-file
# payloads come back as `contents_blob` members under
# `shared-components/static-files/` — including text ones such as `app.css`
# (verified live on APEX 26.1.0, apps 800 and 808, 2026-07-27). They are dropped
# here so `-files` stays the single static-file channel and the collection
# round-trip stays CLOB-only; `shared-components/static-files.apx`, the text
# metadata that references them, is a CLOB member and stays in.
EXPORT_APEXLANG_QUERY = """
DECLARE
    l_files apex_t_export_files;
BEGIN
    l_files := APEX_EXPORT.GET_APPLICATION (
        p_application_id        => :app_id,
        p_split                 => TRUE,
        p_type                  => 'APEXLANG'
    );
    APEX_COLLECTION.CREATE_COLLECTION (
        p_collection_name       => 'ADT_APEX_EXPORT',
        p_truncate_if_exists    => 'YES'
    );
    FOR i IN l_files.FIRST .. l_files.LAST LOOP
        IF (l_files(i).name LIKE 'shared-components/static-files/%'
            OR l_files(i).contents_blob IS NOT NULL) THEN
            CONTINUE;
        END IF;
        APEX_COLLECTION.ADD_MEMBER (
            p_collection_name   => 'ADT_APEX_EXPORT',
            p_c001              => l_files(i).name,
            p_clob001           => l_files(i).contents
        );
    END LOOP;
    COMMIT;
END;
""".strip()

FETCH_FILES_QUERY = """
SELECT
    c.seq_id,
    c.c001      AS file_name,
    c.clob001   AS clob_content
FROM apex_collections c
WHERE c.collection_name = 'ADT_APEX_EXPORT'
""".strip()

RECENT_COMPONENTS_QUERY = """
SELECT
    a.type_name,
    a.id,
    a.name,
    a.used_on_pages
FROM apex_appl_export_comps a
WHERE a.application_id      = :app_id
    AND (a.last_updated_on  >= TRUNC(SYSDATE) + 1 - :recent OR :recent IS NULL)
    AND (
        :changed_since IS NULL
        OR a.last_updated_on >= TO_DATE(:changed_since, 'YYYY-MM-DD HH24:MI:SS')
    )
    AND (a.last_updated_by  = :author OR :author IS NULL)
ORDER BY
    a.type_name,
    CASE WHEN a.type_name = 'PAGE'
        THEN LPAD(a.id, 8, '0')
        ELSE a.name
        END
""".strip()

APEX_FILES_QUERY = """
SELECT
    f.filename,
    f.blob_content
FROM wwv_flow_files f
WHERE f.flow_id                 = :app_id
    AND NVL(f.created_by, '-')  NOT IN ('SYS')
    AND f.content_type          IS NULL
""".strip()

APEX_ID_NAMES_QUERY = """
SELECT
    t.authorization_scheme_id       AS component_id,
    t.authorization_scheme_name     AS component_name,
    'AUTHORIZATION'                 AS component_type
FROM apex_application_authorization t
WHERE t.application_id = :app_id
UNION ALL
SELECT
    t.lov_id,
    t.list_of_values_name,
    'LOV'
FROM apex_application_lovs t
WHERE t.application_id = :app_id
UNION ALL
SELECT
    t.group_id,
    t.page_group_name,
    'PAGE GROUP'
FROM apex_application_page_groups t
WHERE t.application_id = :app_id
UNION ALL
SELECT
    t.list_id,
    t.list_name,
    'LIST'
FROM apex_application_lists t
WHERE t.application_id = :app_id
UNION ALL
SELECT
    t.breadcrumb_id,
    t.breadcrumb_name,
    'BREADCRUMB'
FROM apex_application_breadcrumbs t
WHERE t.application_id = :app_id
UNION ALL
SELECT
    t.email_template_id,
    t.name,
    'EMAIL TEMPLATE'
FROM apex_appl_email_templates t
WHERE t.application_id = :app_id
""".strip()

WORKSPACE_DEVELOPERS_QUERY = """
SELECT
    d.workspace_name    AS workspace,
    d.user_name,
    d.email             AS user_mail
FROM apex_workspace_developers d
WHERE 1 = 1
    AND d.is_application_developer = 'Yes'
    AND d.account_locked = 'No'
    AND d.email NOT LIKE 'dba@%'
    AND d.date_last_updated > TRUNC(SYSDATE) - 90
GROUP BY
    d.workspace_name,
    d.user_name,
    d.email
ORDER BY 1, 2
""".strip()

PAGE_COMMENTS_QUERY = """
SELECT
    t.page_id,
    t.page_name,
    t.last_updated_by,
    t.last_updated_on,
    t.page_comment
FROM apex_application_pages t
WHERE t.application_id = :app_id
    AND t.page_comment IS NOT NULL
""".strip()

PAGE_REGION_COMMENTS_QUERY = """
SELECT
    t.page_id,
    t.page_name,
    t.region_id,
    t.region_name,
    t.last_updated_by,
    t.last_updated_on,
    t.component_comment
FROM apex_application_page_regions t
WHERE t.application_id = :app_id
    AND t.component_comment IS NOT NULL
""".strip()
