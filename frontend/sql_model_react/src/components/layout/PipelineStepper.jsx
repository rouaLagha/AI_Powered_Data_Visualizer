import React from "react";
import StatusIcon from "../shared/StatusIcon.jsx";

export default function PipelineStepper({ steps, activeStep, onSelectStep }) {
  return (
    <aside className="pipeline-stepper" aria-label="Pipeline steps">
      <div className="stepper-title">Pipeline</div>
      <div className="stepper-list">
        {steps.map((step, index) => (
          <button
            key={step.id}
            type="button"
            className={`stepper-item ${index === activeStep ? "active" : ""}`}
            onClick={() => onSelectStep(index)}
          >
            <span className="step-number">{index + 1}</span>
            <span className="step-copy">
              <span className="step-title-row">
                <strong>{step.title}</strong>
                <StatusIcon status={step.status} />
              </span>
            </span>
          </button>
        ))}
      </div>
    </aside>
  );
}
