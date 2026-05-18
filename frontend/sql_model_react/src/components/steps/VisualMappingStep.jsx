import React from "react";
import Badge from "../shared/Badge.jsx";
import Button from "../shared/Button.jsx";
import Card from "../shared/Card.jsx";

function statusTone(status, hasArtifact) {
  if (hasArtifact || status === "completed") return "green";
  if (status === "failed") return "red";
  if (status === "running") return "indigo";
  return "gray";
}

function statusLabel(status, hasArtifact) {
  if (hasArtifact || status === "completed") return "Mapped";
  if (status === "failed") return "Failed";
  if (status === "running") return "Running";
  return "Not mapped";
}

export default function VisualMappingStep({ twbState, onMapVisuals, loading }) {
  const visual = twbState.visualConversion || {};
  const hasArtifact = Boolean(visual.exists || (visual.path && visual.status === "completed"));
  const status = visual.status || "not_started";

  return (
    <Card title="7. Visual mapping" eyebrow="RDL report content">
      <div className="status-card">
        <Badge tone={statusTone(status, hasArtifact)}>{statusLabel(status, hasArtifact)}</Badge>
        <p>
          {hasArtifact
            ? `${visual.name || "Visual workbook"} is ready to merge with the validated data model.`
            : "Map the report visual content after schema validation."}
        </p>
      </div>

      {hasArtifact && (
        <div className="file-summary">
          <div>
            <strong>{visual.name}</strong>
            <span>{visual.path}</span>
          </div>
        </div>
      )}

      {!hasArtifact && visual.error && (
        <div className="file-summary">
          <div>
            <strong>Visual mapping error</strong>
            <span>{visual.error}</span>
          </div>
        </div>
      )}

      <div className="button-row">
        <Button variant="primary" onClick={onMapVisuals} disabled={loading}>
          Map visual content
        </Button>
        {hasArtifact && (
          <a className="btn btn-secondary btn-md" href={visual.downloadUrl}>
            Download mapped visual TWB
          </a>
        )}
      </div>
    </Card>
  );
}
