import React from "react";
import Card from "../shared/Card.jsx";
import Button from "../shared/Button.jsx";
import Badge from "../shared/Badge.jsx";

export default function ConsumerWorkbookStep({ consumerWorkbook, datasourceConfig, onGenerateConsumer, onStartNew }) {
  return (
    <Card title="10. Final workbook" eyebrow="Consumer artifact">
      {consumerWorkbook.generated ? (
        <div className="consumer-success">
          <Badge tone="green">Workbook ready</Badge>
          <div className="published-details">
            <div><span>Workbook</span><strong>{consumerWorkbook.name}</strong></div>
            <div><span>Datasource</span><strong>{datasourceConfig.sourceDatasourceName || datasourceConfig.name}</strong></div>
          </div>
        </div>
      ) : (
        <div className="empty-state">Complete publication, then check the generated workbook artifact.</div>
      )}

      <div className="button-row">
        {!consumerWorkbook.generated && (
          <Button variant="primary" onClick={onGenerateConsumer}>Check workbook</Button>
        )}
        {consumerWorkbook.generated && (
          <a className="btn btn-primary btn-md" href={consumerWorkbook.downloadUrl}>
            Download workbook
          </a>
        )}
        <Button onClick={onStartNew}>Start new pipeline</Button>
      </div>
    </Card>
  );
}
