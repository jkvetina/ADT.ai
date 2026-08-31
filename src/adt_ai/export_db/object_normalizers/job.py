from __future__ import annotations

import re

from adt_ai.export_db.normalizers import NormalizationContext, qualified


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
        job_name    = qualified(context.object_name.upper(), context),
        job_payload = formatted_payload,
        attributes  = _trailing_attributes(lines),
        enabled     = _is_enabled(lines),
    ).splitlines() + [""]


def _is_enabled(lines: list[str]) -> bool:
    """Whether the source DDL enables the job, rather than assuming it does.

    `GET_DDL('PROCOBJ', ...)` always emits `enabled=>FALSE` inside `create_job` and
    then appends a separate `dbms_scheduler.enable(...)` call when, and only when,
    the job is actually enabled. The template used to emit that ENABLE
    unconditionally, so a deliberately disabled job exported as an enabled one and
    deploying the file switched it on. Measured on IVORY 2026-08-20:
    `ICT_COM_INVOICE_TRIGGER_JOB` is `ENABLED = FALSE` and its own comment reads
    "ENABLE ONLY when the pending-invoices region ships (decision D2)".
    """
    return any(
        re.search(r"\bdbms_scheduler\.enable\s*\(", line, flags=re.IGNORECASE)
        for line in lines
    )


def _trailing_attributes(lines: list[str]) -> list[str]:
    """The `set_attribute` calls the DDL actually carries, rebound to `in_job_name`.

    The template used to hardcode `SET_ATTRIBUTE(in_job_name, 'JOB_PRIORITY', 3)`,
    which invents a line for every job: PROCOBJ emits no JOB_PRIORITY attribute when
    the priority is the default 3, and emits a real one when it is not, so the
    hardcoded row was redundant on a default job and wrong on any other. NLS_ENV
    stays dropped, as it always was.
    """
    attributes: list[str] = []
    for line in lines:
        stripped = line.strip()
        if not re.match(
            r"^(sys\.)?dbms_scheduler\.set_attribute\s*\(", stripped, flags=re.IGNORECASE
        ):
            continue
        if "NLS_ENV" in stripped:
            continue
        rewritten = re.sub(
            r"set_attribute\s*\(\s*'\"[^\"]+\"'\s*,",
            "SET_ATTRIBUTE(in_job_name,",
            stripped,
            flags = re.IGNORECASE,
        )
        rewritten = re.sub(
            r"^(sys\.)?dbms_scheduler\.", "DBMS_SCHEDULER.", rewritten, flags=re.IGNORECASE
        )
        attributes.append(f"    {rewritten.rstrip()}")
    return attributes


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

def _job_template(
    job_name: str,
    job_payload: str,
    attributes: list[str] | None = None,
    enabled: bool = True,
) -> str:
    trailing = list(attributes or [])
    if enabled:
        trailing.append("    DBMS_SCHEDULER.ENABLE(in_job_name);")
    trailing_block = "\n".join(trailing)
    if trailing_block:
        trailing_block += "\n"
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
{trailing_block}    COMMIT;
    --
    IF in_run_immediatelly THEN
        DBMS_SCHEDULER.RUN_JOB(in_job_name);
        COMMIT;
    END IF;
END;
/"""
