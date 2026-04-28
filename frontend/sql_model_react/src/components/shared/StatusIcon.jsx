import React from "react";

const LABELS = {
  pending: "Pending",
  in_progress: "In progress",
  completed: "Completed",
  needs_validation: "Needs validation",
  error: "Error",
};

export default function StatusIcon({ status = "pending", showLabel = false }) {
  return (
    <span className={`status-icon status-${status}`} title={LABELS[status] || status}>
      <span className="status-dot" />
      {showLabel && <span>{LABELS[status] || status}</span>}
    </span>
  );
}
