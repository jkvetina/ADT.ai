"""Where an archived patch lands under `patch_archive/` (ADT #517).

Two functions, and they sit here rather than in `settings.py` for two reasons.
`archive_subfolder` is not a config read at all: it takes a folder NAME and
derives a date from it, which is the one thing `settings.py` says of itself that
it never does (`test_the_settings_module_reads_config_and_never_the_filesystem`
guards the neighbouring half of that claim). And `settings.py` was 19.3 KB of a
20 KB context guard before this card, so the module that owns every other key was
one addition from forcing a split of the shared readers; a focused module costs
nothing and leaves that split to a card that means it.

`patch_archive_subfolder`'s DEFAULT stays in `settings.DEFAULTS` with every other
key, because that map is what `config.yaml`, the docs and the tests agree
against, and a second home for defaults is how two spellings of one shipped value
start to disagree.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from adt_ai.patch import settings as _settings


def archive_subfolder_format(config: dict[str, Any]) -> str:
    """`patch_archive_subfolder`: the level `-archive` files a zip under.

    A `strftime` format, `%Y-%m` shipped, so a year of patches reads as twelve
    folders rather than one listing nobody scrolls.

    **Blank is a real answer here, and no other patch key reads it that way.**
    `settings._text` treats an empty value as unset, because an empty folder name
    would write into the parent; here `''` is exactly the request, being how a
    project asks for the flat archive it had before this key existed. So `config`
    is read directly: absent means the shipped default, present and empty means
    the archive root itself.
    """
    value = config.get("patch_archive_subfolder")
    if value is None:
        return str(_settings.DEFAULTS["patch_archive_subfolder"])
    return str(value).strip().strip("/")


def archive_subfolder(config: dict[str, Any], *, folder: str) -> str:
    """Where `folder` files under the archive root, or `""` for the root itself.

    The month is the patch's OWN day, read back through the same
    `patch_folder`/`today_patch` pair that wrote the name, never off the clock: a
    June patch archived in August belongs under June, and archiving one folder
    twice has to land it in the same place both times (Jan, 2026-08-24).

    `""` is the honest answer wherever that day cannot be established, an
    unreadable name, a digits-but-not-a-date day, or a `today_patch` carrying no
    year. `%m%d` is a legal stamp and nothing can say which year `0601` is, so
    the patch goes to the archive root rather than into a month it was never
    built in. Same posture `settings.archive_format` takes on an unknown format:
    a patch that built correctly does not die, or get misfiled, at the archive
    step.
    """
    requested = archive_subfolder_format(config)
    if not requested:
        return ""
    stamp = _settings.today_patch_format(config)
    if any(token in requested for token in ("%Y", "%y")) and not any(
        token in stamp for token in ("%Y", "%y")
    ):
        return ""
    match = _settings.patch_folder_re(config).match(folder)
    if match is None:
        return ""
    try:
        day = datetime.strptime(match.group("day"), stamp)
    except (ValueError, IndexError):
        return ""
    return day.strftime(requested).strip("/")
