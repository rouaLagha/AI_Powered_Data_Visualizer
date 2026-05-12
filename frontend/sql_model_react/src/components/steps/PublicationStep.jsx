import React from "react";
import Card from "../shared/Card.jsx";
import Button from "../shared/Button.jsx";
import Badge from "../shared/Badge.jsx";

export default function PublicationStep({ datasourceConfig, publicationResult, onPublish }) {
  const status = String(publicationResult.status || "pending").toLowerCase();
  const isPublished = ["published", "success", "completed"].includes(status);
  const isPartial = status.includes("datasource_published");
  const isError = status === "error" || status.includes("failed");
  const modeLabel = datasourceConfig.connectionType === "extract" ? "Extract" : "Live TDS";
  const datasourceName = datasourceConfig.sourceDatasourceName || datasourceConfig.name || "Datasource";
  const statusTitle = isPublished
    ? "RDL, datasource and final workbook published"
    : isPartial
      ? "Workbook publish needs attention"
      : isError
        ? "Publish failed"
        : "Ready to publish";
  const statusTone = isPublished ? "success" : isError || isPartial ? "error" : "pending";

  return (
    <Card title="10. Publish" eyebrow="Power BI + Tableau Cloud" className="publication-card">
      <div className={`publication-status-panel publication-${statusTone}`}>
        <div className="publication-status-copy">
          <Badge tone={isPublished ? "green" : isError ? "red" : "indigo"}>{publicationResult.status || "pending"}</Badge>
          <h3>{statusTitle}</h3>
          <p>{publicationResult.message || "Publish the source RDL, prepared datasource, and final workbook."}</p>
        </div>
        <Button variant="primary" onClick={onPublish}>
          {isPublished ? "Publish again" : "Publish source and workbook"}
        </Button>
      </div>

      <div className="publication-target-grid">
        <div>
          <span>Datasource</span>
          <strong>{datasourceName}</strong>
        </div>
        <div>
          <span>Datasource project</span>
          <strong>{datasourceConfig.datasourceProject || publicationResult.datasourceProject || "published_datasources"}</strong>
        </div>
        <div>
          <span>Workbook project</span>
          <strong>{datasourceConfig.workbookProject || publicationResult.workbookProject || "published_reports"}</strong>
        </div>
        <div>
          <span>Mode</span>
          <strong>{modeLabel}</strong>
        </div>
      </div>

      {isPublished && (
        <div className="publication-result-card">
          <h3>Published artifacts</h3>
          <div className="published-details">
            <div><span>Datasource URL</span><a href={publicationResult.url || "#"}>{publicationResult.url || "Returned by Tableau"}</a></div>
            <div><span>Datasource ID</span><code>{publicationResult.id || "Returned by Tableau"}</code></div>
            <div><span>Workbook URL</span><a href={publicationResult.workbookUrl || "#"}>{publicationResult.workbookUrl || "Returned by Tableau"}</a></div>
            <div><span>Workbook ID</span><code>{publicationResult.workbookId || "Returned by Tableau"}</code></div>
          </div>
        </div>
      )}
    </Card>
  );
}
