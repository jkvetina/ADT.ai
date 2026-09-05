"""How many files a finished install script actually runs, and how far a
deploy got, both calculated from live `@` references (ADT #321).

Split out of `deploy.py` when it crossed the 20 KB context guard. A generated
`PROMPT -- FILE:`/`PROMPT -- SCRIPT:` marker is not proof a file runs, only a
LIVE `@` line beneath it is: Jan, on a script hand-edited to skip one file on a
re-deploy, "I can have `-- FILE: ...` / `--@file` and the file is listed,
counted, but not executed, since it is commented out!" Everything here reads
the finished install script directly rather than trusting a printed label, and
excludes anything the project's own `patch_template_dir` links ("skip the
patch_template folders").
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

# `PROMPT -- FILE: <path>` / `PROMPT -- SCRIPT: <path>` in the install script
# reach SQLcl as `PROMPT FILE: <path>` / `PROMPT SCRIPT: <path>` (the deploy
# payload rewrites `PROMPT --` prefixes), so the echoed line carries no comment
# marker. This alone is not a running-progress signal: a marker still echoes
# for a label whose paired `@` line was hand-disabled, so `_deployment_progress`
# below only trusts an echo that also appears in `_countable_references`'s
# ``allowed`` set, never the mere shape of the line.
_COUNTABLE_ECHO_RE = re.compile(r"^(?:FILE|SCRIPT): .*$", re.MULTILINE)


def _is_live_line(line: str) -> bool:
    """Does this line actually reach SQLcl, the same rule `_deployment_payload`
    already applies when it builds the payload SQLcl receives: a line whose
    stripped form is empty or starts with `--` is a comment, never sent."""
    stripped = line.strip()
    return bool(stripped) and not stripped.startswith("--")


def _link_pattern(config: dict[str, Any]) -> re.Pattern[str]:
    """A regex matching exactly this project's `@` link line, whatever quoting
    `patch_file_link` configures, with the linked path captured as ``path``.

    Built by escaping the configured template and reopening only the `#FILE#`
    placeholder `_install_file_link` fills in, so it matches every `@` line the
    install script can contain (object, script, or template) regardless of the
    project's chosen quote style, and nothing else.
    """
    template = str(config.get("patch_file_link") or '@"./#FILE#"')
    body = re.escape(template).replace(re.escape("#FILE#"), r"(?P<path>.+?)")
    if not template.rstrip().endswith(";"):
        body += ";?"
    return re.compile(r"^" + body + r"$")


def linked_references(
    sql_text: str,
    root: Path,
    folder_path: Path,
    config: dict[str, Any],
) -> list[tuple[str, str]]:
    """Every live, non-template reference the script makes, in LINK ORDER.

    ``(label, path)`` per reference: ``label`` is the marker word above the `@`
    line (``FILE`` or ``SCRIPT``, empty when the link carries no marker, which is
    how a `patch_template_dir` link is written), and ``path`` is the marker's own
    text, which is the project-relative path the generator wrote. The resolved
    `@` target is deliberately not used for ``path``: a link points into the
    patch's own `snapshots/` copy (`@"./snapshots/<repo path>"`), so resolving it
    yields a path inside the patch folder rather than the repo file it came from.

    The one ordered walk both readers share. `_countable_references` counted the
    same references and returned a SET of markers, which is everything a progress
    counter needs and nothing a listing can use: `PATCH CONTENTS:` has to print
    the files in the order the patch will run them (ADT #443), and a set has no
    order. Splitting the walk out means the count and the listing cannot disagree
    about which references are live.
    """
    link_re = _link_pattern(config)
    template_root = (
        root / str(config.get("patch_template_dir") or "config/patch_template")
    ).resolve()
    lines = sql_text.splitlines()
    references: list[tuple[str, str]] = []
    for index, line in enumerate(lines):
        if not _is_live_line(line):
            continue
        match = link_re.match(line.strip())
        if match is None:
            continue
        resolved = (folder_path / match.group("path")).resolve()
        try:
            resolved.relative_to(template_root)
            continue  # under patch_template_dir: never counted
        except ValueError:
            pass
        label = lines[index - 1].strip() if index > 0 else ""
        for word in ("FILE", "SCRIPT"):
            prefix = f"PROMPT -- {word}:"
            if label.upper().startswith(prefix):
                references.append((word, label[len(prefix) :].strip()))
                break
        else:
            references.append(("", ""))
    return references


def _countable_references(
    sql_text: str,
    root: Path,
    folder_path: Path,
    config: dict[str, Any],
) -> tuple[int, frozenset[str]]:
    """Every file the finished install script will actually run, calculated from
    its live `@` references, never from a printed label.

    Walks every live line (never a `--`-commented one), matches it against the
    project's own `patch_file_link` shape, resolves what it actually points at,
    and drops anything under `patch_template_dir`, since a template is linked
    exactly like an object or a script, so the exclusion has to be by where the
    link resolves, not by which label preceded it.

    Returns the total, and the set of echoed marker texts (`FILE: <path>` /
    `SCRIPT: <path>`, as SQLcl actually echoes a live `PROMPT --` line) that
    correspond to a genuinely live, non-template reference, the ALLOWED set a
    running deploy's transcript is checked against, so a live label whose paired
    `@` was disabled cannot inflate `deployed` either.
    """
    references = linked_references(sql_text, root, folder_path, config)
    allowed = {
        f"{label}: {path}" for label, path in references if label
    }
    return len(references), frozenset(allowed)


def _countable_file_total(
    sql_text: str,
    root: Path,
    folder_path: Path,
    config: dict[str, Any],
) -> int:
    """How many countable files a FINISHED install script actually links.

    Read straight off the script that is about to run (or already has), so this
    can never drift from what the deploy actually reaches the way the old printed
    `n/m` counter did: that count was computed once, per object-file loop, at
    `-create` time, and never saw the `patch_scripts` a later section of the same
    script also links, or a hand-edit made to the folder afterwards.
    """
    return _countable_references(sql_text, root, folder_path, config)[0]


class DeploymentProgressReader:
    """Counts the same echoes `_deployment_progress` counts, one line at a time.

    `_deployment_progress` below reads a finished transcript, which is all there
    was to read until ADT #434: `run_sqlcl_script` captured SQLcl's stdout over a
    pipe, and the JVM block-buffers a pipe, so a whole deploy's output arrived at
    exit. Handed to `sqlcl_request` as its line reader, this turns the identical
    rule into a running total and calls ``notify(deployed, total)`` each time it
    moves, so the console can repaint the open row.

    The live count and the recorded one agree by construction: both ask whether a
    line is one of the ``allowed`` markers, the set built off live `@` references,
    so a `PROMPT -- FILE:` whose `@` line was commented out inflates neither. The
    result still records `_deployment_progress`'s answer rather than this one -
    what a deploy reports is read off the transcript it kept, never off a
    display counter.
    """

    def __init__(self, allowed: frozenset[str], total: int, notify: Any) -> None:
        self._allowed = allowed
        self._total = total
        self._notify = notify
        self.deployed = 0

    def __call__(self, line: str) -> None:
        if not _COUNTABLE_ECHO_RE.match(line) or line not in self._allowed:
            return
        self.deployed += 1
        self._notify(self.deployed, self._total)


def _deployment_progress(output: str, allowed: frozenset[str]) -> int | None:
    """How many countable files a run's transcript actually reached.

    PROMPT lines echo strictly in execution order, so counting how many
    ALLOWED ones have appeared so far IS the position of the last one, no digit
    to extract. Filtering against ``allowed`` (built off live `@` references,
    never off the label alone) is what stops a live `PROMPT -- FILE:` whose `@`
    line was disabled from inflating this the same way it inflated the total.
    """
    count = sum(1 for line in _COUNTABLE_ECHO_RE.findall(output) if line in allowed)
    return count or None
