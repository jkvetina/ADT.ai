from __future__ import annotations

# `SET SERVEROUTPUT ON|OFF` in STARTUP.sql on the Python path. The directive is
# a client command the database never sees, so shared/startup.py runs the call
# SQL*Plus makes for it instead (ADT #923 moved both blocks into this home).
DBMS_OUTPUT_ENABLE_BLOCK = "BEGIN DBMS_OUTPUT.ENABLE(NULL); END;"
DBMS_OUTPUT_DISABLE_BLOCK = "BEGIN DBMS_OUTPUT.DISABLE; END;"
