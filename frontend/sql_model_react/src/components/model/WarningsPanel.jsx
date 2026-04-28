import React from "react";
import Badge from "../shared/Badge.jsx";

export default function WarningsPanel({ warnings }) {
  if (!warnings.length) {
    return <div className="empty-state">No warnings detected.</div>;
  }

  return (
    <div className="warning-list">
      {warnings.map((warning, index) => (
        <article className={`warning-card warning-${warning.severity}`} key={`${warning.type}-${index}`}>
          <div>
            <strong>{warning.type}</strong>
            <p>{warning.message}</p>
          </div>
          <Badge tone={warning.severity === "medium" ? "orange" : "gray"}>{warning.severity}</Badge>
        </article>
      ))}
    </div>
  );
}
