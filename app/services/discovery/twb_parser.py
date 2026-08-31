# # """
# # Parser for Tableau .twb workbook XML.

# # A .twb is a single XML document describing datasources (with their
# # columns/calculated fields and physical/logical table relationships),
# # worksheets, dashboards, parameters, filters and actions. This module
# # extracts everything into plain dicts that feed the normalization layer.
# # """

# # from __future__ import annotations

# # import re
# # import xml.etree.ElementTree as ET
# # from typing import Any

# # from app.utils.xml_helpers import (
# #     attr_or_default,
# #     is_custom_sql,
# #     is_lod_expression,
# #     is_table_calculation,
# #     strip_bracket,
# #     text_or_default,
# # )


# # class TwbParser:
# #     """Parses a single .twb XML document (as bytes or str)."""

# #     def __init__(self, xml_content: bytes | str):
# #         if isinstance(xml_content, bytes):
# #             xml_content = xml_content.decode("utf-8", errors="ignore")
# #         self.root = ET.fromstring(xml_content)

# #     # ------------------------------------------------------------------
# #     # Datasources / data model
# #     # ------------------------------------------------------------------

# #     def parse_datasources(self) -> list[dict[str, Any]]:
# #         """Parse the workbook's REAL datasource definitions.

# #         IMPORTANT: this must only look at the top-level ``/workbook/datasources/datasource``
# #         elements. Every ``<worksheet>`` in a TWB also carries its own lightweight
# #         ``<view><datasources><datasource>`` reference block (name/caption only, no
# #         columns) declaring which datasources that sheet depends on. A deep
# #         (``.//``) search matches those too and produces one duplicate "datasource"
# #         per worksheet that references it. Scoping to the direct child of the
# #         workbook root avoids that, and we additionally dedupe by name as a
# #         defensive second layer in case a workbook nests real definitions
# #         (e.g. inside a dashboard's stored copy of a datasource in some TWB
# #         variants).
# #         """
# #         datasources = []
# #         seen_names: set[str] = set()

# #         top_level_container = self.root.find("./datasources")
# #         candidates = top_level_container.findall("./datasource") if top_level_container is not None else []

# #         for ds in candidates:
# #             name = attr_or_default(ds, "name")

# #             # Skip the synthetic "Parameters" pseudo-datasource here; it's
# #             # handled separately by parse_parameters().
# #             if name == "Parameters" or not name:
# #                 continue

# #             # A worksheet-scoped reference block has no <column> or <connection>
# #             # children -- it's just a name/caption pointer. Real definitions
# #             # always carry at least one of these. Skip empty pointer blocks
# #             # that slipped through even at this scope.
# #             has_columns = ds.find("./column") is not None
# #             has_connection = ds.find(".//connection") is not None
# #             if not has_columns and not has_connection:
# #                 continue

# #             if name in seen_names:
# #                 continue
# #             seen_names.add(name)

# #             caption = attr_or_default(ds, "caption", name)

# #             connections = self._parse_connections(ds)
# #             relations, joins, custom_sql = self._parse_relations(ds)
# #             columns, calculated_fields, formulas, data_types = self._parse_columns(ds)
# #             relationship_joins = self._parse_object_graph_relationships(ds, relations)

# #             # Prefer the modern "Relationships" (object-graph) model when
# #             # present; fall back to legacy <relation type="join"> parsing
# #             # for older workbooks built with physical joins.
# #             all_joins = relationship_joins if relationship_joins else joins

# #             datasources.append(
# #                 {
# #                     "name": name,
# #                     "caption": caption,
# #                     "version": attr_or_default(ds, "version"),
# #                     "is_embedded": True,
# #                     "connections": connections,
# #                     "tables": relations,
# #                     "joins": all_joins,
# #                     "custom_sql": custom_sql,
# #                     "columns": columns,
# #                     "calculated_fields": calculated_fields,
# #                     "formulas": formulas,
# #                     "data_types": data_types,
# #                 }
# #             )
# #         return datasources

# #     # Tableau disambiguates a field name that exists on more than one
# #     # joined logical table by appending " (TableName)" to it, e.g. a
# #     # `date_key` column on both the fact table and "Dim_Date" becomes
# #     # `date_key (Dim_Date)` for the Dim_Date copy. This is the ACTUAL
# #     # shape relationship expressions reference fields by (verified
# #     # against real Tableau output) -- NOT an "[ObjectId].[Field]" form.
# #     _FIELD_TABLE_SUFFIX = re.compile(r"^(?P<field>.+)\s\((?P<table>[^)]+)\)$")

# #     # Relationship comparisons aren't always equality -- Tableau also
# #     # supports >=, <=, >, <, <> for relationship conditions.
# #     _COMPARISON_OPERATORS = {"=", "==", "!=", "<>", ">", "<", ">=", "<="}

# #     def _parse_object_graph_relationships(
# #         self, ds_element: ET.Element, tables: list[dict[str, Any]]
# #     ) -> list[dict[str, Any]]:
# #         """Parse joins from Tableau's modern "Relationships" data model.

# #         Since Tableau 2020.2, datasources built with the Relationships
# #         canvas store their logical-table linkage in
# #         ``<datasource><object-graph><relationships><relationship>`` rather
# #         than the legacy ``<relation type="join">`` XML.

# #         Each ``<relationship>`` wraps one or more comparison expressions
# #         (``=``, ``>=``, etc.) whose two leaf operands are bracketed field
# #         references, e.g. ``[date_key]`` or ``[date_key (Dim_Date)]``. When
# #         two joined tables both have a same-named column, Tableau appends
# #         `` (TableName)`` to disambiguate -- that suffix is what actually
# #         tells us which table a given operand belongs to; there is no
# #         object-id indirection to resolve through in this form.

# #         An operand with NO suffix (e.g. plain ``[date_key]``) belongs to
# #         whichever table doesn't need disambiguating -- typically the base
# #         / fact table the relationship canvas was built around, since its
# #         columns only need the "(Table)" suffix once a second table joins
# #         in and introduces a name collision. We infer that base table once
# #         all relationships have been scanned: it's whichever of this
# #         datasource's tables never appears as an explicit suffix.

# #         A composite (multi-column) key wraps several comparison pairs in
# #         an ``<expression op="AND">``; we walk the tree recursively so both
# #         single- and multi-column keys resolve correctly.

# #         Older workbooks won't have an ``<object-graph>`` at all, in which
# #         case this returns an empty list and the caller falls back to the
# #         legacy join parser.
# #         """
# #         raw_pairs: list[tuple[str, str, str]] = []  # (operator, left_ref, right_ref)

# #         for relationship in ds_element.findall(".//object-graph/relationships/relationship"):
# #             top_expression = relationship.find("./expression")
# #             if top_expression is None:
# #                 continue
# #             raw_pairs.extend(self._collect_comparison_pairs(top_expression))

# #         if not raw_pairs:
# #             return []

# #         # First pass: resolve every operand we can identify a table for
# #         # directly (via the object-graph id map, or via the "(Table)"
# #         # suffix), and track which of this datasource's tables got
# #         # explicitly named that way.
# #         object_table_map = self._build_object_table_map(ds_element)
# #         resolved: list[tuple[str, tuple[str, str], tuple[str, str]]] = []
# #         mentioned_tables: set[str] = set()

# #         for operator, left_ref, right_ref in raw_pairs:
# #             left = self._resolve_relationship_operand(left_ref, object_table_map)
# #             right = self._resolve_relationship_operand(right_ref, object_table_map)
# #             mentioned_tables.update(t for t, _ in (left, right) if t)
# #             resolved.append((operator, left, right))

# #         # Second pass: any operand left without a table (bare field, no
# #         # suffix, no object-id match) belongs to the one table in this
# #         # datasource that was never explicitly mentioned as a suffix --
# #         # i.e. the base/fact table. Only apply this when it's unambiguous
# #         # (exactly one candidate); otherwise leave it blank rather than
# #         # guessing wrong.
# #         known_table_names = {t.get("name", "") for t in tables if t.get("name")}
# #         unmentioned = known_table_names - mentioned_tables
# #         base_table = next(iter(unmentioned)) if len(unmentioned) == 1 else ""

# #         joins: list[dict[str, Any]] = []
# #         for operator, (left_table, left_field), (right_table, right_field) in resolved:
# #             left_table = left_table or base_table
# #             right_table = right_table or base_table

# #             joins.append(
# #                 {
# #                     "join_type": "relationship",
# #                     "operator": operator,
# #                     "left_table": left_table,
# #                     "left_column": left_field,
# #                     "right_table": right_table,
# #                     "right_column": right_field,
# #                     # Kept for backward compatibility with any existing
# #                     # consumers reading the flat key list.
# #                     "join_keys": [f for f in (left_field, right_field) if f],
# #                 }
# #             )

# #         return joins

# #     def _build_object_table_map(self, ds_element: ET.Element) -> dict[str, str]:
# #         """Maps each logical-table object's id AND caption to its
# #         underlying physical table name, by following
# #         ``<object><properties><relation>`` down to the actual table
# #         reference. This covers TWB variants that DO reference joined
# #         objects by id (``[ObjectId].[Field]``) rather than by the
# #         field-name-suffix form; harmless no-op when a workbook doesn't use
# #         that form, since nothing will match against this map."""

# #         object_table_map: dict[str, str] = {}

# #         for obj in ds_element.findall(".//object-graph/objects/object"):
# #             obj_id = attr_or_default(obj, "id")
# #             caption = attr_or_default(obj, "caption") or obj_id

# #             relation = obj.find("./properties/relation")
# #             table_name = ""
# #             if relation is not None:
# #                 table_name = attr_or_default(relation, "table") or attr_or_default(relation, "name")

# #             resolved = table_name or caption or obj_id

# #             if obj_id:
# #                 object_table_map[obj_id] = resolved
# #             if caption:
# #                 object_table_map[caption] = resolved

# #         return object_table_map

# #     def _collect_comparison_pairs(self, expression: ET.Element) -> list[tuple[str, str, str]]:
# #         """Recursively walks a relationship's <expression> tree and
# #         returns every leaf comparison as (operator, left_ref, right_ref).

# #         A simple relationship is ``<expression op="="><expression op="[a]"/>
# #         <expression op="[b]"/></expression>``. A composite key wraps
# #         multiple such pairs in ``<expression op="AND">``. We don't assume
# #         a fixed shape or that the operator is always "=" -- any node whose
# #         op is a recognized comparison operator with exactly two childless
# #         (leaf) children is treated as one pair; anything else (AND/OR
# #         wrappers, deeper nesting) is descended into further.
# #         """
# #         op = attr_or_default(expression, "op")
# #         children = expression.findall("./expression")

# #         is_leaf_comparison = (
# #             op in self._COMPARISON_OPERATORS
# #             and len(children) == 2
# #             and not children[0].findall("./expression")
# #             and not children[1].findall("./expression")
# #         )

# #         if is_leaf_comparison:
# #             return [(op, attr_or_default(children[0], "op"), attr_or_default(children[1], "op"))]

# #         pairs: list[tuple[str, str, str]] = []
# #         for child in children:
# #             pairs.extend(self._collect_comparison_pairs(child))
# #         return pairs

# #     @classmethod
# #     def _resolve_relationship_operand(
# #         cls, ref: str, object_table_map: dict[str, str]
# #     ) -> tuple[str, str]:
# #         """Resolves one relationship operand to (table_name, field_name).

# #         Tries, in order:
# #           1. ``[ObjectId].[Field]`` -- an object-graph id/caption prefix,
# #              used by some Tableau export variants.
# #           2. ``[Field (Table)]`` -- Tableau's own disambiguation suffix,
# #              which is the form actually seen in real relationship-model
# #              TWB exports and directly names the table.
# #           3. ``[Field]`` with no suffix and no object match: table is left
# #              blank here and resolved by the caller once every operand in
# #              this datasource has been scanned (see the base-table
# #              inference in _parse_object_graph_relationships).
# #         """
# #         two_part = re.match(r"^\[([^\]]+)\]\.\[([^\]]+)\]$", ref or "")
# #         if two_part:
# #             obj_key, field_name = two_part.group(1), two_part.group(2)
# #             if obj_key in object_table_map:
# #                 return object_table_map[obj_key], field_name

# #         bare = re.match(r"^\[([^\]]+)\]$", ref or "")
# #         if not bare:
# #             return "", ""

# #         content = bare.group(1)
# #         suffix_match = cls._FIELD_TABLE_SUFFIX.match(content)
# #         if suffix_match:
# #             return suffix_match.group("table"), suffix_match.group("field")

# #         return "", content

# #     def _parse_connections(self, ds_element: ET.Element) -> list[dict[str, Any]]:
# #         connections = []
# #         for conn in ds_element.findall(".//connection"):
# #             connections.append(
# #                 {
# #                     "class": attr_or_default(conn, "class"),
# #                     "server": attr_or_default(conn, "server"),
# #                     "port": attr_or_default(conn, "port"),
# #                     "database": attr_or_default(conn, "dbname") or attr_or_default(conn, "database"),
# #                     "schema": attr_or_default(conn, "schema"),
# #                     "authentication": attr_or_default(conn, "authentication"),
# #                 }
# #             )
# #         return connections

# #     def _parse_relations(self, ds_element: ET.Element) -> tuple[list[dict], list[dict], list[dict]]:
# #         tables: list[dict[str, Any]] = []
# #         joins: list[dict[str, Any]] = []
# #         custom_sql: list[dict[str, Any]] = []
# #         seen_table_refs: set[str] = set()

# #         # Scope to relations nested under <connection> only. A deep
# #         # ".//relation" search from the datasource root also matches the
# #         # <object-graph><objects><object><properties><relation> pointers
# #         # used by the modern Relationships model -- those describe the
# #         # SAME physical tables the connection already lists, just indexed
# #         # by logical-object id for join resolution (see
# #         # _build_object_table_map). Including them here would just
# #         # duplicate every table/join entry.
# #         for relation in ds_element.findall(".//connection//relation"):
# #             rel_type = attr_or_default(relation, "type")

# #             if rel_type == "text":
# #                 custom_sql.append(
# #                     {
# #                         "name": attr_or_default(relation, "name"),
# #                         "query": text_or_default(relation),
# #                     }
# #                 )
# #             elif rel_type == "join":
# #                 join_type = attr_or_default(relation, "join", "inner")
# #                 clause = relation.find("./clause")

# #                 # Reuse the same recursive comparison-pair walker used for
# #                 # the modern Relationships model -- legacy join clauses
# #                 # have the identical <expression op="="> / op="AND" shape,
# #                 # just without the object-graph indirection. This also
# #                 # fixes the old approach of iterating every <expression>
# #                 # under the clause and appending both its own op AND its
# #                 # children's ops, which produced duplicated, noisy
# #                 # join_keys (e.g. the join operator itself ending up in
# #                 # the list alongside each operand, twice).
# #                 join_keys: list[str] = []
# #                 operator = "="
# #                 if clause is not None:
# #                     top_expr = clause.find("./expression")
# #                     if top_expr is not None:
# #                         for pair_operator, left_ref, right_ref in self._collect_comparison_pairs(top_expr):
# #                             operator = pair_operator
# #                             join_keys.extend([ref for ref in (left_ref, right_ref) if ref])

# #                 child_relations = relation.findall("./relation")
# #                 left = attr_or_default(child_relations[0], "name") if len(child_relations) > 0 else ""
# #                 right = attr_or_default(child_relations[1], "name") if len(child_relations) > 1 else ""

# #                 joins.append(
# #                     {
# #                         "join_type": join_type,
# #                         "operator": operator,
# #                         "left_table": left,
# #                         "right_table": right,
# #                         "join_keys": join_keys,
# #                     }
# #                 )
# #             elif rel_type == "table":
# #                 table_ref = attr_or_default(relation, "table")
# #                 dedupe_key = table_ref or attr_or_default(relation, "name")
# #                 if dedupe_key in seen_table_refs:
# #                     continue
# #                 seen_table_refs.add(dedupe_key)

# #                 tables.append(
# #                     {
# #                         "name": attr_or_default(relation, "name"),
# #                         "table": table_ref,
# #                         "connection": attr_or_default(relation, "connection"),
# #                     }
# #                 )

# #         return tables, joins, custom_sql

# #     def _parse_columns(
# #         self, ds_element: ET.Element
# #     ) -> tuple[list[dict], list[dict], list[dict], list[dict]]:
# #         columns: list[dict[str, Any]] = []
# #         calculated_fields: list[dict[str, Any]] = []
# #         formulas: list[dict[str, Any]] = []
# #         data_types: list[dict[str, Any]] = []

# #         for column in ds_element.findall("./column"):
# #             name = strip_bracket(attr_or_default(column, "name"))
# #             caption = attr_or_default(column, "caption", name)
# #             role = attr_or_default(column, "role")  # dimension | measure
# #             data_type = attr_or_default(column, "datatype")
# #             default_agg = attr_or_default(column, "default-aggregation")

# #             calc_element = column.find("./calculation")
# #             formula = ""
# #             is_calculated = calc_element is not None

# #             if is_calculated:
# #                 formula = attr_or_default(calc_element, "formula")

# #             field_entry = {
# #                 "name": name,
# #                 "caption": caption,
# #                 "role": role,
# #                 "data_type": data_type,
# #                 "default_aggregation": default_agg,
# #                 "is_calculated": is_calculated,
# #             }

# #             data_types.append({"field": name, "data_type": data_type})
# #             columns.append(field_entry)

# #             if is_calculated:
# #                 classification = "lod_expression" if is_lod_expression(formula) else (
# #                     "table_calculation" if is_table_calculation(formula) else "standard"
# #                 )
# #                 calc_entry = {
# #                     **field_entry,
# #                     "formula": formula,
# #                     "classification": classification,
# #                 }
# #                 calculated_fields.append(calc_entry)
# #                 formulas.append({"field": name, "formula": formula, "classification": classification})

# #         return columns, calculated_fields, formulas, data_types

# #     # ------------------------------------------------------------------
# #     # Worksheets / Dashboards / Components
# #     # ------------------------------------------------------------------

# #     def parse_worksheets(self) -> list[dict[str, Any]]:
# #         worksheets = []
# #         for ws in self.root.findall(".//worksheets/worksheet"):
# #             name = attr_or_default(ws, "name")
# #             datasource_deps = [
# #                 attr_or_default(dep, "datasource")
# #                 for dep in ws.findall(".//datasource-dependencies")
# #             ]
# #             fields_used = [
# #                 strip_bracket(attr_or_default(col, "name"))
# #                 for col in ws.findall(".//datasource-dependencies/column")
# #             ]
# #             filters = [
# #                 strip_bracket(attr_or_default(f, "column"))
# #                 for f in ws.findall(".//filter")
# #             ]

# #             # Tableau's mark type (Bar, Line, Circle, Pie, Text, Area,
# #             # Square, Automatic, ...) is the correct signal for choosing a
# #             # comparable Power BI visual type during migration -- much
# #             # more reliable than guessing from field counts.
# #             mark_element = ws.find(".//panes/pane/mark")
# #             mark_type = attr_or_default(mark_element, "class", "Automatic")

# #             worksheets.append(
# #                 {
# #                     "name": name,
# #                     "datasources": [d for d in datasource_deps if d],
# #                     "fields_used": fields_used,
# #                     "filters": filters,
# #                     "mark_type": mark_type,
# #                 }
# #             )
# #         return worksheets

# #     def parse_dashboards(self) -> list[dict[str, Any]]:
# #         dashboards = []
# #         for dash in self.root.findall(".//dashboards/dashboard"):
# #             name = attr_or_default(dash, "name")
# #             zones = dash.findall(".//zone[@name]")
# #             worksheets = sorted({attr_or_default(z, "name") for z in zones if attr_or_default(z, "name")})

# #             actions = []
# #             for action in self.root.findall(f".//actions/action"):
# #                 actions.append(
# #                     {
# #                         "name": attr_or_default(action, "caption") or attr_or_default(action, "name"),
# #                     }
# #                 )

# #             dashboards.append(
# #                 {
# #                     "name": name,
# #                     "worksheets": worksheets,
# #                 }
# #             )
# #         return dashboards

# #     def parse_parameters(self) -> list[dict[str, Any]]:
# #         parameters = []
# #         for ds in self.root.findall(".//datasources/datasource[@name='Parameters']"):
# #             for column in ds.findall("./column"):
# #                 parameters.append(
# #                     {
# #                         "name": strip_bracket(attr_or_default(column, "name")),
# #                         "caption": attr_or_default(column, "caption"),
# #                         "data_type": attr_or_default(column, "datatype"),
# #                         "current_value": attr_or_default(column, "value"),
# #                     }
# #                 )
# #         return parameters

# #     def parse_filters(self) -> list[dict[str, Any]]:
# #         filters = []
# #         for f in self.root.findall(".//worksheets/worksheet//filter"):
# #             filters.append(
# #                 {
# #                     "column": strip_bracket(attr_or_default(f, "column")),
# #                     "class": attr_or_default(f, "class"),
# #                 }
# #             )
# #         return filters

# #     def parse_actions(self) -> list[dict[str, Any]]:
# #         actions = []
# #         for action in self.root.findall(".//actions/action"):
# #             actions.append(
# #                 {
# #                     "name": attr_or_default(action, "caption") or attr_or_default(action, "name"),
# #                 }
# #             )
# #         return actions

# #     # ------------------------------------------------------------------
# #     # Aggregate
# #     # ------------------------------------------------------------------

# #     def parse_all(self) -> dict[str, Any]:
# #         return {
# #             "datasources": self.parse_datasources(),
# #             "worksheets": self.parse_worksheets(),
# #             "dashboards": self.parse_dashboards(),
# #             "parameters": self.parse_parameters(),
# #             "filters": self.parse_filters(),
# #             "actions": self.parse_actions(),
# #         }

# """
# Parser for Tableau .twb workbook XML.

# A .twb is a single XML document describing datasources (with their
# columns/calculated fields and physical/logical table relationships),
# worksheets, dashboards, parameters, filters and actions. This module
# extracts everything into plain dicts that feed the normalization layer.
# """

# from __future__ import annotations

# import re
# import xml.etree.ElementTree as ET
# from typing import Any

# from app.utils.xml_helpers import (
#     attr_or_default,
#     clean_field_reference,
#     is_custom_sql,
#     is_lod_expression,
#     is_table_calculation,
#     text_or_default,
# )


# class TwbParser:
#     """Parses a single .twb XML document (as bytes or str)."""

#     def __init__(self, xml_content: bytes | str):
#         if isinstance(xml_content, bytes):
#             xml_content = xml_content.decode("utf-8", errors="ignore")
#         self.root = ET.fromstring(xml_content)

#     # ------------------------------------------------------------------
#     # Datasources / data model
#     # ------------------------------------------------------------------

#     def parse_datasources(self) -> list[dict[str, Any]]:
#         """Parse the workbook's REAL datasource definitions.

#         IMPORTANT: this must only look at the top-level ``/workbook/datasources/datasource``
#         elements. Every ``<worksheet>`` in a TWB also carries its own lightweight
#         ``<view><datasources><datasource>`` reference block (name/caption only, no
#         columns) declaring which datasources that sheet depends on. A deep
#         (``.//``) search matches those too and produces one duplicate "datasource"
#         per worksheet that references it. Scoping to the direct child of the
#         workbook root avoids that, and we additionally dedupe by name as a
#         defensive second layer in case a workbook nests real definitions
#         (e.g. inside a dashboard's stored copy of a datasource in some TWB
#         variants).
#         """
#         datasources = []
#         seen_names: set[str] = set()

#         top_level_container = self.root.find("./datasources")
#         candidates = top_level_container.findall("./datasource") if top_level_container is not None else []

#         for ds in candidates:
#             name = attr_or_default(ds, "name")

#             # Skip the synthetic "Parameters" pseudo-datasource here; it's
#             # handled separately by parse_parameters().
#             if name == "Parameters" or not name:
#                 continue

#             # A worksheet-scoped reference block has no <column> or <connection>
#             # children -- it's just a name/caption pointer. Real definitions
#             # always carry at least one of these. Skip empty pointer blocks
#             # that slipped through even at this scope.
#             has_columns = ds.find("./column") is not None
#             has_connection = ds.find(".//connection") is not None
#             if not has_columns and not has_connection:
#                 continue

#             if name in seen_names:
#                 continue
#             seen_names.add(name)

#             caption = attr_or_default(ds, "caption", name)

#             connections = self._parse_connections(ds)
#             relations, joins, custom_sql = self._parse_relations(ds)
#             columns, calculated_fields, formulas, data_types = self._parse_columns(ds)
#             relationship_joins = self._parse_object_graph_relationships(ds, relations)

#             # Prefer the modern "Relationships" (object-graph) model when
#             # present; fall back to legacy <relation type="join"> parsing
#             # for older workbooks built with physical joins.
#             all_joins = relationship_joins if relationship_joins else joins

#             datasources.append(
#                 {
#                     "name": name,
#                     "caption": caption,
#                     "version": attr_or_default(ds, "version"),
#                     "is_embedded": True,
#                     "connections": connections,
#                     "tables": relations,
#                     "joins": all_joins,
#                     "custom_sql": custom_sql,
#                     "columns": columns,
#                     "calculated_fields": calculated_fields,
#                     "formulas": formulas,
#                     "data_types": data_types,
#                 }
#             )
#         return datasources

#     # Tableau disambiguates a field name that exists on more than one
#     # joined logical table by appending " (TableName)" to it, e.g. a
#     # `date_key` column on both the fact table and "Dim_Date" becomes
#     # `date_key (Dim_Date)` for the Dim_Date copy. This is the ACTUAL
#     # shape relationship expressions reference fields by (verified
#     # against real Tableau output) -- NOT an "[ObjectId].[Field]" form.
#     _FIELD_TABLE_SUFFIX = re.compile(r"^(?P<field>.+)\s\((?P<table>[^)]+)\)$")

#     # Relationship comparisons aren't always equality -- Tableau also
#     # supports >=, <=, >, <, <> for relationship conditions.
#     _COMPARISON_OPERATORS = {"=", "==", "!=", "<>", ">", "<", ">=", "<="}

#     def _parse_object_graph_relationships(
#         self, ds_element: ET.Element, tables: list[dict[str, Any]]
#     ) -> list[dict[str, Any]]:
#         """Parse joins from Tableau's modern "Relationships" data model.

#         Since Tableau 2020.2, datasources built with the Relationships
#         canvas store their logical-table linkage in
#         ``<datasource><object-graph><relationships><relationship>`` rather
#         than the legacy ``<relation type="join">`` XML.

#         Each ``<relationship>`` wraps one or more comparison expressions
#         (``=``, ``>=``, etc.) whose two leaf operands are bracketed field
#         references, e.g. ``[date_key]`` or ``[date_key (Dim_Date)]``. When
#         two joined tables both have a same-named column, Tableau appends
#         `` (TableName)`` to disambiguate -- that suffix is what actually
#         tells us which table a given operand belongs to; there is no
#         object-id indirection to resolve through in this form.

#         An operand with NO suffix (e.g. plain ``[date_key]``) belongs to
#         whichever table doesn't need disambiguating -- typically the base
#         / fact table the relationship canvas was built around, since its
#         columns only need the "(Table)" suffix once a second table joins
#         in and introduces a name collision. We infer that base table once
#         all relationships have been scanned: it's whichever of this
#         datasource's tables never appears as an explicit suffix.

#         A composite (multi-column) key wraps several comparison pairs in
#         an ``<expression op="AND">``; we walk the tree recursively so both
#         single- and multi-column keys resolve correctly.

#         Older workbooks won't have an ``<object-graph>`` at all, in which
#         case this returns an empty list and the caller falls back to the
#         legacy join parser.
#         """
#         raw_pairs: list[tuple[str, str, str]] = []  # (operator, left_ref, right_ref)

#         for relationship in ds_element.findall(".//object-graph/relationships/relationship"):
#             top_expression = relationship.find("./expression")
#             if top_expression is None:
#                 continue
#             raw_pairs.extend(self._collect_comparison_pairs(top_expression))

#         if not raw_pairs:
#             return []

#         # First pass: resolve every operand we can identify a table for
#         # directly (via the object-graph id map, or via the "(Table)"
#         # suffix), and track which of this datasource's tables got
#         # explicitly named that way.
#         object_table_map = self._build_object_table_map(ds_element)
#         resolved: list[tuple[str, tuple[str, str], tuple[str, str]]] = []
#         mentioned_tables: set[str] = set()

#         for operator, left_ref, right_ref in raw_pairs:
#             left = self._resolve_relationship_operand(left_ref, object_table_map)
#             right = self._resolve_relationship_operand(right_ref, object_table_map)
#             mentioned_tables.update(t for t, _ in (left, right) if t)
#             resolved.append((operator, left, right))

#         # Second pass: any operand left without a table (bare field, no
#         # suffix, no object-id match) belongs to the one table in this
#         # datasource that was never explicitly mentioned as a suffix --
#         # i.e. the base/fact table. Only apply this when it's unambiguous
#         # (exactly one candidate); otherwise leave it blank rather than
#         # guessing wrong.
#         known_table_names = {t.get("name", "") for t in tables if t.get("name")}
#         unmentioned = known_table_names - mentioned_tables
#         base_table = next(iter(unmentioned)) if len(unmentioned) == 1 else ""

#         joins: list[dict[str, Any]] = []
#         for operator, (left_table, left_field), (right_table, right_field) in resolved:
#             left_table = left_table or base_table
#             right_table = right_table or base_table

#             joins.append(
#                 {
#                     "join_type": "relationship",
#                     "operator": operator,
#                     "left_table": left_table,
#                     "left_column": left_field,
#                     "right_table": right_table,
#                     "right_column": right_field,
#                     # Kept for backward compatibility with any existing
#                     # consumers reading the flat key list.
#                     "join_keys": [f for f in (left_field, right_field) if f],
#                 }
#             )

#         return joins

#     def _build_object_table_map(self, ds_element: ET.Element) -> dict[str, str]:
#         """Maps each logical-table object's id AND caption to its
#         underlying physical table name, by following
#         ``<object><properties><relation>`` down to the actual table
#         reference. This covers TWB variants that DO reference joined
#         objects by id (``[ObjectId].[Field]``) rather than by the
#         field-name-suffix form; harmless no-op when a workbook doesn't use
#         that form, since nothing will match against this map."""

#         object_table_map: dict[str, str] = {}

#         for obj in ds_element.findall(".//object-graph/objects/object"):
#             obj_id = attr_or_default(obj, "id")
#             caption = attr_or_default(obj, "caption") or obj_id

#             relation = obj.find("./properties/relation")
#             table_name = ""
#             if relation is not None:
#                 table_name = attr_or_default(relation, "table") or attr_or_default(relation, "name")

#             resolved = table_name or caption or obj_id

#             if obj_id:
#                 object_table_map[obj_id] = resolved
#             if caption:
#                 object_table_map[caption] = resolved

#         return object_table_map

#     def _collect_comparison_pairs(self, expression: ET.Element) -> list[tuple[str, str, str]]:
#         """Recursively walks a relationship's <expression> tree and
#         returns every leaf comparison as (operator, left_ref, right_ref).

#         A simple relationship is ``<expression op="="><expression op="[a]"/>
#         <expression op="[b]"/></expression>``. A composite key wraps
#         multiple such pairs in ``<expression op="AND">``. We don't assume
#         a fixed shape or that the operator is always "=" -- any node whose
#         op is a recognized comparison operator with exactly two childless
#         (leaf) children is treated as one pair; anything else (AND/OR
#         wrappers, deeper nesting) is descended into further.
#         """
#         op = attr_or_default(expression, "op")
#         children = expression.findall("./expression")

#         is_leaf_comparison = (
#             op in self._COMPARISON_OPERATORS
#             and len(children) == 2
#             and not children[0].findall("./expression")
#             and not children[1].findall("./expression")
#         )

#         if is_leaf_comparison:
#             return [(op, attr_or_default(children[0], "op"), attr_or_default(children[1], "op"))]

#         pairs: list[tuple[str, str, str]] = []
#         for child in children:
#             pairs.extend(self._collect_comparison_pairs(child))
#         return pairs

#     @classmethod
#     def _resolve_relationship_operand(
#         cls, ref: str, object_table_map: dict[str, str]
#     ) -> tuple[str, str]:
#         """Resolves one relationship operand to (table_name, field_name).

#         Tries, in order:
#           1. ``[ObjectId].[Field]`` -- an object-graph id/caption prefix,
#              used by some Tableau export variants.
#           2. ``[Field (Table)]`` -- Tableau's own disambiguation suffix,
#              which is the form actually seen in real relationship-model
#              TWB exports and directly names the table.
#           3. ``[Field]`` with no suffix and no object match: table is left
#              blank here and resolved by the caller once every operand in
#              this datasource has been scanned (see the base-table
#              inference in _parse_object_graph_relationships).
#         """
#         two_part = re.match(r"^\[([^\]]+)\]\.\[([^\]]+)\]$", ref or "")
#         if two_part:
#             obj_key, field_name = two_part.group(1), two_part.group(2)
#             if obj_key in object_table_map:
#                 return object_table_map[obj_key], field_name

#         bare = re.match(r"^\[([^\]]+)\]$", ref or "")
#         if not bare:
#             return "", ""

#         content = bare.group(1)
#         suffix_match = cls._FIELD_TABLE_SUFFIX.match(content)
#         if suffix_match:
#             return suffix_match.group("table"), suffix_match.group("field")

#         return "", content

#     def _parse_connections(self, ds_element: ET.Element) -> list[dict[str, Any]]:
#         connections = []
#         for conn in ds_element.findall(".//connection"):
#             connections.append(
#                 {
#                     "class": attr_or_default(conn, "class"),
#                     "server": attr_or_default(conn, "server"),
#                     "port": attr_or_default(conn, "port"),
#                     "database": attr_or_default(conn, "dbname") or attr_or_default(conn, "database"),
#                     "schema": attr_or_default(conn, "schema"),
#                     "authentication": attr_or_default(conn, "authentication"),
#                 }
#             )
#         return connections

#     def _parse_relations(self, ds_element: ET.Element) -> tuple[list[dict], list[dict], list[dict]]:
#         tables: list[dict[str, Any]] = []
#         joins: list[dict[str, Any]] = []
#         custom_sql: list[dict[str, Any]] = []
#         seen_table_refs: set[str] = set()

#         # Scope to relations nested under <connection> only. A deep
#         # ".//relation" search from the datasource root also matches the
#         # <object-graph><objects><object><properties><relation> pointers
#         # used by the modern Relationships model -- those describe the
#         # SAME physical tables the connection already lists, just indexed
#         # by logical-object id for join resolution (see
#         # _build_object_table_map). Including them here would just
#         # duplicate every table/join entry.
#         for relation in ds_element.findall(".//connection//relation"):
#             rel_type = attr_or_default(relation, "type")

#             if rel_type == "text":
#                 custom_sql.append(
#                     {
#                         "name": attr_or_default(relation, "name"),
#                         "query": text_or_default(relation),
#                     }
#                 )
#             elif rel_type == "join":
#                 join_type = attr_or_default(relation, "join", "inner")
#                 clause = relation.find("./clause")

#                 # Reuse the same recursive comparison-pair walker used for
#                 # the modern Relationships model -- legacy join clauses
#                 # have the identical <expression op="="> / op="AND" shape,
#                 # just without the object-graph indirection. This also
#                 # fixes the old approach of iterating every <expression>
#                 # under the clause and appending both its own op AND its
#                 # children's ops, which produced duplicated, noisy
#                 # join_keys (e.g. the join operator itself ending up in
#                 # the list alongside each operand, twice).
#                 join_keys: list[str] = []
#                 operator = "="
#                 if clause is not None:
#                     top_expr = clause.find("./expression")
#                     if top_expr is not None:
#                         for pair_operator, left_ref, right_ref in self._collect_comparison_pairs(top_expr):
#                             operator = pair_operator
#                             join_keys.extend([ref for ref in (left_ref, right_ref) if ref])

#                 child_relations = relation.findall("./relation")
#                 left = attr_or_default(child_relations[0], "name") if len(child_relations) > 0 else ""
#                 right = attr_or_default(child_relations[1], "name") if len(child_relations) > 1 else ""

#                 joins.append(
#                     {
#                         "join_type": join_type,
#                         "operator": operator,
#                         "left_table": left,
#                         "right_table": right,
#                         "join_keys": join_keys,
#                     }
#                 )
#             elif rel_type == "table":
#                 table_ref = attr_or_default(relation, "table")
#                 dedupe_key = table_ref or attr_or_default(relation, "name")
#                 if dedupe_key in seen_table_refs:
#                     continue
#                 seen_table_refs.add(dedupe_key)

#                 tables.append(
#                     {
#                         "name": attr_or_default(relation, "name"),
#                         "table": table_ref,
#                         "connection": attr_or_default(relation, "connection"),
#                     }
#                 )

#         return tables, joins, custom_sql

#     def _parse_columns(
#         self, ds_element: ET.Element
#     ) -> tuple[list[dict], list[dict], list[dict], list[dict]]:
#         columns: list[dict[str, Any]] = []
#         calculated_fields: list[dict[str, Any]] = []
#         formulas: list[dict[str, Any]] = []
#         data_types: list[dict[str, Any]] = []

#         for column in ds_element.findall("./column"):
#             name = clean_field_reference(attr_or_default(column, "name"))
#             caption = attr_or_default(column, "caption", name)
#             role = attr_or_default(column, "role")  # dimension | measure
#             data_type = attr_or_default(column, "datatype")
#             default_agg = attr_or_default(column, "default-aggregation")

#             calc_element = column.find("./calculation")
#             formula = ""
#             is_calculated = calc_element is not None

#             if is_calculated:
#                 formula = attr_or_default(calc_element, "formula")

#             field_entry = {
#                 "name": name,
#                 "caption": caption,
#                 "role": role,
#                 "data_type": data_type,
#                 "default_aggregation": default_agg,
#                 "is_calculated": is_calculated,
#             }

#             data_types.append({"field": name, "data_type": data_type})
#             columns.append(field_entry)

#             if is_calculated:
#                 classification = "lod_expression" if is_lod_expression(formula) else (
#                     "table_calculation" if is_table_calculation(formula) else "standard"
#                 )
#                 calc_entry = {
#                     **field_entry,
#                     "formula": formula,
#                     "classification": classification,
#                 }
#                 calculated_fields.append(calc_entry)
#                 formulas.append({"field": name, "formula": formula, "classification": classification})

#         return columns, calculated_fields, formulas, data_types

#     # ------------------------------------------------------------------
#     # Worksheets / Dashboards / Components
#     # ------------------------------------------------------------------

#     def parse_worksheets(self) -> list[dict[str, Any]]:
#         worksheets = []
#         for ws in self.root.findall(".//worksheets/worksheet"):
#             name = attr_or_default(ws, "name")
#             datasource_deps = [
#                 attr_or_default(dep, "datasource")
#                 for dep in ws.findall(".//datasource-dependencies")
#             ]
#             fields_used = [
#                 clean_field_reference(attr_or_default(col, "name"))
#                 for col in ws.findall(".//datasource-dependencies/column")
#             ]
#             filters = [
#                 clean_field_reference(attr_or_default(f, "column"))
#                 for f in ws.findall(".//filter")
#             ]

#             # Tableau's mark type (Bar, Line, Circle, Pie, Text, Area,
#             # Square, Automatic, ...) is the correct signal for choosing a
#             # comparable Power BI visual type during migration -- much
#             # more reliable than guessing from field counts.
#             mark_element = ws.find(".//panes/pane/mark")
#             mark_type = attr_or_default(mark_element, "class", "Automatic")

#             worksheets.append(
#                 {
#                     "name": name,
#                     "datasources": [d for d in datasource_deps if d],
#                     "fields_used": fields_used,
#                     "filters": filters,
#                     "mark_type": mark_type,
#                 }
#             )
#         return worksheets

#     def parse_dashboards(self) -> list[dict[str, Any]]:
#         dashboards = []
#         for dash in self.root.findall(".//dashboards/dashboard"):
#             name = attr_or_default(dash, "name")
#             zones = dash.findall(".//zone[@name]")
#             worksheets = sorted({attr_or_default(z, "name") for z in zones if attr_or_default(z, "name")})

#             actions = []
#             for action in self.root.findall(f".//actions/action"):
#                 actions.append(
#                     {
#                         "name": attr_or_default(action, "caption") or attr_or_default(action, "name"),
#                     }
#                 )

#             dashboards.append(
#                 {
#                     "name": name,
#                     "worksheets": worksheets,
#                 }
#             )
#         return dashboards

#     def parse_parameters(self) -> list[dict[str, Any]]:
#         parameters = []
#         for ds in self.root.findall(".//datasources/datasource[@name='Parameters']"):
#             for column in ds.findall("./column"):
#                 parameters.append(
#                     {
#                         "name": clean_field_reference(attr_or_default(column, "name")),
#                         "caption": attr_or_default(column, "caption"),
#                         "data_type": attr_or_default(column, "datatype"),
#                         "current_value": attr_or_default(column, "value"),
#                     }
#                 )
#         return parameters

#     def parse_filters(self) -> list[dict[str, Any]]:
#         filters = []
#         for f in self.root.findall(".//worksheets/worksheet//filter"):
#             filters.append(
#                 {
#                     "column": clean_field_reference(attr_or_default(f, "column")),
#                     "class": attr_or_default(f, "class"),
#                 }
#             )
#         return filters

#     def parse_actions(self) -> list[dict[str, Any]]:
#         actions = []
#         for action in self.root.findall(".//actions/action"):
#             actions.append(
#                 {
#                     "name": attr_or_default(action, "caption") or attr_or_default(action, "name"),
#                 }
#             )
#         return actions

#     # ------------------------------------------------------------------
#     # Aggregate
#     # ------------------------------------------------------------------

#     def parse_all(self) -> dict[str, Any]:
#         return {
#             "datasources": self.parse_datasources(),
#             "worksheets": self.parse_worksheets(),
#             "dashboards": self.parse_dashboards(),
#             "parameters": self.parse_parameters(),
#             "filters": self.parse_filters(),
#             "actions": self.parse_actions(),
#         }

# """
# Parser for Tableau .twb workbook XML.

# A .twb is a single XML document describing datasources (with their
# columns/calculated fields and physical/logical table relationships),
# worksheets, dashboards, parameters, filters and actions. This module
# extracts everything into plain dicts that feed the normalization layer.
# """

# from __future__ import annotations

# import re
# import xml.etree.ElementTree as ET
# from typing import Any

# from app.utils.xml_helpers import (
#     attr_or_default,
#     is_custom_sql,
#     is_lod_expression,
#     is_table_calculation,
#     strip_bracket,
#     text_or_default,
# )


# class TwbParser:
#     """Parses a single .twb XML document (as bytes or str)."""

#     def __init__(self, xml_content: bytes | str):
#         if isinstance(xml_content, bytes):
#             xml_content = xml_content.decode("utf-8", errors="ignore")
#         self.root = ET.fromstring(xml_content)

#     # ------------------------------------------------------------------
#     # Datasources / data model
#     # ------------------------------------------------------------------

#     def parse_datasources(self) -> list[dict[str, Any]]:
#         """Parse the workbook's REAL datasource definitions.

#         IMPORTANT: this must only look at the top-level ``/workbook/datasources/datasource``
#         elements. Every ``<worksheet>`` in a TWB also carries its own lightweight
#         ``<view><datasources><datasource>`` reference block (name/caption only, no
#         columns) declaring which datasources that sheet depends on. A deep
#         (``.//``) search matches those too and produces one duplicate "datasource"
#         per worksheet that references it. Scoping to the direct child of the
#         workbook root avoids that, and we additionally dedupe by name as a
#         defensive second layer in case a workbook nests real definitions
#         (e.g. inside a dashboard's stored copy of a datasource in some TWB
#         variants).
#         """
#         datasources = []
#         seen_names: set[str] = set()

#         top_level_container = self.root.find("./datasources")
#         candidates = top_level_container.findall("./datasource") if top_level_container is not None else []

#         for ds in candidates:
#             name = attr_or_default(ds, "name")

#             # Skip the synthetic "Parameters" pseudo-datasource here; it's
#             # handled separately by parse_parameters().
#             if name == "Parameters" or not name:
#                 continue

#             # A worksheet-scoped reference block has no <column> or <connection>
#             # children -- it's just a name/caption pointer. Real definitions
#             # always carry at least one of these. Skip empty pointer blocks
#             # that slipped through even at this scope.
#             has_columns = ds.find("./column") is not None
#             has_connection = ds.find(".//connection") is not None
#             if not has_columns and not has_connection:
#                 continue

#             if name in seen_names:
#                 continue
#             seen_names.add(name)

#             caption = attr_or_default(ds, "caption", name)

#             connections = self._parse_connections(ds)
#             relations, joins, custom_sql = self._parse_relations(ds)
#             columns, calculated_fields, formulas, data_types = self._parse_columns(ds)
#             relationship_joins = self._parse_object_graph_relationships(ds, relations)

#             # Prefer the modern "Relationships" (object-graph) model when
#             # present; fall back to legacy <relation type="join"> parsing
#             # for older workbooks built with physical joins.
#             all_joins = relationship_joins if relationship_joins else joins

#             datasources.append(
#                 {
#                     "name": name,
#                     "caption": caption,
#                     "version": attr_or_default(ds, "version"),
#                     "is_embedded": True,
#                     "connections": connections,
#                     "tables": relations,
#                     "joins": all_joins,
#                     "custom_sql": custom_sql,
#                     "columns": columns,
#                     "calculated_fields": calculated_fields,
#                     "formulas": formulas,
#                     "data_types": data_types,
#                 }
#             )
#         return datasources

#     # Tableau disambiguates a field name that exists on more than one
#     # joined logical table by appending " (TableName)" to it, e.g. a
#     # `date_key` column on both the fact table and "Dim_Date" becomes
#     # `date_key (Dim_Date)` for the Dim_Date copy. This is the ACTUAL
#     # shape relationship expressions reference fields by (verified
#     # against real Tableau output) -- NOT an "[ObjectId].[Field]" form.
#     _FIELD_TABLE_SUFFIX = re.compile(r"^(?P<field>.+)\s\((?P<table>[^)]+)\)$")

#     # Relationship comparisons aren't always equality -- Tableau also
#     # supports >=, <=, >, <, <> for relationship conditions.
#     _COMPARISON_OPERATORS = {"=", "==", "!=", "<>", ">", "<", ">=", "<="}

#     def _parse_object_graph_relationships(
#         self, ds_element: ET.Element, tables: list[dict[str, Any]]
#     ) -> list[dict[str, Any]]:
#         """Parse joins from Tableau's modern "Relationships" data model.

#         Since Tableau 2020.2, datasources built with the Relationships
#         canvas store their logical-table linkage in
#         ``<datasource><object-graph><relationships><relationship>`` rather
#         than the legacy ``<relation type="join">`` XML.

#         Each ``<relationship>`` wraps one or more comparison expressions
#         (``=``, ``>=``, etc.) whose two leaf operands are bracketed field
#         references, e.g. ``[date_key]`` or ``[date_key (Dim_Date)]``. When
#         two joined tables both have a same-named column, Tableau appends
#         `` (TableName)`` to disambiguate -- that suffix is what actually
#         tells us which table a given operand belongs to; there is no
#         object-id indirection to resolve through in this form.

#         An operand with NO suffix (e.g. plain ``[date_key]``) belongs to
#         whichever table doesn't need disambiguating -- typically the base
#         / fact table the relationship canvas was built around, since its
#         columns only need the "(Table)" suffix once a second table joins
#         in and introduces a name collision. We infer that base table once
#         all relationships have been scanned: it's whichever of this
#         datasource's tables never appears as an explicit suffix.

#         A composite (multi-column) key wraps several comparison pairs in
#         an ``<expression op="AND">``; we walk the tree recursively so both
#         single- and multi-column keys resolve correctly.

#         Older workbooks won't have an ``<object-graph>`` at all, in which
#         case this returns an empty list and the caller falls back to the
#         legacy join parser.
#         """
#         raw_pairs: list[tuple[str, str, str]] = []  # (operator, left_ref, right_ref)

#         for relationship in ds_element.findall(".//object-graph/relationships/relationship"):
#             top_expression = relationship.find("./expression")
#             if top_expression is None:
#                 continue
#             raw_pairs.extend(self._collect_comparison_pairs(top_expression))

#         if not raw_pairs:
#             return []

#         # First pass: resolve every operand we can identify a table for
#         # directly (via the object-graph id map, or via the "(Table)"
#         # suffix), and track which of this datasource's tables got
#         # explicitly named that way.
#         object_table_map = self._build_object_table_map(ds_element)
#         resolved: list[tuple[str, tuple[str, str], tuple[str, str]]] = []
#         mentioned_tables: set[str] = set()

#         for operator, left_ref, right_ref in raw_pairs:
#             left = self._resolve_relationship_operand(left_ref, object_table_map)
#             right = self._resolve_relationship_operand(right_ref, object_table_map)
#             mentioned_tables.update(t for t, _ in (left, right) if t)
#             resolved.append((operator, left, right))

#         # Second pass: any operand left without a table (bare field, no
#         # suffix, no object-id match) belongs to the one table in this
#         # datasource that was never explicitly mentioned as a suffix --
#         # i.e. the base/fact table. Only apply this when it's unambiguous
#         # (exactly one candidate); otherwise leave it blank rather than
#         # guessing wrong.
#         known_table_names = {t.get("name", "") for t in tables if t.get("name")}
#         unmentioned = known_table_names - mentioned_tables
#         base_table = next(iter(unmentioned)) if len(unmentioned) == 1 else ""

#         joins: list[dict[str, Any]] = []
#         for operator, (left_table, left_field), (right_table, right_field) in resolved:
#             left_table = left_table or base_table
#             right_table = right_table or base_table

#             joins.append(
#                 {
#                     "join_type": "relationship",
#                     "operator": operator,
#                     "left_table": left_table,
#                     "left_column": left_field,
#                     "right_table": right_table,
#                     "right_column": right_field,
#                     # Kept for backward compatibility with any existing
#                     # consumers reading the flat key list.
#                     "join_keys": [f for f in (left_field, right_field) if f],
#                 }
#             )

#         return joins

#     def _build_object_table_map(self, ds_element: ET.Element) -> dict[str, str]:
#         """Maps each logical-table object's id AND caption to its
#         underlying physical table name, by following
#         ``<object><properties><relation>`` down to the actual table
#         reference. This covers TWB variants that DO reference joined
#         objects by id (``[ObjectId].[Field]``) rather than by the
#         field-name-suffix form; harmless no-op when a workbook doesn't use
#         that form, since nothing will match against this map."""

#         object_table_map: dict[str, str] = {}

#         for obj in ds_element.findall(".//object-graph/objects/object"):
#             obj_id = attr_or_default(obj, "id")
#             caption = attr_or_default(obj, "caption") or obj_id

#             relation = obj.find("./properties/relation")
#             table_name = ""
#             if relation is not None:
#                 table_name = attr_or_default(relation, "table") or attr_or_default(relation, "name")

#             resolved = table_name or caption or obj_id

#             if obj_id:
#                 object_table_map[obj_id] = resolved
#             if caption:
#                 object_table_map[caption] = resolved

#         return object_table_map

#     def _collect_comparison_pairs(self, expression: ET.Element) -> list[tuple[str, str, str]]:
#         """Recursively walks a relationship's <expression> tree and
#         returns every leaf comparison as (operator, left_ref, right_ref).

#         A simple relationship is ``<expression op="="><expression op="[a]"/>
#         <expression op="[b]"/></expression>``. A composite key wraps
#         multiple such pairs in ``<expression op="AND">``. We don't assume
#         a fixed shape or that the operator is always "=" -- any node whose
#         op is a recognized comparison operator with exactly two childless
#         (leaf) children is treated as one pair; anything else (AND/OR
#         wrappers, deeper nesting) is descended into further.
#         """
#         op = attr_or_default(expression, "op")
#         children = expression.findall("./expression")

#         is_leaf_comparison = (
#             op in self._COMPARISON_OPERATORS
#             and len(children) == 2
#             and not children[0].findall("./expression")
#             and not children[1].findall("./expression")
#         )

#         if is_leaf_comparison:
#             return [(op, attr_or_default(children[0], "op"), attr_or_default(children[1], "op"))]

#         pairs: list[tuple[str, str, str]] = []
#         for child in children:
#             pairs.extend(self._collect_comparison_pairs(child))
#         return pairs

#     @classmethod
#     def _resolve_relationship_operand(
#         cls, ref: str, object_table_map: dict[str, str]
#     ) -> tuple[str, str]:
#         """Resolves one relationship operand to (table_name, field_name).

#         Tries, in order:
#           1. ``[ObjectId].[Field]`` -- an object-graph id/caption prefix,
#              used by some Tableau export variants.
#           2. ``[Field (Table)]`` -- Tableau's own disambiguation suffix,
#              which is the form actually seen in real relationship-model
#              TWB exports and directly names the table.
#           3. ``[Field]`` with no suffix and no object match: table is left
#              blank here and resolved by the caller once every operand in
#              this datasource has been scanned (see the base-table
#              inference in _parse_object_graph_relationships).
#         """
#         two_part = re.match(r"^\[([^\]]+)\]\.\[([^\]]+)\]$", ref or "")
#         if two_part:
#             obj_key, field_name = two_part.group(1), two_part.group(2)
#             if obj_key in object_table_map:
#                 return object_table_map[obj_key], field_name

#         bare = re.match(r"^\[([^\]]+)\]$", ref or "")
#         if not bare:
#             return "", ""

#         content = bare.group(1)
#         suffix_match = cls._FIELD_TABLE_SUFFIX.match(content)
#         if suffix_match:
#             return suffix_match.group("table"), suffix_match.group("field")

#         return "", content

#     def _parse_connections(self, ds_element: ET.Element) -> list[dict[str, Any]]:
#         connections = []
#         for conn in ds_element.findall(".//connection"):
#             connections.append(
#                 {
#                     "class": attr_or_default(conn, "class"),
#                     "server": attr_or_default(conn, "server"),
#                     "port": attr_or_default(conn, "port"),
#                     "database": attr_or_default(conn, "dbname") or attr_or_default(conn, "database"),
#                     "schema": attr_or_default(conn, "schema"),
#                     "authentication": attr_or_default(conn, "authentication"),
#                 }
#             )
#         return connections

#     def _parse_relations(self, ds_element: ET.Element) -> tuple[list[dict], list[dict], list[dict]]:
#         tables: list[dict[str, Any]] = []
#         joins: list[dict[str, Any]] = []
#         custom_sql: list[dict[str, Any]] = []
#         seen_table_refs: set[str] = set()

#         # Scope to relations nested under <connection> only. A deep
#         # ".//relation" search from the datasource root also matches the
#         # <object-graph><objects><object><properties><relation> pointers
#         # used by the modern Relationships model -- those describe the
#         # SAME physical tables the connection already lists, just indexed
#         # by logical-object id for join resolution (see
#         # _build_object_table_map). Including them here would just
#         # duplicate every table/join entry.
#         for relation in ds_element.findall(".//connection//relation"):
#             rel_type = attr_or_default(relation, "type")

#             if rel_type == "text":
#                 custom_sql.append(
#                     {
#                         "name": attr_or_default(relation, "name"),
#                         "query": text_or_default(relation),
#                     }
#                 )
#             elif rel_type == "join":
#                 join_type = attr_or_default(relation, "join", "inner")
#                 clause = relation.find("./clause")

#                 # Reuse the same recursive comparison-pair walker used for
#                 # the modern Relationships model -- legacy join clauses
#                 # have the identical <expression op="="> / op="AND" shape,
#                 # just without the object-graph indirection. This also
#                 # fixes the old approach of iterating every <expression>
#                 # under the clause and appending both its own op AND its
#                 # children's ops, which produced duplicated, noisy
#                 # join_keys (e.g. the join operator itself ending up in
#                 # the list alongside each operand, twice).
#                 join_keys: list[str] = []
#                 operator = "="
#                 if clause is not None:
#                     top_expr = clause.find("./expression")
#                     if top_expr is not None:
#                         for pair_operator, left_ref, right_ref in self._collect_comparison_pairs(top_expr):
#                             operator = pair_operator
#                             join_keys.extend([ref for ref in (left_ref, right_ref) if ref])

#                 child_relations = relation.findall("./relation")
#                 left = attr_or_default(child_relations[0], "name") if len(child_relations) > 0 else ""
#                 right = attr_or_default(child_relations[1], "name") if len(child_relations) > 1 else ""

#                 joins.append(
#                     {
#                         "join_type": join_type,
#                         "operator": operator,
#                         "left_table": left,
#                         "right_table": right,
#                         "join_keys": join_keys,
#                     }
#                 )
#             elif rel_type == "table":
#                 table_ref = attr_or_default(relation, "table")
#                 dedupe_key = table_ref or attr_or_default(relation, "name")
#                 if dedupe_key in seen_table_refs:
#                     continue
#                 seen_table_refs.add(dedupe_key)

#                 tables.append(
#                     {
#                         "name": attr_or_default(relation, "name"),
#                         "table": table_ref,
#                         "connection": attr_or_default(relation, "connection"),
#                     }
#                 )

#         return tables, joins, custom_sql

#     def _parse_columns(
#         self, ds_element: ET.Element
#     ) -> tuple[list[dict], list[dict], list[dict], list[dict]]:
#         columns: list[dict[str, Any]] = []
#         calculated_fields: list[dict[str, Any]] = []
#         formulas: list[dict[str, Any]] = []
#         data_types: list[dict[str, Any]] = []

#         for column in ds_element.findall("./column"):
#             name = strip_bracket(attr_or_default(column, "name"))
#             caption = attr_or_default(column, "caption", name)
#             role = attr_or_default(column, "role")  # dimension | measure
#             data_type = attr_or_default(column, "datatype")
#             default_agg = attr_or_default(column, "default-aggregation")

#             calc_element = column.find("./calculation")
#             formula = ""
#             is_calculated = calc_element is not None

#             if is_calculated:
#                 formula = attr_or_default(calc_element, "formula")

#             field_entry = {
#                 "name": name,
#                 "caption": caption,
#                 "role": role,
#                 "data_type": data_type,
#                 "default_aggregation": default_agg,
#                 "is_calculated": is_calculated,
#             }

#             data_types.append({"field": name, "data_type": data_type})
#             columns.append(field_entry)

#             if is_calculated:
#                 classification = "lod_expression" if is_lod_expression(formula) else (
#                     "table_calculation" if is_table_calculation(formula) else "standard"
#                 )
#                 calc_entry = {
#                     **field_entry,
#                     "formula": formula,
#                     "classification": classification,
#                 }
#                 calculated_fields.append(calc_entry)
#                 formulas.append({"field": name, "formula": formula, "classification": classification})

#         return columns, calculated_fields, formulas, data_types

#     # ------------------------------------------------------------------
#     # Worksheets / Dashboards / Components
#     # ------------------------------------------------------------------

#     def parse_worksheets(self) -> list[dict[str, Any]]:
#         worksheets = []
#         for ws in self.root.findall(".//worksheets/worksheet"):
#             name = attr_or_default(ws, "name")
#             datasource_deps = [
#                 attr_or_default(dep, "datasource")
#                 for dep in ws.findall(".//datasource-dependencies")
#             ]
#             fields_used = [
#                 strip_bracket(attr_or_default(col, "name"))
#                 for col in ws.findall(".//datasource-dependencies/column")
#             ]
#             filters = [
#                 strip_bracket(attr_or_default(f, "column"))
#                 for f in ws.findall(".//filter")
#             ]

#             # Tableau's mark type (Bar, Line, Circle, Pie, Text, Area,
#             # Square, Automatic, ...) is the correct signal for choosing a
#             # comparable Power BI visual type during migration -- much
#             # more reliable than guessing from field counts.
#             mark_element = ws.find(".//panes/pane/mark")
#             mark_type = attr_or_default(mark_element, "class", "Automatic")

#             worksheets.append(
#                 {
#                     "name": name,
#                     "datasources": [d for d in datasource_deps if d],
#                     "fields_used": fields_used,
#                     "filters": filters,
#                     "mark_type": mark_type,
#                 }
#             )
#         return worksheets

#     def parse_dashboards(self) -> list[dict[str, Any]]:
#         dashboards = []
#         for dash in self.root.findall(".//dashboards/dashboard"):
#             name = attr_or_default(dash, "name")
#             zones = dash.findall(".//zone[@name]")
#             worksheets = sorted({attr_or_default(z, "name") for z in zones if attr_or_default(z, "name")})

#             actions = []
#             for action in self.root.findall(f".//actions/action"):
#                 actions.append(
#                     {
#                         "name": attr_or_default(action, "caption") or attr_or_default(action, "name"),
#                     }
#                 )

#             dashboards.append(
#                 {
#                     "name": name,
#                     "worksheets": worksheets,
#                 }
#             )
#         return dashboards

#     def parse_parameters(self) -> list[dict[str, Any]]:
#         parameters = []
#         for ds in self.root.findall(".//datasources/datasource[@name='Parameters']"):
#             for column in ds.findall("./column"):
#                 parameters.append(
#                     {
#                         "name": strip_bracket(attr_or_default(column, "name")),
#                         "caption": attr_or_default(column, "caption"),
#                         "data_type": attr_or_default(column, "datatype"),
#                         "current_value": attr_or_default(column, "value"),
#                     }
#                 )
#         return parameters

#     def parse_filters(self) -> list[dict[str, Any]]:
#         filters = []
#         for f in self.root.findall(".//worksheets/worksheet//filter"):
#             filters.append(
#                 {
#                     "column": strip_bracket(attr_or_default(f, "column")),
#                     "class": attr_or_default(f, "class"),
#                 }
#             )
#         return filters

#     def parse_actions(self) -> list[dict[str, Any]]:
#         actions = []
#         for action in self.root.findall(".//actions/action"):
#             actions.append(
#                 {
#                     "name": attr_or_default(action, "caption") or attr_or_default(action, "name"),
#                 }
#             )
#         return actions

#     # ------------------------------------------------------------------
#     # Aggregate
#     # ------------------------------------------------------------------

#     def parse_all(self) -> dict[str, Any]:
#         return {
#             "datasources": self.parse_datasources(),
#             "worksheets": self.parse_worksheets(),
#             "dashboards": self.parse_dashboards(),
#             "parameters": self.parse_parameters(),
#             "filters": self.parse_filters(),
#             "actions": self.parse_actions(),
#         }

"""
Parser for Tableau .twb workbook XML.

A .twb is a single XML document describing datasources (with their
columns/calculated fields and physical/logical table relationships),
worksheets, dashboards, parameters, filters and actions. This module
extracts everything into plain dicts that feed the normalization layer.
"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from typing import Any

from app.utils.xml_helpers import (
    attr_or_default,
    clean_field_reference,
    is_custom_sql,
    is_lod_expression,
    is_table_calculation,
    text_or_default,
)


class TwbParser:
    """Parses a single .twb XML document (as bytes or str)."""

    def __init__(self, xml_content: bytes | str):
        if isinstance(xml_content, bytes):
            xml_content = xml_content.decode("utf-8", errors="ignore")
        self.root = ET.fromstring(xml_content)

    # ------------------------------------------------------------------
    # Datasources / data model
    # ------------------------------------------------------------------

    def parse_datasources(self) -> list[dict[str, Any]]:
        """Parse the workbook's REAL datasource definitions.

        IMPORTANT: this must only look at the top-level ``/workbook/datasources/datasource``
        elements. Every ``<worksheet>`` in a TWB also carries its own lightweight
        ``<view><datasources><datasource>`` reference block (name/caption only, no
        columns) declaring which datasources that sheet depends on. A deep
        (``.//``) search matches those too and produces one duplicate "datasource"
        per worksheet that references it. Scoping to the direct child of the
        workbook root avoids that, and we additionally dedupe by name as a
        defensive second layer in case a workbook nests real definitions
        (e.g. inside a dashboard's stored copy of a datasource in some TWB
        variants).
        """
        datasources = []
        seen_names: set[str] = set()

        top_level_container = self.root.find("./datasources")
        candidates = top_level_container.findall("./datasource") if top_level_container is not None else []

        for ds in candidates:
            name = attr_or_default(ds, "name")

            # Skip the synthetic "Parameters" pseudo-datasource here; it's
            # handled separately by parse_parameters().
            if name == "Parameters" or not name:
                continue

            # A worksheet-scoped reference block has no <column> or <connection>
            # children -- it's just a name/caption pointer. Real definitions
            # always carry at least one of these. Skip empty pointer blocks
            # that slipped through even at this scope.
            has_columns = ds.find("./column") is not None
            has_connection = ds.find(".//connection") is not None
            if not has_columns and not has_connection:
                continue

            if name in seen_names:
                continue
            seen_names.add(name)

            caption = attr_or_default(ds, "caption", name)

            connections = self._parse_connections(ds)
            relations, joins, custom_sql = self._parse_relations(ds)
            columns, calculated_fields, formulas, data_types = self._parse_columns(ds)
            relationship_joins = self._parse_object_graph_relationships(ds, relations)
            columns_by_table = self._parse_table_columns(ds)
            for table in relations:
                table_key = table.get("name") or table.get("table", "")
                table["columns"] = columns_by_table.get(table_key.strip("[]"), [])

            # Prefer the modern "Relationships" (object-graph) model when
            # present; fall back to legacy <relation type="join"> parsing
            # for older workbooks built with physical joins.
            all_joins = relationship_joins if relationship_joins else joins

            datasources.append(
                {
                    "name": name,
                    "caption": caption,
                    "version": attr_or_default(ds, "version"),
                    "is_embedded": True,
                    "connections": connections,
                    "tables": relations,
                    "joins": all_joins,
                    "custom_sql": custom_sql,
                    "columns": columns,
                    "calculated_fields": calculated_fields,
                    "formulas": formulas,
                    "data_types": data_types,
                }
            )
        return datasources

    # Tableau disambiguates a field name that exists on more than one
    # joined logical table by appending " (TableName)" to it, e.g. a
    # `date_key` column on both the fact table and "Dim_Date" becomes
    # `date_key (Dim_Date)` for the Dim_Date copy. This is the ACTUAL
    # shape relationship expressions reference fields by (verified
    # against real Tableau output) -- NOT an "[ObjectId].[Field]" form.
    _FIELD_TABLE_SUFFIX = re.compile(r"^(?P<field>.+)\s\((?P<table>[^)]+)\)$")

    # Relationship comparisons aren't always equality -- Tableau also
    # supports >=, <=, >, <, <> for relationship conditions.
    _COMPARISON_OPERATORS = {"=", "==", "!=", "<>", ">", "<", ">=", "<="}

    def _parse_object_graph_relationships(
        self, ds_element: ET.Element, tables: list[dict[str, Any]]
    ) -> list[dict[str, Any]]:
        """Parse joins from Tableau's modern "Relationships" data model.

        Since Tableau 2020.2, datasources built with the Relationships
        canvas store their logical-table linkage in
        ``<datasource><object-graph><relationships><relationship>`` rather
        than the legacy ``<relation type="join">`` XML.

        Each ``<relationship>`` wraps one or more comparison expressions
        (``=``, ``>=``, etc.) whose two leaf operands are bracketed field
        references, e.g. ``[date_key]`` or ``[date_key (Dim_Date)]``. When
        two joined tables both have a same-named column, Tableau appends
        `` (TableName)`` to disambiguate -- that suffix is what actually
        tells us which table a given operand belongs to; there is no
        object-id indirection to resolve through in this form.

        An operand with NO suffix (e.g. plain ``[date_key]``) belongs to
        whichever table doesn't need disambiguating -- typically the base
        / fact table the relationship canvas was built around, since its
        columns only need the "(Table)" suffix once a second table joins
        in and introduces a name collision. We infer that base table once
        all relationships have been scanned: it's whichever of this
        datasource's tables never appears as an explicit suffix.

        A composite (multi-column) key wraps several comparison pairs in
        an ``<expression op="AND">``; we walk the tree recursively so both
        single- and multi-column keys resolve correctly.

        Older workbooks won't have an ``<object-graph>`` at all, in which
        case this returns an empty list and the caller falls back to the
        legacy join parser.
        """
        raw_pairs: list[tuple[str, str, str]] = []  # (operator, left_ref, right_ref)

        for relationship in ds_element.findall(".//object-graph/relationships/relationship"):
            top_expression = relationship.find("./expression")
            if top_expression is None:
                continue
            raw_pairs.extend(self._collect_comparison_pairs(top_expression))

        if not raw_pairs:
            return []

        # First pass: resolve every operand we can identify a table for
        # directly (via the object-graph id map, or via the "(Table)"
        # suffix), and track which of this datasource's tables got
        # explicitly named that way.
        object_table_map = self._build_object_table_map(ds_element)
        resolved: list[tuple[str, tuple[str, str], tuple[str, str]]] = []
        mentioned_tables: set[str] = set()

        for operator, left_ref, right_ref in raw_pairs:
            left = self._resolve_relationship_operand(left_ref, object_table_map)
            right = self._resolve_relationship_operand(right_ref, object_table_map)
            mentioned_tables.update(t for t, _ in (left, right) if t)
            resolved.append((operator, left, right))

        # Second pass: any operand left without a table (bare field, no
        # suffix, no object-id match) belongs to the one table in this
        # datasource that was never explicitly mentioned as a suffix --
        # i.e. the base/fact table. Only apply this when it's unambiguous
        # (exactly one candidate); otherwise leave it blank rather than
        # guessing wrong.
        known_table_names = {t.get("name", "") for t in tables if t.get("name")}
        unmentioned = known_table_names - mentioned_tables
        base_table = next(iter(unmentioned)) if len(unmentioned) == 1 else ""

        joins: list[dict[str, Any]] = []
        for operator, (left_table, left_field), (right_table, right_field) in resolved:
            left_table = left_table or base_table
            right_table = right_table or base_table

            joins.append(
                {
                    "join_type": "relationship",
                    "operator": operator,
                    "left_table": left_table,
                    "left_column": left_field,
                    "right_table": right_table,
                    "right_column": right_field,
                    # Kept for backward compatibility with any existing
                    # consumers reading the flat key list.
                    "join_keys": [f for f in (left_field, right_field) if f],
                }
            )

        return joins
    

    def _build_object_table_map(self, ds_element: ET.Element) -> dict[str, str]:
        """Maps each logical-table object's id AND caption to its
        underlying physical table name, by following
        ``<object><properties><relation>`` down to the actual table
        reference. This covers TWB variants that DO reference joined
        objects by id (``[ObjectId].[Field]``) rather than by the
        field-name-suffix form; harmless no-op when a workbook doesn't use
        that form, since nothing will match against this map."""

        object_table_map: dict[str, str] = {}

        for obj in ds_element.findall(".//object-graph/objects/object"):
            obj_id = attr_or_default(obj, "id")
            caption = attr_or_default(obj, "caption") or obj_id

        

            relation = obj.find("./properties/relation")
            table_name = ""
            if relation is not None:
                table_name = attr_or_default(relation, "name") or attr_or_default(relation, "table")

            resolved = table_name or caption or obj_id

            if obj_id:
                object_table_map[obj_id] = resolved
            if caption:
                object_table_map[caption] = resolved

        return object_table_map

    def _collect_comparison_pairs(self, expression: ET.Element) -> list[tuple[str, str, str]]:
        """Recursively walks a relationship's <expression> tree and
        returns every leaf comparison as (operator, left_ref, right_ref).

        A simple relationship is ``<expression op="="><expression op="[a]"/>
        <expression op="[b]"/></expression>``. A composite key wraps
        multiple such pairs in ``<expression op="AND">``. We don't assume
        a fixed shape or that the operator is always "=" -- any node whose
        op is a recognized comparison operator with exactly two childless
        (leaf) children is treated as one pair; anything else (AND/OR
        wrappers, deeper nesting) is descended into further.
        """
        op = attr_or_default(expression, "op")
        children = expression.findall("./expression")

        is_leaf_comparison = (
            op in self._COMPARISON_OPERATORS
            and len(children) == 2
            and not children[0].findall("./expression")
            and not children[1].findall("./expression")
        )

        if is_leaf_comparison:
            return [(op, attr_or_default(children[0], "op"), attr_or_default(children[1], "op"))]

        pairs: list[tuple[str, str, str]] = []
        for child in children:
            pairs.extend(self._collect_comparison_pairs(child))
        return pairs

    @classmethod
    def _resolve_relationship_operand(
        cls, ref: str, object_table_map: dict[str, str]
    ) -> tuple[str, str]:
        """Resolves one relationship operand to (table_name, field_name).

        Tries, in order:
          1. ``[ObjectId].[Field]`` -- an object-graph id/caption prefix,
             used by some Tableau export variants.
          2. ``[Field (Table)]`` -- Tableau's own disambiguation suffix,
             which is the form actually seen in real relationship-model
             TWB exports and directly names the table.
          3. ``[Field]`` with no suffix and no object match: table is left
             blank here and resolved by the caller once every operand in
             this datasource has been scanned (see the base-table
             inference in _parse_object_graph_relationships).
        """
        two_part = re.match(r"^\[([^\]]+)\]\.\[([^\]]+)\]$", ref or "")
        if two_part:
            obj_key, field_name = two_part.group(1), two_part.group(2)
            if obj_key in object_table_map:
                return object_table_map[obj_key], field_name

        bare = re.match(r"^\[([^\]]+)\]$", ref or "")
        if not bare:
            return "", ""

        content = bare.group(1)
        suffix_match = cls._FIELD_TABLE_SUFFIX.match(content)
        if suffix_match:
            return suffix_match.group("table"), suffix_match.group("field")

        return "", content

    def _parse_connections(self, ds_element: ET.Element) -> list[dict[str, Any]]:
        connections = []
        for conn in ds_element.findall(".//connection"):
            connections.append(
                {
                    "class": attr_or_default(conn, "class"),
                    "server": attr_or_default(conn, "server"),
                    "port": attr_or_default(conn, "port"),
                    "database": attr_or_default(conn, "dbname") or attr_or_default(conn, "database"),
                    "schema": attr_or_default(conn, "schema"),
                    "authentication": attr_or_default(conn, "authentication"),
                }
            )
        return connections

    def _parse_relations(self, ds_element: ET.Element) -> tuple[list[dict], list[dict], list[dict]]:
        tables: list[dict[str, Any]] = []
        joins: list[dict[str, Any]] = []
        custom_sql: list[dict[str, Any]] = []
        seen_table_refs: set[str] = set()

        # Scope to relations nested under <connection> only. A deep
        # ".//relation" search from the datasource root also matches the
        # <object-graph><objects><object><properties><relation> pointers
        # used by the modern Relationships model -- those describe the
        # SAME physical tables the connection already lists, just indexed
        # by logical-object id for join resolution (see
        # _build_object_table_map). Including them here would just
        # duplicate every table/join entry.
        for relation in ds_element.findall(".//connection//relation"):
            rel_type = attr_or_default(relation, "type")

            if rel_type == "text":
                custom_sql.append(
                    {
                        "name": attr_or_default(relation, "name"),
                        "query": text_or_default(relation),
                    }
                )
            elif rel_type == "join":
                join_type = attr_or_default(relation, "join", "inner")
                clause = relation.find("./clause")

                # Reuse the same recursive comparison-pair walker used for
                # the modern Relationships model -- legacy join clauses
                # have the identical <expression op="="> / op="AND" shape,
                # just without the object-graph indirection. This also
                # fixes the old approach of iterating every <expression>
                # under the clause and appending both its own op AND its
                # children's ops, which produced duplicated, noisy
                # join_keys (e.g. the join operator itself ending up in
                # the list alongside each operand, twice).
                join_keys: list[str] = []
                operator = "="
                if clause is not None:
                    top_expr = clause.find("./expression")
                    if top_expr is not None:
                        for pair_operator, left_ref, right_ref in self._collect_comparison_pairs(top_expr):
                            operator = pair_operator
                            join_keys.extend([ref for ref in (left_ref, right_ref) if ref])

                child_relations = relation.findall("./relation")
                left = attr_or_default(child_relations[0], "name") if len(child_relations) > 0 else ""
                right = attr_or_default(child_relations[1], "name") if len(child_relations) > 1 else ""

                joins.append(
                    {
                        "join_type": join_type,
                        "operator": operator,
                        "left_table": left,
                        "right_table": right,
                        "join_keys": join_keys,
                    }
                )
            elif rel_type == "table":
                table_ref = attr_or_default(relation, "table")
                dedupe_key = table_ref or attr_or_default(relation, "name")
                if dedupe_key in seen_table_refs:
                    continue
                seen_table_refs.add(dedupe_key)

                tables.append(
                    {
                        "name": attr_or_default(relation, "name"),
                        "table": table_ref,
                        "connection": attr_or_default(relation, "connection"),
                    }
                )

        return tables, joins, custom_sql

    def _parse_columns(
        self, ds_element: ET.Element
    ) -> tuple[list[dict], list[dict], list[dict], list[dict]]:
        columns: list[dict[str, Any]] = []
        calculated_fields: list[dict[str, Any]] = []
        formulas: list[dict[str, Any]] = []
        data_types: list[dict[str, Any]] = []

        for column in ds_element.findall("./column"):
            name = clean_field_reference(attr_or_default(column, "name"))
            caption = attr_or_default(column, "caption", name)
            role = attr_or_default(column, "role")  # dimension | measure
            data_type = attr_or_default(column, "datatype")
            default_agg = attr_or_default(column, "default-aggregation")

            calc_element = column.find("./calculation")
            formula = ""
            is_calculated = calc_element is not None

            if is_calculated:
                formula = attr_or_default(calc_element, "formula")

            field_entry = {
                "name": name,
                "caption": caption,
                "role": role,
                "data_type": data_type,
                "default_aggregation": default_agg,
                "is_calculated": is_calculated,
            }

            data_types.append({"field": name, "data_type": data_type})
            columns.append(field_entry)

            if is_calculated:
                classification = "lod_expression" if is_lod_expression(formula) else (
                    "table_calculation" if is_table_calculation(formula) else "standard"
                )
                calc_entry = {
                    **field_entry,
                    "formula": formula,
                    "classification": classification,
                }
                calculated_fields.append(calc_entry)
                formulas.append({"field": name, "formula": formula, "classification": classification})

        return columns, calculated_fields, formulas, data_types

    def _parse_table_columns(self, ds_element: ET.Element) -> dict[str, list[str]]:

            columns_by_table: dict[str, list[str]] = {}

            for record in ds_element.findall(".//metadata-records/metadata-record[@class='column']"):
                parent = (
                    text_or_default(record.find("./parent-name"))
                    or text_or_default(record.find("./class-qualified-name"))
                    or attr_or_default(record, "parent-name")
                )
                col_name = (
                    text_or_default(record.find("./local-name"))
                    or text_or_default(record.find("./remote-name"))
                    or attr_or_default(record, "local-name")
                )

                if not parent or not col_name:
                    continue

                parent = parent.strip("[]")
                col_name = col_name.strip("[]")

                columns_by_table.setdefault(parent, [])
                if col_name not in columns_by_table[parent]:
                    columns_by_table[parent].append(col_name)

            return columns_by_table

    # ------------------------------------------------------------------
    # Worksheets / Dashboards / Components
    # ------------------------------------------------------------------

    def parse_worksheets(self) -> list[dict[str, Any]]:
        worksheets = []
        for ws in self.root.findall(".//worksheets/worksheet"):
            name = attr_or_default(ws, "name")
            datasource_deps = [
                attr_or_default(dep, "datasource")
                for dep in ws.findall(".//datasource-dependencies")
            ]
            fields_used = [
                clean_field_reference(attr_or_default(col, "name"))
                for col in ws.findall(".//datasource-dependencies/column")
            ]
            filters = [
                clean_field_reference(attr_or_default(f, "column"))
                for f in ws.findall(".//filter")
            ]

            # Tableau's mark type (Bar, Line, Circle, Pie, Text, Area,
            # Square, Automatic, ...) is the correct signal for choosing a
            # comparable Power BI visual type during migration -- much
            # more reliable than guessing from field counts.
            mark_element = ws.find(".//panes/pane/mark")
            mark_type = attr_or_default(mark_element, "class", "Automatic")

            worksheets.append(
                {
                    "name": name,
                    "datasources": [d for d in datasource_deps if d],
                    "fields_used": fields_used,
                    "filters": filters,
                    "mark_type": mark_type,
                }
            )
        return worksheets

    def parse_dashboards(self) -> list[dict[str, Any]]:
        dashboards = []
        for dash in self.root.findall(".//dashboards/dashboard"):
            name = attr_or_default(dash, "name")
            zones = dash.findall(".//zone[@name]")
            worksheets = sorted({attr_or_default(z, "name") for z in zones if attr_or_default(z, "name")})

            actions = []
            for action in self.root.findall(f".//actions/action"):
                actions.append(
                    {
                        "name": attr_or_default(action, "caption") or attr_or_default(action, "name"),
                    }
                )

            dashboards.append(
                {
                    "name": name,
                    "worksheets": worksheets,
                }
            )
        return dashboards

    def parse_parameters(self) -> list[dict[str, Any]]:
        parameters = []
        for ds in self.root.findall(".//datasources/datasource[@name='Parameters']"):
            for column in ds.findall("./column"):
                parameters.append(
                    {
                        "name": clean_field_reference(attr_or_default(column, "name")),
                        "caption": attr_or_default(column, "caption"),
                        "data_type": attr_or_default(column, "datatype"),
                        "current_value": attr_or_default(column, "value"),
                    }
                )
        return parameters

    def parse_filters(self) -> list[dict[str, Any]]:
        filters = []
        for f in self.root.findall(".//worksheets/worksheet//filter"):
            filters.append(
                {
                    "column": clean_field_reference(attr_or_default(f, "column")),
                    "class": attr_or_default(f, "class"),
                }
            )
        return filters

    def parse_actions(self) -> list[dict[str, Any]]:
        actions = []
        for action in self.root.findall(".//actions/action"):
            actions.append(
                {
                    "name": attr_or_default(action, "caption") or attr_or_default(action, "name"),
                }
            )
        return actions

    # ------------------------------------------------------------------
    # Aggregate
    # ------------------------------------------------------------------

    def parse_all(self) -> dict[str, Any]:
        return {
            "datasources": self.parse_datasources(),
            "worksheets": self.parse_worksheets(),
            "dashboards": self.parse_dashboards(),
            "parameters": self.parse_parameters(),
            "filters": self.parse_filters(),
            "actions": self.parse_actions(),
        }

    
