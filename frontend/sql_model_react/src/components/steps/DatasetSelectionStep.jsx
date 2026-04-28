import React, { useState } from "react";
import Card from "../shared/Card.jsx";
import Button from "../shared/Button.jsx";
import Badge from "../shared/Badge.jsx";
import CodePreview from "../shared/CodePreview.jsx";

export default function DatasetSelectionStep({ datasets, selectedDataset, setSelectedDataset, sqlText, setSqlText, onAnalyze }) {
  const [editing, setEditing] = useState(false);

  async function choose(dataset) {
    setSqlText(dataset.sql || "");
    await setSelectedDataset(dataset);
  }

  return (
    <Card title="3. Dataset and SQL" eyebrow="Model input">
      <div className="split-grid">
        <div className="dataset-list">
          {datasets.map((dataset) => (
            <button
              key={dataset.id}
              className={`dataset-item ${selectedDataset?.id === dataset.id ? "selected" : ""}`}
              onClick={() => choose(dataset)}
              type="button"
            >
              <strong>{dataset.name}</strong>
              <span>{dataset.dataSource || "Unknown source"}</span>
              <small>{dataset.fieldCount} fields</small>
            </button>
          ))}
        </div>

        <div className="sql-panel">
          <div className="panel-subheader">
            <strong>SQL preview</strong>
            <Badge tone="indigo">{selectedDataset?.queryType || "No dataset"}</Badge>
          </div>
          {editing ? (
            <textarea
              className="sql-editor"
              value={sqlText}
              onChange={(event) => setSqlText(event.target.value)}
              placeholder="Enter SQL here..."
            />
          ) : (
            <CodePreview code={sqlText} />
          )}
        </div>
      </div>

      <div className="button-row">
        <Button onClick={() => setEditing((value) => !value)} disabled={!selectedDataset}>
          {editing ? "Preview SQL" : "Edit SQL"}
        </Button>
        <Button variant="primary" onClick={onAnalyze} disabled={!selectedDataset || !sqlText.trim()}>
          Analyze model
        </Button>
      </div>
    </Card>
  );
}
