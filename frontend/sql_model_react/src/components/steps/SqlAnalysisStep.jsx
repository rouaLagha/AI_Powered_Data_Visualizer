import React from "react";
import Card from "../shared/Card.jsx";
import Button from "../shared/Button.jsx";
import ProcessingProgress from "../shared/ProcessingProgress.jsx";

function ListBlock({ title, items }) {
  return (
    <div className="insight-card">
      <strong>{title}</strong>
      {items.length ? (
        <ul>{items.slice(0, 8).map((item) => <li key={item}>{item}</li>)}</ul>
      ) : (
        <span className="muted">No values yet.</span>
      )}
    </div>
  );
}

export default function SqlAnalysisStep({ analysisResult, onRunAnalysis, loading, loadingText }) {
  const detectedTables = analysisResult.detectedTables || [];
  const detectedJoins = analysisResult.detectedJoins || [];
  const detectedMeasures = analysisResult.detectedMeasures || [];

  return (
    <Card title="4. SQL analysis" eyebrow="Backend plus LLM">
      <div className="metric-grid">
        <div className="metric-card"><strong>{detectedTables.length}</strong><span>Tables</span></div>
        <div className="metric-card"><strong>{detectedJoins.length}</strong><span>Joins</span></div>
        <div className="metric-card"><strong>{detectedMeasures.length}</strong><span>Measures</span></div>
      </div>

      <div className="analysis-grid">
        <ListBlock title="Tables" items={detectedTables} />
        <ListBlock title="Joins" items={detectedJoins} />
        <ListBlock title="Measures" items={detectedMeasures} />
      </div>

      {loading && <ProcessingProgress label={loadingText || "SQL analysis and LLM reasoning"} />}

      <div className="button-row analysis-actions">
        <Button variant="primary" onClick={onRunAnalysis} disabled={loading} className={loading ? "btn-loading" : ""}>
          {loading ? "Running..." : "Run analysis"}
        </Button>
      </div>
    </Card>
  );
}
