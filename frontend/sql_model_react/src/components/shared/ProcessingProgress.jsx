import React from "react";

const processingStepsByLabel = [
  {
    match: /parse|rdl/i,
    steps: ["Read report definition", "Extract datasets", "Normalize SQL queries", "Refresh pipeline state"],
  },
  {
    match: /dataset/i,
    steps: ["Load dataset SQL", "Refresh source context", "Prepare analysis input"],
  },
  {
    match: /analysis|llm|correction|model/i,
    steps: ["Inspect SQL structure", "Detect tables and joins", "Infer measures", "Update semantic model"],
  },
  {
    match: /validat|schema/i,
    steps: ["Check relationships", "Validate model shape", "Mark schema approval"],
  },
  {
    match: /twb|workbook/i,
    steps: ["Build workbook XML", "Validate generated artifact", "Prepare download link"],
  },
  {
    match: /tableau|publish|datasource/i,
    steps: ["Load Tableau settings", "Prepare datasource package", "Publish to Tableau", "Collect result"],
  },
  {
    match: /reset/i,
    steps: ["Clear current state", "Reset backend session", "Reload defaults"],
  },
];

function stepsForLabel(label) {
  const match = processingStepsByLabel.find((item) => item.match.test(label));
  return match?.steps || ["Prepare request", "Run backend task", "Update interface"];
}

export default function ProcessingProgress({ label }) {
  const steps = stepsForLabel(label || "");

  return (
    <div className="processing-progress" role="status" aria-live="polite">
      <div className="processing-progress-header">
        <span className="processing-spinner" aria-hidden="true" />
        <div>
          <strong>{label || "Processing"}</strong>
          <span>Backend work in progress</span>
        </div>
      </div>
      <div className="processing-steps">
        {steps.map((step, index) => (
          <span className="processing-step" style={{ "--step-index": index }} key={step}>
            {step}
          </span>
        ))}
      </div>
    </div>
  );
}
