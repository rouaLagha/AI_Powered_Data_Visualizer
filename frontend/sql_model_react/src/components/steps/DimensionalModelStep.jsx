import React from "react";
import Card from "../shared/Card.jsx";
import Badge from "../shared/Badge.jsx";
import SchemaFlowFrame from "../model/SchemaFlowFrame.jsx";
import WarningsPanel from "../model/WarningsPanel.jsx";

export default function DimensionalModelStep({ model, backendModel }) {
  const tables = model.tables || [];
  const warnings = Array.isArray(model.warnings) ? model.warnings : [];
  const factCount = tables.filter((table) => table.type === "fact").length;
  const dimensionCount = tables.filter((table) => table.type === "dimension").length;

  return (
    <Card title="5. Dimensional Model" eyebrow="Proposed semantic structure">
      <div className="model-grid">
        <SchemaFlowFrame model={model} backendModel={backendModel} />
        <aside className="model-summary">
          <div className="schema-legend">
            <h3>Legend</h3>
            <div className="legend-row">
              <span className="legend-dot legend-dot-fact" />
              <span>Fact table</span>
            </div>
            <div className="legend-row">
              <span className="legend-dot legend-dot-dimension" />
              <span>Dimension</span>
            </div>
            <div className="legend-row">
              <span className="legend-dot legend-dot-snowflake" />
              <span>Snowflake</span>
            </div>
            <div className="legend-row legend-relation">
              <span className="legend-line legend-line-star" />
              <span>Star (Fact - Dim)</span>
              <small>1 - *</small>
            </div>
            <div className="legend-row legend-relation">
              <span className="legend-line legend-line-snowflake" />
              <span>Snowflake (Dim - Dim)</span>
              <small>1 - *</small>
            </div>
          </div>

          <div className="schema-summary-panel">
            <h3>Schema summary</h3>
            <div className="summary-row"><span>Schema type</span><Badge tone="indigo">{model.schemaType}</Badge></div>
            <div className="summary-row"><span>Fact tables</span><strong>{factCount}</strong></div>
            <div className="summary-row"><span>Dimensions</span><strong>{dimensionCount}</strong></div>
            <div className="summary-row"><span>Relationships</span><strong>{model.relationships.length}</strong></div>
            <div className="summary-row"><span>Measures</span><strong>{model.measures.length}</strong></div>
          </div>

          {warnings.length > 0 && (
            <div className="schema-summary-panel">
              <h3>Warnings</h3>
              <WarningsPanel warnings={warnings} />
            </div>
          )}
        </aside>
      </div>
    </Card>
  );
}
