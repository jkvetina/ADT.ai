"""Dictionary reads for the four 26ai object types `DBMS_METADATA` will not write.

`DOMAIN`, `PROPERTY GRAPH`, `MLE MODULE` and `MLE ENVIRONMENT` each carry their own
`user_objects.object_type` row, so discovery finds them the moment `config.yaml`
gives them a destination (`#738`). What none of them has is a `GET_DDL` handler:
measured on the live 26ai fixture (Oracle AI Database 23.26.3.0.0, SANDBOX,
2026-09-09) every one of the four answers

    ORA-31600: invalid input value <TYPE> for parameter OBJECT_TYPE in function GET_DDL

and `DBMS_DEVELOPER.GET_METADATA`, the other 23ai source, answers `ORA-03407:
Unknown object type` for all four as well. So the dictionary views below are not a
fallback, they are the only source, exactly as `user_assertions` is for an
ASSERTION (`#740`). `dictionary_ddl.py` assembles them into a statement.

Every query spells the object owner-qualified and double-quoted, the shape
`DBMS_METADATA` hands over, so the common normalizer pass lowercases the
identifiers and strips the owner for a schema-neutral file without any of these
types needing a rule of its own.
"""

from __future__ import annotations

# `TO_CLOB` accepts a BLOB on 26ai (measured against `user_mle_modules.MODULE`,
# which is a BLOB), so the module source needs neither a 32k `DBMS_LOB.SUBSTR`
# window nor a PL/SQL conversion block -- both of which would truncate or refuse a
# module larger than one VARCHAR2.
#
# `LANGUAGE` and `VERSION` are the only two clauses `CREATE MLE MODULE` takes
# besides the source itself, and the dictionary carries both.
MLE_MODULE_DDL_QUERY = """
SELECT 'CREATE OR REPLACE MLE MODULE "' || USER || '"."' || m.module_name || '"'
       || ' LANGUAGE ' || m.language_name
       || CASE
              WHEN m.version IS NOT NULL THEN ' VERSION ''' || m.version || ''''
              ELSE ''
          END
       || ' AS' || CHR(10)
       || TO_CLOB(m.module) AS ddl
FROM   user_mle_modules m
WHERE  m.module_name = :object_name
""".strip()

# The environment's own row. Its `user_objects` type is `MLE ENVIRONMENT` while the
# DDL keyword is `MLE ENV`, which is why the vocabulary carries both spellings and
# the rendered statement uses the second.
MLE_ENV_QUERY = """
SELECT e.env_name, e.language_options
FROM   user_mle_envs e
WHERE  e.env_name = :object_name
""".strip()

MLE_ENV_IMPORTS_QUERY = """
SELECT i.import_name, i.module_owner, i.module_name
FROM   user_mle_env_imports i
WHERE  i.env_name = :object_name
ORDER  BY i.import_name
""".strip()

DOMAIN_QUERY = """
SELECT d.name, d.type, d.data_display, d.data_order, d.selector
FROM   user_domains d
WHERE  d.name = :object_name
""".strip()

DOMAIN_COLUMNS_QUERY = """
SELECT c.column_name, c.data_type, c.data_length, c.data_precision, c.data_scale,
       c.char_col_decl_length, c.data_default, c.collation, c.discriminant
FROM   user_domain_cols c
WHERE  c.domain_name = :object_name
ORDER  BY c.column_id
""".strip()

DOMAIN_CONSTRAINTS_QUERY = """
SELECT k.name, k.search_condition
FROM   user_domain_constraints k
WHERE  k.domain_name = :object_name
AND    k.constraint_type = 'C'
ORDER  BY k.name
""".strip()

# A domain-level annotation carries no column name; a column one belongs to the
# table that uses the domain rather than to the domain, so it is not exported here.
DOMAIN_ANNOTATIONS_QUERY = """
SELECT a.annotation_name, a.annotation_value
FROM   user_annotations_usage a
WHERE  a.object_type = 'DOMAIN'
AND    a.object_name = :object_name
AND    a.column_name IS NULL
ORDER  BY a.annotation_name
""".strip()

PROPERTY_GRAPH_ELEMENTS_QUERY = """
SELECT e.element_name, e.element_kind, e.object_owner, e.object_name
FROM   user_pg_elements e
WHERE  e.graph_name = :object_name
ORDER  BY CASE e.element_kind WHEN 'VERTEX' THEN 0 ELSE 1 END, e.element_name
""".strip()

# `user_pg_keys` carries no ordering column, and a composite key that comes back
# in dictionary order would replay `KEY (dst, src)` for a `KEY (src, dst)` graph.
# The element's own table is where the order lives, so the key columns are ordered
# by `column_id` there -- the same order the CREATE statement was written in.
PROPERTY_GRAPH_KEYS_QUERY = """
SELECT k.element_name, k.column_name, c.column_id
FROM   user_pg_keys k
JOIN   user_pg_elements e
    ON e.graph_name = k.graph_name
   AND e.element_name = k.element_name
LEFT   JOIN user_tab_cols c
    ON c.table_name = e.object_name
   AND c.column_name = k.column_name
WHERE  k.graph_name = :object_name
ORDER  BY k.element_name, c.column_id, k.column_name
""".strip()

PROPERTY_GRAPH_LABELS_QUERY = """
SELECT l.element_name, l.label_name
FROM   user_pg_element_labels l
WHERE  l.graph_name = :object_name
ORDER  BY l.element_name, l.label_name
""".strip()

# `PROPERTY_ORDER` is the label's own property order, which is the order the
# PROPERTIES list was written in; the definition view says which column each one
# reads, and carries the expression instead when the property is computed.
PROPERTY_GRAPH_PROPERTIES_QUERY = """
SELECT d.element_name, d.property_name, d.column_name, d.column_expr, p.property_order
FROM   user_pg_prop_definitions d
LEFT   JOIN user_pg_element_labels l
    ON l.graph_name = d.graph_name
   AND l.element_name = d.element_name
LEFT   JOIN user_pg_label_properties p
    ON p.graph_name = d.graph_name
   AND p.label_name = l.label_name
   AND p.property_name = d.property_name
WHERE  d.graph_name = :object_name
ORDER  BY d.element_name, p.property_order, d.property_name
""".strip()

PROPERTY_GRAPH_EDGES_QUERY = """
SELECT r.edge_tab_name, r.vertex_tab_name, r.edge_end, r.edge_col_name, r.vertex_col_name
FROM   user_pg_edge_relationships r
WHERE  r.graph_name = :object_name
ORDER  BY r.edge_tab_name, CASE r.edge_end WHEN 'SOURCE' THEN 0 ELSE 1 END
""".strip()
