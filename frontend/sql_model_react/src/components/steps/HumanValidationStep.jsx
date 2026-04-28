import React from "react";
import Card from "../shared/Card.jsx";
import Tabs from "../shared/Tabs.jsx";
import Badge from "../shared/Badge.jsx";
import Button from "../shared/Button.jsx";
import RelationshipEditor from "../model/RelationshipEditor.jsx";
import MeasuresPanel from "../model/MeasuresPanel.jsx";

export default function HumanValidationStep({
  model,
  changeHistory,
  onApprove,
  onRequestReanalysis,
  onAddRelationship,
  onDeleteRelationship,
  validationStatus,
}) {
  const hasModel = Boolean(model.tables.length);
  const tableRows = (
    <div className="table-wrap">
      <table>
        <thead>
          <tr>
            <th>Table</th>
            <th>Type</th>
          </tr>
        </thead>
        <tbody>
          {model.tables.map((table) => (
            <tr key={table.id}>
              <td>{table.name}</td>
              <td><Badge tone={table.type === "fact" ? "indigo" : "green"}>{table.type}</Badge></td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );

  return (
    <Card title="6. Validation" eyebrow="Human approval">
      <Tabs
        tabs={[
          { id: "tables", label: "Tables", content: tableRows },
          {
            id: "relationships",
            label: "Relationships",
            content: (
              <RelationshipEditor
                relationships={model.relationships}
                onAddRelationship={onAddRelationship}
                onDeleteRelationship={onDeleteRelationship}
              />
            ),
          },
          { id: "measures", label: "Measures", content: <MeasuresPanel measures={model.measures} /> },
        ]}
      />

      <div className="validation-footer">
        <Badge tone={validationStatus === "approved" ? "green" : "orange"}>{validationStatus}</Badge>
        <div className="button-row">
          <Button onClick={onRequestReanalysis}>Re-run analysis</Button>
          <Button variant="primary" onClick={onApprove} disabled={!hasModel}>Approve model</Button>
        </div>
      </div>
    </Card>
  );
}
