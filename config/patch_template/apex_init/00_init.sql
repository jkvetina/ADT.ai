-- `SET DEFINE OFF`, the workspace and the keep-sessions call all used to sit
-- here behind a hand-edited <APEX_WORKSPACE> placeholder. `patch -create`
-- emits every one of them itself now, with the workspace resolved from
-- `config/apex_apps.yaml` (ADT #298), and it emits them BEFORE this file so
-- anything you put here still wins.
SET SERVEROUTPUT OFF
