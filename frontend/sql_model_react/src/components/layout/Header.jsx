import React from "react";

export default function Header({
  activeStepTitle,
  validationStatus,
  context = {},
  navItems = [],
  activePage = "pipeline",
  onNavigate,
  showValidationBadge = true,
}) {
  const backendReady = context.backendReady;

  return (
    <header className="app-header">
      <div className="header-title">
        <h1>AI Powered Data Visualizer</h1>
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
      {/* header-meta intentionally removed to keep the header minimal */}
    </header>
  );
}
