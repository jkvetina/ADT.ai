from __future__ import annotations

import re

from adt_ai.export_db.normalizers import (
    NormalizationContext,
    _drop_create_wrap,
    _matching_parenthesis_index,
    _trim_trailing_blank_lines,
    qualified,
)

# Oracle has no `CREATE OR REPLACE ASSERTION`, so the file needs the same
# drop-before-create guard a materialized view and its log carry, and for the same
# second reason it takes no trailing `/`: an assertion is plain SQL, so SQLcl has
# already run the statement by the time a `/` would re-run the buffer against an
# object that cannot be created twice.
_SLASH_AFTER_CREATE = False

#: The `CHECK` keyword that opens the condition. Its parenthesis is the seam this
#: normalizer splits on: everything inside is the user's own SQL and is kept
#: verbatim, everything after it is dictionary-formatted option text.
_CHECK_KEYWORD = re.compile(r"\bCHECK\b", flags=re.IGNORECASE)


def normalize_assertion(
    lines: list[str],
    context: NormalizationContext,
) -> list[str]:
    """Render one `user_assertions.DEFINITION_SQL` as a replayable file (`#740`).

    `DEFINITION_SQL` is the only DDL source ADT has for this type -- `DBMS_METADATA`
    refuses it with `ORA-31600` -- and it is not shaped like anything `GET_DDL`
    hands over. Three differences, all measured on the live 26ai fixture:

    * it is always owner-qualified, where an exported file is schema-neutral unless
      `keep_owner` asks otherwise (the common pass above has already stripped it);
    * its option clauses (`ENABLE`, `NOT DEFERRABLE`, `INITIALLY IMMEDIATE`,
      `VALIDATE`) each carry a leading space no other exported type carries;
    * the condition between them is the author's own SQL, which is kept byte for
      byte, because reindenting a `NOT EXISTS` block would rewrite what someone
      wrote rather than what the dictionary formatted.
    """
    condition, options = _split_condition("\n".join(lines))
    kept = [f"CREATE ASSERTION {qualified(context.display_name, context)} CHECK"]
    kept.extend(condition.splitlines())
    kept.extend(stripped for line in options.splitlines() if (stripped := line.strip()))
    kept = _trim_trailing_blank_lines(kept)
    return _drop_create_wrap(
        kept,
        f"DROP ASSERTION {qualified(context.object_name.upper(), context)}",
        slash = _SLASH_AFTER_CREATE,
    )


def _split_condition(text: str) -> tuple[str, str]:
    """Split the `CHECK (...)` condition from the option clauses that follow it.

    Returns the whole text as the condition and no options when the shape is not
    the expected one, so an assertion Oracle spells differently exports as it came
    rather than losing half of itself to a regex that did not match.
    """
    keyword = _CHECK_KEYWORD.search(text)
    if keyword is None:
        return text, ""
    open_index = text.find("(", keyword.end())
    if open_index == -1:
        return text, ""
    close_index = _matching_parenthesis_index(text, open_index)
    if close_index is None:
        return text, ""
    return text[open_index : close_index + 1], text[close_index + 1 :]
