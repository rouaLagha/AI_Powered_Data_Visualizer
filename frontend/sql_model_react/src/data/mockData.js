export const STATUS = {
  pending: "pending",
  inProgress: "in_progress",
  completed: "completed",
  needsValidation: "needs_validation",
  error: "error",
};

export const pipelineDefinitions = [
  {
    id: "rdl-upload",
    title: "RDL Upload",
    description: "Upload the SSRS report definition file.",
  },
  {
    id: "rdl-parsing",
    title: "Parsing RDL",
    description: "Extract datasets, data sources, parameters, and SQL.",
  },
  {
    id: "dataset-selection",
    title: "Dataset / SQL Selection",
    description: "Select the dataset and inspect the SQL query.",
  },
  {
    id: "sql-analysis",
    title: "SQL Analysis + LLM",
    description: "Detect tables, joins, measures, and reasoning hints.",
  },
  {
    id: "dimensional-model",
    title: "Dimensional Model",
    description: "Review the proposed star or snowflake schema.",
  },
  {
    id: "human-validation",
    title: "Human Validation",
    description: "Approve model changes before generation.",
  },
  {
    id: "visual-mapping",
    title: "Visual Mapping",
    description: "Map report visuals after the data model is validated.",
  },
  {
    id: "twb-generation",
    title: "TWB Generation",
    description: "Generate Tableau workbook XML from the model.",
  },
  {
    id: "tableau-datasource",
    title: "Tableau Datasource Preparation",
    description: "Configure the published Tableau datasource.",
  },
  {
    id: "datasource-publication",
    title: "Datasource Publication",
    description: "Publish the datasource to Tableau Cloud.",
  },
  {
    id: "consumer-workbook",
    title: "Consumer Workbook Generation",
    description: "Create the workbook connected to the published datasource.",
  },
];

export const mockSql = `SELECT
    dc.MonthNumberOfYear AS MonthNumber,
    dc.EnglishMonthName AS Month,
    dst.SalesTerritoryGroup AS SalesTerritoryRegion,
    SUM(fs.SalesAmount) AS Sales,
    SUM(fs.OrderQuantity) AS Quantity
FROM FactResellerSales fs
JOIN DimSalesTerritory dst
    ON fs.SalesTerritoryKey = dst.SalesTerritoryKey
JOIN DimDate dc
    ON fs.OrderDateKey = dc.DateKey
JOIN DimProduct dp
    ON fs.ProductKey = dp.ProductKey
GROUP BY
    dc.MonthNumberOfYear,
    dc.EnglishMonthName,
    dst.SalesTerritoryGroup;`;

export const mockDatasets = [
  {
    id: "reseller_sales_summary",
    name: "ResellerSalesSummary",
    queryType: "Text SQL",
    dataSource: "AdventureWorksDW2022",
    status: "Ready",
    sql: mockSql,
  },
  {
    id: "sales_quota",
    name: "SalesQuotaByEmployee",
    queryType: "Text SQL",
    dataSource: "AdventureWorksDW2022",
    status: "Review",
    sql: "SELECT EmployeeKey, CalendarYear, SUM(SalesAmountQuota) AS SalesQuota FROM FactSalesQuota GROUP BY EmployeeKey, CalendarYear;",
  },
  {
    id: "territory_lookup",
    name: "TerritoryLookup",
    queryType: "Lookup SQL",
    dataSource: "AdventureWorksDW2022",
    status: "Ready",
    sql: "SELECT SalesTerritoryKey, SalesTerritoryRegion, SalesTerritoryGroup FROM DimSalesTerritory;",
  },
];

export const mockParsingSummary = {
  datasetsFound: 3,
  dataSourcesFound: 1,
  parametersFound: 4,
  sqlQueriesExtracted: 3,
};

export const mockAnalysisResult = {
  timeline: [
    { label: "SQL parsed", status: STATUS.completed },
    { label: "Tables detected", status: STATUS.completed },
    { label: "Joins extracted", status: STATUS.completed },
    { label: "Measures detected", status: STATUS.completed },
    { label: "LLM reasoning completed", status: STATUS.completed },
  ],
  detectedTables: ["FactResellerSales", "DimDate", "DimProduct", "DimSalesTerritory"],
  detectedJoins: [
    "FactResellerSales.SalesTerritoryKey = DimSalesTerritory.SalesTerritoryKey",
    "FactResellerSales.OrderDateKey = DimDate.DateKey",
    "FactResellerSales.ProductKey = DimProduct.ProductKey",
  ],
  detectedMeasures: ["SUM(SalesAmount)", "SUM(OrderQuantity)"],
};

export const mockModel = {
  schemaType: "Snowflake",
  tables: [
    {
      id: "fact_reseller_sales",
      name: "FactResellerSales",
      type: "fact",
      columns: ["ProductKey", "OrderDateKey", "SalesTerritoryKey", "ResellerKey", "SalesAmount", "OrderQuantity"],
      measures: ["SalesAmount", "OrderQuantity"],
    },
    {
      id: "dim_date",
      name: "DimDate",
      type: "dimension",
      columns: ["DateKey", "EnglishMonthName", "MonthNumberOfYear"],
    },
    {
      id: "dim_product",
      name: "DimProduct",
      type: "dimension",
      columns: ["ProductKey", "ProductSubcategoryKey", "EnglishProductName"],
    },
    {
      id: "dim_product_subcategory",
      name: "DimProductSubcategory",
      type: "dimension",
      columns: ["ProductSubcategoryKey", "ProductCategoryKey", "EnglishProductSubcategoryName"],
    },
    {
      id: "dim_product_category",
      name: "DimProductCategory",
      type: "dimension",
      columns: ["ProductCategoryKey", "EnglishProductCategoryName"],
    },
    {
      id: "dim_sales_territory",
      name: "DimSalesTerritory",
      type: "dimension",
      columns: ["SalesTerritoryKey", "SalesTerritoryGroup"],
    },
    {
      id: "dim_reseller",
      name: "DimReseller",
      type: "dimension",
      columns: ["ResellerKey", "ResellerName", "GeographyKey"],
    },
  ],
  relationships: [
    {
      id: "rel_sales_date",
      fromTable: "FactResellerSales",
      fromColumn: "OrderDateKey",
      toTable: "DimDate",
      toColumn: "DateKey",
      cardinality: "many-to-one",
    },
    {
      id: "rel_sales_product",
      fromTable: "FactResellerSales",
      fromColumn: "ProductKey",
      toTable: "DimProduct",
      toColumn: "ProductKey",
      cardinality: "many-to-one",
    },
    {
      id: "rel_product_subcategory",
      fromTable: "DimProduct",
      fromColumn: "ProductSubcategoryKey",
      toTable: "DimProductSubcategory",
      toColumn: "ProductSubcategoryKey",
      cardinality: "many-to-one",
    },
    {
      id: "rel_subcategory_category",
      fromTable: "DimProductSubcategory",
      fromColumn: "ProductCategoryKey",
      toTable: "DimProductCategory",
      toColumn: "ProductCategoryKey",
      cardinality: "many-to-one",
    },
    {
      id: "rel_sales_territory",
      fromTable: "FactResellerSales",
      fromColumn: "SalesTerritoryKey",
      toTable: "DimSalesTerritory",
      toColumn: "SalesTerritoryKey",
      cardinality: "many-to-one",
    },
  ],
  measures: [
    {
      name: "Sales",
      expression: "SUM(FactResellerSales.SalesAmount)",
      sourceTable: "FactResellerSales",
      aggregation: "SUM",
      status: "valid",
    },
    {
      name: "Quantity",
      expression: "SUM(FactResellerSales.OrderQuantity)",
      sourceTable: "FactResellerSales",
      aggregation: "SUM",
      status: "valid",
    },
  ],

};
