import React from "react";
import Badge from "../shared/Badge.jsx";

export default function Header({
  activeStepTitle,
  validationStatus,
  context = {},
  navItems = [],
  activePage = "pipeline",
  onNavigate,
  showValidationBadge = true,
}) {
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
      {navItems.length > 0 && (
        <nav className="page-nav" aria-label="Application pages">
          {navItems.map((item) => (
            <button
              key={item.id}
              type="button"
              className={`page-nav-item ${activePage === item.id ? "active" : ""}`}
              onClick={() => onNavigate?.(item.id)}
            >
              {item.label}
            </button>
          ))}
        </nav>
      )}
      <div className="header-meta">
        <Badge tone={backendReady ? "green" : "orange"}>
          {backendReady ? "Backend connected" : "Connecting"}
        </Badge>
        <Badge tone="indigo">{activeStepTitle || "Start"}</Badge>
        {showValidationBadge && (
          <Badge tone={validationStatus === "approved" ? "green" : "orange"}>
            {validationStatus === "approved" ? "Model approved" : "Needs approval"}
          </Badge>
        )}
      </div>
    </header>
  );
}
