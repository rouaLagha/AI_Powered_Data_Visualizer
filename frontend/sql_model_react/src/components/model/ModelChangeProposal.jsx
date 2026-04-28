import React from "react";
import Badge from "../shared/Badge.jsx";
import ChangeDiffViewer from "./ChangeDiffViewer.jsx";

export default function ModelChangeProposal({ proposal, onApply, onReject }) {
  return (
    <div className="proposal-card">
      <div className="proposal-header">
        <div>
          <strong>Proposed Model Change</strong>
          <div className="muted">{proposal.title}</div>
        </div>
        <Badge tone="indigo">{proposal.type}</Badge>
      </div>
      <ChangeDiffViewer before={proposal.before} after={proposal.after} />
      <p className="proposal-reason">{proposal.reason}</p>
      <div className="proposal-actions">
        <button className="btn btn-primary btn-sm" onClick={() => onApply(proposal.id)}>
          Apply change
        </button>
        <button className="btn btn-secondary btn-sm" onClick={() => onReject(proposal.id)}>
          Reject
        </button>
      </div>
    </div>
  );
}
