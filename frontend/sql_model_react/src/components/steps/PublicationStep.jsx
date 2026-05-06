import React from "react";
import Card from "../shared/Card.jsx";
import Button from "../shared/Button.jsx";
import Badge from "../shared/Badge.jsx";

export default function PublicationStep({ datasourceConfig, publicationResult, onPublish }) {
  const status = String(publicationResult.status || "pending").toLowerCase();
  const isPublished = ["published", "success", "completed"].includes(status);
  const isError = status === "error";
  const modeLabel = datasourceConfig.connectionType === "extract" ? "Extract" : "Live TDS";
  const datasourceName = datasourceConfig.sourceDatasourceName || datasourceConfig.name || "Datasource";
  const statusTitle = isPublished ? "Datasource published" : isError ? "Publish failed" : "Ready to publish";
  const statusTone = isPublished ? "success" : isError ? "error" : "pending";

  return (
    <Card title="10. Publish" eyebrow="Tableau Cloud" className="publication-card">
      <div className={`publication-status-panel publication-${statusTone}`}>
        <div className="publication-status-copy">
          <Badge tone={isPublished ? "green" : isError ? "red" : "indigo"}>{publicationResult.status || "pending"}</Badge>
          <h3>{statusTitle}</h3>
          <p>{publicationResult.message || "Publish the prepared datasource package to Tableau Cloud."}</p>
        </div>
        <Button variant="primary" onClick={onPublish}>
          {isPublished ? "Publish again" : "Publish datasource"}
        </Button>
      </div>

      <div className="publication-target-grid">
        <div>
          <span>Datasource</span>
          <strong>{datasourceName}</strong>
        </div>
        <div>
          <span>Project</span>
          <strong>{datasourceConfig.project || "Default"}</strong>
        </div>
        <div>
          <span>Mode</span>
          <strong>{modeLabel}</strong>
        </div>
      </div>

      {isPublished && (
        <div className="publication-result-card">
          <h3>Published artifact</h3>
          <div className="published-details">
            <div><span>Datasource URL</span><a href={publicationResult.url || "#"}>{publicationResult.url || "Returned by Tableau"}</a></div>
            <div><span>Datasource ID</span><code>{publicationResult.id || "Returned by Tableau"}</code></div>
          </div>
        </div>
      )}
    </Card>
  );
}
