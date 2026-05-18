from __future__ import annotations

import json


SEMANTIC_SYSTEM_PROMPT = """You are Agent-1 in a BI report conversion pipeline.
Goal: transform parsed RDL metadata into exactly 3 semantic JSON models.

STRICT OUTPUT POLICY:
- Return JSON only (single object)
- No markdown, no prose, no code fences
- Do NOT omit required keys
- Do NOT add extra keys

GROUNDING:
- Use ONLY provided PARSED_RDL as source of truth
- Do NOT hallucinate fields, datasets, visuals, or parameters
- If something is missing → use empty array or null

INFERENCE RULE:
- Only infer if absolutely necessary
- Add: "inferred": true and "reason": "..."

DETERMINISM:
- Preserve input order
- Use stable naming (no random suffixes)
"""


XML_SYSTEM_PROMPT = """You are Agent-2 in a BI report conversion pipeline.
Goal: transform semantic JSON models into Tableau TWB XML.

STRICT OUTPUT POLICY:
- Return XML only
- No markdown, no prose, no JSON, no code fences
- Output must be a single valid workbook XML document (one root element only)

SCHEMA RULES:
- Root must be <workbook>
- Use ONLY elements/attributes from TWB_XSD_SUMMARY
- Do NOT generate unknown attributes

REFERENCE TARGET PROFILE (inspired by known-working TWB files):
- Keep workbook header attributes: version, source-build, source-platform
- Include top-level sections: preferences, style, datasources, worksheets, windows
- Datasource profile:
    - <datasource name="..." caption="..." inline="true" hasconnection="true">
    - Prefer SQL Server federated pattern when source is SQL-like:
        <connection class="federated">
            <named-connections>
                <named-connection name="sqlserver.<id>" caption="<server>">
                    <connection class="sqlserver" server="..." dbname="..." authentication="sspi|username-password|prompt" />
                </named-connection>
            </named-connections>
            <relation type="collection">...</relation>
            <cols><map key="[Field]" value="[Table].[Field]" /></cols>
        </connection>
    - Include datasource columns: <column name="[...]" role="dimension|measure" datatype="string|real|date" type="nominal|quantitative|ordinal" caption="..." />
    - Include datasource layout/style nodes only when needed, but NEVER emit <layout-options> under <datasource>
- Worksheet profile:
    - <worksheet name="..."> with <layout-options /> and <table>
    - Table must contain: view, style, panes, rows, cols
    - View should contain: datasources, datasource-dependencies, perspectives, aggregation
    - datasource-dependencies should include both <column> and matching <column-instance>
    - Rows/cols should reference valid generated instances like [DataSource].[sum:Measure:qk] and [DataSource].[none:Dimension:nk]
    - KPI/text-card worksheets MUST use <mark class="Text" />, keep <rows /> and <cols /> empty, and put the KPI value on the Text mark encoding:
        <encodings><text column="[DataSource].[sum:Measure:qk]" /></encodings>
    - Never place a KPI measure on Rows or Columns; that creates an unwanted axis instead of a KPI card
    - RDL filters/report parameters are NOT worksheets. Do not create sheets named "Filter - ..."; apply them as filters/parameter controls on the relevant worksheet views.
- Dashboard composition rules:
    - Create exactly one worksheet per visual element from VISUAL_MODEL.sheets
    - Create at least one dashboard
    - Place every generated worksheet into dashboard zones (no orphan worksheet)
    - Dashboard windows/viewpoints must reference all worksheet names

    IMPORTANT GROUNDING OVERRIDE:
    - The reference profile is a STRUCTURAL STYLE only
    - Do NOT copy sample table names, field names, worksheet names, or joins from any example
    - Every datasource/field/worksheet/dependency in output MUST come from DATA_MODEL, VISUAL_MODEL, and MAPPING
    - If a field is not present in semantic inputs, do not emit it

COHERENCE RULES:
- Every field referenced in rows/cols/encodings MUST exist in datasource-dependencies column-instance names
- Every datasource-dependencies column-instance MUST reference an existing datasource-dependencies column
- Keep worksheet names, datasource names, and connection semantics stable

DO:
- Keep output grounded to DATA_MODEL, VISUAL_MODEL, and MAPPING
- Keep worksheet list aligned with VISUAL_MODEL.sheets
- Keep a strict 1:1 mapping between visual elements and worksheets
- Ensure all worksheets are placed into a dashboard layout
- Keep datasource fields aligned with DATA_MODEL.fields
- If SQL projections define aliases, prefer physical source column names for TWB field names and keep aliases as captions when appropriate
- Keep <layout-options> only as a direct child of <worksheet>

DON'T:
- Don't invent extra worksheets
- Don't invent extra fields or rename aliases unless semantic inputs explicitly provide this mapping
- Don't copy literal names from examples that are absent from current inputs
- Don't put worksheet-only nodes (<layout-options>, <table>, <view>, <rows>, <cols>, <panes>) inside <datasource>
- Don't leave any worksheet outside dashboards

CRITICAL:
- Prefer minimal valid XML over complex invalid XML
- Preserve datasource connection info and worksheet names
"""


XML_FEW_SHOT_GUIDANCE = {
    "input_signature_example": {
        "datasource_name": "AdventureWorksDW2022",
        "dataset_name": "DataSet1",
        "dataset_fields": ["Country", "TotalSales"],
        "sheet_names": ["ReportTitle", "Chart3"],
        "sheet_kinds": {
            "ReportTitle": "Textbox",
            "Chart3": "Chart",
        },
    },
    "good_output_fragments": [
        "<datasource name=\"AdventureWorksDW2022\" ...>",
        "<column name=\"[Country]\" role=\"dimension\" ... />",
        "<column name=\"[TotalSales]\" role=\"measure\" ... />",
        "<worksheet name=\"ReportTitle\"> ... <mark class=\"Text\" /> ... <rows /> <cols /> ... </worksheet>",
        "<worksheet name=\"TotalSalesKPI\"> ... <mark class=\"Text\" /> <encodings><text column=\"[AdventureWorksDW2022].[sum:TotalSales:qk]\" /></encodings> ... <rows /> <cols /> ... </worksheet>",
        "<worksheet name=\"Chart3\"> ... <mark class=\"Bar\" /> ... <rows>[AdventureWorksDW2022].[sum:TotalSales:qk]</rows> <cols>[AdventureWorksDW2022].[none:Country:nk]</cols> ... </worksheet>",
    ],
    "do_rules": [
        "Use worksheet names from VISUAL_MODEL.sheets",
        "Use only field names from DATA_MODEL.fields for columns, column-instances, rows, cols, and encodings",
        "Keep datasource names from DATA_MODEL.datasources",
        "When dataset SQL has alias projections (e.g. source AS Alias), prefer source column name in TWB field name and alias as caption",
        "When visual type is Textbox/title-like, keep shelves empty (rows/cols)",
        "When a Textbox represents a numeric KPI, put the measure in the Text mark encoding and keep rows/cols empty",
        "For chart-like visuals, place measure on rows and dimension on cols",
        "Represent report filters as worksheet view filters/cards, not as separate worksheets",
        "Use <layout-options> only inside <worksheet>, never inside <datasource>",
    ],
    "dont_rules": [
        "Do not emit fields absent from DATA_MODEL.fields",
        "Do not put KPI measures on rows or columns",
        "Do not create dedicated filter worksheets or dashboard zones for filters",
        "Do not rename aliases (example: Country -> SalesTerritoryCountry) unless explicitly present in semantic inputs",
        "Do not add worksheets that are not present in VISUAL_MODEL.sheets",
        "Do not invent joins/tables unrelated to dataset SQL",
        "Do not copy literal table/field/worksheet names from reference examples unless they are present in current inputs",
        "Do not place worksheet-only tags (layout-options, table, view, panes, rows, cols) under datasource",
    ],
}


XML_REPAIR_SYSTEM_PROMPT = """You are a Tableau TWB XML repair agent.

INPUT:
- Invalid TWB XML
- Validation issues

GOAL:
- Fix XML while preserving business meaning

STRICT OUTPUT:
- XML only
- No markdown, no explanations

RULES:
- Preserve datasource names and connections
- Preserve worksheet names
- Apply minimal changes
"""


def build_semantic_user_prompt(
    parsed_rdl: dict,
    rdl_xsd_summary: dict,
    twb_xsd_summary: dict,
) -> str:
    output_contract = {
        "required_root_keys": ["data_model", "visual_model", "mapping"],
        "data_model": {
            "required_keys": ["datasources", "datasets", "fields", "parameters"]
        },
        "visual_model": {
            "required_keys": ["sheets", "visuals", "layout", "interactions"]
        },
        "mapping": {
            "required_keys": ["visual_to_dataset", "visual_to_fields", "parameter_usage"]
        }
    }

    transformation_rules = {
        "grounding": [
            "Use PARSED_RDL only",
            "Never invent data",
        ],
        "anti_hallucination": [
            "Do not create fields/datasets/visuals not present in PARSED_RDL",
            "If missing, return empty arrays instead of guessing",
        ],
        "integrity": [
            "Every visual MUST be mapped in visual_to_dataset",
            "Every mapped dataset MUST exist in data_model.datasets",
            "Every mapped field MUST exist OR be explicitly inferred",
        ],
        "determinism": [
            "Preserve order from PARSED_RDL",
            "No random naming",
        ],
        "normalization": [
            "All required keys must be present",
            "Use null or [] instead of omitting values",
            "No extra keys",
        ]
    }

    return (
        "Task: Generate ONE JSON object with keys: data_model, visual_model, mapping.\n"
        "Strictly follow OUTPUT_CONTRACT and TRANSFORMATION_RULES.\n\n"
        f"OUTPUT_CONTRACT={json.dumps(output_contract, ensure_ascii=False)}\n"
        f"TRANSFORMATION_RULES={json.dumps(transformation_rules, ensure_ascii=False)}\n\n"
        "INPUTS:\n"
        f"PARSED_RDL={json.dumps(parsed_rdl, ensure_ascii=False)}\n"
        f"RDL_XSD_SUMMARY={json.dumps(rdl_xsd_summary, ensure_ascii=False)}\n"
        f"TWB_XSD_SUMMARY={json.dumps(twb_xsd_summary, ensure_ascii=False)}\n\n"
        "HARD CONSTRAINTS:\n"
        "1) JSON only\n"
        "2) No markdown\n"
        "3) No extra top-level keys\n"
        "4) All required keys must exist\n"
        "5) Respect types strictly\n"
    )


def build_xml_user_prompt(
    data_model: dict,
    visual_model: dict,
    mapping: dict,
    twb_xsd_summary: dict,
) -> str:
    xml_contract = {
        "root": "workbook",
        "required_top_level": ["datasources", "worksheets", "windows"],
        "dashboard_requirements": [
            "at least one dashboard when worksheets exist",
            "every worksheet name must appear in dashboard zones",
            "dashboard window viewpoints should reference all worksheets"
        ],
        "required_workbook_attributes": ["version", "source-build", "source-platform"],
        "recommended_top_level": ["preferences", "style"],
        "datasource_required_attributes": ["name", "caption", "inline", "hasconnection"],
        "datasource_forbidden_children": ["layout-options", "table", "view", "rows", "cols", "panes"],
        "worksheet_structure": ["layout-options", "table"],
        "table_structure": ["view", "style", "panes", "rows", "cols"],
        "view_structure": ["datasources", "datasource-dependencies", "aggregation"],
        "datasource_dependency_structure": ["column", "column-instance"],
        "column_instance_patterns": ["[none:<Field>:nk]", "[sum:<Field>:qk]"],
        "forbidden": [
            "markdown",
            "json",
            "explanations",
            "simple-id",
            "worksheet-number",
            "datagraph",
            "explain-data"
        ]
    }

    xml_strategy = {
        "priority": [
            "Valid XML structure FIRST",
            "Then preserve semantics",
            "Then mirror known-working TWB patterns for datasource and worksheet dependencies",
        ],
        "fallback": [
            "If unsure → generate minimal valid workbook",
        ],
        "connection_strategy": [
            "For SQL-like providers, prefer a federated connection with named-connections",
            "Use SQL Server inner connection class when provider is SQL Server",
            "Keep authentication consistent with source metadata",
        ],
        "relation_strategy": [
            "Prefer relation type=collection with table relations when table metadata is inferable",
            "Otherwise use a single safe text relation with sanitized SQL",
        ],
        "dependency_strategy": [
            "Create datasource-dependencies with columns and matching column-instances",
            "Ensure rows/cols references point to existing instances",
            "Use only fields present in DATA_MODEL.fields and mappings",
            "Use worksheet names from VISUAL_MODEL.sheets (or mapped visual names)",
        ],
        "dashboard_strategy": [
            "Create at least one dashboard",
            "Place all generated worksheets into dashboard zones",
            "Ensure dashboard windows/viewpoints cover all worksheets",
        ],
        "kpi_strategy": [
            "For numeric KPI cards, use a Text mark with a text encoding bound to the aggregate measure",
            "Keep KPI rows and cols empty so Tableau renders a single value instead of an axis",
            "Do not place KPI measures on Columns or Rows",
        ],
        "filter_strategy": [
            "Do not generate filter worksheets",
            "Use existing worksheet views for report filters by adding filter nodes and exposing filter cards",
            "Apply a report parameter/filter to every worksheet whose datasource contains the filtered field",
        ],
        "sql_rules": [
            "For SQL Server: ORDER BY only allowed with TOP/OFFSET/FOR XML",
            "Otherwise REMOVE ORDER BY",
            "No trailing semicolon",
        ],
        "skeleton": [
            "<workbook>",
            "  <datasources>",
            "  <worksheets>",
            "    <worksheet>",
            "      <table>",
            "        <view>",
            "  <windows>",
            "</workbook>"
        ]
    }

    return (
        "Task: Generate valid Tableau TWB XML.\n"
        "Output MUST be strictly XML.\n\n"
        f"XML_CONTRACT={json.dumps(xml_contract, ensure_ascii=False)}\n"
        f"XML_STRATEGY={json.dumps(xml_strategy, ensure_ascii=False)}\n\n"
        f"FEW_SHOT_GUIDANCE={json.dumps(XML_FEW_SHOT_GUIDANCE, ensure_ascii=False)}\n\n"
        f"TWB_XSD_SUMMARY={json.dumps(twb_xsd_summary, ensure_ascii=False)}\n"
        f"DATA_MODEL={json.dumps(data_model, ensure_ascii=False)}\n"
        f"VISUAL_MODEL={json.dumps(visual_model, ensure_ascii=False)}\n"
        f"MAPPING={json.dumps(mapping, ensure_ascii=False)}\n\n"
        "HARD CONSTRAINTS:\n"
        "1) XML only\n"
        "2) No markdown or JSON\n"
        "3) No undeclared attributes\n"
        "4) Respect required structure\n"
        "5) Emit exactly one workbook root document (no surrounding text)\n"
    )


def build_xml_repair_user_prompt(
    invalid_xml: str,
    issues: list[str],
    twb_xsd_summary: dict,
) -> str:
    repair_contract = {
        "objective": "Fix XML structure while preserving semantics",
        "must_fix": [
            "Invalid root or attributes",
            "Wrong element order",
            "Missing required nodes",
            "Forbidden elements",
            "Malformed structures",
            "Illegal parent-child placement (example: layout-options under datasource)",
            "Missing dashboard when worksheets exist",
            "Worksheets not placed in dashboard zones",
        ],
        "must_preserve": [
            "datasource names",
            "connection info",
            "worksheet names",
        ]
    }

    return (
        "Task: Repair invalid Tableau TWB XML.\n\n"
        f"REPAIR_CONTRACT={json.dumps(repair_contract, ensure_ascii=False)}\n"
        f"ISSUES={json.dumps(issues, ensure_ascii=False)}\n"
        f"TWB_XSD_SUMMARY={json.dumps(twb_xsd_summary, ensure_ascii=False)}\n\n"
        f"INVALID_XML={invalid_xml}\n\n"
        "REPAIR STEPS:\n"
        "1) Keep workbook root\n"
        "2) Fix structure and order\n"
        "3) Remove or relocate forbidden elements to valid parent nodes\n"
        "4) Fix invalid attributes\n"
        "5) Ensure required sections exist\n"
        "6) Return XML only\n"
    )
