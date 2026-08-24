"""Rendering a configured path template into the folder names an export writes.

`path_objects` and `path_apex` are TEMPLATES, never folders: `<schema>` and
`<object_type>` stand in for values resolved per run. **The case of the token
decides the case of the value it renders**, so `<schema>/database/` writes
`app_owner/database/` and `<SCHEMA>/database/` writes `APP_OWNER/database/`.

Why the token carries the case instead of a config switch. Two keys hold
`<schema>`, so a switch is either global (and then the template no longer says
what it does) or duplicated per key; reading the case off the token keeps the
config line literally true, which is the one job a template has. It was asked
for by a customer migrating from old ADT, which substituted `{$INFO_SCHEMA}`
with whatever the config spelled and so left uppercase folders behind, while
ADT.ai hardcoded `.lower()` at three separate call sites and exported beside
them instead of into them (ADT #411).

Case is applied to a value ADT learned at RUNTIME, never to one the user typed.
A schema name is learned, from the data dictionary or a connection key or a
`-schema` argument, where `app_owner` is as likely as `APP_OWNER`, so no
spelling is authoritative and the template picks one. An `<object_type>` folder
is typed by the user in `object_types`, so recasing it would destroy a spelling
somebody chose; that token substitutes verbatim and `<OBJECT_TYPE>` is refused.

Normalizing happens HERE, at the display layer, never at the source. The same
schema name keys the SQLcl connection and names patch groups, so uppercasing it
at `Connections.resolve()` would move exported files on disk (ADT #240).
"""

from __future__ import annotations

import re
from collections.abc import Sequence

# The tokens a path template may carry. A CASED token renders the case its own
# spelling names; a VERBATIM one has exactly one spelling, because its value is
# already the user's own text.
CASED_TOKEN_NAMES    = ("schema",)
VERBATIM_TOKEN_NAMES = ("object_type",)
KNOWN_TOKEN_NAMES    = CASED_TOKEN_NAMES + VERBATIM_TOKEN_NAMES

# `apex_path_app` is the one key spelling its tokens the old-ADT `{$NAME}` way,
# and this is the whole vocabulary it has (ADT #474). It lives here rather than
# in either module because it has two readers who disagreed about it:
# `export_apex/files._render_app_folder` substituted these four by name while
# `patch/layout._apex_app_pattern` matched any `{$[A-Z_]+}`, so `patch` would
# parse a folder the export could never have written.
APEX_APP_TOKEN_NAMES = ("APP_ID", "APP_ALIAS", "APP_NAME", "APP_GROUP")

# What that key names when a project configures nothing. Beside the vocabulary
# for the same reason: `export_apex` writes the folder and `patch` reads it back,
# and a default spelled twice is a default that can disagree with itself.
DEFAULT_PATH_APP = "{$APP_ID}_{$APP_ALIAS}"

# Every angle-bracket run, whatever is inside it. Deliberately not a whitelist:
# the point is to SEE the tokens nothing resolves, so they can be reported
# rather than written to disk as a folder name.
_TOKEN_RE = re.compile(r"<[^<>]*>")

# The same posture for the curly dialect: see every `{$NAME}`, then judge it.
_CURLY_TOKEN_RE = re.compile(r"\{\$[^{}]*\}")


def contains_run(parts: Sequence[str], run: Sequence[str]) -> bool:
    """Whether `run` appears as a consecutive stretch of `parts`.

    A configured folder is a path, not a name, so `static/files` has to match
    those two components side by side and never a stray `files` somewhere else
    in the tree. `export_apex` reads its own static-files folder this way and
    `patch` has to recognise what that export wrote, so the walk lives here
    rather than once per reader (ADT #429).
    """
    stretch = tuple(run)
    if not stretch:
        return False
    walked = tuple(parts)
    return any(
        walked[start:start + len(stretch)] == stretch
        for start in range(len(walked) - len(stretch) + 1)
    )


def token_for(template: str, name: str) -> str | None:
    """The exact spelling `template` uses for the `name` token, or None."""
    for match in _TOKEN_RE.finditer(str(template)):
        if _token_name(match.group(0)) == name:
            return match.group(0)
    return None


def schema_token(template: str) -> str | None:
    """`<schema>`, `<SCHEMA>`, or None when the template pins no schema level."""
    return token_for(template, "schema")


def object_type_token(template: str) -> str | None:
    """`<object_type>`, or None when the per-type folder is appended instead."""
    return token_for(template, "object_type")


def is_schema_placeholder(segment: str) -> bool:
    """Is this one path segment the schema token, in either accepted spelling?"""
    return _token_name(segment) == "schema"


def render_cased(token: str, value: str) -> str:
    """`value` cased the way `token` is spelled: `<schema>` down, `<SCHEMA>` up."""
    return value.upper() if _token_body(token).isupper() else value.lower()


def render_path_template(
    template   : str,
    *,
    schema     : str | None = None,
    object_type: str | None = None,
) -> str:
    """`template` with every token it carries replaced by the value passed for it.

    A value of None leaves its token in place, which is how a caller renders the
    schema level while keeping `<object_type>` for a later step.
    """
    rendered = str(template)
    schema_placeholder = schema_token(rendered)
    if schema is not None and schema_placeholder is not None:
        rendered = rendered.replace(schema_placeholder, render_cased(schema_placeholder, schema))
    object_type_placeholder = object_type_token(rendered)
    if object_type is not None and object_type_placeholder is not None:
        rendered = rendered.replace(object_type_placeholder, object_type)
    return rendered


def unsupported_tokens(
    template: str,
    allowed : Sequence[str] = KNOWN_TOKEN_NAMES,
) -> list[str]:
    """Every `<token>` in `template` that no substitution will ever resolve.

    Three ways to earn a place here, and all three end the same way, as a real
    directory named after the token: an unknown name (`<NAME>`), a known name
    the caller does not substitute (`<object_type>` inside `path_apex`), and a
    known name spelled in a case that names nothing (`<Schema>`, or
    `<OBJECT_TYPE>` for a folder the user already spelled).
    """
    found: list[str] = []
    for match in _TOKEN_RE.finditer(str(template)):
        token = match.group(0)
        name = _token_name(token)
        resolved = name in allowed and _spelling_is_accepted(token, name)
        if not resolved and token not in found:
            found.append(token)
    return found


def unsupported_curly_tokens(template: str, allowed: Sequence[str]) -> list[str]:
    """Every `{$TOKEN}` in `template` that no substitution will ever resolve.

    The curly counterpart of `unsupported_tokens`, for the one key that spells
    its tokens this way. `allowed` is empty for every other key, which is what
    makes an old-ADT `{$INFO_SCHEMA}` in `path_objects` an error there and a
    legitimate `{$APP_ID}` in `apex_path_app` fine here.
    """
    found: list[str] = []
    for match in _CURLY_TOKEN_RE.finditer(str(template)):
        token = match.group(0)
        if token[2:-1] not in allowed and token not in found:
            found.append(token)
    return found


def supported_spelling(
    allowed      : Sequence[str] = KNOWN_TOKEN_NAMES,
    curly_allowed: Sequence[str] = (),
) -> str:
    """The accepted tokens, for an error message that names the way out."""
    spellings: list[str] = []
    for name in allowed:
        spellings.append(f"<{name}>")
        if name in CASED_TOKEN_NAMES:
            spellings.append(f"<{name.upper()}>")
    spellings.extend(f"{{${name}}}" for name in curly_allowed)
    if len(spellings) < 2:
        return "".join(spellings)
    return ", ".join(spellings[:-1]) + f" and {spellings[-1]}"


def _spelling_is_accepted(token: str, name: str) -> bool:
    body = _token_body(token)
    if name in CASED_TOKEN_NAMES:
        return body.islower() or body.isupper()
    return body.islower()


def _token_name(token: str) -> str:
    body = _token_body(token)
    return body.lower() if body else ""


def _token_body(token: str) -> str:
    text = str(token)
    if len(text) < 2 or not text.startswith("<") or not text.endswith(">"):
        return ""
    return text[1:-1]
