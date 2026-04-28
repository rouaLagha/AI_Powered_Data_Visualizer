import React from "react";
import Card from "../shared/Card.jsx";
import Button from "../shared/Button.jsx";
import Badge from "../shared/Badge.jsx";

export default function TwbGenerationStep({ twbState, onGenerate }) {
  return (
    <Card title="7. TWB generation" eyebrow="Tableau workbook XML">
      <div className="status-card">
        <Badge tone={twbState.generated ? "green" : "gray"}>
          {twbState.generated ? "Generated" : "Not generated"}
        </Badge>
        <p>
          {twbState.generated
            ? `${twbState.name || "Workbook"} is ready for Tableau publication.`
            : "Generate the TWB after model validation."}
        </p>
      </div>

      <div className="progress-track">
        <span style={{ width: `${twbState.progress || 0}%` }} />
      </div>

      {twbState.generated && (
        <div className="file-summary">
          <div>
            <strong>{twbState.name}</strong>
            <span>{twbState.path}</span>
          </div>
        </div>
      )}

      <div className="button-row">
        <Button variant="primary" onClick={onGenerate}>Generate TWB</Button>
        {twbState.generated && (
          <a className="btn btn-secondary btn-md" href={twbState.downloadUrl}>
            Download TWB
          </a>
        )}
      </div>
    </Card>
  );
}
