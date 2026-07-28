from __future__ import annotations


def apex_version_tuple(apex_version: str | None) -> tuple[int, ...]:
    """Parse an APEX release string into a comparable ``(major, minor)`` tuple.

    Returns an empty tuple when the version is missing or unparseable, so every
    caller can spell "probe miss" as a falsy value and stay permissive rather
    than silently gating a feature off on an unknown instance.
    """
    if not apex_version:
        return ()
    parts: list[int] = []
    for raw in str(apex_version).split(".")[:2]:
        digits = ""
        for char in raw:
            if char.isdigit():
                digits += char
            elif digits:
                break
        if not digits:
            break
        parts.append(int(digits))
    return tuple(parts)


def supports_apexlang(apex_version: str | None) -> bool:
    """Whether this instance can export the APEXlang (``.apx``) format.

    APEX 26.1 added ``APEX_EXPORT.c_type_apexlang``. An unknown version stays
    permissive: the export simply fails loudly if the instance cannot make it,
    which beats silently skipping a format the user explicitly asked for.
    """
    parsed = apex_version_tuple(apex_version)
    return not parsed or parsed >= (26, 1)


def readable_yaml_removed(apex_version: str | None) -> bool:
    """Whether ``READABLE_YAML`` is gone as a distinct format on this instance.

    26.1 turned it into a deprecated alias of ``APEXLANG``, so a readable export
    there would write APEXlang content into the readable tree. An unknown
    version keeps the pre-26.1 behavior.
    """
    parsed = apex_version_tuple(apex_version)
    return bool(parsed) and parsed >= (26, 1)
