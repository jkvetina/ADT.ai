"""Folder names an APEX export writes, spelled once (ADT #602).

Three packages ask about the same folder for three different reasons:
`export_apex` writes it, `validate` compiles what is inside it, and `patch`
decides that a file in it installs through an application import rather than
through SQLcl. Until this module the name lived as a constant in
`validate/files.py` and as a literal in `export_apex/files.py`, which is two
spellings of one folder and the shape ADT #474 made a rule about.
"""

from __future__ import annotations

# The whole-application APEXlang tree. Not a config key, unlike `path_apex` and
# its siblings: the exporter copies SQLcl's own layout verbatim under one root,
# so a project renaming this would be renaming somebody else's output.
APEXLANG_DIR = "apexlang"

__all__ = ["APEXLANG_DIR"]
