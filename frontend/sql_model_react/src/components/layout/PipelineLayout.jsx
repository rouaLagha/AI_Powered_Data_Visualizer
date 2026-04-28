import React from "react";
import Header from "./Header.jsx";
import PipelineStepper from "./PipelineStepper.jsx";
import RightChatPanel from "./RightChatPanel.jsx";

export default function PipelineLayout({
  steps,
  activeStep,
  onSelectStep,
  children,
  chatProps,
  validationStatus,
  headerContext,
}) {
  return (
    <div className="app-shell">
      <Header
        activeStepTitle={steps[activeStep]?.title || ""}
        validationStatus={validationStatus}
        context={headerContext}
      />
      <div className="app-grid">
        <PipelineStepper steps={steps} activeStep={activeStep} onSelectStep={onSelectStep} />
        <main className="workspace">
          {children}
        </main>
        <RightChatPanel activeStep={activeStep} {...chatProps} />
      </div>
    </div>
  );
}
