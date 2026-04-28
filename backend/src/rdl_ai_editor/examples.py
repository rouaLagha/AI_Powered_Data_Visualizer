from __future__ import annotations

SAMPLE_RDL_XML = """<?xml version=\"1.0\" encoding=\"utf-8\"?>
<Report xmlns=\"http://schemas.microsoft.com/sqlserver/reporting/2016/01/reportdefinition\">
  <DataSets>
    <DataSet Name=\"dsMain\">
      <Fields>
        <Field Name=\"Sales\"><DataField>Sales</DataField></Field>
        <Field Name=\"Month\"><DataField>Month</DataField></Field>
      </Fields>
    </DataSet>
  </DataSets>
  <Body>
    <ReportItems>
      <Textbox Name=\"ReportTitle\">
        <Paragraphs>
          <Paragraph><TextRuns><TextRun><Value>Regional Sales</Value></TextRun></TextRuns></Paragraph>
        </Paragraphs>
      </Textbox>
      <Chart Name=\"main_chart\">
        <ChartData>
          <ChartSeriesCollection>
            <ChartSeries Name=\"Sales\"><Type>Line</Type></ChartSeries>
          </ChartSeriesCollection>
        </ChartData>
      </Chart>
    </ReportItems>
  </Body>
</Report>
"""

SAMPLE_QUERY = "Add a filter on sales for 2022 and change the chart to a bar chart"

SAMPLE_PATCH_JSON = {
    "operations": [
        {
            "op": "add_filter",
            "target_dataset": "dsMain",
            "field": "Sales",
            "operator": "=",
            "value": 2022,
        },
        {
            "op": "change_chart_type",
            "target": "main_chart",
            "to": "bar",
        },
    ]
}

SAMPLE_MODIFIED_RDL_XML = """<?xml version='1.0' encoding='utf-8'?>
<Report xmlns=\"http://schemas.microsoft.com/sqlserver/reporting/2016/01/reportdefinition\">
  <DataSets>
    <DataSet Name=\"dsMain\">
      <Fields>
        <Field Name=\"Sales\"><DataField>Sales</DataField></Field>
        <Field Name=\"Month\"><DataField>Month</DataField></Field>
      </Fields>
      <Filters>
        <Filter>
          <FilterExpression>=Fields!Sales.Value</FilterExpression>
          <Operator>Equal</Operator>
          <FilterValues><FilterValue>=2022</FilterValue></FilterValues>
        </Filter>
      </Filters>
    </DataSet>
  </DataSets>
  <Body>
    <ReportItems>
      <Textbox Name=\"ReportTitle\">...</Textbox>
      <Chart Name=\"main_chart\">...
        <Type>Bar</Type>
      </Chart>
    </ReportItems>
  </Body>
</Report>
"""
