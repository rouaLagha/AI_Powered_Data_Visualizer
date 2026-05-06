import { expect, test } from "@playwright/test";
import path from "node:path";
import { fileURLToPath } from "node:url";

const __dirname = path.dirname(fileURLToPath(import.meta.url));

const selectedSql = `
SELECT
  fis.SalesOrderNumber,
  fis.SalesAmount,
  fis.OrderQuantity,
  dp.EnglishProductName,
  dd.CalendarYear,
  dc.CustomerKey
FROM dbo.FactInternetSales fis
INNER JOIN dbo.DimProduct dp ON fis.ProductKey = dp.ProductKey
INNER JOIN dbo.DimDate dd ON fis.OrderDateKey = dd.DateKey
INNER JOIN dbo.DimCustomer dc ON fis.CustomerKey = dc.CustomerKey
`;

const parsedReportSummary = {
  data_sets: [
    {
      name: "SalesDataset",
      data_source_name: "AdventureWorksDW",
      has_query: true,
      field_count: 6,
    },
  ],
  data_sources: [
    {
      name: "AdventureWorksDW",
      provider: "SQL",
      server: "localhost",
      database: "AdventureWorksDW2022",
    },
  ],
  parameter_count: 1,
  parameters: [{ name: "CalendarYear", type: "Integer" }],
};

const baseModel = {
  model_type: "Star Schema",
  schema_confidence: "high",
  model_summary: "Internet sales model generated from the selected RDL dataset.",
  fact_tables: [
    {
      name: "FactInternetSales",
      grain: "One row per sales order line",
      foreign_keys: ["ProductKey", "OrderDateKey", "CustomerKey"],
      measures: ["SalesAmount", "OrderQuantity"],
    },
  ],
  direct_dimensions: [
    {
      name: "DimProduct",
      natural_key: "ProductKey",
      attributes: ["EnglishProductName", "Color"],
    },
    {
      name: "DimDate",
      natural_key: "DateKey",
      attributes: ["CalendarYear", "MonthNumberOfYear"],
    },
    {
      name: "DimCustomer",
      natural_key: "CustomerKey",
      attributes: ["CustomerKey", "Gender"],
    },
  ],
  snowflake_dimensions: [],
  relationships: [
    {
      from_table: "FactInternetSales",
      to_table: "DimProduct",
      join_condition: "FactInternetSales.ProductKey = DimProduct.ProductKey",
      cardinality: "many-to-one",
    },
    {
      from_table: "FactInternetSales",
      to_table: "DimDate",
      join_condition: "FactInternetSales.OrderDateKey = DimDate.DateKey",
      cardinality: "many-to-one",
    },
  ],
};

const correctedModel = {
  ...baseModel,
  relationships: [
    ...baseModel.relationships,
    {
      from_table: "FactInternetSales",
      to_table: "DimCustomer",
      join_condition: "FactInternetSales.CustomerKey = DimCustomer.CustomerKey",
      cardinality: "many-to-one",
    },
  ],
};

function defaultState(overrides = {}) {
  return {
    ok: true,
    report_name: "",
    report_summary: {
      data_sets: [],
      data_sources: [],
      parameter_count: 0,
      parameters: [],
    },
    selected_dataset_name: "",
    selected_datasource_name: "",
    sql_query: "",
    conversation: [],
    latest_model: {},
    latest_model_summary: {},
    database_context: {
      datasource_name: "",
      dataset_name: "",
      provider: "",
      server: "",
      database: "",
      connected: false,
      inventory_source: "rdl_context",
      total_tables: 0,
      available_tables: [],
      error: "",
    },
    schema_validated: false,
    artifact_workspace: {
      root: {},
      manifest: {},
      folders: {},
    },
    generated_twb: {
      exists: false,
      name: "",
      path: "",
      download_url: "",
      size_bytes: 0,
    },
    visual_conversion: {
      exists: false,
      name: "",
      path: "",
      download_url: "",
      size_bytes: 0,
      status: "not_started",
      output_dir: "",
      error: "",
    },
    visual_model_twb: {
      exists: false,
      name: "",
      path: "",
      download_url: "",
      size_bytes: 0,
      status: "not_started",
      error: "",
    },
    generated_twb_name: "data_model_to_publish.twb",
    publish_context_summary: {},
    publish_report: {},
    publish_error: "",
    consumer_workbook: {
      exists: false,
      name: "",
      path: "",
      download_url: "",
      size_bytes: 0,
    },
    quality_comparison: {},
    defaults: {
      config_path: "backend/config/llm_config.example.json",
      template_path: "backend/config/RegionalSales.canonical.twb",
      output_name: "data_model_to_publish.twb",
      tableau: {
        server_url: "https://tableau.example.test",
        site_content_url: "qa",
        project_name: "BI QA",
        source_datasource_name: "Regional Sales Datasource",
        datasource_publish_mode: "live_tds",
        auth_method: "username_password",
        username: "qa@example.test",
        credentials_ready: true,
        pat_name: "",
        pat_secret_ready: false,
      },
    },
    ...overrides,
  };
}

async function installMockApi(page) {
  let state = defaultState();
  const calls = {
    parse: 0,
    selectDataset: 0,
    analyze: 0,
    correction: 0,
    validate: 0,
    visual: 0,
    twb: 0,
    publish: 0,
    quality: 0,
  };

  function setState(patch) {
    state = { ...state, ...patch };
    return state;
  }

  async function fulfill(route, payload) {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify(payload),
    });
  }

  await page.route("**/schema-flow/**", async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "text/html",
      body: "<!doctype html><html><body>Schema flow test harness</body></html>",
    });
  });

  await page.route("**/api/**", async (route) => {
    const request = route.request();
    const url = new URL(request.url());
    const pathname = url.pathname;

    if (!pathname.startsWith("/api/")) {
      await route.fallback();
      return;
    }

    if (request.method() === "GET" && pathname === "/api/state") {
      await fulfill(route, state);
      return;
    }

    const body = request.method() === "POST" ? request.postDataJSON() : {};

    if (request.method() === "POST" && pathname === "/api/reset") {
      state = defaultState();
      await fulfill(route, state);
      return;
    }

    if (request.method() === "POST" && pathname === "/api/rdl/parse") {
      calls.parse += 1;
      expect(body.file_name).toBe("regional-sales-quality.rdl");
      expect(body.content).toContain("FactInternetSales");
      expect(body.content).toContain("DimProduct");
      await fulfill(
        route,
        setState({
          report_name: body.file_name,
          report_summary: parsedReportSummary,
          selected_dataset_name: "SalesDataset",
          selected_datasource_name: "AdventureWorksDW",
          sql_query: selectedSql,
          database_context: {
            datasource_name: "AdventureWorksDW",
            dataset_name: "SalesDataset",
            provider: "SQL",
            server: "localhost",
            database: "AdventureWorksDW2022",
            connected: true,
            inventory_source: "mock_catalog",
            total_tables: 4,
            available_tables: [],
            error: "",
          },
        }),
      );
      return;
    }

    if (request.method() === "POST" && pathname === "/api/dataset/select") {
      calls.selectDataset += 1;
      expect(body.dataset_name).toBe("SalesDataset");
      await fulfill(
        route,
        setState({
          selected_dataset_name: "SalesDataset",
          selected_datasource_name: "AdventureWorksDW",
          sql_query: selectedSql,
        }),
      );
      return;
    }

    if (request.method() === "POST" && pathname === "/api/model/analyze") {
      expect(body.sql_query).toContain("FactInternetSales");
      expect(body.sql_query).toContain("DimProduct");

      if (body.follow_up) {
        calls.correction += 1;
        expect(body.follow_up).toContain("customer relationship");
        await fulfill(
          route,
          setState({
            latest_model: correctedModel,
            conversation: [
              { role: "user", content: body.follow_up },
              {
                role: "assistant",
                content: "Applied correction: added the missing DimCustomer relationship.",
              },
            ],
          }),
        );
        return;
      }

      calls.analyze += 1;
      await fulfill(
        route,
        setState({
          latest_model: baseModel,
          conversation: [
            {
              role: "assistant",
              content: "Detected a star schema with sales fact and conformed dimensions.",
            },
          ],
        }),
      );
      return;
    }

    if (request.method() === "POST" && pathname === "/api/schema/validate") {
      calls.validate += 1;
      await fulfill(
        route,
        setState({
          latest_model: correctedModel,
          schema_validated: true,
        }),
      );
      return;
    }

    if (request.method() === "POST" && pathname === "/api/visual/map") {
      calls.visual += 1;
      await fulfill(
        route,
        setState({
          visual_conversion: {
            exists: true,
            name: "visual_content_mapped.twb",
            path: "C:/tmp/outputs/rdl_to_twb/20260506T120000Z_RegionalSales/03_conversion_and_visual_mapping/visual_content_mapped.twb",
            download_url: "/api/file?path=C%3A%2Ftmp%2Foutputs%2Frdl_to_twb%2F20260506T120000Z_RegionalSales%2F03_conversion_and_visual_mapping%2Fvisual_content_mapped.twb",
            size_bytes: 58291,
            status: "completed",
            output_dir: "C:/tmp/outputs/rdl_to_twb/20260506T120000Z_RegionalSales/03_conversion_and_visual_mapping",
            error: "",
          },
        }),
      );
      return;
    }

    if (request.method() === "POST" && pathname === "/api/twb/generate") {
      calls.twb += 1;
      expect(body.output_name).toBe("data_model_to_publish.twb");
      await fulfill(
        route,
        setState({
          artifact_workspace: {
            root: {
              exists: true,
              name: "20260506T120000Z_RegionalSales",
              path: "C:/tmp/outputs/rdl_to_twb/20260506T120000Z_RegionalSales",
            },
            manifest: {
              exists: true,
              name: "artifact_manifest.json",
              path: "C:/tmp/outputs/rdl_to_twb/20260506T120000Z_RegionalSales/artifact_manifest.json",
              download_url: "/api/file?path=C%3A%2Ftmp%2Foutputs%2Frdl_to_twb%2F20260506T120000Z_RegionalSales%2Fartifact_manifest.json",
            },
          },
          generated_twb: {
            exists: true,
            name: "data_model_to_publish.twb",
            path: "C:/tmp/outputs/rdl_to_twb/20260506T120000Z_RegionalSales/04_data_model_to_publish/data_model_to_publish.twb",
            download_url: "/api/file?path=C%3A%2Ftmp%2Foutputs%2Frdl_to_twb%2F20260506T120000Z_RegionalSales%2F04_data_model_to_publish%2Fdata_model_to_publish.twb",
            size_bytes: 48291,
          },
          visual_model_twb: {
            exists: true,
            name: "data_model_with_mapped_visuals.twb",
            path: "C:/tmp/outputs/rdl_to_twb/20260506T120000Z_RegionalSales/05_final_workbook_with_visuals/data_model_with_mapped_visuals.twb",
            download_url: "/api/file?path=C%3A%2Ftmp%2Foutputs%2Frdl_to_twb%2F20260506T120000Z_RegionalSales%2F05_final_workbook_with_visuals%2Fdata_model_with_mapped_visuals.twb",
            size_bytes: 68291,
            status: "completed",
            error: "",
          },
          publish_context_summary: {
            data_source: {
              name: "AdventureWorksDW",
              connection_info: {
                server: "localhost",
                database: "AdventureWorksDW2022",
              },
            },
            dataset: {
              name: "SalesDataset",
              data_source_name: "AdventureWorksDW",
            },
            db_catalog: {
              total_tables: 4,
            },
          },
        }),
      );
      return;
    }

    if (request.method() === "POST" && pathname === "/api/tableau/publish") {
      calls.publish += 1;
      expect(body.tableau.source_datasource_name).toContain("Regional Sales");
      await fulfill(
        route,
        setState({
          publish_report: {
            status: "published",
            message: "Datasource and linked consumer workbook published for QA.",
            datasource_url: "https://tableau.example.test/#/datasources/regional-sales",
            datasource_id: "ds-regional-sales-001",
          },
          publish_error: "",
          consumer_workbook: {
            exists: true,
            name: "regional_sales_consumer.twb",
            path: "C:/tmp/regional_sales_consumer.twb",
            download_url: "/api/consumer-workbook/download",
            size_bytes: 52981,
          },
        }),
      );
      return;
    }

    if (request.method() === "POST" && pathname === "/api/quality/compare") {
      calls.quality += 1;
      await fulfill(
        route,
        setState({
          quality_comparison: {
            executed: true,
            status: "completed",
            global_score: 92,
            summary: "Quality comparison completed between the source RDL and generated Tableau workbook.",
            metrics: [
              {
                label: "Dataset extraction",
                score: 100,
                detail: "SalesDataset was parsed from the uploaded RDL.",
              },
              {
                label: "SQL table coverage",
                score: 95,
                detail: "FactInternetSales, DimProduct, DimDate, and DimCustomer are represented.",
              },
              {
                label: "Measures coverage",
                score: 90,
                detail: "SalesAmount and OrderQuantity are available as Tableau measures.",
              },
              {
                label: "Relationship coverage",
                score: 90,
                detail: "All three expected fact-to-dimension relationships are present.",
              },
              {
                label: "Visual mapping fidelity",
                score: 88,
                detail: "The generated workbook preserves the RDL table analysis intent.",
              },
              {
                label: "Tableau artifact readiness",
                score: 92,
                detail: "TWB and consumer workbook links are available.",
              },
            ],
          },
        }),
      );
      return;
    }

    await route.fulfill({
      status: 404,
      contentType: "application/json",
      body: JSON.stringify({ ok: false, error: `Unhandled API mock: ${request.method()} ${pathname}` }),
    });
  });

  return calls;
}

test("TC-CONV-001 - Complete RDL to Tableau conversion with quality comparison", async ({ page }) => {
  const calls = await installMockApi(page);
  const fixturePath = path.join(__dirname, "fixtures", "regional-sales-quality.rdl");

  await page.goto("/");

  await test.step("upload success and RDL parsing success", async () => {
    await page.locator('input[type="file"]').first().setInputFiles(fixturePath);
    await expect(page.locator(".file-summary").getByText("regional-sales-quality.rdl")).toBeVisible();
    await expect(page.locator(".file-summary").getByText("Ready")).toBeVisible();

    await page.getByRole("button", { name: "Parse RDL", exact: true }).click();
    await expect(page.getByRole("heading", { name: "2. RDL parsing" })).toBeVisible();
    await expect(page.locator(".metric-card").filter({ hasText: "Datasets" })).toContainText("1");
    await expect(page.locator(".metric-card").filter({ hasText: "SQL queries" })).toContainText("1");
  });

  await test.step("dataset extracted and SQL preview contains expected tables", async () => {
    await expect(page.getByRole("cell", { name: "SalesDataset" })).toBeVisible();
    await expect(page.getByRole("cell", { name: "AdventureWorksDW" })).toBeVisible();

    await page.getByRole("button", { name: "Choose dataset" }).click();
    await expect(page.getByRole("heading", { name: "3. Dataset and SQL" })).toBeVisible();
    await page.locator(".dataset-item").filter({ hasText: "SalesDataset" }).click();
    await expect(page.getByText("SQL preview")).toBeVisible();
    await expect(page.locator(".code-preview")).toContainText("FactInternetSales");
    await expect(page.locator(".code-preview")).toContainText("DimProduct");
    await expect(page.locator(".code-preview")).toContainText("DimDate");
    await expect(page.locator(".code-preview")).toContainText("DimCustomer");
  });

  await test.step("dimensional model generated with facts, dimensions, measures, and relationships", async () => {
    await page.getByRole("button", { name: "Analyze model", exact: true }).click();
    await expect(page.getByRole("heading", { name: "5. Dimensional Model" })).toBeVisible();

    const summary = page.locator(".schema-summary-panel");
    await expect(summary).toContainText("Star Schema");
    await expect(summary.locator(".summary-row").filter({ hasText: "Fact tables" })).toContainText("1");
    await expect(summary.locator(".summary-row").filter({ hasText: "Dimensions" })).toContainText("3");
    await expect(summary.locator(".summary-row").filter({ hasText: "Relationships" })).toContainText("2");
    await expect(summary.locator(".summary-row").filter({ hasText: "Measures" })).toContainText("2");
  });

  await test.step("human correction can be applied", async () => {
    await page
      .getByPlaceholder("Describe a model correction...")
      .fill("Add the missing customer relationship to the dimensional model.");
    await page.getByRole("button", { name: "Apply correction" }).click();

    await expect(page.getByText("Applied correction: added the missing DimCustomer relationship.")).toBeVisible();
    await expect(page.locator(".schema-summary-panel").locator(".summary-row").filter({ hasText: "Relationships" })).toContainText("3");
  });

  await test.step("model validation works", async () => {
    await page.getByTestId("pipeline-step-human-validation").click();
    await expect(page.getByRole("heading", { name: "6. Validation" })).toBeVisible();
    await page.getByRole("button", { name: "Relationships", exact: true }).click();
    await expect(page.getByRole("row").filter({ hasText: "DimCustomer" })).toBeVisible();
    await page.getByRole("button", { name: "Measures", exact: true }).click();
    await expect(page.getByRole("row").filter({ hasText: "SalesAmount" })).toBeVisible();

    await page.getByRole("button", { name: "Approve model", exact: true }).click();
    await expect(page.getByRole("heading", { name: "7. Visual mapping" })).toBeVisible();
  });

  await test.step("visual content mapping works after schema validation", async () => {
    await page.getByRole("button", { name: "Map visual content", exact: true }).click();
    await expect(page.getByRole("heading", { name: "8. TWB generation" })).toBeVisible();
    await page.getByTestId("pipeline-step-visual-mapping").click();
    await expect(page.getByText("Mapped", { exact: true })).toBeVisible();
    await expect(page.locator(".file-summary strong").filter({ hasText: "visual_content_mapped.twb" })).toBeVisible();
    await page.getByTestId("pipeline-step-twb-generation").click();
  });

  await test.step("TWB generation works and generated file link appears", async () => {
    await page.getByRole("button", { name: "Generate TWB", exact: true }).click();
    await expect(page.getByRole("heading", { name: "9. Tableau datasource" })).toBeVisible();
    await page.getByTestId("pipeline-step-twb-generation").click();
    await expect(page.getByText("Generated", { exact: true })).toBeVisible();
    await expect(page.locator(".file-summary").filter({ hasText: "Data model TWB to publish" })).toContainText("data_model_to_publish.twb");
    await expect(page.locator(".file-summary").filter({ hasText: "Data model + mapped visuals TWB" })).toContainText("data_model_with_mapped_visuals.twb");
    await expect(page.locator(".file-summary").filter({ hasText: "Output workspace" })).toContainText("outputs/rdl_to_twb");
    await expect(page.getByRole("link", { name: "Download data model TWB" })).toHaveAttribute("href", /\/api\/file/);
    await expect(page.getByRole("link", { name: "Download data model + visuals TWB" })).toHaveAttribute("href", /\/api\/file/);
    await page.getByTestId("pipeline-step-tableau-datasource").click();
  });

  await test.step("Tableau datasource is prepared and published, then consumer workbook is generated", async () => {
    await expect(page.getByRole("heading", { name: "9. Tableau datasource" })).toBeVisible();
    await expect(page.getByLabel("Datasource name")).toHaveValue("Regional Sales Datasource");
    await page.getByRole("button", { name: "Prepare publish" }).click();

    await expect(page.getByRole("heading", { name: "10. Publish" })).toBeVisible();
    await page.getByRole("button", { name: "Publish datasource" }).click();
    await expect(page.getByRole("heading", { name: "11. Final workbook" })).toBeVisible();
    await expect(page.getByText("Workbook ready")).toBeVisible();
    await expect(page.getByText("regional_sales_consumer.twb")).toBeVisible();
    await expect(page.getByRole("link", { name: "Download workbook" })).toHaveAttribute(
      "href",
      "/api/consumer-workbook/download",
    );
  });

  await test.step("quality comparison is executed with global score >= 85% and detailed metrics", async () => {
    await page.getByRole("button", { name: "Compare quality" }).click();
    await expect(page.getByRole("heading", { name: "12. Quality comparison" })).toBeVisible();

    await page.getByRole("button", { name: "Run quality comparison" }).click();
    await expect(page.getByText("Quality comparison completed between the source RDL and generated Tableau workbook.")).toBeVisible();

    const scoreText = await page.getByTestId("global-quality-score").textContent();
    const globalScore = Number.parseInt(scoreText || "0", 10);
    expect(globalScore).toBeGreaterThanOrEqual(85);

    await expect(page.getByTestId("quality-metrics")).toBeVisible();
    await expect(page.getByTestId("quality-metric-dataset_extraction")).toContainText("100%");
    await expect(page.getByTestId("quality-metric-sql_table_coverage")).toContainText("95%");
    await expect(page.getByTestId("quality-metric-measures_coverage")).toContainText("90%");
    await expect(page.getByTestId("quality-metric-relationship_coverage")).toContainText("90%");
    await expect(page.getByTestId("quality-metric-visual_mapping_fidelity")).toContainText("88%");
    await expect(page.getByTestId("quality-metric-tableau_artifact_readiness")).toContainText("92%");
  });

  expect(calls).toEqual({
    parse: 1,
    selectDataset: 1,
    analyze: 1,
    correction: 1,
    validate: 1,
    visual: 1,
    twb: 1,
    publish: 1,
    quality: 1,
  });
});
