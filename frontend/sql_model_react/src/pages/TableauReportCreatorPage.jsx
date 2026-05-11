import React, { useMemo, useState } from "react";
import Badge from "../components/shared/Badge.jsx";
import Button from "../components/shared/Button.jsx";
import Card from "../components/shared/Card.jsx";
import CodePreview from "../components/shared/CodePreview.jsx";

const VISUAL_TYPES = ["bar", "line", "scatter", "text table", "map", "kpi"];

function parseList(value) {
  return String(value || "")
    .split(/[\n,;]/)
    .map((item) => item.trim())
    .filter(Boolean);
}

function normalizeName(value, fallback) {
  return String(value || "")
    .trim()
    .replace(/\s+/g, " ")
    || fallback;
}

function fileStem(value) {
  return normalizeName(value, "tableau_report")
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "_")
    .replace(/^_+|_+$/g, "")
    || "tableau_report";
}

function xmlAttr(value) {
  return String(value || "")
    .replace(/&/g, "&amp;")
    .replace(/"/g, "&quot;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;");
}

function fieldName(value) {
  return `[${String(value || "").replace(/[\[\]]/g, "").trim()}]`;
}

function inferFocus(brief) {
  const text = String(brief || "").toLowerCase();
  if (text.includes("trend") || text.includes("time") || text.includes("month") || text.includes("year")) return "trend analysis";
  if (text.includes("map") || text.includes("region") || text.includes("country")) return "geographic performance";
  if (text.includes("compare") || text.includes("vs") || text.includes("variance")) return "comparison";
  if (text.includes("kpi") || text.includes("scorecard") || text.includes("target")) return "executive KPI tracking";
  return "exploratory dashboard";
}

function buildSheets({ dimensions, measures, filters, visualType }) {
  const primaryDimension = dimensions[0] || "Category";
  const primaryMeasure = measures[0] || "Value";
  const sheets = [
    {
      name: "Executive Overview",
      visual_type: visualType,
      columns: [primaryDimension],
      rows: [primaryMeasure],
      filters,
    },
  ];

  measures.slice(1, 4).forEach((measure, index) => {
    sheets.push({
      name: `${measure} Detail`,
      visual_type: index % 2 === 0 ? "bar" : "line",
      columns: dimensions.slice(0, 2).length ? dimensions.slice(0, 2) : [primaryDimension],
      rows: [measure],
      filters,
    });
  });

  if (filters.length) {
    sheets.push({
      name: "Filtered View",
      visual_type: "text table",
      columns: dimensions.slice(0, 3).length ? dimensions.slice(0, 3) : [primaryDimension],
      rows: measures.slice(0, 3).length ? measures.slice(0, 3) : [primaryMeasure],
      filters,
    });
  }

  return sheets;
}

function buildWorkbookXml({ workbookName, datasourceName, dimensions, measures, sheets }) {
  const fieldNodes = [
    ...dimensions.map((dimension) => ({
      name: fieldName(dimension),
      caption: dimension,
      role: "dimension",
      type: "nominal",
      datatype: "string",
    })),
    ...measures.map((measure) => ({
      name: fieldName(measure),
      caption: measure,
      role: "measure",
      type: "quantitative",
      datatype: "real",
    })),
  ];

  const columnsXml = fieldNodes
    .map(
      (field) =>
        `      <column caption="${xmlAttr(field.caption)}" datatype="${field.datatype}" name="${xmlAttr(field.name)}" role="${field.role}" type="${field.type}" />`,
    )
    .join("\n");

  const worksheetsXml = sheets
    .map(
      (sheet) => `    <worksheet name="${xmlAttr(sheet.name)}">
      <table>
        <view>
          <datasources>
            <datasource caption="${xmlAttr(datasourceName)}" name="${xmlAttr(datasourceName)}" />
          </datasources>
        </view>
        <style />
      </table>
    </worksheet>`,
    )
    .join("\n");

  return `<?xml version="1.0" encoding="utf-8"?>
<workbook source-build="AI Tableau report creator" version="18.1">
  <preferences />
  <datasources>
    <datasource caption="${xmlAttr(datasourceName)}" inline="true" name="${xmlAttr(datasourceName)}">
${columnsXml || "      <column caption=\"Value\" datatype=\"real\" name=\"[Value]\" role=\"measure\" type=\"quantitative\" />"}
    </datasource>
  </datasources>
  <worksheets>
${worksheetsXml}
  </worksheets>
  <dashboards>
    <dashboard name="${xmlAttr(workbookName)} Dashboard">
      <style />
    </dashboard>
  </dashboards>
</workbook>`;
}

function dataUrl(content, type) {
  return `data:${type};charset=utf-8,${encodeURIComponent(content)}`;
}

export default function TableauReportCreatorPage() {
  const [workbookName, setWorkbookName] = useState("Executive Tableau Report");
  const [datasourceName, setDatasourceName] = useState("Primary datasource");
  const [brief, setBrief] = useState("Show revenue, margin, and quantity by region and month.");
  const [dimensionsText, setDimensionsText] = useState("Region\nMonth\nProduct Category");
  const [measuresText, setMeasuresText] = useState("Revenue\nMargin\nQuantity");
  const [filtersText, setFiltersText] = useState("Year\nRegion");
  const [visualType, setVisualType] = useState("bar");
  const [density, setDensity] = useState("balanced");
  const [generatedAt, setGeneratedAt] = useState("");

  const blueprint = useMemo(() => {
    const dimensions = parseList(dimensionsText);
    const measures = parseList(measuresText);
    const filters = parseList(filtersText);
    const normalizedWorkbookName = normalizeName(workbookName, "Tableau Report");
    const normalizedDatasourceName = normalizeName(datasourceName, "Primary datasource");
    const sheets = buildSheets({ dimensions, measures, filters, visualType });

    return {
      workbook_name: normalizedWorkbookName,
      datasource_name: normalizedDatasourceName,
      report_brief: normalizeName(brief, "Create a Tableau report."),
      ai_focus: inferFocus(brief),
      layout_density: density,
      fields: {
        dimensions,
        measures,
        filters,
      },
      sheets,
      dashboard: {
        name: `${normalizedWorkbookName} Dashboard`,
        sheet_order: sheets.map((sheet) => sheet.name),
      },
      generated_at: generatedAt || null,
    };
  }, [brief, datasourceName, density, dimensionsText, filtersText, generatedAt, measuresText, visualType, workbookName]);

  const blueprintJson = JSON.stringify(blueprint, null, 2);
  const workbookXml = buildWorkbookXml({
    workbookName: blueprint.workbook_name,
    datasourceName: blueprint.datasource_name,
    dimensions: blueprint.fields.dimensions,
    measures: blueprint.fields.measures,
    sheets: blueprint.sheets,
  });
  const stem = fileStem(blueprint.workbook_name);
  const ready = Boolean(blueprint.fields.dimensions.length || blueprint.fields.measures.length);

  return (
    <div className="page-stack tableau-creator-page">
      <Card
        title="AI Tableau report creator"
        eyebrow="Workbook draft"
        actions={<Badge tone={ready ? "green" : "orange"}>{ready ? "Draft ready" : "Needs fields"}</Badge>}
      >
        <div className="tableau-creator-form">
          <label>
            Workbook name
            <input value={workbookName} onChange={(event) => setWorkbookName(event.target.value)} />
          </label>
          <label>
            Datasource name
            <input value={datasourceName} onChange={(event) => setDatasourceName(event.target.value)} />
          </label>
          <label>
            Default visual
            <select value={visualType} onChange={(event) => setVisualType(event.target.value)}>
              {VISUAL_TYPES.map((type) => (
                <option value={type} key={type}>
                  {type}
                </option>
              ))}
            </select>
          </label>
          <label>
            Layout density
            <select value={density} onChange={(event) => setDensity(event.target.value)}>
              <option value="compact">compact</option>
              <option value="balanced">balanced</option>
              <option value="spacious">spacious</option>
            </select>
          </label>
          <label className="creator-wide">
            Report brief
            <textarea value={brief} onChange={(event) => setBrief(event.target.value)} />
          </label>
          <label>
            Dimensions
            <textarea value={dimensionsText} onChange={(event) => setDimensionsText(event.target.value)} />
          </label>
          <label>
            Measures
            <textarea value={measuresText} onChange={(event) => setMeasuresText(event.target.value)} />
          </label>
          <label>
            Filters
            <textarea value={filtersText} onChange={(event) => setFiltersText(event.target.value)} />
          </label>
        </div>

        <div className="button-row">
          <Button variant="primary" onClick={() => setGeneratedAt(new Date().toISOString())} disabled={!ready}>
            Generate draft
          </Button>
          <a className="btn btn-secondary btn-md" href={dataUrl(blueprintJson, "application/json")} download={`${stem}_blueprint.json`}>
            Export blueprint
          </a>
          <a className="btn btn-secondary btn-md" href={dataUrl(workbookXml, "application/xml")} download={`${stem}_starter.twb`}>
            Export TWB starter
          </a>
        </div>
      </Card>

      <div className="tableau-creator-board">
        <Card title="Report blueprint" eyebrow={generatedAt ? "Generated" : "Live draft"}>
          <CodePreview code={blueprintJson} language="json" />
        </Card>
        <Card title="Workbook structure" eyebrow={`${blueprint.sheets.length} sheets`}>
          <div className="creator-sheet-list">
            {blueprint.sheets.map((sheet) => (
              <div className="creator-sheet" key={sheet.name}>
                <div>
                  <strong>{sheet.name}</strong>
                  <span>{sheet.visual_type}</span>
                </div>
                <div className="creator-sheet-fields">
                  <span>Columns: {sheet.columns.join(", ") || "-"}</span>
                  <span>Rows: {sheet.rows.join(", ") || "-"}</span>
                  <span>Filters: {sheet.filters.join(", ") || "-"}</span>
                </div>
              </div>
            ))}
          </div>
        </Card>
      </div>
    </div>
  );
}
