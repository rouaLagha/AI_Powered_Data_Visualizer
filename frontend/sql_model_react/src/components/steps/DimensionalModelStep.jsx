import React from "react";
import Card from "../shared/Card.jsx";
import Badge from "../shared/Badge.jsx";
import SchemaFlowFrame from "../model/SchemaFlowFrame.jsx";

export default function DimensionalModelStep({ model, backendModel }) {
  const tables = model.tables || [];
  const factCount = tables.filter((table) => table.type === "fact").length;
  const dimensionCount = tables.filter((table) => table.type === "dimension").length;

  return (
    <Card title="5. Dimensional Model" eyebrow="Proposed semantic structure">
      <div className="model-grid">
        <SchemaFlowFrame model={model} backendModel={backendModel} />
        <aside className="model-summary">
          <h3>Schema summary</h3>
          <div className="summary-row"><span>Schema type</span><Badge tone="indigo">{model.schemaType}</Badge></div>
          <div className="summary-row"><span>Fact tables</span><strong>{factCount}</strong></div>
          <div className="summary-row"><span>Dimensions</span><strong>{dimensionCount}</strong></div>
          <div className="summary-row"><span>Relationships</span><strong>{model.relationships.length}</strong></div>
          <div className="summary-row"><span>Measures</span><strong>{model.measures.length}</strong></div>
        </aside>
      </div>
    </Card>
  );
}
