import React from "react";
import Card from "../shared/Card.jsx";
import Button from "../shared/Button.jsx";
import Badge from "../shared/Badge.jsx";

export default function TwbGenerationStep({ twbState, onGenerate }) {
  const visualConversionStatus = twbState.visualConversion?.status || "not_started";
  const visualModel = twbState.visualModel || {};
  const artifactWorkspace = twbState.artifactWorkspace || {};

  return (
    <Card title="8. TWB generation" eyebrow="Tableau workbook XML">
      <div className="status-card">
        <Badge tone={twbState.generated ? "green" : "gray"}>
          {twbState.generated ? "Generated" : "Not generated"}
        </Badge>
        <p>
          {twbState.generated
            ? "The publish-ready data model TWB and the visual workbook TWB are saved in outputs."
            : "Generate the publish-ready data model TWB after schema validation and visual mapping."}
        </p>
      </div>

      <div className="progress-track">
        <span style={{ width: `${twbState.progress || 0}%` }} />
      </div>

      {twbState.generated && (
        <div className="file-summary">
          <div>
            <strong>Data model TWB to publish</strong>
            <span>{twbState.name}</span>
            <span>{twbState.path}</span>
          </div>
        </div>
      )}

      {artifactWorkspace.exists && (
        <div className="file-summary">
          <div>
            <strong>Output workspace</strong>
            <span>{artifactWorkspace.path}</span>
          </div>
        </div>
      )}

      <div className="file-summary">
        <div>
          <strong>Mapped report visuals</strong>
          <span>
            {visualConversionStatus === "running"
              ? "Visual mapping is running"
              : twbState.visualConversion?.path || twbState.visualConversion?.error || "Not started"}
          </span>
        </div>
      </div>

      {visualModel.generated && (
        <div className="file-summary">
          <div>
            <strong>Data model + mapped visuals TWB</strong>
            <span>{visualModel.name}</span>
            <span>{visualModel.path}</span>
          </div>
        </div>
      )}

      {!visualModel.generated && visualModel.error && (
        <div className="file-summary">
          <div>
            <strong>Visual model workbook</strong>
            <span>{visualModel.error}</span>
          </div>
        </div>
      )}

      <div className="button-row">
        <Button variant="primary" onClick={onGenerate}>Generate TWB</Button>
        {twbState.generated && (
          <a className="btn btn-secondary btn-md" href={twbState.downloadUrl}>
            Download data model TWB
          </a>
        )}
        {visualModel.generated && (
          <a className="btn btn-secondary btn-md" href={visualModel.downloadUrl}>
            Download data model + visuals TWB
          </a>
        )}
      </div>
    </Card>
  );
}
