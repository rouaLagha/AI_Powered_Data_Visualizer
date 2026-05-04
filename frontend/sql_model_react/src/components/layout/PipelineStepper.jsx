import React from "react";
import StatusIcon from "../shared/StatusIcon.jsx";

export default function PipelineStepper({
  steps,
  activeStep,
  onSelectStep,
  panelWidth,
  onResize,
}) {
  function startResize(event) {
    if (!onResize) return;
    event.preventDefault();

    const startX = event.clientX;
    const startWidth = panelWidth;

    function handleMove(moveEvent) {
      onResize(startWidth + moveEvent.clientX - startX);
    }

    function stopResize() {
      window.removeEventListener("pointermove", handleMove);
      window.removeEventListener("pointerup", stopResize);
      document.body.classList.remove("resizing-panel");
    }

    document.body.classList.add("resizing-panel");
    window.addEventListener("pointermove", handleMove);
    window.addEventListener("pointerup", stopResize);
  }

  return (
    <aside className="pipeline-stepper" aria-label="Pipeline steps">
      <div className="stepper-title">
        <span className="panel-title-text">Pipeline</span>
      </div>
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
              <span className="step-description">{step.description}</span>
            </span>
          </button>
        ))}
      </div>
      <div
        className="resize-handle resize-handle-right"
        role="separator"
        aria-orientation="vertical"
        aria-label="Resize pipeline panel"
        onPointerDown={startResize}
      />
    </aside>
  );
}
