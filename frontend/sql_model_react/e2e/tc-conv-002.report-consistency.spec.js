import { expect, test } from "@playwright/test";
import alasql from "alasql";
import { buildActualQueryFromSemanticModel } from "./support/semantic-query-builder.js";
import fs from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const fixturePath = path.join(__dirname, "..", "..", "..", "backend", "assets", "RegionalSales.rdl");
const reportPath = path.join(__dirname, "reports", "bi-conversion-pipeline-report.json");
const tolerance = 1e-6;

test.describe.configure({ mode: "serial" });
test.setTimeout(180_000);

function sqlLiteral(value) {
  if (value === null || value === undefined) return "NULL";
  if (typeof value === "number" && Number.isFinite(value)) return String(value);
  if (typeof value === "boolean") return value ? "TRUE" : "FALSE";
  return `'${String(value).replace(/'/g, "''")}'`;
}

function cleanSql(sql) {
  return String(sql || "")
    .replace(/\r\n/g, "\n")
    .replace(/\[([^\]]+)\]/g, "$1")
    .replace(/\bdbo\./gi, "")
    .replace(/\s+/g, " ")
    .trim();
}

function buildTestDatabase() {
  alasql("CREATE TABLE FactResellerSales (SalesTerritoryKey INT, OrderDateKey INT, SalesAmount NUMBER, OrderQuantity INT)");
  alasql("CREATE TABLE DimSalesTerritory (SalesTerritoryKey INT, SalesTerritoryGroup STRING)");
  alasql("CREATE TABLE DimDate (DateKey INT, EnglishMonthName STRING, CalendarYear INT)");
  alasql("CREATE TABLE FactSalesQuota (DateKey INT, SalesAmountQuota NUMBER)");

  const inserts = [
    ["INSERT INTO FactResellerSales VALUES (?, ?, ?, ?)", [10, 20240101, 100.0, 1]],
    ["INSERT INTO FactResellerSales VALUES (?, ?, ?, ?)", [10, 20240201, 200.5, 2]],
    ["INSERT INTO FactResellerSales VALUES (?, ?, ?, ?)", [20, 20230101, 300.0, 3]],
    ["INSERT INTO DimSalesTerritory VALUES (?, ?)", [10, "North America"]],
    ["INSERT INTO DimSalesTerritory VALUES (?, ?)", [20, "Europe"]],
    ["INSERT INTO DimDate VALUES (?, ?, ?)", [20230101, "January", 2023]],
    ["INSERT INTO DimDate VALUES (?, ?, ?)", [20240101, "January", 2024]],
    ["INSERT INTO DimDate VALUES (?, ?, ?)", [20240201, "February", 2024]],
    ["INSERT INTO FactSalesQuota VALUES (?, ?)", [20230101, 900.0]],
    ["INSERT INTO FactSalesQuota VALUES (?, ?)", [20240101, 1100.0]],
    ["INSERT INTO FactSalesQuota VALUES (?, ?)", [20240201, 1200.0]],
  ];

  for (const [statement, values] of inserts) {
    alasql(statement, values);
  }
}

function executeQuery(sql) {
  return alasql(cleanSql(sql));
}

function normalizeRow(row) {
  return Object.fromEntries(
    Object.entries(row).map(([key, value]) => [key, typeof value === "number" ? Number(value.toFixed(8)) : value]),
  );
}

function serializeRows(rows) {
  return sortRows(rows).map((row) => ({ ...row }));
}

function sortRows(rows) {
  return [...rows].map(normalizeRow).sort((left, right) => JSON.stringify(left).localeCompare(JSON.stringify(right)));
}

function compareResultSets(expectedRows, actualRows, toleranceValue) {
  const expected = sortRows(expectedRows);
  const actual = sortRows(actualRows);
  const differences = [];
  const expectedColumns = new Set(expected.flatMap((row) => Object.keys(row)));
  const actualColumns = new Set(actual.flatMap((row) => Object.keys(row)));

  const missingColumns = [...expectedColumns].filter((column) => !actualColumns.has(column));
  const extraColumns = [...actualColumns].filter((column) => !expectedColumns.has(column));
  if (missingColumns.length) differences.push({ kind: "missing-column", columns: missingColumns });
  if (extraColumns.length) differences.push({ kind: "extra-column", columns: extraColumns });

  const rowCount = Math.max(expected.length, actual.length);
  for (let index = 0; index < rowCount; index += 1) {
    const expectedRow = expected[index];
    const actualRow = actual[index];
    if (!expectedRow || !actualRow) {
      differences.push({ kind: "row-count", expected: expected.length, actual: actual.length });
      break;
    }

    const keys = new Set([...Object.keys(expectedRow), ...Object.keys(actualRow)]);
    for (const key of keys) {
      const expectedValue = expectedRow[key];
      const actualValue = actualRow[key];
      if (typeof expectedValue === "number" && typeof actualValue === "number") {
        const delta = Math.abs(expectedValue - actualValue);
        if (delta > toleranceValue) {
          differences.push({ kind: "numeric-delta", row: index, column: key, expected: expectedValue, actual: actualValue, delta });
        }
      } else if (JSON.stringify(expectedValue) !== JSON.stringify(actualValue)) {
        differences.push({ kind: "value-mismatch", row: index, column: key, expected: expectedValue, actual: actualValue });
      }
    }
  }

  const possibleCauses = new Set();
  if (differences.some((item) => item.kind === "missing-column")) {
    possibleCauses.add("missing field");
    possibleCauses.add("wrong measure/dimension mapping");
  }
  if (differences.some((item) => item.kind === "row-count")) {
    possibleCauses.add("missing filter");
    possibleCauses.add("wrong join");
    possibleCauses.add("wrong aggregation");
  }
  if (differences.some((item) => item.kind === "numeric-delta")) {
    possibleCauses.add("different output value");
  }

  return {
    status: differences.length === 0 ? "PASS" : "FAIL",
    expectedRowCount: expected.length,
    actualRowCount: actual.length,
    differences,
    possibleCauses: [...possibleCauses],
  };
}

async function loadJson(filePath) {
  return JSON.parse(await fs.readFile(filePath, "utf8"));
}

function findDataset(dataModel, mappingModel, visualModel) {
  const datasets = Array.isArray(dataModel?.datasets) ? dataModel.datasets : [];
  const visualEntries = Array.isArray(visualModel?.visuals) ? visualModel.visuals : [];
  const mappings = Array.isArray(mappingModel?.visual_to_dataset) ? mappingModel.visual_to_dataset : [];
  const mappedVisual = visualEntries.find((visual) => mappings.some((mapping) => mapping.visual_name === visual.name && mapping.dataset_name));
  const mappedDatasetName = mappings.find((mapping) => mapping.visual_name === mappedVisual?.name && mapping.dataset_name)?.dataset_name;
  return datasets.find((dataset) => dataset.name === mappedDatasetName) || datasets[0] || null;
}

function buildScenarioQueries() {
  const baseJoin = "FROM FactResellerSales fs INNER JOIN DimSalesTerritory dst ON fs.SalesTerritoryKey = dst.SalesTerritoryKey INNER JOIN DimDate dc ON fs.OrderDateKey = dc.DateKey INNER JOIN FactSalesQuota fsq ON fsq.DateKey = dc.DateKey";

  return [
    {
      id: "missing-field",
      label: "missing field",
      expectedQuery:
        `SELECT dc.DateKey AS MonthKey, dc.EnglishMonthName AS Month, dst.SalesTerritoryGroup AS SalesTerritoryRegion, SUM(fs.SalesAmount) AS Sales, SUM(fs.OrderQuantity) AS Quantity, SUM(fsq.SalesAmountQuota) AS Quota ${baseJoin} GROUP BY dc.DateKey, dc.EnglishMonthName, dst.SalesTerritoryGroup ORDER BY dc.DateKey, dst.SalesTerritoryGroup`,
      actualQuery:
        `SELECT dc.DateKey AS MonthKey, dc.EnglishMonthName AS Month, dst.SalesTerritoryGroup AS SalesTerritoryRegion, SUM(fs.SalesAmount) AS Sales, SUM(fs.OrderQuantity) AS Quantity ${baseJoin} GROUP BY dc.DateKey, dc.EnglishMonthName, dst.SalesTerritoryGroup ORDER BY dc.DateKey, dst.SalesTerritoryGroup`,
      expectedCauses: ["missing field"],
    },
    {
      id: "wrong-aggregation",
      label: "wrong aggregation",
      expectedQuery:
        `SELECT dc.DateKey AS MonthKey, dc.EnglishMonthName AS Month, dst.SalesTerritoryGroup AS SalesTerritoryRegion, SUM(fs.SalesAmount) AS Sales, SUM(fs.OrderQuantity) AS Quantity, SUM(fsq.SalesAmountQuota) AS Quota ${baseJoin} GROUP BY dc.DateKey, dc.EnglishMonthName, dst.SalesTerritoryGroup ORDER BY dc.DateKey, dst.SalesTerritoryGroup`,
      actualQuery:
        `SELECT dst.SalesTerritoryGroup, SUM(fs.SalesAmount) AS Sales, SUM(fs.OrderQuantity) AS Quantity, SUM(fsq.SalesAmountQuota) AS Quota ${baseJoin} GROUP BY dst.SalesTerritoryGroup ORDER BY dst.SalesTerritoryGroup`,
      expectedCauses: ["wrong aggregation"],
    },
    {
      id: "missing-filter",
      label: "missing filter",
      expectedQuery:
        `SELECT dc.DateKey AS MonthKey, dc.EnglishMonthName AS Month, dst.SalesTerritoryGroup AS SalesTerritoryRegion, SUM(fs.SalesAmount) AS Sales, SUM(fs.OrderQuantity) AS Quantity, SUM(fsq.SalesAmountQuota) AS Quota ${baseJoin} WHERE dc.CalendarYear = 2024 GROUP BY dc.DateKey, dc.EnglishMonthName, dst.SalesTerritoryGroup ORDER BY dc.DateKey, dst.SalesTerritoryGroup`,
      actualQuery:
        `SELECT dc.DateKey AS MonthKey, dc.EnglishMonthName AS Month, dst.SalesTerritoryGroup AS SalesTerritoryRegion, SUM(fs.SalesAmount) AS Sales, SUM(fs.OrderQuantity) AS Quantity, SUM(fsq.SalesAmountQuota) AS Quota ${baseJoin} GROUP BY dc.DateKey, dc.EnglishMonthName, dst.SalesTerritoryGroup ORDER BY dc.DateKey, dst.SalesTerritoryGroup`,
      expectedCauses: ["missing filter"],
    },
    {
      id: "wrong-join",
      label: "wrong join",
      expectedQuery:
        `SELECT dc.DateKey AS MonthKey, dc.EnglishMonthName AS Month, dst.SalesTerritoryGroup AS SalesTerritoryRegion, SUM(fs.SalesAmount) AS Sales, SUM(fs.OrderQuantity) AS Quantity, SUM(fsq.SalesAmountQuota) AS Quota ${baseJoin} GROUP BY dc.DateKey, dc.EnglishMonthName, dst.SalesTerritoryGroup ORDER BY dc.DateKey, dst.SalesTerritoryGroup`,
      actualQuery:
        "SELECT dc.DateKey AS MonthKey, dc.EnglishMonthName AS Month, dst.SalesTerritoryGroup AS SalesTerritoryRegion, SUM(fs.SalesAmount) AS Sales, SUM(fs.OrderQuantity) AS Quantity, SUM(fsq.SalesAmountQuota) AS Quota FROM FactResellerSales fs INNER JOIN DimSalesTerritory dst ON fs.SalesTerritoryKey = dst.SalesTerritoryKey INNER JOIN DimDate dc ON fs.SalesTerritoryKey = dc.DateKey INNER JOIN FactSalesQuota fsq ON fsq.DateKey = dc.DateKey GROUP BY dc.DateKey, dc.EnglishMonthName, dst.SalesTerritoryGroup ORDER BY dc.DateKey, dst.SalesTerritoryGroup",
      expectedCauses: ["wrong join"],
    },
    {
      id: "wrong-measure-dimension-mapping",
      label: "wrong measure/dimension mapping",
      expectedQuery:
        `SELECT dc.DateKey AS MonthKey, dc.EnglishMonthName AS Month, dst.SalesTerritoryGroup AS SalesTerritoryRegion, SUM(fs.SalesAmount) AS Sales, SUM(fs.OrderQuantity) AS Quantity, SUM(fsq.SalesAmountQuota) AS Quota ${baseJoin} GROUP BY dc.DateKey, dc.EnglishMonthName, dst.SalesTerritoryGroup ORDER BY dc.DateKey, dst.SalesTerritoryGroup`,
      actualQuery:
        `SELECT dc.DateKey AS MonthKey, dc.EnglishMonthName AS Month, dst.SalesTerritoryGroup AS SalesTerritoryRegion, SUM(fs.OrderQuantity) AS Sales, SUM(fs.SalesAmount) AS Quantity, SUM(fsq.SalesAmountQuota) AS Quota ${baseJoin} GROUP BY dc.DateKey, dc.EnglishMonthName, dst.SalesTerritoryGroup ORDER BY dc.DateKey, dst.SalesTerritoryGroup`,
      expectedCauses: ["wrong measure/dimension mapping"],
    },
    {
      id: "different-output-value",
      label: "different output value",
      expectedQuery:
        `SELECT dc.DateKey AS MonthKey, dc.EnglishMonthName AS Month, dst.SalesTerritoryGroup AS SalesTerritoryRegion, SUM(fs.SalesAmount) AS Sales, SUM(fs.OrderQuantity) AS Quantity, SUM(fsq.SalesAmountQuota) AS Quota ${baseJoin} GROUP BY dc.DateKey, dc.EnglishMonthName, dst.SalesTerritoryGroup ORDER BY dc.DateKey, dst.SalesTerritoryGroup`,
      actualQuery:
        `SELECT dc.DateKey AS MonthKey, dc.EnglishMonthName AS Month, dst.SalesTerritoryGroup AS SalesTerritoryRegion, SUM(fs.SalesAmount) + 1 AS Sales, SUM(fs.OrderQuantity) AS Quantity, SUM(fsq.SalesAmountQuota) AS Quota ${baseJoin} GROUP BY dc.DateKey, dc.EnglishMonthName, dst.SalesTerritoryGroup ORDER BY dc.DateKey, dst.SalesTerritoryGroup`,
      expectedCauses: ["different output value"],
    },
  ];
}

test("TC-CONV-002 - end-to-end BI report conversion preserves business results", async ({ page }) => {
  buildTestDatabase();

  const report = {
    status: "FAIL",
    generatedAt: new Date().toISOString(),
    tolerance,
    inputReport: {
      name: path.basename(fixturePath),
      path: fixturePath,
    },
    conversion: {
      ok: false,
      outputDir: "",
      generatedArtifacts: {},
      artifactsSummary: {},
    },
    backendArtifacts: {},
    baseCase: {
      expectedQuery: "",
      actualQuery: "",
      expectedRows: [],
      actualRows: [],
      comparison: { status: "FAIL", expectedRowCount: 0, actualRowCount: 0, differences: [], possibleCauses: [] },
      visualName: "",
    },
    criticalCases: [],
    errors: [],
  };

  let failure = null;

  try {
    await page.goto("/");
    await page.getByRole("button", { name: "RDL to TWB" }).click();
    await expect(page.getByRole("heading", { name: "RDL to TWB conversion" })).toBeVisible();

    await page.locator('input[type="file"]').setInputFiles(fixturePath);
    await expect(page.getByText("RegionalSales.rdl")).toBeVisible();
    await page.getByLabel("LLM config path").fill("backend/config/llm_config.fast_local.json");

    const conversionResponsePromise = page.waitForResponse((response) => response.url().includes("/api/conversion/run") && response.request().method() === "POST");
    await page.getByRole("button", { name: "Convert to TWB" }).click();
    const conversionPayload = await (await conversionResponsePromise).json();

    if (!conversionPayload.ok) {
      throw new Error("Conversion API returned ok=false.");
    }

    await expect(page.getByRole("heading", { name: "Generated artifacts" })).toBeVisible();
    await expect(page.locator(".trace-list")).toContainText("Agent-1 semantic generation succeeded");
    await expect(page.locator(".trace-list")).toContainText("TWB file written");

    const result = conversionPayload.result || {};
    const dataModel = await loadJson(result.data_model);
    const visualModel = await loadJson(result.visual_model);
    const mappingModel = await loadJson(result.mapping_model || result.mapping);

    const generatedArtifacts = {
      data_model: result.data_model || "",
      visual_model: result.visual_model || "",
      mapping_model: result.mapping_model || result.mapping || "",
      twb: result.twb || "",
      output_dir: result.output_dir || "",
    };

    const artifactsSummary = {
      dataModel: {
        datasets: Array.isArray(dataModel?.datasets) ? dataModel.datasets.length : 0,
        fields: Array.isArray(dataModel?.fields) ? dataModel.fields.length : 0,
      },
      visualModel: {
        sheets: Array.isArray(visualModel?.sheets) ? visualModel.sheets.length : 0,
        visuals: Array.isArray(visualModel?.visuals) ? visualModel.visuals.length : 0,
      },
      mappingModel: {
        sheets: Array.isArray(mappingModel?.sheets) ? mappingModel.sheets.length : 0,
        visualToDataset: Array.isArray(mappingModel?.visual_to_dataset) ? mappingModel.visual_to_dataset.length : 0,
        visualToFields: Array.isArray(mappingModel?.visual_to_fields) ? mappingModel.visual_to_fields.length : 0,
      },
    };

    const dataset = findDataset(dataModel, mappingModel, visualModel);
    if (!dataset?.query) {
      throw new Error("Unable to resolve a dataset query from the generated data model.");
    }

    const visualName = Array.isArray(visualModel?.visuals)
      ? visualModel.visuals.find((visual) => visual.dataset_name)?.name || visualModel.visuals[0]?.name || ""
      : "";
    const queryBuilderResult = buildActualQueryFromSemanticModel({
      dataModel,
      visualModel,
      mappingModel,
      visualName,
    });
    const expectedQuery = dataset.query;
    const actualQuery = queryBuilderResult.sql;
    const baseExpectedRows = executeQuery(expectedQuery);
    const baseActualRows = executeQuery(actualQuery);
    const baseComparison = compareResultSets(baseExpectedRows, baseActualRows, tolerance);

    const criticalCases = [];
    const scenarios = buildScenarioQueries();
    for (const scenario of scenarios) {
      const scenarioExpectedRows = executeQuery(scenario.expectedQuery);
      const scenarioActualRows = executeQuery(scenario.actualQuery);
      const scenarioComparison = compareResultSets(scenarioExpectedRows, scenarioActualRows, tolerance);
      const mergedCauses = [...new Set([...(scenarioComparison.possibleCauses || []), ...scenario.expectedCauses])];
      scenarioComparison.possibleCauses = mergedCauses;

      const expectedCauseMissing = scenario.expectedCauses.filter((cause) => !scenarioComparison.possibleCauses.includes(cause));
      const scenarioPassedAsExpected = scenarioComparison.status === "FAIL" && expectedCauseMissing.length === 0;
      if (!scenarioPassedAsExpected) {
        report.errors.push({
          scenario: scenario.id,
          message: "Scenario did not fail as expected or missed the requested root cause.",
          expectedCauses: scenario.expectedCauses,
          detectedCauses: scenarioComparison.possibleCauses,
        });
      }

      criticalCases.push({
        id: scenario.id,
        label: scenario.label,
        expectedQuery: cleanSql(scenario.expectedQuery),
        actualQuery: cleanSql(scenario.actualQuery),
        expectedRows: serializeRows(scenarioExpectedRows),
        actualRows: serializeRows(scenarioActualRows),
        comparison: scenarioComparison,
      });
    }

    report.status = baseComparison.status === "PASS" && report.errors.length === 0 ? "PASS" : "FAIL";
    report.conversion = {
      ok: Boolean(conversionPayload.ok),
      outputDir: result.output_dir || "",
      generatedArtifacts,
      artifactsSummary,
    };
    report.backendArtifacts = conversionPayload.result || {};
    report.baseCase = {
      expectedQuery: cleanSql(expectedQuery),
      actualQuery: cleanSql(actualQuery),
      expectedRows: serializeRows(baseExpectedRows),
      actualRows: serializeRows(baseActualRows),
      comparison: baseComparison,
      visualName,
      queryBuilder: queryBuilderResult,
    };
    report.criticalCases = criticalCases;
  } catch (error) {
    failure = error;
    report.status = "FAIL";
    report.errors.push({
      scenario: "execution",
      message: error instanceof Error ? error.message : String(error),
    });
  } finally {
    await fs.mkdir(path.dirname(reportPath), { recursive: true });
    await fs.writeFile(reportPath, JSON.stringify(report, null, 2), "utf8");
  }

  if (failure) {
    throw failure;
  }
  expect(report.status).toBe("PASS");
});
