from __future__ import annotations

# Application header: workspace + names for one app. Columns come back uppercase
# from the Oracle cursor (unquoted aliases), so the Python layer reads WORKSPACE,
# APP_ID, APP_NAME, APP_ALIAS.
APP_METADATA_QUERY = """
SELECT a.workspace        AS workspace,
       a.application_id    AS app_id,
       a.application_name  AS app_name,
       a.alias             AS app_alias
FROM   apex_applications a
WHERE  a.application_id = :app_id
""".strip()

# Page label cache (readable output / diagram node text).
APP_PAGES_QUERY = """
SELECT p.page_id     AS page_id,
       p.page_name   AS page_name,
       p.page_alias  AS page_alias
FROM   apex_application_pages p
WHERE  p.application_id = :app_id
ORDER  BY p.page_id
""".strip()

# Whole navigation flow map for one application: the 12 link-source views from
# KNOWLEDGEBASE/APEX/page_navigation_flow_map, UNION ALL'd, with the f?p app/page
# token regex and the flag CASE. The note's two `define`s become the :app_id bind;
# raw_target keeps the original link string so DYNAMIC/OTHER stay meaningful; no
# target filter, so every flag (PAGE/CROSS_APP/DYNAMIC/OTHER/NONE) is returned and
# the Python layer resolves target_app_id.
NAV_EDGES_QUERY = """
WITH raw_edges AS (
  -- 1) Branches: "Branch to Page" (server-side redirect after processing)
  SELECT 'BRANCH' src_type, page_id src_page, branch_id component_id,
         branch_name component, branch_action raw_target,
         CAST(NULL AS VARCHAR2(120)) app_token,
         CASE WHEN branch_type = 'Branch to Page' THEN branch_action END page_token
  FROM   apex_application_page_branches
  WHERE  application_id = :app_id AND branch_type = 'Branch to Page'
  UNION ALL
  -- 2) Buttons: Redirect to Page / to Page in a different App
  SELECT 'BUTTON', page_id, button_id, button_name, redirect_url,
         regexp_substr(redirect_url,'f\\?p=([^:]*)',1,1,'i',1),
         regexp_substr(redirect_url,'f\\?p=[^:]*:([^:]*)',1,1,'i',1)
  FROM   apex_application_page_buttons
  WHERE  application_id = :app_id
  AND    button_action_code IN ('REDIRECT_PAGE','REDIRECT_APP')
  UNION ALL
  -- 3) Standard tabs (TAB_PAGE is a bare numeric page id)
  SELECT 'TAB', NULL, tab_id, tab_label, to_char(tab_page), NULL, to_char(tab_page)
  FROM   apex_application_tabs WHERE application_id = :app_id
  UNION ALL
  -- 4) Parent tabs
  SELECT 'PARENT_TAB', NULL, parent_tab_id, tab_label, tab_target,
         regexp_substr(tab_target,'f\\?p=([^:]*)',1,1,'i',1),
         regexp_substr(tab_target,'f\\?p=[^:]*:([^:]*)',1,1,'i',1)
  FROM   apex_application_parent_tabs WHERE application_id = :app_id
  UNION ALL
  -- 5) List entries (navigation menus / lists; shared across pages)
  SELECT 'LIST_ENTRY', NULL, list_entry_id, entry_text, entry_target,
         regexp_substr(entry_target,'f\\?p=([^:]*)',1,1,'i',1),
         regexp_substr(entry_target,'f\\?p=[^:]*:([^:]*)',1,1,'i',1)
  FROM   apex_application_list_entries WHERE application_id = :app_id
  UNION ALL
  -- 6) Breadcrumb entries (DEFINED_FOR_PAGE = page it is attached to)
  SELECT 'BREADCRUMB', defined_for_page, breadcrumb_entry_id, entry_label, url,
         regexp_substr(url,'f\\?p=([^:]*)',1,1,'i',1),
         regexp_substr(url,'f\\?p=[^:]*:([^:]*)',1,1,'i',1)
  FROM   apex_application_bc_entries WHERE application_id = :app_id
  UNION ALL
  -- 7) Navigation bar entries (global; ICON_TARGET may be js-wrapped f?p)
  SELECT 'NAV_BAR', NULL, nav_bar_id, icon_subtext, icon_target,
         regexp_substr(icon_target,'f\\?p=([^:]*)',1,1,'i',1),
         regexp_substr(icon_target,'f\\?p=[^:]*:([^:]*)',1,1,'i',1)
  FROM   apex_application_nav_bar
  WHERE  application_id = :app_id AND icon_target IS NOT NULL
  UNION ALL
  -- 8) Interactive report column links
  SELECT 'IR_COL_LINK', page_id, column_id, column_alias, column_link,
         regexp_substr(column_link,'f\\?p=([^:]*)',1,1,'i',1),
         regexp_substr(column_link,'f\\?p=[^:]*:([^:]*)',1,1,'i',1)
  FROM   apex_application_page_ir_col
  WHERE  application_id = :app_id AND column_link IS NOT NULL
  UNION ALL
  -- 9) Classic report column links
  SELECT 'RPT_COL_LINK', page_id, region_report_column_id, column_alias, column_link_url,
         regexp_substr(column_link_url,'f\\?p=([^:]*)',1,1,'i',1),
         regexp_substr(column_link_url,'f\\?p=[^:]*:([^:]*)',1,1,'i',1)
  FROM   apex_application_page_rpt_cols
  WHERE  application_id = :app_id AND column_link_url IS NOT NULL
  UNION ALL
  -- 10) Chart series drill links
  SELECT 'CHART_SERIES', page_id, series_id, series_name, link_target,
         regexp_substr(link_target,'f\\?p=([^:]*)',1,1,'i',1),
         regexp_substr(link_target,'f\\?p=[^:]*:([^:]*)',1,1,'i',1)
  FROM   apex_application_page_chart_s
  WHERE  application_id = :app_id AND link_target IS NOT NULL
  UNION ALL
  -- 11) Region "more" / source link
  SELECT 'REGION_LINK', page_id, region_id, region_name, url,
         regexp_substr(url,'f\\?p=([^:]*)',1,1,'i',1),
         regexp_substr(url,'f\\?p=[^:]*:([^:]*)',1,1,'i',1)
  FROM   apex_application_page_regions
  WHERE  application_id = :app_id AND url IS NOT NULL
  UNION ALL
  -- 12) Page-level duplicate-submission redirect
  SELECT 'PAGE_DUP_GOTO', page_id, page_id, page_name, on_dup_submission_goto_url,
         regexp_substr(on_dup_submission_goto_url,'f\\?p=([^:]*)',1,1,'i',1),
         regexp_substr(on_dup_submission_goto_url,'f\\?p=[^:]*:([^:]*)',1,1,'i',1)
  FROM   apex_application_pages
  WHERE  application_id = :app_id AND on_dup_submission_goto_url IS NOT NULL
),
edges AS (
  SELECT src_type, src_page, component_id, component, raw_target,
         app_token AS target_app,
         CASE WHEN regexp_like(page_token,'^\\d+$')
              THEN to_number(page_token) END AS target_page,
         CASE
           WHEN page_token IS NULL THEN 'NONE'
           WHEN regexp_like(page_token,'^\\d+$')
                AND (app_token IS NULL
                     OR NOT regexp_like(app_token,'^\\d+$')
                     OR app_token = to_char(:app_id))
                THEN 'PAGE'
           WHEN regexp_like(page_token,'^\\d+$')
                AND regexp_like(app_token,'^\\d+$')
                AND app_token <> to_char(:app_id)
                THEN 'CROSS_APP'
           WHEN page_token LIKE '&%' THEN 'DYNAMIC'
           ELSE 'OTHER'
         END AS flag
  FROM   raw_edges
)
SELECT src_type     AS src_type,
       src_page     AS src_page,
       component_id AS component_id,
       component    AS component,
       raw_target   AS raw_target,
       target_app   AS target_app,
       target_page  AS target_page,
       flag         AS flag
FROM   edges
ORDER  BY src_type, src_page, component_id
""".strip()
