import React, { useState } from "react";
import ModelChangeProposal from "../model/ModelChangeProposal.jsx";

const helperText = [
  "Load an RDL file to start the real pipeline.",
  "Check the extracted datasets before choosing the SQL source.",
  "Choose the dataset that should drive the semantic model.",
];

export default function RightChatPanel({
  activeStep,
  chatMessages,
  changeProposals,
  onSendCorrection,
  onApplyProposal,
  onRejectProposal,
}) {
  const [draft, setDraft] = useState("");
  const chatEnabled = activeStep >= 3;
  const visibleMessages = chatMessages.slice(-4);

  function submit(event) {
    event.preventDefault();
    if (!draft.trim()) return;
    onSendCorrection(draft.trim());
    setDraft("");
  }

  return (
    <aside className="right-chat">
      <header className="chat-header">
        <div>
          <p className="eyebrow">{chatEnabled ? "Corrections" : "Context"}</p>
          <h2>{chatEnabled ? "Adjust model" : "Next action"}</h2>
        </div>
      </header>

      <div className="chat-body">
        {!chatEnabled && (
          <div className="helper-card">
            <p>{helperText[activeStep] || "Complete analysis to unlock model corrections."}</p>
          </div>
        )}

        {visibleMessages.map((message) => (
          <div key={message.id} className={`chat-message chat-${message.role}`}>
            <span>{message.role === "user" ? "You" : "Assistant"}</span>
            <p>{message.content}</p>
          </div>
        ))}

        {changeProposals
          .filter((proposal) => proposal.status === "pending")
          .map((proposal) => (
            <ModelChangeProposal
              key={proposal.id}
              proposal={proposal}
              onApply={onApplyProposal}
              onReject={onRejectProposal}
            />
          ))}
      </div>

      <form className="chat-input" onSubmit={submit}>
        <textarea
          value={draft}
          onChange={(event) => setDraft(event.target.value)}
          placeholder={chatEnabled ? "Describe a model correction..." : "Available after analysis"}
          disabled={!chatEnabled}
        />
        <button className="btn btn-primary" type="submit" disabled={!chatEnabled || !draft.trim()}>
          Apply correction
        </button>
      </form>
    </aside>
  );
}
