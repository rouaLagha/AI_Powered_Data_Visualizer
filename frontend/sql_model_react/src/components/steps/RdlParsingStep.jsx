import React from "react";
import Card from "../shared/Card.jsx";
import Badge from "../shared/Badge.jsx";
import Button from "../shared/Button.jsx";

export default function RdlParsingStep({ parsingSummary, datasets, onContinue }) {
  const metrics = [
    { label: "Datasets", value: parsingSummary.datasetsFound },
    { label: "Data sources", value: parsingSummary.dataSourcesFound },
    { label: "Parameters", value: parsingSummary.parametersFound },
    { label: "SQL queries", value: parsingSummary.sqlQueriesExtracted },
  ];

  return (
    <Card title="2. RDL parsing" eyebrow="Extracted metadata">
      <div className="metric-grid">
        {metrics.map(({ label, value }) => (
          <div className="metric-card" key={label}>
            <strong>{value}</strong>
            <span>{label}</span>
          </div>
        ))}
      </div>

      <div className="table-wrap">
        <table>
          <thead>
            <tr>
              <th>Dataset</th>
              <th>Source</th>
              <th>Fields</th>
              <th>Status</th>
            </tr>
          </thead>
          <tbody>
            {datasets.map((dataset) => (
              <tr key={dataset.id}>
                <td><strong>{dataset.name}</strong></td>
                <td>{dataset.dataSource || "Unknown"}</td>
                <td>{dataset.fieldCount}</td>
                <td>
                  <Badge tone={dataset.status === "Ready" ? "green" : "orange"}>
                    {dataset.status}
                  </Badge>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      <div className="button-row">
        <Button variant="primary" onClick={onContinue}>Choose dataset</Button>
      </div>
    </Card>
  );
}
