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
  navItems,
  activePage,
  onNavigate,
  panelWidths,
  panelWidthLimits,
  onPanelWidthChange,
}) {
  const layoutPanelWidthLimits = panelWidthLimits || {
    pipeline: { default: 320, min: 220, max: 500 },
    chat: { default: 360, min: 280, max: 520 },
  };
  const gridStyle = {
    "--left-width": `${panelWidths?.pipeline ?? layoutPanelWidthLimits.pipeline.default}px`,
    "--right-width": `${panelWidths?.chat ?? layoutPanelWidthLimits.chat.default}px`,
  };

  return (
    <div className="app-shell">
      <Header
        activeStepTitle={steps[activeStep]?.title || ""}
        validationStatus={validationStatus}
        context={headerContext}
        navItems={navItems}
        activePage={activePage}
        onNavigate={onNavigate}
        showValidationBadge
      />
      <div className="app-grid" style={gridStyle}>
        <PipelineStepper
          steps={steps}
          activeStep={activeStep}
          onSelectStep={onSelectStep}
          panelWidth={panelWidths?.pipeline ?? layoutPanelWidthLimits.pipeline.default}
          onResize={(value) => onPanelWidthChange?.("pipeline", value)}
        />
        <main className="workspace">
          {children}
        </main>
        <RightChatPanel
          activeStep={activeStep}
          panelWidth={panelWidths?.chat ?? layoutPanelWidthLimits.chat.default}
          onResize={(value) => onPanelWidthChange?.("chat", value)}
          {...chatProps}
        />
      </div>
    </div>
  );
}
