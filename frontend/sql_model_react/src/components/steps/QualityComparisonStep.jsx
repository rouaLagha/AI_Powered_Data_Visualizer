import React from "react";
import Badge from "../shared/Badge.jsx";
import Button from "../shared/Button.jsx";
import Card from "../shared/Card.jsx";
import ProcessingProgress from "../shared/ProcessingProgress.jsx";

function scoreTone(score) {
  if (score >= 85) return "green";
  if (score >= 70) return "orange";
  return "red";
}

function statusLabel(comparison) {
  if (!comparison.executed) return "Pending";
  return comparison.status === "completed" ? "Completed" : comparison.status;
}

export default function QualityComparisonStep({
  qualityComparison,
  onRunComparison,
  loading,
  loadingText,
}) {
  const comparison = qualityComparison || {};
  const globalScore = comparison.globalScore || 0;
  const metrics = comparison.metrics || [];
  const passed = comparison.executed && globalScore >= 85;

  return (
    <Card
      title="12. Quality comparison"
      eyebrow="RDL vs Tableau output"
      className="quality-comparison-card"
      actions={<Badge tone={comparison.executed ? scoreTone(globalScore) : "indigo"}>{statusLabel(comparison)}</Badge>}
    >
      <div className="quality-score-panel" data-testid="quality-comparison">
        <div>
          <span>Global quality score</span>
          <strong data-testid="global-quality-score">{comparison.executed ? `${globalScore}%` : "--"}</strong>
          {comparison.executed && (
            <Badge tone={passed ? "green" : "orange"}>{passed ? "Target met" : "Needs review"}</Badge>
          )}
        </div>
        <p>
          {comparison.summary ||
            "Run the comparison after workbook generation to verify dataset, model, relationship, artifact, and visual coverage."}
        </p>
      </div>

      {metrics.length > 0 && (
        <div className="quality-metric-list" data-testid="quality-metrics">
          {metrics.map((metric) => (
            <div className="quality-metric-row" data-testid={`quality-metric-${metric.id}`} key={metric.id}>
              <div>
                <strong>{metric.label}</strong>
                {metric.detail && <span>{metric.detail}</span>}
              </div>
              <Badge tone={scoreTone(metric.score)}>{metric.score}%</Badge>
            </div>
          ))}
        </div>
      )}

      {loading && <ProcessingProgress label={loadingText || "Quality comparison"} />}

      <div className="button-row">
        <Button variant="primary" onClick={onRunComparison} disabled={loading}>
          {comparison.executed ? "Run comparison again" : "Run quality comparison"}
        </Button>
      </div>
    </Card>
  );
}
