function cleanSql(sql) {
  return String(sql || "")
    .replace(/\r\n/g, "\n")
    .replace(/\[([^\]]+)\]/g, "$1")
    .replace(/\bdbo\./gi, "")
    .replace(/\s+/g, " ")
    .replace(/;+\s*$/, "")
    .trim();
}

function splitTopLevel(value, separator = ",") {
  const parts = [];
  let current = "";
  let depth = 0;
  let quote = "";

  for (const char of String(value || "")) {
    if (quote) {
      current += char;
      if (char === quote) quote = "";
      continue;
    }
    if (char === "'" || char === '"') {
      quote = char;
      current += char;
      continue;
    }
    if (char === "(") depth += 1;
    if (char === ")" && depth > 0) depth -= 1;
    if (char === separator && depth === 0) {
      parts.push(current.trim());
      current = "";
      continue;
    }
    current += char;
  }

  if (current.trim()) parts.push(current.trim());
  return parts;
}

function findKeyword(sql, keyword, startIndex = 0) {
  const pattern = new RegExp(`\\b${keyword.replace(/\s+/g, "\\s+")}\\b`, "gi");
  pattern.lastIndex = startIndex;
  const match = pattern.exec(sql);
  return match ? match.index : -1;
}

function nextClauseIndex(sql, startIndex, keywords) {
  const indexes = keywords
    .map((keyword) => findKeyword(sql, keyword, startIndex))
    .filter((index) => index >= 0);
  return indexes.length ? Math.min(...indexes) : sql.length;
}

function clause(sql, keyword, endKeywords) {
  const start = findKeyword(sql, keyword);
  if (start < 0) return "";
  const contentStart = start + keyword.length;
  const end = nextClauseIndex(sql, contentStart, endKeywords);
  return sql.slice(contentStart, end).trim();
}

function parseSelectItem(rawItem) {
  const item = String(rawItem || "").trim();
  const aliasMatch = item.match(/\s+AS\s+([A-Za-z_][A-Za-z0-9_]*)$/i);
  const alias = aliasMatch ? aliasMatch[1] : "";
  const expression = aliasMatch ? item.slice(0, aliasMatch.index).trim() : item;
  return {
    expression,
    alias: alias || expression.split(".").pop(),
    aggregate: /\b(SUM|COUNT|AVG|MIN|MAX)\s*\(/i.test(expression),
    refs: refsFromExpression(expression),
  };
}

function sqlLiteral(value) {
  if (value === null || value === undefined) return "NULL";
  if (typeof value === "number" && Number.isFinite(value)) return String(value);
  if (typeof value === "boolean") return value ? "TRUE" : "FALSE";
  return `'${String(value).replace(/'/g, "''")}'`;
}

function refsFromExpression(expression) {
  const refs = [];
  const pattern = /\b([A-Za-z_][A-Za-z0-9_]*)\.([A-Za-z_][A-Za-z0-9_]*)\b/g;
  let match = pattern.exec(String(expression || ""));
  while (match) {
    refs.push({ alias: match[1], column: match[2] });
    match = pattern.exec(String(expression || ""));
  }
  return refs;
}

function parseFromAndJoins(fromClause) {
  const text = String(fromClause || "").trim();
  const baseMatch = text.match(/^([A-Za-z_][A-Za-z0-9_.]*)(?:\s+([A-Za-z_][A-Za-z0-9_]*))?/);
  if (!baseMatch) {
    throw new Error("Query Builder could not parse the base table from the data model query.");
  }

  const baseTable = {
    table: baseMatch[1],
    alias: baseMatch[2] || baseMatch[1],
  };
  const joins = [];
  const joinPattern =
    /\b((?:INNER|LEFT|RIGHT|FULL|CROSS)\s+)?JOIN\s+([A-Za-z_][A-Za-z0-9_.]*)(?:\s+([A-Za-z_][A-Za-z0-9_]*))?\s+ON\s+([\s\S]*?)(?=\b(?:(?:INNER|LEFT|RIGHT|FULL|CROSS)\s+)?JOIN\b|$)/gi;
  let match = joinPattern.exec(text);
  while (match) {
    joins.push({
      type: `${(match[1] || "").trim()} JOIN`.trim(),
      table: match[2],
      alias: match[3] || match[2],
      condition: match[4].trim(),
      refs: refsFromExpression(match[4]),
    });
    match = joinPattern.exec(text);
  }

  return { baseTable, joins };
}

function parseDatasetQuery(sql) {
  const normalized = cleanSql(sql);
  const selectPart = clause(normalized, "SELECT", ["FROM"]);
  const fromPart = clause(normalized, "FROM", ["WHERE", "GROUP BY", "ORDER BY"]);
  const wherePart = clause(normalized, "WHERE", ["GROUP BY", "ORDER BY"]);
  const groupByPart = clause(normalized, "GROUP BY", ["ORDER BY"]);
  const orderByPart = clause(normalized, "ORDER BY", []);
  const distinct = /^DISTINCT\b/i.test(selectPart);
  const selectBody = selectPart.replace(/^DISTINCT\b/i, "").trim();

  return {
    distinct,
    selectItems: splitTopLevel(selectBody).map(parseSelectItem),
    whereClauses: wherePart ? [wherePart] : [],
    groupBy: splitTopLevel(groupByPart),
    orderBy: splitTopLevel(orderByPart),
    ...parseFromAndJoins(fromPart),
  };
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

function datasetForVisual(dataModel, mappingModel, visualModel, visualName) {
  const datasets = Array.isArray(dataModel?.datasets) ? dataModel.datasets : [];
  const visualToDataset = Array.isArray(mappingModel?.visual_to_dataset) ? mappingModel.visual_to_dataset : [];
  const visuals = flattenVisuals(visualModel?.visuals);
  const visual = visuals.find((entry) => entry.name === visualName) || visuals.find((entry) => entry.dataset_name);
  const name =
    visualToDataset.find((entry) => entry.visual_name === (visualName || visual?.name) && entry.dataset_name)?.dataset_name ||
    visual?.dataset_name ||
    visualToDataset.find((entry) => entry.dataset_name)?.dataset_name ||
    "";
  return datasets.find((dataset) => dataset.name === name) || datasets[0] || null;
}

function fieldsFromVisualProperties(visual) {
  if (!visual || typeof visual !== "object") return [];
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
  return [...propertyFields, ...layoutFields, ...expressionFields];
}

function fieldsForVisual(mappingModel, visualModel, visualName, fieldSource = "combined") {
  const visualToFields = Array.isArray(mappingModel?.visual_to_fields) ? mappingModel.visual_to_fields : [];
  const visuals = flattenVisuals(visualModel?.visuals);
  const targetVisual = visuals.find((entry) => entry.name === visualName) || visuals.find((entry) => entry.dataset_name) || {};
  const mapped = visualToFields.find((entry) => entry.visual_name === (visualName || targetVisual.name));
  const mappedFields = Array.isArray(mapped?.fields) ? mapped.fields : [];
  const propertyFields = fieldsFromVisualProperties(targetVisual);
  const sourceFields =
    fieldSource === "mapping"
      ? mappedFields
      : fieldSource === "visual"
        ? propertyFields
        : [...mappedFields, ...propertyFields];
  return new Set(sourceFields.map((field) => String(field || "").trim()).filter(Boolean));
}

function aliasForExpression(selectItems, expression) {
  const normalizedExpression = cleanSql(expression);
  const item = selectItems.find((selectItem) => cleanSql(selectItem.expression) === normalizedExpression);
  return item?.alias || "";
}

function selectedItemsForVisual(parsedQuery, requestedFields) {
  const requiredAliases = new Set([...requestedFields]);
  for (const groupExpression of parsedQuery.groupBy) {
    const alias = aliasForExpression(parsedQuery.selectItems, groupExpression);
    if (alias) requiredAliases.add(alias);
  }
  for (const orderExpression of parsedQuery.orderBy) {
    const alias = aliasForExpression(parsedQuery.selectItems, orderExpression);
    if (alias) requiredAliases.add(alias);
  }

  const selected = parsedQuery.selectItems.filter((item) => requiredAliases.has(item.alias));
  return selected.length ? selected : parsedQuery.selectItems;
}

function selectItemsByAliases(parsedQuery, aliases) {
  const expectedAliases = new Set([...aliases].map((alias) => String(alias || "").trim()).filter(Boolean));
  return parsedQuery.selectItems.filter((item) => expectedAliases.has(item.alias));
}

function uniqueSelectItems(items) {
  const seen = new Set();
  const output = [];
  for (const item of items) {
    const key = item.alias || cleanSql(item.expression);
    if (seen.has(key)) continue;
    seen.add(key);
    output.push(item);
  }
  return output;
}

function aliasesFromItems(items) {
  return new Set(items.flatMap((item) => item.refs.map((ref) => ref.alias)));
}

function renderTable(table) {
  return table.alias && table.alias !== table.table ? `${table.table} ${table.alias}` : table.table;
}

function renderSelectItem(item) {
  return item.alias ? `${item.expression} AS ${item.alias}` : item.expression;
}

function filterGroupBy(parsedQuery, selectedItems) {
  const selectedAliases = new Set(selectedItems.map((item) => item.alias));
  return parsedQuery.groupBy.filter((expression) => {
    const alias = aliasForExpression(parsedQuery.selectItems, expression);
    return alias ? selectedAliases.has(alias) : true;
  });
}

function filterOrderBy(parsedQuery, selectedItems) {
  const selectedAliases = new Set(selectedItems.map((item) => item.alias));
  return parsedQuery.orderBy.filter((expression) => {
    const alias = aliasForExpression(parsedQuery.selectItems, expression);
    return alias ? selectedAliases.has(alias) : true;
  });
}

function buildJoinList(parsedQuery, selectedItems, groupBy, orderBy) {
  const requiredAliases = aliasesFromItems(selectedItems);
  refsFromExpression([...groupBy, ...orderBy, ...parsedQuery.whereClauses].join(" ")).forEach((ref) => {
    requiredAliases.add(ref.alias);
  });
  requiredAliases.add(parsedQuery.baseTable.alias);

  return parsedQuery.joins.filter((join) => requiredAliases.has(join.alias));
}

function relationshipFromJoin(join) {
  const aliases = join.refs.map((ref) => ref.alias);
  const otherAlias = aliases.find((alias) => alias !== join.alias) || "";
  return {
    from: otherAlias || aliases[0] || "",
    to: join.alias,
    joinType: join.type,
    condition: join.condition,
  };
}

function literalValueFromFilter(rawValue, fixedParameters, dataType = "") {
  const valueText = String(rawValue || "").trim();
  const parameterMatch = valueText.match(/^=?Parameters!([A-Za-z0-9_]+)\.Value$/i);
  if (parameterMatch) {
    const parameterName = parameterMatch[1];
    if (Object.prototype.hasOwnProperty.call(fixedParameters, parameterName)) {
      return fixedParameters[parameterName];
    }
    return undefined;
  }

  const stripped = valueText.replace(/^=/, "").replace(/^"|"$/g, "");
  const normalizedDataType = String(dataType || "").toLowerCase();
  if (/^(integer|int|float|decimal|double|number)$/i.test(normalizedDataType) && /^-?\d+(\.\d+)?$/.test(stripped)) {
    return Number(stripped);
  }
  return stripped;
}

function literalFilterSpec(filter, fixedParameters = {}) {
  const expression = String(filter?.expression || "");
  const values = Array.isArray(filter?.values) ? filter.values : [];
  if (!expression || !values.length) return "";
  const fieldMatch = expression.match(/Fields!([A-Za-z0-9_]+)\.Value/i);
  if (!fieldMatch) return null;
  const resolvedValues = values
    .map((value) => literalValueFromFilter(value, fixedParameters, filter?.data_type))
    .filter((value) => value !== undefined);
  if (!resolvedValues.length) return null;
  return {
    fieldAlias: fieldMatch[1],
    operator: filter?.operator || "Equal",
    values: resolvedValues,
    parameterReferences: Array.isArray(filter?.parameter_references) ? filter.parameter_references : [],
  };
}

function filterSpecsFromModel(dataset, fixedParameters = {}) {
  const filters = Array.isArray(dataset?.filters) ? dataset.filters : [];
  return filters.map((filter) => literalFilterSpec(filter, fixedParameters)).filter(Boolean);
}

function renderOuterFilter(filterSpec) {
  const fieldRef = `__rdl.${filterSpec.fieldAlias}`;
  const values = Array.isArray(filterSpec.values) ? filterSpec.values : [];
  const operator = String(filterSpec.operator || "Equal").toLowerCase();
  if (operator === "in" || values.length > 1) {
    return `${fieldRef} IN (${values.map(sqlLiteral).join(", ")})`;
  }
  const value = sqlLiteral(values[0]);
  if (operator === "notequal" || operator === "not_equal" || operator === "<>") return `${fieldRef} <> ${value}`;
  if (operator === "greaterthan" || operator === ">") return `${fieldRef} > ${value}`;
  if (operator === "greaterthanorequal" || operator === ">=") return `${fieldRef} >= ${value}`;
  if (operator === "lessthan" || operator === "<") return `${fieldRef} < ${value}`;
  if (operator === "lessthanorequal" || operator === "<=") return `${fieldRef} <= ${value}`;
  if (operator === "like") return `${fieldRef} LIKE ${value}`;
  return `${fieldRef} = ${value}`;
}

function renderOuterSelectItem(item) {
  const alias = item.alias || item.expression.split(".").pop();
  return `__rdl.${alias} AS ${alias}`;
}

function renderQuery({
  parsedQuery,
  selectedItems,
  innerItems,
  joins,
  groupBy,
  orderBy,
  filterSpecs,
}) {
  const innerLines = [
    `SELECT ${parsedQuery.distinct ? "DISTINCT " : ""}${innerItems.map(renderSelectItem).join(", ")}`,
    `FROM ${renderTable(parsedQuery.baseTable)}`,
    ...joins.map((join) => `${join.type} ${renderTable(join)} ON ${join.condition}`),
  ];
  if (parsedQuery.whereClauses.length) innerLines.push(`WHERE ${parsedQuery.whereClauses.join(" AND ")}`);
  if (groupBy.length) innerLines.push(`GROUP BY ${groupBy.join(", ")}`);

  if (!filterSpecs.length) {
    if (orderBy.length) innerLines.push(`ORDER BY ${orderBy.join(", ")}`);
    return `${innerLines.join(" ")};`;
  }

  const outerLines = [
    `SELECT ${selectedItems.map(renderOuterSelectItem).join(", ")}`,
    `FROM (${innerLines.join(" ")}) AS __rdl`,
    `WHERE ${filterSpecs.map(renderOuterFilter).join(" AND ")}`,
  ];
  return `${outerLines.join(" ")};`;
}

export function buildActualQueryFromSemanticModel({
  dataModel,
  visualModel,
  mappingModel,
  visualName = "",
  datasetName = "",
  fieldSource = "combined",
  fixedParameters = {},
  requireFields = false,
}) {
  const dataset =
    (Array.isArray(dataModel?.datasets) ? dataModel.datasets : []).find((entry) => entry.name === datasetName) ||
    datasetForVisual(dataModel, mappingModel, visualModel, visualName);
  if (!dataset?.query) {
    throw new Error("Query Builder could not resolve a dataset query from the semantic data model.");
  }

  const parsedQuery = parseDatasetQuery(dataset.query);
  const requestedFields = fieldsForVisual(mappingModel, visualModel, visualName, fieldSource);
  if (requireFields && !requestedFields.size) {
    throw new Error(`Query Builder could not resolve business fields for visual '${visualName}'.`);
  }
  const selectedItems = selectedItemsForVisual(parsedQuery, requestedFields);
  const filterSpecs = filterSpecsFromModel(dataset, fixedParameters);
  const filterItems = selectItemsByAliases(
    parsedQuery,
    new Set(filterSpecs.map((filterSpec) => filterSpec.fieldAlias)),
  );
  const innerItems = uniqueSelectItems([...selectedItems, ...filterItems]);
  const groupBy = filterGroupBy(parsedQuery, innerItems);
  const orderBy = filterOrderBy(parsedQuery, selectedItems);
  const joins = buildJoinList(parsedQuery, innerItems, groupBy, orderBy);

  return {
    sql: renderQuery({
      parsedQuery,
      selectedItems,
      innerItems,
      joins,
      groupBy,
      orderBy,
      filterSpecs,
    }),
    visualName: visualName || "",
    datasetName: dataset.name || "",
    source: `data_model + ${fieldSource === "visual" ? "visual_model" : fieldSource === "mapping" ? "mapping_model" : "visual_model + mapping_model"}`,
    selectedFields: selectedItems.map((item) => item.alias),
    dimensions: selectedItems.filter((item) => !item.aggregate).map((item) => item.alias),
    measures: selectedItems.filter((item) => item.aggregate).map((item) => item.alias),
    tables: [parsedQuery.baseTable, ...joins].map((table) => ({ table: table.table, alias: table.alias })),
    relationships: joins.map(relationshipFromJoin),
    filters: [
      ...parsedQuery.whereClauses,
      ...filterSpecs.map((filterSpec) => ({
        field: filterSpec.fieldAlias,
        operator: filterSpec.operator,
        values: filterSpec.values,
        parameterReferences: filterSpec.parameterReferences,
      })),
    ],
    fixedParameters,
    groupBy,
    orderBy,
  };
}
