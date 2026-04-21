# RDL AI Editor - End-to-End Example

This feature is implemented as a new independent flow and does not modify the existing RDL->TWB pipeline.

## Flow

1. Input: RDL XML + natural language query.
2. NLP stage: Convert query to structured patch JSON operations.
3. Validation stage: Enforce strict schema and normalize operations.
4. XML stage: Apply deterministic XPath-based modifications to RDL.
5. Output: Modified RDL XML + operation logs.

## Example

Natural language query:

```
Add a filter on Sales for 2022 and change chart main_chart to bar.
```

Patch JSON:

```json
{
  "operations": [
    {
      "op": "add_filter",
      "target_dataset": "dsMain",
      "field": "Sales",
      "operator": "=",
      "value": 2022
    },
    {
      "op": "change_chart_type",
      "target": "main_chart",
      "to": "bar"
    }
  ]
}
```

After validation and normalization:

```json
{
  "operations": [
    {
      "op": "add_filter",
      "target_dataset": "dsMain",
      "field": "Sales",
      "operator": "Equal",
      "value": 2022,
      "value_kind": "literal"
    },
    {
      "op": "change_chart_type",
      "target": "main_chart",
      "to": "Bar"
    }
  ]
}
```

The deterministic XML engine then updates the report by adding the filter node and changing chart type nodes.

## Complete Filter Removal Example

Natural language query:

Remove the CalendarYear filter completely from the report.

Expected patch JSON:

{
  "operations": [
    {
      "op": "remove_query_condition",
      "target_dataset": "dsMain",
      "condition_text": "dc.CalendarYear = @CalendarYear"
    },
    {
      "op": "remove_query_parameter",
      "target_dataset": "dsMain",
      "parameter": "@CalendarYear"
    },
    {
      "op": "remove_report_parameter",
      "parameter": "CalendarYear"
    },
    {
      "op": "remove_expression_references",
      "parameter": "CalendarYear"
    }
  ]
}

What the engine guarantees:

1. Removes the SQL condition and cleans dangling WHERE/AND/OR syntax.
2. Removes matching QueryParameter entries.
3. Removes matching ReportParameter definitions and layout references.
4. Removes parameter expression references without leaving broken expressions.
