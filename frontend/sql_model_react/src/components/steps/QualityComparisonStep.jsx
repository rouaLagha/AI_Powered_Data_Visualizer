import React from "react";
import Badge from "../shared/Badge.jsx";
import Button from "../shared/Button.jsx";
import Card from "../shared/Card.jsx";
import ProcessingProgress from "../shared/ProcessingProgress.jsx";

function normalizeName(value) {
  return String(value || "").trim().toLowerCase();
}

function scoreTone(score) {
  if (score >= 85) return "green";
  if (score >= 70) return "orange";
  return "red";
}

function statusLabel(comparison) {
  if (!comparison.executed) return "Pending";
  return comparison.status === "completed" ? "Completed" : comparison.status;
}

function rowStatus(row) {
  return row?.conformity_status || row?.status || row?.raw_status || "";
}

function statusTone(status) {
  const normalized = normalizeName(status);
  if (["passed", "conforme", "conformant", "completed"].includes(normalized)) return "green";
  if (["partial", "partially_conformant", "ecart_mineur", "minor", "skipped", "non_extractible"].includes(normalized)) {
    return "orange";
  }
  if (["failed", "non_conforme", "non_conformant", "error"].includes(normalized)) return "red";
  return "indigo";
}

function readableStatus(status) {
  const normalized = normalizeName(status);
  const labels = {
    passed: "Conforme",
    conforme: "Conforme",
    conformant: "Conforme",
    completed: "Completed",
    partial: "Partiel",
    partially_conformant: "Partiellement conforme",
    ecart_mineur: "Ecart mineur",
    skipped: "A verifier",
    non_extractible: "Non-extractable",
    failed: "Non conforme",
    non_conforme: "Non conforme",
    non_conformant: "Non conforme",
  };
  return labels[normalized] || String(status || "Unknown");
}

function formatValue(value) {
  if (value === undefined || value === null || value === "") return "-";
  if (Array.isArray(value)) {
    return value.length > 0 ? value.map((item) => formatValue(item)).join(", ") : "matched";
  }
  if (typeof value === "number") {
    return Number.isInteger(value) ? value.toLocaleString() : value.toLocaleString(undefined, { maximumFractionDigits: 4 });
  }
  if (typeof value === "object") return JSON.stringify(value);
  return String(value);
}

function formatTableValue(value) {
  if (value === undefined || value === null || value === "") return "-";
  if (Array.isArray(value)) {
    if (value.length === 0) return "matched";
    return value.map((item) => formatValue(item)).join("; ");
  }
  return formatValue(value);
}

function parsePointString(value) {
  const text = String(value || "").trim();
  if (!text || !text.includes("=")) return null;
  const fields = {};
  text.split(",").forEach((part) => {
    const [key, ...rest] = part.split("=");
    const normalizedKey = String(key || "").trim().toLowerCase();
    const fieldValue = rest.join("=").trim();
    if (normalizedKey && fieldValue) fields[normalizedKey] = fieldValue;
  });
  if (!fields.x && !fields.y && !fields.measure && !fields.axis) return null;
  return {
    x: fields.x || "-",
    measure: fields.measure || fields.series || "-",
    axis: fields.axis || "-",
    y: fields.y || "-",
  };
}

function parsePointList(value) {
  if (!Array.isArray(value)) return [];
  return value.map(parsePointString).filter(Boolean);
}

function formatPoint(point) {
  return `${point.x} | ${point.measure} | ${point.axis}: ${point.y}`;
}

function fullValueText(value) {
  const points = parsePointList(value);
  if (points.length > 0) return points.map(formatPoint).join("; ");
  return formatTableValue(value);
}

function groupPointsByMeasure(points) {
  const groups = new Map();
  points.forEach((point) => {
    const measure = point.measure || "-";
    const axis = point.axis || "-";
    const key = `${measure}__${axis}`;
    if (!groups.has(key)) {
      groups.set(key, { measure, axis, points: [] });
    }
    groups.get(key).points.push(point);
  });
  return Array.from(groups.values());
}

function renderPointGroups(points) {
  if (!points.length) return <span className="quality-detail-empty">No chart points extracted</span>;
  return (
    <div className="quality-point-groups">
      {groupPointsByMeasure(points).map((group) => (
        <div className="quality-point-group" key={`${group.measure}-${group.axis}`}>
          <div className="quality-point-group-title">
            <strong>{group.measure}</strong>
            <span>{group.axis} axis</span>
          </div>
          <div className="quality-point-chip-grid">
            {group.points.map((point, index) => (
              <span className="quality-point-chip" key={`${point.x}-${point.y}-${index}`}>
                <span>{point.x}</span>
                <strong>{point.y}</strong>
              </span>
            ))}
          </div>
        </div>
      ))}
    </div>
  );
}

function pointDisplayValue(point) {
  if (!point || typeof point !== "object") return "-";
  if (point.display_value !== undefined && point.display_value !== null && point.display_value !== "") return point.display_value;
  if (
    point.comparison_display_value !== undefined &&
    point.comparison_display_value !== null &&
    point.comparison_display_value !== ""
  ) {
    return point.comparison_display_value;
  }
  if (point.normalized_value !== undefined && point.normalized_value !== null) return formatValue(point.normalized_value);
  return point.raw_value || formatValue(point.value);
}

function seriesPointDisplayValue(point, side) {
  const directValue = side === "source" ? point?.source_value : point?.target_value;
  if (directValue !== undefined && directValue !== null && directValue !== "") return directValue;
  return pointDisplayValue(side === "source" ? point?.source : point?.target);
}

function boundedConformityScore(value) {
  const numeric = numberOrNull(value);
  if (numeric === null) return null;
  const percent = numeric >= 0 && numeric <= 1 ? numeric * 100 : numeric;
  return Math.min(99, Math.max(1, percent));
}

function pointConformityValue(point) {
  return boundedConformityScore(point?.conformity_score ?? point?.conformity_percent);
}

function groupMatchedPointsByMeasure(matches) {
  const groups = new Map();
  matches.forEach((match) => {
    const source = match?.source || {};
    const target = match?.target || {};
    const sourceMeasure = source.measure || source.series || "-";
    const targetMeasure = target.measure || target.series || "-";
    const key = `${sourceMeasure}__${targetMeasure}__${source.axis || target.axis || ""}`;
    if (!groups.has(key)) {
      groups.set(key, {
        sourceMeasure,
        targetMeasure,
        axis: source.axis || target.axis || "",
        points: [],
      });
    }
    groups.get(key).points.push({
      category: source.category || target.category || "-",
      sourceValue: pointDisplayValue(source),
      targetValue: pointDisplayValue(target),
    });
  });
  return Array.from(groups.values());
}

function renderChartMeasurePointList(row, sourcePoints, targetPoints) {
  const seriesComparisons = row?.chart_point_comparison?.series_comparisons;
  if (Array.isArray(seriesComparisons) && seriesComparisons.length > 0) {
    return (
      <div className="quality-chart-measure-list">
        {seriesComparisons.map((series, seriesIndex) => {
          const sourceMeasure = series.source_measure || "RDL";
          const targetMeasure = series.target_measure || "Tableau";
          const points = Array.isArray(series.points) ? series.points : [];
          return (
            <div className="quality-chart-measure-card" key={`${sourceMeasure}-${targetMeasure}-${series.axis || ""}-${seriesIndex}`}>
              <div className="quality-chart-measure-title">
                <strong>{sourceMeasure}</strong>
                {targetMeasure && targetMeasure !== sourceMeasure && <span>{targetMeasure}</span>}
              </div>
              <div className="quality-chart-point-table">
                <div className="quality-chart-point-row quality-chart-point-row--head quality-chart-point-row--scored">
                  <span>Point</span>
                  <b>RDL</b>
                  <b>Tableau</b>
                  <b>Score</b>
                </div>
                {points.map((point, pointIndex) => {
                  const pointScore = pointConformityValue(point);
                  return (
                    <div
                      className={`quality-chart-point-row quality-chart-point-row--scored quality-chart-point-row--${normalizeName(point.status || "unknown")}`}
                      key={`${point.category || pointIndex}-${pointIndex}`}
                      title={point.reason || ""}
                    >
                      <span>{point.category || "-"}</span>
                      <strong>{seriesPointDisplayValue(point, "source")}</strong>
                      <strong>{seriesPointDisplayValue(point, "target")}</strong>
                      <strong>{pointScore === null ? "-" : formatPercentValue(pointScore)}</strong>
                    </div>
                  );
                })}
              </div>
            </div>
          );
        })}
      </div>
    );
  }

  const matches = row?.chart_point_comparison?.matched_points;
  if (!Array.isArray(matches) || matches.length === 0) {
    return (
      <div className="quality-chart-measure-list">
        <div className="quality-chart-measure-card">
          <div className="quality-chart-measure-title">
            <strong>RDL points</strong>
          </div>
          <div className="quality-chart-point-table">
            {sourcePoints.map((point, index) => (
              <div className="quality-chart-point-row" key={`source-${point.x}-${index}`}>
                <span>{point.x}</span>
                <strong>{point.y}</strong>
              </div>
            ))}
          </div>
        </div>
        <div className="quality-chart-measure-card">
          <div className="quality-chart-measure-title">
            <strong>Tableau points</strong>
          </div>
          <div className="quality-chart-point-table">
            {targetPoints.map((point, index) => (
              <div className="quality-chart-point-row" key={`target-${point.x}-${index}`}>
                <span>{point.x}</span>
                <strong>{point.y}</strong>
              </div>
            ))}
          </div>
        </div>
      </div>
    );
  }

  return (
    <div className="quality-chart-measure-list">
      {groupMatchedPointsByMeasure(matches).map((group) => (
        <div className="quality-chart-measure-card" key={`${group.sourceMeasure}-${group.targetMeasure}-${group.axis}`}>
          <div className="quality-chart-measure-title">
            <strong>{group.sourceMeasure}</strong>
            {group.targetMeasure && group.targetMeasure !== group.sourceMeasure && <span>{group.targetMeasure}</span>}
          </div>
          <div className="quality-chart-point-table">
            <div className="quality-chart-point-row quality-chart-point-row--head">
              <span>Point</span>
              <b>RDL</b>
              <b>Tableau</b>
            </div>
            {group.points.map((point, index) => (
              <div className="quality-chart-point-row" key={`${point.category}-${index}`}>
                <span>{point.category}</span>
                <strong>{point.sourceValue}</strong>
                <strong>{point.targetValue}</strong>
              </div>
            ))}
          </div>
        </div>
      ))}
    </div>
  );
}

function renderTableValue(value) {
  const points = parsePointList(value);
  if (points.length > 0) return `${points.length} points`;
  return formatTableValue(value);
}

function numberOrNull(value) {
  const numeric = Number(value);
  return Number.isFinite(numeric) ? numeric : null;
}

function formatPercentValue(value) {
  const numeric = Number(value);
  if (!Number.isFinite(numeric)) return "-";
  const rounded = Math.abs(numeric - Math.round(numeric)) < 0.005
    ? String(Math.round(numeric))
    : numeric.toLocaleString(undefined, { maximumFractionDigits: 2 });
  return `${rounded}%`;
}

function rowDeltaPercent(row) {
  if (row?.delta_percent !== undefined && row?.delta_percent !== null) {
    return numberOrNull(row.delta_percent);
  }
  const matchedValue = row?.numeric_comparison?.matched_values?.[0];
  if (matchedValue?.delta_percent !== undefined && matchedValue?.delta_percent !== null) {
    return numberOrNull(matchedValue.delta_percent);
  }
  return null;
}

function formatConformity(row) {
  const explicit =
    row?.conformity_percent ??
    row?.conformity_score ??
    row?.score_percent ??
    row?.score;
  const explicitNumeric = numberOrNull(explicit);
  if (explicitNumeric !== null) {
    return formatPercentValue(boundedConformityScore(explicitNumeric));
  }
  const delta = rowDeltaPercent(row);
  if (delta !== null) return formatPercentValue(boundedConformityScore(100 - Math.abs(delta)));
  if (isPassedRow(row)) return "99%";
  if (isReviewRow(row)) return "Partiel";
  if (isFailedRow(row)) return "1%";
  return "-";
}

function rowName(row, index) {
  return row?.visual_name || row?.name || row?.source_title || row?.target_title || `${row?.type || "Visual"} ${index + 1}`;
}

function rowType(row) {
  return row?.visual_type || row?.chart_type || row?.source_chart_type || row?.target_chart_type || row?.kind || row?.type || "visual";
}

function displayTypeLabel(row) {
  const type = rowType(row);
  const normalized = normalizeName(type);
  if (normalized.includes("line")) return "Line";
  if (normalized.includes("bar") || normalized.includes("column")) return "Bar";
  if (normalized.includes("pie") || normalized.includes("donut")) return "Pie";
  if (normalized.includes("kpi")) return "Kpi";
  return String(type || "visual").replace(/[_-]+/g, " ");
}

function rowDetails(row) {
  if (!row) return "";
  if (row.axis_binding_comparison) {
    const axisDetails = formatAxisBindingComparison(row.axis_binding_comparison);
    if (axisDetails) return axisDetails;
  }
  if (row.reason) return row.reason;
  if (row.error) return row.error;
  if (Array.isArray(row.missing_in_target) && row.missing_in_target.length > 0) {
    return `Missing in Tableau: ${row.missing_in_target.join(", ")}`;
  }
  if (Array.isArray(row.missing_in_source) && row.missing_in_source.length > 0) {
    return `Extra in Tableau: ${row.missing_in_source.join(", ")}`;
  }
  if (row.source_signature || row.target_signature) {
    return `RDL: ${row.source_signature || "-"} | Tableau: ${row.target_signature || "-"}`;
  }
  return "";
}

function formatAxisBindingComparison(comparison) {
  if (!comparison || typeof comparison !== "object") return "";
  const source = formatAxisBindings(comparison.source_bindings);
  const target = formatAxisBindings(comparison.target_bindings);
  const reason = comparison.reason || comparison.error || "";
  if (!source && !target && !reason) return "";
  return [`RDL axes: ${source || "-"}`, `Tableau axes: ${target || "-"}`, reason].filter(Boolean).join(" | ");
}

function formatAxisBindings(bindings) {
  if (!Array.isArray(bindings) || bindings.length === 0) return "";
  return bindings
    .map((binding) => {
      const measure = binding?.measure || "";
      const axis = binding?.axis || "";
      const scale = binding?.scale || "";
      const unit = binding?.unit || "";
      return [measure && `${measure} -> ${axis || "axis unknown"}`, scale && `scale=${scale}`, unit && `unit=${unit}`]
        .filter(Boolean)
        .join(", ");
    })
    .filter(Boolean)
    .join("; ");
}

function comparisonRowKey(row, index) {
  return `${row?.type || "visual"}-${rowName(row, index)}-${index}`;
}

function hasExpandedDetails(row, details) {
  const source = fullValueText(row?.source_value);
  const target = fullValueText(row?.target_value);
  return Boolean((details && details !== "Matched") || (source && source !== "-") || (target && target !== "-"));
}

function renderDetailsControl(row, details, isOpen, onToggle) {
  if (!hasExpandedDetails(row, details)) return <span className="quality-muted">Matched</span>;
  return (
    <button className="quality-details-button" type="button" onClick={onToggle}>
      {isOpen ? "Hide details" : "View details"}
    </button>
  );
}

function renderAxisBindingsList(bindings) {
  if (!Array.isArray(bindings) || bindings.length === 0) return <span className="quality-detail-empty">No axis binding extracted</span>;
  return (
    <div className="quality-axis-list">
      {bindings.map((binding, index) => (
        <span key={`${binding?.measure || "measure"}-${binding?.axis || "axis"}-${index}`}>
          <strong>{binding?.measure || "-"}</strong>
          <b>{binding?.axis || "axis unknown"}</b>
          {(binding?.scale || binding?.unit) && <em>{[binding.scale, binding.unit].filter(Boolean).join(" | ")}</em>}
        </span>
      ))}
    </div>
  );
}

function renderExpandedDetails(row, index, details) {
  const sourcePoints = parsePointList(row?.source_value);
  const targetPoints = parsePointList(row?.target_value);
  const hasChartPoints = sourcePoints.length > 0 || targetPoints.length > 0;
  if (hasChartPoints) {
    return (
      <div className="quality-expanded-details">
        <div className="quality-expanded-header">
          <div>
            <strong>{rowName(row, index)}</strong>
            <span>{displayTypeLabel(row)} chart points by measure</span>
          </div>
          <span className={`quality-status-pill tone-${statusTone(rowStatus(row))}`}>
            <span className="quality-status-dot" />
            {readableStatus(rowStatus(row))}
          </span>
        </div>

        {renderChartMeasurePointList(row, sourcePoints, targetPoints)}
      </div>
    );
  }

  return (
    <div className="quality-expanded-details quality-expanded-details--simple">
      <div className="quality-detail-columns">
        <section>
          <h4>RDL value</h4>
          <p>{fullValueText(row?.source_value)}</p>
        </section>
        <section>
          <h4>Tableau value</h4>
          <p>{fullValueText(row?.target_value)}</p>
        </section>
      </div>
      {details && (
        <div className="quality-detail-note">
          <strong>Details</strong>
          <span>{details}</span>
        </div>
      )}
    </div>
  );
}

function isPassedRow(row) {
  const status = normalizeName(rowStatus(row));
  return ["passed", "conforme", "conformant"].includes(status);
}

function isFailedRow(row) {
  const status = normalizeName(rowStatus(row));
  return ["failed", "non_conforme", "non_conformant", "error"].includes(status);
}

function isNonExtractableRow(row) {
  const status = normalizeName(rowStatus(row));
  return ["skipped", "non_extractible"].includes(status);
}

function isReviewRow(row) {
  const status = normalizeName(rowStatus(row));
  return ["partial", "partially_conformant", "ecart_mineur", "minor"].includes(status);
}

function warningText(item) {
  if (!item) return "";
  if (typeof item === "string") return item;
  return item.message || rowDetails(item) || JSON.stringify(item);
}

function extractCount(summary, pattern) {
  const match = String(summary || "").match(pattern);
  if (!match) return null;
  const value = Number(match[1]);
  return Number.isFinite(value) ? value : null;
}

function metricScore(metrics, keywords, fallback) {
  const metric = metrics.find((item) => {
    const text = normalizeName(`${item.id || ""} ${item.label || ""}`);
    return keywords.some((keyword) => text.includes(keyword));
  });
  return Number.isFinite(Number(metric?.score)) ? Number(metric.score) : fallback;
}

function normalizeDiffPath(path) {
  if (!path) return "";
  return String(path).replace(/\\/g, "/");
}

export default function QualityComparisonStep({
  qualityComparison,
  publishedReportTest,
  onRunComparison,
  loading,
  loadingText,
}) {
  const comparison = qualityComparison || {};
  const reportTest = publishedReportTest || {};
  const globalScore = comparison.globalScore || 0;
  const metrics = comparison.metrics || [];
  const passed = comparison.executed && globalScore >= 85;

  const screenshotTests = reportTest.screenshot_tests || {};
  const screenshotComparison = screenshotTests.secondary_visual_check || screenshotTests.comparison || {};
  const dataTests = reportTest.data_tests || {};
  const dataChecks = Array.isArray(dataTests.checks) ? dataTests.checks : [];
  const comparisonRows = Array.isArray(dataTests.comparison_report) ? dataTests.comparison_report : [];
  const warningItems = Array.isArray(dataTests.warnings) ? dataTests.warnings : [];

  const kpiNames = new Set(
    comparisonRows
      .filter((row) => normalizeName(row.type) === "kpi")
      .map((row) => normalizeName(row.visual_name || row.name || row.source_title || row.target_title)),
  );
  const displayComparisonRows = comparisonRows.filter((row) => {
    const type = normalizeName(row.type);
    const name = normalizeName(row.visual_name || row.name || row.source_title || row.target_title);
    return !(type === "visual" && kpiNames.has(name) && isPassedRow(row));
  });

  const passedRows = displayComparisonRows.filter((row) => isPassedRow(row));
  const failedRows = displayComparisonRows.filter((row) => isFailedRow(row));
  const nonExtractableRows = displayComparisonRows.filter((row) => isNonExtractableRow(row));
  const reviewRows = displayComparisonRows.filter((row) => isReviewRow(row));
  const matchedVisuals =
    extractCount(dataTests.summary, /(\d+)\s+visual match/i) ??
    extractCount(dataTests.summary, /(\d+)\/\d+\s+visual/i) ??
    passedRows.length;
  const nonExtractableCount =
    extractCount(dataTests.summary, /(\d+)\s+non-extractable/i) ??
    nonExtractableRows.length;
  const reviewCount = reviewRows.length + warningItems.length;
  const publishedContentScore = metricScore(metrics, ["published report content", "content fidelity"], globalScore);
  const finalStatus = dataTests.conformance_status || dataTests.status || comparison.status;
  const finalStatusTone = statusTone(finalStatus);
  const diffPath = normalizeDiffPath(screenshotComparison.diff_path);
  const [expandedDetailKey, setExpandedDetailKey] = React.useState(null);

  return (
    <Card
      title="12. Quality comparison"
      eyebrow="RDL vs Tableau output"
      className="quality-comparison-card"
      actions={<Badge tone={comparison.executed ? scoreTone(globalScore) : "indigo"}>{statusLabel(comparison)}</Badge>}
    >
      <div className="quality-dashboard" data-testid="quality-comparison">
        <section className="quality-hero">
          <div className="quality-hero-main">
            <div className="quality-hero-title">
              <span>Global quality score</span>
              <strong data-testid="global-quality-score">{comparison.executed ? `${globalScore}%` : "--"}</strong>
              {comparison.executed && <Badge tone={passed ? "green" : "orange"}>{passed ? "Target met" : "Needs review"}</Badge>}
            </div>
            <p>
              {comparison.summary ||
                "Run the comparison after workbook generation to verify published report screenshots and semantic content."}
            </p>
          </div>
        </section>

        <section className="quality-kpi-grid" aria-label="Quality comparison summary">
          <div className="quality-kpi-card tone-green">
            <b>OK</b>
            <span>Conformity score</span>
            <strong>{comparison.executed ? `${publishedContentScore}%` : "--"}</strong>
          </div>
          <div className="quality-kpi-card tone-green">
            <b>OK</b>
            <span>Matched visuals</span>
            <strong>{matchedVisuals}</strong>
          </div>
          <div className="quality-kpi-card tone-orange">
            <b>!</b>
            <span>To review</span>
            <strong>{reviewCount}</strong>
          </div>
          <div className="quality-kpi-card tone-red">
            <b>X</b>
            <span>Failed</span>
            <strong>{failedRows.length}</strong>
          </div>
          <div className="quality-kpi-card tone-slate">
            <b>-</b>
            <span>Non-extractable</span>
            <strong>{nonExtractableCount}</strong>
          </div>
        </section>

        {reportTest.status && (
          <section className="quality-section-card quality-screenshot-card" data-testid="published-report-test">
            <div className="quality-section-heading">
              <div>
                <strong>Screenshot capture validation</strong>
              </div>
              <span className="quality-info-dot">i</span>
            </div>
            <div className="quality-screenshot-grid">
              <div>
                <span>Pixel similarity</span>
                <strong>{screenshotComparison.similarity_percent ?? "-"}%</strong>
              </div>
              <div>
                <span>Threshold</span>
                <strong>{screenshotComparison.min_similarity_percent ?? "-"}%</strong>
              </div>
              <div>
                <span>Status</span>
                <Badge tone={statusTone(screenshotTests.status)}>{readableStatus(screenshotTests.status)}</Badge>
              </div>
              <div>
                <span>Diff image</span>
                {diffPath ? (
                  <details className="quality-diff-details">
                    <summary>View diff</summary>
                    <span>{diffPath}</span>
                  </details>
                ) : (
                  <strong>-</strong>
                )}
              </div>
            </div>
            {(screenshotTests.error || screenshotComparison.error || screenshotComparison.reason) && (
              <p className="quality-error">{screenshotTests.error || screenshotComparison.error || screenshotComparison.reason}</p>
            )}
          </section>
        )}

        {dataTests.status && (
          <section className={`quality-section-card tone-${finalStatusTone}`}>
            <div className="quality-section-heading">
              <div>
                <strong>Semantic report content validation</strong>
                <p>Primary decision based on extracted values, labels, axes, legends, and visual meaning.</p>
              </div>
              <Badge tone={finalStatusTone}>{readableStatus(finalStatus)}</Badge>
            </div>

            {displayComparisonRows.length > 0 && (
              <div className="quality-comparison-table-block">
                <div className="quality-table-wrap">
                  <table className="quality-result-table">
                    <thead>
                      <tr>
                        <th>Visual name</th>
                        <th>Type</th>
                        <th>RDL value</th>
                        <th>Tableau value</th>
                        <th>Conformité</th>
                        <th>Status</th>
                        <th>Details</th>
                      </tr>
                    </thead>
                    <tbody>
                      {displayComparisonRows.map((row, index) => {
                        const status = rowStatus(row);
                        const details = rowDetails(row);
                        const detailKey = comparisonRowKey(row, index);
                        const detailsOpen = expandedDetailKey === detailKey;
                        return (
                          <React.Fragment key={detailKey}>
                            <tr>
                              <td>
                                <span className="quality-visual-name">{rowName(row, index)}</span>
                              </td>
                              <td>
                                <span className="quality-type-pill" title={rowType(row)}>{displayTypeLabel(row)}</span>
                              </td>
                              <td className="quality-value-cell" title={formatValue(row.source_value)}>
                                {renderTableValue(row.source_value)}
                              </td>
                              <td className="quality-value-cell" title={formatValue(row.target_value)}>
                                {renderTableValue(row.target_value)}
                              </td>
                              <td className="quality-conformity-cell">{formatConformity(row)}</td>
                              <td>
                                <span className={`quality-status-pill tone-${statusTone(status)}`}>
                                  <span className="quality-status-dot" />
                                  {readableStatus(status)}
                                </span>
                              </td>
                              <td>
                                {renderDetailsControl(row, details, detailsOpen, () => {
                                  setExpandedDetailKey(detailsOpen ? null : detailKey);
                                })}
                              </td>
                            </tr>
                            {detailsOpen && (
                              <tr className="quality-detail-row">
                                <td colSpan={7}>{renderExpandedDetails(row, index, details)}</td>
                              </tr>
                            )}
                          </React.Fragment>
                        );
                      })}
                    </tbody>
                  </table>
                </div>
              </div>
            )}

            {comparisonRows.length === 0 && dataChecks.length > 0 && (
              <details className="quality-technical-details">
                <summary>Show technical checks</summary>
                <div className="quality-check-list">
                  {dataChecks.map((check, index) => (
                    <div key={`${check.type || "check"}-${check.name || index}`}>
                      <strong>{check.name || check.type || `Check ${index + 1}`}</strong>
                      <Badge tone={statusTone(rowStatus(check))}>{readableStatus(rowStatus(check))}</Badge>
                      {rowDetails(check) && <span>{rowDetails(check)}</span>}
                    </div>
                  ))}
                </div>
              </details>
            )}

            {(reviewRows.length > 0 || nonExtractableRows.length > 0 || warningItems.length > 0) && (
              <details className="quality-technical-details">
                <summary>Show items to review</summary>
                <ul className="quality-review-list">
                  {[...reviewRows, ...nonExtractableRows].slice(0, 8).map((row, index) => (
                    <li key={`review-${rowName(row, index)}-${index}`}>
                      <strong>{rowName(row, index)}:</strong> {rowDetails(row) || readableStatus(rowStatus(row))}
                    </li>
                  ))}
                  {warningItems.slice(0, 8).map((warning, index) => (
                    <li key={`warning-${index}`}>{warningText(warning)}</li>
                  ))}
                </ul>
              </details>
            )}
          </section>
        )}

        {metrics.length > 0 && (
          <details className="quality-technical-details">
            <summary>Show model fidelity metrics</summary>
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
          </details>
        )}

        {reportTest.status === "skipped" && (
          <section className="quality-section-card tone-orange">
            <div className="quality-section-heading">
              <div>
                <strong>Tests skipped</strong>
                <p>Tableau URL: {reportTest.tableau_url || "not found"}</p>
                <p>Power BI URL: {reportTest.powerbi_url || "not found"}</p>
                {reportTest.reason && <p>{reportTest.reason}</p>}
              </div>
              <Badge tone="orange">Skipped</Badge>
            </div>
          </section>
        )}

        {reportTest.reports && typeof reportTest.reports === "object" && reportTest.status !== "skipped" && (
          <details className="quality-technical-details">
            <summary>Show report execution details</summary>
            <div className="quality-check-list">
              {Object.entries(reportTest.reports).map(([reportName, reportResult]) => (
                <div key={reportName}>
                  <strong>{reportName}</strong>
                  <Badge tone={reportResult.status === "passed" ? "green" : "red"}>
                    {reportResult.status === "passed" ? "Passed" : "Failed"}
                  </Badge>
                  {reportResult.screenshot_path && <span>Screenshot: {reportResult.screenshot_path}</span>}
                  {reportResult.failure_screenshot_path && <span>Failure screenshot: {reportResult.failure_screenshot_path}</span>}
                  {reportResult.error && <span className="quality-error">Error: {reportResult.error}</span>}
                </div>
              ))}
            </div>
          </details>
        )}
      </div>

      {loading && <ProcessingProgress label={loadingText || "Quality comparison"} />}

      <div className="button-row">
        <Button variant="primary" onClick={onRunComparison} disabled={loading}>
          {comparison.executed ? "Run comparison again" : "Run quality comparison"}
        </Button>
      </div>
    </Card>
  );
}
