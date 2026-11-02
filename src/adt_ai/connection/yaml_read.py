"""How the connection editor reads a connection file before it edits one.

Split out of `connection/runner.py` (ADT #1095) to keep that module under the
context-file size guard: the safe-load gate, and a round-trip load that keeps a
repeated key's LAST value.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from ruamel.yaml import YAML
from ruamel.yaml.nodes import MappingNode, ScalarNode, SequenceNode

from adt_ai.connection.errors import ConnectionEditError
from adt_ai.shared.yaml_io import credential_safe_yaml_reason

_MERGE_TAG = "tag:yaml.org,2002:merge"


def safe_text(path: Path) -> str:
    """The file's text, refused when a safe loader would refuse it.

    The editor loads with ruamel's RoundTrip loader to preserve comments and key
    order on write, but RoundTrip is *not* a safe loader. A connection file
    authored elsewhere could carry a ``!!python/...`` (or other custom) tag. A
    safe-load pass first ensures the document is plain YAML; only then do we hand
    it to the round-tripper. (In practice ruamel's RoundTrip loader degrades such
    tags to plain data rather than executing them, so this is defense-in-depth,
    not an RCE patch, but it keeps unsupported documents out entirely.) A file
    saved in cp1250, or one this process cannot open, is refused on the same
    screen rather than as an UNEXPECTED ERROR traceback (#1094).
    """
    try:
        text = path.read_text(encoding="utf-8")
        probe = YAML(typ="safe")
        # A repeated key is not an error: every other command reads the file
        # through PyYAML, which keeps the last value, and `load_last_wins`
        # below makes the editor agree (#1095).
        probe.allow_duplicate_keys = True
        probe.load(text)
    except Exception as error:
        # Position only, and nothing chained: a parser's refusal quotes the
        # offending line, and in this file that line can be a `pwd:` (#958).
        raise ConnectionEditError(
            "UNSUPPORTED OR UNSAFE YAML IN CONNECTION FILE\n\n"
            f"{credential_safe_yaml_reason(error)}"
        ) from None
    return text


def _drop_earlier_duplicates(node: Any, seen: set[int]) -> None:
    """Keep only the LAST pair of every repeated key, in every mapping below."""
    if id(node) in seen:
        return
    seen.add(id(node))
    if isinstance(node, MappingNode):
        last: dict[tuple[Any, Any], int] = {}
        for index, (key, _) in enumerate(node.value):
            if isinstance(key, ScalarNode) and key.tag != _MERGE_TAG:
                last[(key.tag, key.value)] = index
        node.value = [
            pair for index, pair in enumerate(node.value)
            if not isinstance(pair[0], ScalarNode)
            or pair[0].tag == _MERGE_TAG
            or last[(pair[0].tag, pair[0].value)] == index
        ]
        for _, value in node.value:
            _drop_earlier_duplicates(value, seen)
    elif isinstance(node, SequenceNode):
        for item in node.value:
            _drop_earlier_duplicates(item, seen)


def load_last_wins(yaml: YAML, text: str) -> Any:
    """Round-trip load that resolves a repeated key the way PyYAML does.

    ruamel either refuses a repeated key or, with `allow_duplicate_keys`, keeps
    the FIRST value, while the loader every other command reads through keeps the
    LAST. The editor composes the document, drops each earlier pair of a repeated
    key, and only then builds the round-trip tree, so comments and order survive
    and the dumped file holds the key once with the value the loader would use
    (ADT #1095).
    """
    node = yaml.compose(text)
    if node is None:
        return None
    _drop_earlier_duplicates(node, set())
    return yaml.constructor.construct_document(node)
