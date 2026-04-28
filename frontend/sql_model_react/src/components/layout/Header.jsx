import React from "react";
import Badge from "../shared/Badge.jsx";

export default function Header({ activeStepTitle, validationStatus, context = {} }) {
  const reportName = context.reportName || "No RDL loaded";
  const datasetName = context.datasetName || "No dataset";
  const backendReady = context.backendReady;

  return (
    <header className="app-header">
      <div className="header-title">
        <p className="eyebrow">Live backend pipeline</p>
        <h1>SQL Model Assistant</h1>
        <div className="header-context">
          <span>{reportName}</span>
          <span>{datasetName}</span>
        </div>
      </div>
      <div className="header-meta">
        <Badge tone={backendReady ? "green" : "orange"}>
          {backendReady ? "Backend connected" : "Connecting"}
        </Badge>
        <Badge tone="indigo">{activeStepTitle || "Start"}</Badge>
        <Badge tone={validationStatus === "approved" ? "green" : "orange"}>
          {validationStatus === "approved" ? "Model approved" : "Needs approval"}
        </Badge>
      </div>
    </header>
  );
}
