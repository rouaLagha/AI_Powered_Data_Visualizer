import React from "react";
import Card from "../shared/Card.jsx";
import Button from "../shared/Button.jsx";
import Badge from "../shared/Badge.jsx";

export default function PublicationStep({ datasourceConfig, publicationResult, onPublish }) {
  const isPublished = ["published", "success", "completed"].includes(String(publicationResult.status || "").toLowerCase());
  const isError = String(publicationResult.status || "").toLowerCase() === "error";

  return (
    <Card title="9. Publish" eyebrow="Tableau Cloud">
      <div className="publication-grid">
        <div className="summary-card"><span>Datasource</span><strong>{datasourceConfig.sourceDatasourceName || datasourceConfig.name}</strong></div>
        <div className="summary-card"><span>Project</span><strong>{datasourceConfig.project || "Default"}</strong></div>
        <div className="summary-card"><span>Mode</span><strong>{datasourceConfig.connectionType}</strong></div>
      </div>

      <div className={`status-card ${isPublished ? "success" : ""}`}>
        <Badge tone={isPublished ? "green" : isError ? "red" : "gray"}>{publicationResult.status}</Badge>
        <p>{publicationResult.message}</p>
      </div>

      <Button variant="primary" onClick={onPublish}>Publish datasource</Button>

      {isPublished && (
        <div className="published-details">
          <div><span>Datasource URL</span><a href={publicationResult.url || "#"}>{publicationResult.url || "Returned by Tableau"}</a></div>
          <div><span>Datasource ID</span><code>{publicationResult.id || "Returned by Tableau"}</code></div>
        </div>
      )}
    </Card>
  );
}
