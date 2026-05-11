import { expect, test } from "@playwright/test";
import alasql from "alasql";
import { buildActualQueryFromSemanticModel } from "./support/semantic-query-builder.js";
import fs from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const validRdlPath = path.join(__dirname, "..", "..", "..", "backend", "assets", "RegionalSales.rdl");
const reportPath = path.join(__dirname, "reports", "bi-conversion-pipeline-constraints-report.json");
const tolerance = 1e-6;

test.describe.configure({ mode: "serial" });
test.setTimeout(360_000);

const noDatasetRdl = `<?xml version="1.0" encoding="utf-8"?>
<Report xmlns="http://schemas.microsoft.com/sqlserver/reporting/2016/01/reportdefinition">
  <DataSources>
    <DataSource Name="SalesDb">
      <ConnectionProperties>
        <DataProvider>SQL</DataProvider>
        <ConnectString>Data Source=localhost;Initial Catalog=Sales</ConnectString>
      </ConnectionProperties>
    </DataSource>
  </DataSources>
  <ReportSections>
    <ReportSection>
      <Body>
        <ReportItems />
        <Height>1in</Height>
      </Body>
      <Width>6in</Width>
    </ReportSection>
  </ReportSections>
</Report>`;

function baseRegionalRelationshipModel() {
  return {
    model_type: "Star schema",
    schema_confidence: "high",
    model_summary: "RegionalSales semantic model used for deterministic relationship guardrail testing.",
    fact_tables: [
      {
        name: "FactResellerSales",
        foreign_keys: [
          "OrderDateKey",
          "CurrencyKey",
          "EmployeeKey",
          "ProductKey",
          "SalesTerritoryKey",
          "PromotionKey",
        ],
        measures: ["SalesAmount", "OrderQuantity"],
      },
    ],
    direct_dimensions: [
      { name: "DimDate", natural_key: "DateKey", attributes: ["EnglishMonthName", "CalendarYear"] },
      { name: "DimCurrency", natural_key: "CurrencyKey", attributes: ["CurrencyName"] },
      { name: "DimEmployee", natural_key: "EmployeeKey", attributes: ["EmployeeName"] },
      { name: "DimProduct", natural_key: "ProductKey", attributes: ["ProductName", "ProductSubcategoryKey"] },
      { name: "DimProductSubcategory", natural_key: "ProductSubcategoryKey", attributes: ["ProductCategoryKey"] },
      { name: "DimSalesTerritory", natural_key: "SalesTerritoryKey", attributes: ["SalesTerritoryGroup"] },
    ],
    relationships: [
      {
        from_table: "FactResellerSales",
        to_table: "DimDate",
        join_condition: "FactResellerSales.OrderDateKey = DimDate.DateKey",
        cardinality: "many-to-one",
      },
      {
        from_table: "FactResellerSales",
        to_table: "DimEmployee",
        join_condition: "FactResellerSales.EmployeeKey = DimEmployee.EmployeeKey",
        cardinality: "many-to-one",
      },
      {
        from_table: "DimProduct",
        to_table: "DimProductSubcategory",
        join_condition: "DimProduct.ProductSubcategoryKey = DimProductSubcategory.ProductSubcategoryKey",
        cardinality: "many-to-one",
      },
    ],
    warnings: [],
  };
}

function cleanSql(sql) {
  return String(sql || "")
    .replace(/\r\n/g, "\n")
    .replace(/\[([^\]]+)\]/g, "$1")
    .replace(/\bdbo\./gi, "")
    .replace(/\s+/g, " ")
    .trim();
}

let ExpectedDB = null;
let ActualDB = null;

const sourceTableSchemas = {
  FactResellerSales: [
    { name: "SalesTerritoryKey", type: "INT" },
    { name: "OrderDateKey", type: "INT" },
    { name: "SalesAmount", type: "NUMBER" },
    { name: "OrderQuantity", type: "INT" },
  ],
  DimSalesTerritory: [
    { name: "SalesTerritoryKey", type: "INT" },
    { name: "SalesTerritoryGroup", type: "STRING" },
  ],
  DimDate: [
    { name: "DateKey", type: "INT" },
    { name: "EnglishMonthName", type: "STRING" },
    { name: "CalendarYear", type: "INT" },
  ],
  FactSalesQuota: [
    { name: "DateKey", type: "INT" },
    { name: "SalesAmountQuota", type: "NUMBER" },
  ],
};

const sourceRows = {
  FactResellerSales: [
    [10, 20240101, 100.0, 1],
    [10, 20240201, 200.5, 2],
    [20, 20230101, 300.0, 3],
    [20, 20240101, 150.0, 1],
  ],
  DimSalesTerritory: [
    [10, "North America"],
    [20, "Europe"],
  ],
  DimDate: [
    [20230101, "January", 2023],
    [20240101, "January", 2024],
    [20240201, "February", 2024],
  ],
  FactSalesQuota: [
    [20230101, 900.0],
    [20240101, 1100.0],
    [20240201, 1200.0],
  ],
};

function createTableStatement(tableName, columns) {
  return `CREATE TABLE ${tableName} (${columns.map((column) => `${column.name} ${column.type}`).join(", ")})`;
}

function insertRows(dbInstance, tableName, rows, columns) {
  const placeholders = columns.map(() => "?").join(", ");
  for (const row of rows) {
    dbInstance.exec(`INSERT INTO ${tableName} VALUES (${placeholders})`, row);
  }
}

function buildExpectedDatabase() {
  // ExpectedDB represents the original RDL source database.
  alasql.databases.expectedDB = new alasql.Database("expectedDB");
  ExpectedDB = alasql.databases.expectedDB;

  for (const [tableName, columns] of Object.entries(sourceTableSchemas)) {
    ExpectedDB.exec(createTableStatement(tableName, columns));
    insertRows(ExpectedDB, tableName, sourceRows[tableName] || [], columns);
  }
}

function normalizeModelTableName(tableName) {
  return String(tableName || "")
    .replace(/\[([^\]]+)\]/g, "$1")
    .replace(/^dbo\./i, "")
    .split(".")
    .pop()
    .trim();
}

function isSqlKeyword(value) {
  return [
    "WHERE",
    "GROUP",
    "ORDER",
    "HAVING",
    "INNER",
    "LEFT",
    "RIGHT",
    "FULL",
    "CROSS",
    "JOIN",
    "ON",
  ].includes(String(value || "").toUpperCase());
}

function collectTableAliases(sql) {
  const aliasToTable = new Map();
  const query = cleanSql(sql);
  const tablePattern = /\b(?:FROM|JOIN)\s+([A-Za-z_][A-Za-z0-9_.]*)(?:\s+(?:AS\s+)?([A-Za-z_][A-Za-z0-9_]*))?/gi;
  let match = tablePattern.exec(query);
  while (match) {
    const tableName = normalizeModelTableName(match[1]);
    const alias = match[2] && !isSqlKeyword(match[2]) ? match[2] : tableName;
    if (tableName) {
      aliasToTable.set(alias, tableName);
      aliasToTable.set(tableName, tableName);
    }
    match = tablePattern.exec(query);
  }
  return aliasToTable;
}

function requiredSourceTablesFromDataModel(dataModel) {
  const required = new Map();
  const datasets = Array.isArray(dataModel?.datasets) ? dataModel.datasets : [];
  for (const dataset of datasets) {
    if (!dataset?.query) continue;
    const query = cleanSql(dataset.query);
    const aliasToTable = collectTableAliases(query);
    for (const tableName of aliasToTable.values()) {
      if (!required.has(tableName)) required.set(tableName, new Set());
    }

    const refPattern = /\b([A-Za-z_][A-Za-z0-9_]*)\.([A-Za-z_][A-Za-z0-9_]*)\b/g;
    let refMatch = refPattern.exec(query);
    while (refMatch) {
      const tableName = aliasToTable.get(refMatch[1]);
      if (tableName) {
        if (!required.has(tableName)) required.set(tableName, new Set());
        required.get(tableName).add(refMatch[2]);
      }
      refMatch = refPattern.exec(query);
    }
  }
  return required;
}

function buildActualDatabaseFromValidatedModel(dataModel, sourceDb) {
  // ActualDB represents the generated/validated semantic model, not a hardcoded clone.
  alasql.databases.actualDB = new alasql.Database("actualDB");
  ActualDB = alasql.databases.actualDB;

  const required = requiredSourceTablesFromDataModel(dataModel);
  const tables = [];
  for (const [tableName, requiredColumns] of required.entries()) {
    const sourceColumns = sourceTableSchemas[tableName];
    if (!sourceColumns) {
      throw new Error(`Validated data model references unknown source table '${tableName}'.`);
    }

    const selectedColumns = sourceColumns.filter((column) => requiredColumns.size === 0 || requiredColumns.has(column.name));
    const missingColumns = [...requiredColumns].filter((columnName) => !sourceColumns.some((column) => column.name === columnName));
    if (missingColumns.length) {
      throw new Error(`Validated data model references unknown columns on '${tableName}': ${missingColumns.join(", ")}`);
    }
    if (!selectedColumns.length) {
      throw new Error(`Validated data model resolved table '${tableName}' without any usable columns.`);
    }

    ActualDB.exec(createTableStatement(tableName, selectedColumns));
    const columnNames = selectedColumns.map((column) => column.name);
    const sourceRowsForTable = sourceDb.exec(`SELECT ${columnNames.join(", ")} FROM ${tableName}`);
    insertRows(
      ActualDB,
      tableName,
      sourceRowsForTable.map((row) => columnNames.map((columnName) => row[columnName])),
      selectedColumns,
    );
    tables.push({
      tableName,
      columns: columnNames,
      rowCount: sourceRowsForTable.length,
      source: "validated_data_model",
    });
  }

  return {
    name: "ActualDB",
    source: "validated_data_model",
    tableCount: tables.length,
    tables,
  };
}

function executeQuery(sql, dbInstance) {
  if (!dbInstance) {
    throw new Error("Database instance not provided");
  }
  try {
    return dbInstance.exec(cleanSql(sql));
  } catch (error) {
    throw new Error(`Query execution failed: ${error.message}`);
  }
}
function normalizeRow(row) {
  return Object.fromEntries(
    Object.entries(row).map(([key, value]) => [key, typeof value === "number" ? Number(value.toFixed(8)) : value]),
  );
}

function sortRows(rows) {
  return [...rows].map(normalizeRow).sort((left, right) => JSON.stringify(left).localeCompare(JSON.stringify(right)));
}

function serializeRows(rows) {
  return sortRows(rows).map((row) => ({ ...row }));
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

function flattenVisuals(visuals) {
  const output = [];
  const visit = (visual) => {
    if (!visual || typeof visual !== "object") return;
    output.push(visual);
    if (Array.isArray(visual.children)) {
      visual.children.forEach(visit);
    }
  };
  if (Array.isArray(visuals)) {
    visuals.forEach(visit);
  }
  return output;
}

function visualFieldReferences(visual) {
  const propertyFields = Array.isArray(visual?.properties?.field_references)
    ? visual.properties.field_references
    : [];
  const layoutFields =
    typeof visual?.layout?.referenced_fields === "string"
      ? visual.layout.referenced_fields.split(",").map((field) => field.trim())
      : [];
  const expressionFields = Array.isArray(visual?.expressions)
    ? visual.expressions.flatMap((expression) => {
        const refs = [];
        const pattern = /Fields!([A-Za-z0-9_]+)\.Value/gi;
        let match = pattern.exec(String(expression || ""));
        while (match) {
          refs.push(match[1]);
          match = pattern.exec(String(expression || ""));
        }
        return refs;
      })
    : [];
  return [...new Set([...propertyFields, ...layoutFields, ...expressionFields].map((field) => String(field || "").trim()).filter(Boolean))];
}

function mappedFieldsForVisual(mappingModel, visualName) {
  const visualToFields = Array.isArray(mappingModel?.visual_to_fields) ? mappingModel.visual_to_fields : [];
  const entry = visualToFields.find((mapping) => mapping.visual_name === visualName);
  return Array.isArray(entry?.fields)
    ? [...new Set(entry.fields.map((field) => String(field || "").trim()).filter(Boolean))]
    : [];
}

function datasetNameForVisual(mappingModel, visual) {
  const visualToDataset = Array.isArray(mappingModel?.visual_to_dataset) ? mappingModel.visual_to_dataset : [];
  return (
    visualToDataset.find((mapping) => mapping.visual_name === visual?.name && mapping.dataset_name)?.dataset_name ||
    visual?.dataset_name ||
    ""
  );
}

function businessVisualCases(dataModel, visualModel, mappingModel) {
  const datasetsByName = new Map((Array.isArray(dataModel?.datasets) ? dataModel.datasets : []).map((dataset) => [dataset.name, dataset]));
  const seen = new Set();
  const cases = [];
  for (const visual of flattenVisuals(visualModel?.visuals)) {
    const visualName = String(visual?.name || "").trim();
    if (!visualName || seen.has(visualName)) continue;
    seen.add(visualName);

    const datasetName = datasetNameForVisual(mappingModel, visual);
    const dataset = datasetsByName.get(datasetName);
    const sourceFields = visualFieldReferences(visual);
    const mappedFields = mappedFieldsForVisual(mappingModel, visualName);
    const hasBusinessFields = sourceFields.length > 0 || mappedFields.length > 0;
    if (!dataset?.query || !hasBusinessFields) continue;

    cases.push({
      visualName,
      visualType: visual.visual_type || "",
      semanticHint: visual?.properties?.semantic_hint || "",
      datasetName,
      sourceFields,
      mappedFields,
    });
  }
  return cases;
}

function coerceParameterValue(value, type) {
  const normalizedType = String(type || "").toLowerCase();
  const text = String(value ?? "").replace(/^=/, "").replace(/^"|"$/g, "").trim();
  if (/^(integer|int|float|decimal|double|number)$/i.test(normalizedType) && /^-?\d+(\.\d+)?$/.test(text)) {
    return Number(text);
  }
  return text;
}

function firstRowValue(rows, fieldName) {
  const firstRow = Array.isArray(rows) ? rows[0] : null;
  if (!firstRow || typeof firstRow !== "object") return undefined;
  if (fieldName && Object.prototype.hasOwnProperty.call(firstRow, fieldName)) {
    return firstRow[fieldName];
  }
  const firstKey = Object.keys(firstRow)[0];
  return firstKey ? firstRow[firstKey] : undefined;
}

function resolveFixedParameters(dataModel, parsedRdl, dbInstance) {
  const datasetsByName = new Map((Array.isArray(dataModel?.datasets) ? dataModel.datasets : []).map((dataset) => [dataset.name, dataset]));
  const parsedParameters = Array.isArray(parsedRdl?.report_parameters) ? parsedRdl.report_parameters : [];
  const fallbackParameters = Array.isArray(dataModel?.parameters) ? dataModel.parameters : [];
  const parameters = parsedParameters.length ? parsedParameters : fallbackParameters;
  const values = {};
  const details = [];

  for (const parameter of parameters) {
    const name = String(parameter?.name || "").trim();
    if (!name) continue;

    const defaultValues = Array.isArray(parameter?.default_values) ? parameter.default_values : [];
    if (defaultValues.length) {
      values[name] = coerceParameterValue(defaultValues[0], parameter?.type);
      details.push({ parameter: name, value: values[name], source: "rdl_default_value" });
      continue;
    }

    const datasetReference = parameter?.valid_values?.dataset_reference || parameter?.default_values?.dataset_reference || {};
    const datasetName = datasetReference.dataset_name || `dsParam${name}`;
    const valueField = datasetReference.value_field || name;
    const dataset = datasetsByName.get(datasetName);
    if (dataset?.query) {
      const rows = executeQuery(dataset.query, dbInstance);
      const resolved = firstRowValue(rows, valueField);
      if (resolved !== undefined) {
        values[name] = coerceParameterValue(resolved, parameter?.type);
        details.push({ parameter: name, value: values[name], source: "first_valid_value", datasetName, valueField });
      }
    }
  }

  return { values, details };
}

function unresolvedFilterParameters(dataModel, fixedParameters) {
  const unresolved = new Set();
  const datasets = Array.isArray(dataModel?.datasets) ? dataModel.datasets : [];
  for (const dataset of datasets) {
    const filters = Array.isArray(dataset?.filters) ? dataset.filters : [];
    for (const filter of filters) {
      const references = Array.isArray(filter?.parameter_references) ? filter.parameter_references : [];
      for (const parameterName of references) {
        if (!Object.prototype.hasOwnProperty.call(fixedParameters, parameterName)) {
          unresolved.add(parameterName);
        }
      }
    }
  }
  return [...unresolved];
}

function buildScenarioQueries() {
  const baseJoin = "FROM FactResellerSales fs INNER JOIN DimSalesTerritory dst ON fs.SalesTerritoryKey = dst.SalesTerritoryKey INNER JOIN DimDate dc ON fs.OrderDateKey = dc.DateKey INNER JOIN FactSalesQuota fsq ON fsq.DateKey = dc.DateKey";
  const baseline =
    `SELECT dc.DateKey AS MonthKey, dc.EnglishMonthName AS Month, dst.SalesTerritoryGroup AS SalesTerritoryRegion, SUM(fs.SalesAmount) AS Sales, SUM(fs.OrderQuantity) AS Quantity, SUM(fsq.SalesAmountQuota) AS Quota ${baseJoin} GROUP BY dc.DateKey, dc.EnglishMonthName, dst.SalesTerritoryGroup ORDER BY dc.DateKey, dst.SalesTerritoryGroup`;

  return [
    {
      id: "missing-field",
      label: "missing field",
      expectedQuery: baseline,
      actualQuery:
        `SELECT dc.DateKey AS MonthKey, dc.EnglishMonthName AS Month, dst.SalesTerritoryGroup AS SalesTerritoryRegion, SUM(fs.SalesAmount) AS Sales, SUM(fs.OrderQuantity) AS Quantity ${baseJoin} GROUP BY dc.DateKey, dc.EnglishMonthName, dst.SalesTerritoryGroup ORDER BY dc.DateKey, dst.SalesTerritoryGroup`,
      expectedCauses: ["missing field"],
    },
    {
      id: "wrong-aggregation",
      label: "wrong aggregation",
      expectedQuery: baseline,
      actualQuery:
        `SELECT dst.SalesTerritoryGroup, SUM(fs.SalesAmount) AS Sales, SUM(fs.OrderQuantity) AS Quantity, SUM(fsq.SalesAmountQuota) AS Quota ${baseJoin} GROUP BY dst.SalesTerritoryGroup ORDER BY dst.SalesTerritoryGroup`,
      expectedCauses: ["wrong aggregation"],
    },
    {
      id: "missing-filter",
      label: "missing filter",
      expectedQuery:
        `SELECT dc.DateKey AS MonthKey, dc.EnglishMonthName AS Month, dst.SalesTerritoryGroup AS SalesTerritoryRegion, SUM(fs.SalesAmount) AS Sales, SUM(fs.OrderQuantity) AS Quantity, SUM(fsq.SalesAmountQuota) AS Quota ${baseJoin} WHERE dc.CalendarYear = 2024 GROUP BY dc.DateKey, dc.EnglishMonthName, dst.SalesTerritoryGroup ORDER BY dc.DateKey, dst.SalesTerritoryGroup`,
      actualQuery: baseline,
      expectedCauses: ["missing filter"],
    },
    {
      id: "wrong-join",
      label: "wrong join",
      expectedQuery: baseline,
      actualQuery:
        "SELECT dc.DateKey AS MonthKey, dc.EnglishMonthName AS Month, dst.SalesTerritoryGroup AS SalesTerritoryRegion, SUM(fs.SalesAmount) AS Sales, SUM(fs.OrderQuantity) AS Quantity, SUM(fsq.SalesAmountQuota) AS Quota FROM FactResellerSales fs INNER JOIN DimSalesTerritory dst ON fs.SalesTerritoryKey = dst.SalesTerritoryKey INNER JOIN DimDate dc ON fs.SalesTerritoryKey = dc.DateKey INNER JOIN FactSalesQuota fsq ON fsq.DateKey = dc.DateKey GROUP BY dc.DateKey, dc.EnglishMonthName, dst.SalesTerritoryGroup ORDER BY dc.DateKey, dst.SalesTerritoryGroup",
      expectedCauses: ["wrong join"],
    },
    {
      id: "wrong-measure-dimension-mapping",
      label: "wrong measure/dimension mapping",
      expectedQuery: baseline,
      actualQuery:
        `SELECT dc.DateKey AS MonthKey, dc.EnglishMonthName AS Month, dst.SalesTerritoryGroup AS SalesTerritoryRegion, SUM(fs.OrderQuantity) AS Sales, SUM(fs.SalesAmount) AS Quantity, SUM(fsq.SalesAmountQuota) AS Quota ${baseJoin} GROUP BY dc.DateKey, dc.EnglishMonthName, dst.SalesTerritoryGroup ORDER BY dc.DateKey, dst.SalesTerritoryGroup`,
      expectedCauses: ["wrong measure/dimension mapping"],
    },
    {
      id: "different-output-value",
      label: "different output value",
      expectedQuery: baseline,
      actualQuery:
        `SELECT dc.DateKey AS MonthKey, dc.EnglishMonthName AS Month, dst.SalesTerritoryGroup AS SalesTerritoryRegion, SUM(fs.SalesAmount) + 1 AS Sales, SUM(fs.OrderQuantity) AS Quantity, SUM(fsq.SalesAmountQuota) AS Quota ${baseJoin} GROUP BY dc.DateKey, dc.EnglishMonthName, dst.SalesTerritoryGroup ORDER BY dc.DateKey, dst.SalesTerritoryGroup`,
      expectedCauses: ["different output value"],
    },
  ];
}

function normalized(value) {
  return String(value || "").replace(/\s+/g, "").toLowerCase();
}

function relationshipExists(model, fromTable, toTable, joinCondition = "") {
  const expectedJoin = normalized(joinCondition);
  return (model.relationships || []).some((relationship) => {
    const sameTables =
      String(relationship.from_table || "").toLowerCase() === fromTable.toLowerCase() &&
      String(relationship.to_table || "").toLowerCase() === toTable.toLowerCase();
    if (!sameTables) return false;
    if (!expectedJoin) return true;
    return normalized(relationship.join_condition) === expectedJoin;
  });
}

function relationshipCount(model, fromTable, toTable, joinCondition = "") {
  return (model.relationships || []).filter((relationship) =>
    relationshipExists({ relationships: [relationship] }, fromTable, toTable, joinCondition),
  ).length;
}

function findRelationship(model, fromTable, toTable) {
  return (model.relationships || []).find(
    (relationship) =>
      String(relationship.from_table || "").toLowerCase() === fromTable.toLowerCase() &&
      String(relationship.to_table || "").toLowerCase() === toTable.toLowerCase(),
  );
}

function tableExists(model, tableName) {
  const allTables = [
    ...(model.fact_tables || []),
    ...(model.direct_dimensions || []),
    ...(model.snowflake_dimensions || []),
  ];
  return allTables.some((table) => String(table.name || "").toLowerCase() === tableName.toLowerCase());
}

function warningCodesFromPayload(payload) {
  return (payload.warnings || []).map((warning) => warning.code || warning.type);
}

async function writeFixture(testInfo, fileName, content) {
  const fixturePath = testInfo.outputPath(fileName);
  await fs.writeFile(fixturePath, content, "utf8");
  return fixturePath;
}

async function openRdlConversion(page) {
  await page.goto("/");
  await page.getByRole("button", { name: "RDL to TWB" }).click();
  await expect(page.getByRole("heading", { name: "RDL to TWB conversion" })).toBeVisible();
}

async function runConversionUpload(page, filePath) {
  await openRdlConversion(page);
  await page.locator('input[type="file"]').setInputFiles(filePath);
  await page.getByLabel("LLM config path").fill("backend/config/llm_config.fast_local.json");
  const responsePromise = page.waitForResponse(
    (response) => response.url().includes("/api/conversion/run") && response.request().method() === "POST",
  );
  await page.getByRole("button", { name: "Convert to TWB" }).click();
  const payload = await (await responsePromise).json();
  if (!payload.status) {
    throw new Error(`Conversion response missing status: ${JSON.stringify(payload)}`);
  }
  await expect(page.getByText(payload.status, { exact: true }).first()).toBeVisible();
  return payload;
}

async function prepareChatbotPipelineState(page, request) {
  const validContent = await fs.readFile(validRdlPath, "utf8");
  const resetResponse = await request.post("/api/reset", { data: {} });
  expect(resetResponse.ok()).toBeTruthy();

  const parseResponse = await request.post("/api/rdl/parse", {
    data: { file_name: "RegionalSales.rdl", content: validContent },
  });
  expect(parseResponse.ok()).toBeTruthy();

  const loadResponse = await request.post("/api/model/relationships/apply", {
    data: {
      operation: "load",
      model: baseRegionalRelationshipModel(),
      preview_only: false,
    },
  });
  expect(loadResponse.ok()).toBeTruthy();

  await page.goto("/");
  await expect(page.getByRole("heading", { name: "5. Dimensional Model" })).toBeVisible();
  await expect(page.locator('textarea[placeholder="Describe a model correction..."]')).toBeEnabled();
}

async function sendChatInstruction(page, instruction, expectedVisibleText = "") {
  const input = page.locator('textarea[placeholder="Describe a model correction..."]');
  await expect(input).toBeEnabled();
  await input.fill(instruction);

  const responsePromise = page.waitForResponse(
    (response) => response.url().includes("/api/model/analyze") && response.request().method() === "POST",
  );
  await page.getByRole("button", { name: "Apply correction" }).click();
  const payload = await (await responsePromise).json();
  await expect(page.getByRole("heading", { name: "5. Dimensional Model" })).toBeVisible();
  if (expectedVisibleText) {
    await expect(page.getByText(expectedVisibleText, { exact: false }).first()).toBeVisible();
  }
  return payload;
}

test("TC-CONV-003 - pipeline guardrails, chatbot relationships, and data comparison", async ({ page, request }, testInfo) => {
  const report = {
    status: "FAIL",
    generatedAt: new Date().toISOString(),
    tolerance,
    rdlCases: [],
    chatbotRelationshipCases: [],
    generateBeforeValidation: {},
    finalComparison: {},
    errors: [],
  };

  let failure = null;
  let validConversionPayload = null;

  try {
    const rdlCases = [
      {
        id: "empty-rdl",
        description: "Playwright uploads an empty .rdl file.",
        filePath: await writeFixture(testInfo, "empty.rdl", ""),
        expectedStatus: "failed",
        expectedWarning: "empty_rdl",
        expectedText: "Uploaded RDL content is empty",
      },
      {
        id: "invalid-xml-rdl",
        description: "Playwright uploads malformed RDL XML.",
        filePath: await writeFixture(testInfo, "invalid-xml.rdl", "<Report><DataSets><DataSet Name=\"Broken\"></Report>"),
        expectedStatus: "failed",
        expectedWarning: "invalid_xml_rdl",
        expectedText: "RDL parsing failed",
      },
      {
        id: "missing-dataset",
        description: "Playwright uploads an RDL with no dataset.",
        filePath: await writeFixture(testInfo, "missing-dataset.rdl", noDatasetRdl),
        expectedStatus: "blocked",
        expectedWarning: "missing_dataset",
        expectedText: "Pipeline blocked",
      },
      {
        id: "valid-rdl",
        description: "Playwright uploads RegionalSales.rdl and runs the full conversion.",
        filePath: validRdlPath,
        expectedStatus: "completed",
        expectedWarning: "",
        expectedText: "Generated artifacts",
      },
    ];

    for (const scenario of rdlCases) {
      const payload = await runConversionUpload(page, scenario.filePath);
      const warningCodes = warningCodesFromPayload(payload);
      const dataModelGenerated = payload.artifacts?.data_model?.exists === true;
      const reportedStatus = scenario.expectedStatus === "failed" ? "rejected" : payload.status;
      const passed =
        payload.status === scenario.expectedStatus &&
        (!scenario.expectedWarning || warningCodes.includes(scenario.expectedWarning)) &&
        (scenario.id !== "valid-rdl" || dataModelGenerated);

      await expect(page.getByText(scenario.expectedText, { exact: false }).first()).toBeVisible();
      if (scenario.expectedWarning) {
        await expect(page.getByText(scenario.expectedWarning, { exact: false }).first()).toBeVisible();
      }
      if (scenario.id === "valid-rdl") {
        validConversionPayload = payload;
      }

      report.rdlCases.push({
        id: scenario.id,
        description: scenario.description,
        status: reportedStatus,
        warningCodes,
        dataModelGenerated,
        result: passed ? "PASS" : "FAIL",
      });
      expect(passed).toBeTruthy();
    }

    await prepareChatbotPipelineState(page, request);

    const addInstruction = `Ajoute une relation manquante entre FactResellerSales et DimCurrency.
Condition de jointure : FactResellerSales.CurrencyKey = DimCurrency.CurrencyKey.
Cardinalit\u00e9 : many-to-one.
Ne modifie pas les autres relations.
Applique uniquement cette modification et affiche un warning si la relation est invalide, dupliqu\u00e9e ou introuvable.`;
    const addPayload = await sendChatInstruction(page, addInstruction, "Relationship added");
    expect(relationshipExists(addPayload.latest_model, "FactResellerSales", "DimCurrency", "FactResellerSales.CurrencyKey = DimCurrency.CurrencyKey")).toBeTruthy();
    await expect(page.getByText("DimCurrency", { exact: false }).first()).toBeVisible();
    report.chatbotRelationshipCases.push({
      id: "add-relationship",
      prompt: addInstruction,
      result: "PASS",
      assertion: "FactResellerSales -> DimCurrency is added and visible in the model.",
    });

    const modifyInstruction = `Modifie la relation existante entre DimProduct et DimProductSubcategory.
Garde la condition de jointure actuelle :
DimProduct.ProductSubcategoryKey = DimProductSubcategory.ProductSubcategoryKey.
Change uniquement la cardinalit\u00e9 en one-to-many.
Ne modifie pas les autres relations.
Applique uniquement cette modification et affiche un warning si la relation est invalide, dupliqu\u00e9e ou introuvable.`;
    const modifyPayload = await sendChatInstruction(page, modifyInstruction, "relationship_modified");
    const modifiedRelationship = findRelationship(modifyPayload.latest_model, "DimProduct", "DimProductSubcategory");
    expect(normalized(modifiedRelationship?.join_condition)).toBe(normalized("DimProduct.ProductSubcategoryKey = DimProductSubcategory.ProductSubcategoryKey"));
    expect(modifiedRelationship?.cardinality).toBe("one-to-many");
    report.chatbotRelationshipCases.push({
      id: "modify-relationship",
      prompt: modifyInstruction,
      result: "PASS",
      assertion: "Join condition is preserved and cardinality becomes one-to-many.",
      warnings: warningCodesFromPayload({ warnings: modifyPayload.latest_model?.warnings || [] }),
    });

    const deleteInstruction = `Supprime la relation entre FactResellerSales et DimEmployee.
Condition actuelle : FactResellerSales.EmployeeKey = DimEmployee.EmployeeKey.
Supprime aussi la table DimEmployee du mod\u00e8le de donn\u00e9es.
Ne modifie pas les autres relations.
Applique uniquement cette modification et affiche un warning si la relation est invalide, dupliqu\u00e9e ou introuvable.`;
    const deletePayload = await sendChatInstruction(page, deleteInstruction, "table_deleted");
    expect(relationshipExists(deletePayload.latest_model, "FactResellerSales", "DimEmployee")).toBeFalsy();
    expect(tableExists(deletePayload.latest_model, "DimEmployee")).toBeFalsy();
    report.chatbotRelationshipCases.push({
      id: "delete-relationship-and-table",
      prompt: deleteInstruction,
      result: "PASS",
      assertion: "FactResellerSales -> DimEmployee is removed and DimEmployee disappears.",
      warnings: warningCodesFromPayload({ warnings: deletePayload.latest_model?.warnings || [] }),
    });

    const duplicateInstruction = `Ajoute la relation d\u00e9j\u00e0 existante entre FactResellerSales et DimDate.
Condition de jointure : FactResellerSales.OrderDateKey = DimDate.DateKey.
Cardinalit\u00e9 : many-to-one.
Ne modifie pas les autres relations.
Applique uniquement cette modification et affiche un warning si la relation est invalide, dupliqu\u00e9e ou introuvable.`;
    const duplicatePayload = await sendChatInstruction(page, duplicateInstruction, "duplicate_relationship");
    expect(relationshipCount(duplicatePayload.latest_model, "FactResellerSales", "DimDate", "FactResellerSales.OrderDateKey = DimDate.DateKey")).toBe(1);
    report.chatbotRelationshipCases.push({
      id: "duplicate-relationship",
      prompt: duplicateInstruction,
      result: "PASS",
      assertion: "Duplicate relationship is refused and the frontend warning is visible.",
      warnings: warningCodesFromPayload({ warnings: duplicatePayload.latest_model?.warnings || [] }),
    });

    const invalidInstruction = `Ajoute une relation entre FactResellerSales et DimDate.
Condition de jointure : FactResellerSales.FakeKey = DimDate.DateKey.
Cardinalit\u00e9 : many-to-one.
Ne modifie pas les autres relations.
Applique uniquement cette modification et affiche un warning si la relation est invalide, dupliqu\u00e9e ou introuvable.`;
    const invalidPayload = await sendChatInstruction(page, invalidInstruction, "invalid_relationship");
    expect(relationshipExists(invalidPayload.latest_model, "FactResellerSales", "DimDate", "FactResellerSales.FakeKey = DimDate.DateKey")).toBeFalsy();
    report.chatbotRelationshipCases.push({
      id: "invalid-relationship",
      prompt: invalidInstruction,
      result: "PASS",
      assertion: "Invalid relationship is refused or blocks validation with a frontend warning.",
      warnings: warningCodesFromPayload({ warnings: invalidPayload.latest_model?.warnings || [] }),
    });

    await page.getByTestId("pipeline-step-twb-generation").click();
    const generateBeforeValidationResponse = page.waitForResponse(
      (response) => response.url().includes("/api/twb/generate") && response.request().method() === "POST",
    );
    await page.getByRole("button", { name: "Generate TWB", exact: true }).click();
    const generatePayload = await (await generateBeforeValidationResponse).json();
    await expect(page.getByText("Validate the schema before generating the TWB.", { exact: false }).first()).toBeVisible();
    report.generateBeforeValidation = {
      result: "PASS",
      apiOk: generatePayload.ok === false,
      error: generatePayload.error || "",
      assertion: "Generate TWB is blocked before schema validation.",
    };

    const conversionResult = validConversionPayload?.result || {};
    const dataModel = await loadJson(conversionResult.data_model);
    const visualModel = await loadJson(conversionResult.visual_model);
    const mappingModel = await loadJson(conversionResult.mapping_model || conversionResult.mapping);
    const parsedRdl = conversionResult.parsed_rdl ? await loadJson(conversionResult.parsed_rdl) : {};
    buildExpectedDatabase();

    const fixedParameterResult = resolveFixedParameters(dataModel, parsedRdl, ExpectedDB);
    const missingParameters = unresolvedFilterParameters(dataModel, fixedParameterResult.values);
    expect(missingParameters).toEqual([]);
    const actualDatabase = buildActualDatabaseFromValidatedModel(dataModel, ExpectedDB);

    const visualCases = businessVisualCases(dataModel, visualModel, mappingModel);
    if (!visualCases.length) {
      throw new Error("No KPI/chart/business visuals with dataset fields were found for exhaustive comparison.");
    }

    const comparisons = [];
    for (const visualCase of visualCases) {
      const expectedQueryBuilder = buildActualQueryFromSemanticModel({
        dataModel,
        visualModel,
        mappingModel,
        visualName: visualCase.visualName,
        datasetName: visualCase.datasetName,
        fieldSource: "visual",
        fixedParameters: fixedParameterResult.values,
        requireFields: true,
      });
      const actualQueryBuilder = buildActualQueryFromSemanticModel({
        dataModel,
        visualModel,
        mappingModel,
        visualName: visualCase.visualName,
        datasetName: visualCase.datasetName,
        fieldSource: "mapping",
        fixedParameters: fixedParameterResult.values,
        requireFields: true,
      });

      const expectedRows = executeQuery(expectedQueryBuilder.sql, ExpectedDB);
      const actualRows = executeQuery(actualQueryBuilder.sql, ActualDB);
      const comparison = compareResultSets(expectedRows, actualRows, tolerance);
      comparisons.push({
        visualName: visualCase.visualName,
        visualType: visualCase.visualType,
        semanticHint: visualCase.semanticHint,
        datasetName: visualCase.datasetName,
        sourceFields: visualCase.sourceFields,
        mappedFields: visualCase.mappedFields,
        expectedQuery: cleanSql(expectedQueryBuilder.sql),
        actualQuery: cleanSql(actualQueryBuilder.sql),
        expectedRows: serializeRows(expectedRows),
        actualRows: serializeRows(actualRows),
        comparison,
        expectedQueryBuilder,
        actualQueryBuilder,
      });
    }

    const failedComparisons = comparisons.filter((entry) => entry.comparison.status !== "PASS");
    expect(failedComparisons).toEqual([]);

    report.finalComparison = {
      result: failedComparisons.length === 0 ? "PASS" : "FAIL",
      scope: "all mapped business visuals with dataset fields",
      fixedParameters: fixedParameterResult.values,
      fixedParameterDetails: fixedParameterResult.details,
      unresolvedFilterParameters: missingParameters,
      expectedDatabase: {
        name: "ExpectedDB",
        source: "rdl_source_fixture",
        tables: Object.entries(sourceTableSchemas).map(([tableName, columns]) => ({
          tableName,
          columns: columns.map((column) => column.name),
          rowCount: sourceRows[tableName]?.length || 0,
        })),
      },
      actualDatabase,
      visualCaseCount: visualCases.length,
      passedCount: comparisons.length - failedComparisons.length,
      failedCount: failedComparisons.length,
      comparisons,
      artifacts: {
        parsedRdl: conversionResult.parsed_rdl || "",
        dataModel: conversionResult.data_model || "",
        visualModel: conversionResult.visual_model || "",
        mappingModel: conversionResult.mapping_model || conversionResult.mapping || "",
        twb: conversionResult.twb || "",
      },
    };

    report.status = "PASS";
  } catch (error) {
    failure = error;
    report.status = "FAIL";
    report.errors.push(error instanceof Error ? error.message : String(error));
  } finally {
    await fs.mkdir(path.dirname(reportPath), { recursive: true });
    await fs.writeFile(reportPath, JSON.stringify(report, null, 2), "utf8");
  }

  if (failure) {
    throw failure;
  }
  expect(report.status).toBe("PASS");
});
