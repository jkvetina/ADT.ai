from __future__ import annotations

import re

from adt_ai.export_db.normalizers import NormalizationContext


def normalize_job(lines: list[str], context: NormalizationContext) -> list[str]:
    job_payload = _extract_scheduler_job_payload(lines)
    if not job_payload:
        return lines

    action_match = re.search(
        r"job_action\s*=>\s*'((?:''|[^'])*)'",
        job_payload,
        flags=re.IGNORECASE | re.DOTALL,
    )
    job_action = action_match.group(1).strip() if action_match else ""
    if action_match:
        job_payload = job_payload.replace(job_action, "{JOB_ACTION}")

    job_payload = re.sub(
        r"start_date=>TO_TIMESTAMP_TZ[^)]*[)]",
        "start_date=>SYSDATE",
        job_payload,
        flags=re.IGNORECASE,
    )
    job_payload = job_payload.replace("end_date=>NULL,", "")
    job_payload = job_payload.replace("job_class=>\'\"DEFAULT_JOB_CLASS\"\',", "")
    payload_lines = ["job_name=>in_job_name,"]
    payload_lines.extend(
        re.sub(
            r"\s*,\s*([a-z_]+)\s*=>\s*",
            r",\n\1=>",
            job_payload,
            flags=re.IGNORECASE,
        ).splitlines()
    )
    formatted_payload = "\n".join(_format_job_attribute(line) for line in payload_lines if line)
    formatted_payload = formatted_payload.replace("{JOB_ACTION}", job_action)

    return _job_template(
        job_name    = context.object_name.upper(),
        job_payload = formatted_payload,
    ).splitlines() + [""]


def _extract_scheduler_job_payload(lines: list[str]) -> str:
    cleaned = [
        line
        for line in lines
        if not (line.lstrip().startswith("sys.dbms_scheduler.set_attribute(") and "NLS_ENV" in line)
    ]
    for index, line in enumerate(cleaned):
        if line.startswith(");"):
            return "\n".join(cleaned[2:index])
    return ""

def _format_job_attribute(line: str) -> str:
    parts = line.split("=>")
    return f"        {parts[0]:<20}=> {'=>'.join(parts[1:])}"

def _job_template(job_name: str, job_payload: str) -> str:
    return f"""DECLARE
    in_job_name             CONSTANT VARCHAR2(128)  := '{job_name}';
    in_run_immediatelly     CONSTANT BOOLEAN        := FALSE;
BEGIN
    DBMS_OUTPUT.PUT_LINE('--');
    DBMS_OUTPUT.PUT_LINE('-- REPLACE JOB ' || UPPER(in_job_name));
    DBMS_OUTPUT.PUT_LINE('--');
    --
    BEGIN
        DBMS_SCHEDULER.DROP_JOB(in_job_name, TRUE);
    EXCEPTION
    WHEN OTHERS THEN
        NULL;
    END;
    --
    DBMS_SCHEDULER.CREATE_JOB (
{job_payload}
    );
    --
    DBMS_SCHEDULER.SET_ATTRIBUTE(in_job_name, 'JOB_PRIORITY', 3);
    DBMS_SCHEDULER.ENABLE(in_job_name);
    COMMIT;
    --
    IF in_run_immediatelly THEN
        DBMS_SCHEDULER.RUN_JOB(in_job_name);
        COMMIT;
    END IF;
END;
/"""
