"""
Builds a Power BI semantic model definition (TMSL / model.bim) from one
workbook's normalized Tableau metadata.

KNOWN GAP this module works around: Tableau's Relationships-model
datasource exposes fields as a single FLAT list scoped to the datasource,
not per physical table -- there's no direct "this column belongs to this
table" signal from the Metadata API or REST API. The only place that
information survives is Tableau's own field-naming convention: a column
that collides across two joined tables gets " (TableName)" appended (the
same convention app.services.discovery.twb_parser already decodes to
resolve relationship endpoints). We reuse that here to attribute each
field to its real table. Fields with a bare name (no suffix) are
attributed to the "hub" table -- whichever table participates in the
most relationships in this datasource, which is the standard shape of a
star schema's fact table. This is a heuristic, not a guarantee, and is
surfaced in SemanticModelMigrationResponse.warnings when a field can't be
confidently attributed.
"""

from __future__ import annotations

import re
from collections import Counter
from typing import Any

from app.services.ai.openai_client import OpenAIAnalysisClient
from app.services.migration.dax_translator import translate_formula_to_dax

_FIELD_TABLE_SUFFIX = re.compile(r"^(?P<field>.+)\s\((?P<table>[^)]+)\)$")

_DATA_TYPE_MAP = {
    "string": "string",
    "integer": "int64",
    "real": "double",
    "date": "dateTime",
    "datetime": "dateTime",
    "boolean": "boolean",
    "table": None,  # internal object-graph pseudo-field, never a real column
}

_AGGREGATION_FN_PATTERN = re.compile(r"(SUM|AVG|COUNT|MIN|MAX|MEDIAN)\s*\(", re.IGNORECASE)


def _infer_hub_table(tables: list[dict[str, Any]], relationships: list[dict[str, Any]]) -> str:
    """The table that appears in the most relationships is treated as the
    fact/hub table -- standard star-schema shape. Falls back to the first
    table if there are no relationships to infer from (e.g. a
    single-table datasource)."""

    if not relationships:
        return tables[0]["name"] if tables else ""

    counts = Counter()
    for rel in relationships:
        counts[rel.get("left_table", "")] += 1
        counts[rel.get("right_table", "")] += 1
    counts.pop("", None)

    if not counts:
        return tables[0]["name"] if tables else ""

    return counts.most_common(1)[0][0]


def _attribute_field_to_table(field_name: str, hub_table: str, known_tables: set[str]) -> tuple[str, str]:
    """Returns (table_name, bare_column_name) for a flat field name using
    Tableau's " (TableName)" disambiguation suffix, falling back to the
    hub table for unsuffixed fields."""

    match = _FIELD_TABLE_SUFFIX.match(field_name)
    if match and match.group("table") in known_tables:
        return match.group("table"), match.group("field")
    return hub_table, field_name


def _tmsl_column(name: str, data_type: str) -> dict[str, Any]:
    return {
        "name": name,
        "dataType": data_type,
        "sourceColumn": name,
        "summarizeBy": "none",
    }


def _build_m_partition_expression(
    connection: dict[str, Any], database: str, schema: str, table: str
) -> dict[str, Any]:
    """Best-effort Power Query (M) source pointing at the same relational
    database Tableau was reading from. This assumes the Fabric workspace
    can reach that server directly (cloud connection or on-prem gateway
    binding) -- that binding is a separate, manual step after deployment
    since it requires credentials Fabric manages itself, not something
    this API can configure blind. Flagged in migration warnings.
    """
    server = connection.get("server_address", "") if connection else ""
    conn_type = (connection.get("type", "") if connection else "").lower()

    # Only Sql Server family connections get a native M translation here;
    # anything else falls back to a placeholder the person must fill in
    # Power BI Desktop / Power Query, since Tableau supports dozens of
    # connector types this module can't all special-case.
    if "sql" in conn_type:
        m_lines = [
            "let",
            f'    Source = Sql.Database("{server}", "{database}"),',
            f'    Data = Source{{[Schema="{schema}",Item="{table}"]}}[Data]',
            "in",
            "    Data",
        ]
    else:
        m_lines = [
            "let",
            f'    Source = "TODO: connect to {conn_type or "source"} at {server}, table {schema}.{table}"',
            "in",
            "    Source",
        ]

    return {"type": "m", "expression": m_lines}


def build_tmsl_model(
    workbook_bundle: dict[str, Any], semantic_model_name: str
) -> dict[str, Any]:
    """Synchronous structural build -- tables, columns, relationships,
    and partitions. Measures (which need the async DAX translator) are
    added separately by build_measures() and merged in by the caller."""

    data_model = workbook_bundle.get("data_model", {})
    tables_meta = data_model.get("tables", [])
    relationships_meta = data_model.get("relationships", [])
    connections = data_model.get("connections", [])
    databases = data_model.get("databases", [])
    schemas = data_model.get("schemas", [])
    fields = workbook_bundle.get("fields", {})

    known_table_names = {t.get("name", "") for t in tables_meta if t.get("name")}
    hub_table = _infer_hub_table(tables_meta, relationships_meta)

    database_name = databases[0].get("name", "") if databases else ""
    connection = connections[0] if connections else {}
    schema_by_table: dict[str, str] = {}
    for t in tables_meta:
        # Table refs look like "[dbo].[Fact_Production]"; schemas list
        # also carries this, but the table ref itself is more reliable
        # per-table than falling back to the datasource's single inferred
        # schema entry.
        ref = t.get("table", "")
        m = re.match(r"^\[([^\]]+)\]\.\[([^\]]+)\]$", ref)
        schema_by_table[t.get("name", "")] = m.group(1) if m else (schemas[0]["schema"] if schemas else "dbo")

    # Group flat fields into per-table column buckets.
    columns_by_table: dict[str, list[dict[str, Any]]] = {name: [] for name in known_table_names}
    unattributed_fields: list[str] = []

    all_plain_fields = fields.get("dimensions", []) + [
        m for m in fields.get("measures", []) if not m.get("is_calculated")
    ]

    seen_columns: set[tuple[str, str]] = set()
    for field in all_plain_fields:
        data_type = _DATA_TYPE_MAP.get(field.get("data_type", ""), "string")
        if data_type is None:  # internal object-graph pseudo-field
            continue

        table_name, column_name = _attribute_field_to_table(
            field.get("name", ""), hub_table, known_table_names
        )
        if table_name not in columns_by_table:
            unattributed_fields.append(field.get("name", ""))
            continue

        dedupe_key = (table_name, column_name)
        if dedupe_key in seen_columns:
            continue
        seen_columns.add(dedupe_key)

        columns_by_table[table_name].append(_tmsl_column(column_name, data_type))

    tmsl_tables = []
    for table_meta in tables_meta:
        table_name = table_meta.get("name", "")
        schema = schema_by_table.get(table_name, "dbo")

        tmsl_tables.append(
            {
                "name": table_name,
                "columns": columns_by_table.get(table_name, []),
                "measures": [],  # filled in by merge_measures()
                "partitions": [
                    {
                        "name": f"{table_name}-partition",
                        "mode": "import",
                        "source": _build_m_partition_expression(connection, database_name, schema, table_name),
                    }
                ],
            }
        )

    tmsl_relationships = []
    relationships_needing_review = []
    for i, rel in enumerate(relationships_meta):
        left_table = rel.get("left_table", "")
        right_table = rel.get("right_table", "")
        left_column = rel.get("left_column", "")
        right_column = rel.get("right_column", "")

        if not (left_table and right_table and left_column and right_column):
            continue

        # Star-schema heuristic: the hub/fact table is the "many" side,
        # the other end is "one". This is genuinely inferred -- Tableau's
        # Relationships model doesn't encode fact/dimension roles the way
        # Power BI's cardinality model expects, so this always needs a
        # human check before go-live.
        if left_table == hub_table:
            from_table, from_column, to_table, to_column = left_table, left_column, right_table, right_column
        elif right_table == hub_table:
            from_table, from_column, to_table, to_column = right_table, right_column, left_table, left_column
        else:
            from_table, from_column, to_table, to_column = left_table, left_column, right_table, right_column

        relationship_def = {
            "name": f"rel_{i}_{from_table}_{to_table}",
            "fromTable": from_table,
            "fromColumn": from_column,
            "toTable": to_table,
            "toColumn": to_column,
            "fromCardinality": "many",
            "toCardinality": "one",
            "crossFilteringBehavior": "automatic",
        }
        tmsl_tables_names = {t["name"] for t in tmsl_tables}
        if from_table not in tmsl_tables_names or to_table not in tmsl_tables_names:
            continue

        tmsl_relationships.append(relationship_def)
        relationships_needing_review.append(
            {
                "relationship": f"{from_table}.{from_column} -> {to_table}.{to_column}",
                "reason": "Cardinality (many/one) inferred from relationship frequency, not confirmed against actual row counts.",
            }
        )

    model_bim = {
        "name": semantic_model_name,
        "compatibilityLevel": 1567,
        "model": {
            "culture": "en-US",
            "dataAccessOptions": {"legacyRedirects": True, "returnErrorValuesAsNull": True},
            "defaultPowerBIDataSourceVersion": "powerBI_V3",
            "sourceQueryCulture": "en-US",
            "tables": tmsl_tables,
            "relationships": tmsl_relationships,
        },
    }

    warnings = []
    if unattributed_fields:
        warnings.append(
            f"{len(unattributed_fields)} field(s) could not be attributed to a known table and were "
            f"skipped: {', '.join(unattributed_fields[:10])}"
            + (" ... (truncated)" if len(unattributed_fields) > 10 else "")
        )
    if not database_name:
        warnings.append(
            "No source database name was discovered; generated M partition queries are placeholders "
            "and will need a real data source connection configured in Power BI."
        )

    return {
        "model_bim": model_bim,
        "relationships_needing_review": relationships_needing_review,
        "warnings": warnings,
        "hub_table": hub_table,
    }


async def build_measures(
    ai_client: OpenAIAnalysisClient,
    workbook_bundle: dict[str, Any],
    hub_table: str,
    known_table_names: set[str],
    translate: bool,
) -> tuple[dict[str, list[dict[str, Any]]], list[dict[str, Any]]]:
    """Translates every Tableau calculated field (fields.calculated_fields)
    into a DAX measure and buckets the resulting TMSL measure defs by
    table name. Returns (measures_by_table, translation_records) where
    translation_records is the full MeasureTranslation-shaped list for
    the API response.

    Every calculated field becomes a MEASURE (not a calculated column).
    This is a deliberate scope choice: this app's calculated fields are
    overwhelmingly aggregate KPIs (see app.services.discovery.kpi_service),
    which map naturally onto DAX measures. Row-context calculated columns
    are a different DAX construct and aren't attempted here; any
    dimension-role calculated field would need manual conversion.
    """

    calculated_fields = workbook_bundle.get("fields", {}).get("calculated_fields", [])
    measures_by_table: dict[str, list[dict[str, Any]]] = {}
    translations: list[dict[str, Any]] = []

    for calc in calculated_fields:
        field_name = calc.get("name", "")
        display_name = calc.get("caption") or field_name
        formula = calc.get("formula", "")
        classification = calc.get("classification", "standard")
        data_type = calc.get("data_type", "string")

        table_name, _ = _attribute_field_to_table(field_name, hub_table, known_table_names)
        if table_name not in known_table_names:
            table_name = hub_table

        if not translate:
            translations.append(
                {
                    "tableau_field_name": field_name,
                    "dax_measure_name": display_name,
                    "tableau_formula": formula,
                    "dax_expression": "",
                    "confidence": "low",
                    "needs_review": True,
                    "review_reason": "translate_calculated_fields was false; DAX was not generated.",
                }
            )
            continue

        translation = await translate_formula_to_dax(ai_client, formula, data_type, classification, table_name)

        measures_by_table.setdefault(table_name, []).append(
            {
                "name": display_name,
                "expression": translation["dax_expression"] or "BLANK() /* translation failed, see notes */",
                "formatString": "0.00" if data_type == "real" else ("#,0" if data_type == "integer" else None),
            }
        )

        translations.append(
            {
                "tableau_field_name": field_name,
                "dax_measure_name": display_name,
                "tableau_formula": formula,
                "dax_expression": translation["dax_expression"],
                "confidence": translation["confidence"],
                "needs_review": translation["needs_review"],
                "review_reason": translation["review_reason"],
            }
        )

    return measures_by_table, translations


def merge_measures_into_model(model_bim: dict[str, Any], measures_by_table: dict[str, list[dict[str, Any]]]) -> None:
    """Mutates model_bim in place, attaching each table's measures."""
    for table in model_bim["model"]["tables"]:
        table["measures"] = measures_by_table.get(table["name"], [])
