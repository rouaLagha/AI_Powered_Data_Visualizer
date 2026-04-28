import React from "react";
import Badge from "../shared/Badge.jsx";

export default function TableCard({ table, compact = false }) {
  const tone = table.type === "fact" ? "indigo" : table.type === "dimension" ? "green" : "gray";
  return (
    <article className={`table-card ${table.type || ""} ${compact ? "compact" : ""}`.trim()}>
      <header>
        <strong>{table.name}</strong>
        <Badge tone={tone}>{table.type}</Badge>
      </header>
      {!compact && (
        <>
          <div className="confidence-bar">
            <span style={{ width: `${Math.round((table.confidence || 0) * 100)}%` }} />
          </div>
          <ul>
            {(table.columns || []).slice(0, 5).map((column) => (
              <li key={column}>{column}</li>
            ))}
          </ul>
        </>
      )}
    </article>
  );
}
