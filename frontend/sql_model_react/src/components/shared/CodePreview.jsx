import React from "react";

const SQL_KEYWORDS = new Set([
  "ADD",
  "AND",
  "AS",
  "ASC",
  "BETWEEN",
  "BY",
  "CASE",
  "CREATE",
  "DELETE",
  "DESC",
  "DISTINCT",
  "ELSE",
  "END",
  "FROM",
  "FULL",
  "GROUP",
  "HAVING",
  "IN",
  "INNER",
  "INSERT",
  "INTO",
  "IS",
  "JOIN",
  "LEFT",
  "LIKE",
  "NOT",
  "NULL",
  "ON",
  "OR",
  "ORDER",
  "OUTER",
  "RIGHT",
  "SELECT",
  "THEN",
  "UNION",
  "UPDATE",
  "WHEN",
  "WHERE",
  "WITH",
]);

const SQL_FUNCTIONS = new Set([
  "AVG",
  "CAST",
  "COALESCE",
  "CONVERT",
  "COUNT",
  "DATEADD",
  "DATEDIFF",
  "ISNULL",
  "MAX",
  "MIN",
  "MONTH",
  "SUM",
  "YEAR",
]);

const SQL_TOKEN_PATTERN =
  /(--.*?$|\/\*[\s\S]*?\*\/|'(?:''|[^'])*'|"(?:\"\"|[^"])*"|\b\d+(?:\.\d+)?\b|\b[A-Za-z_][\w$]*\b|[()[\],.;=<>+\-*/%])/gm;

function tokenClass(token) {
  const upperToken = token.toUpperCase();
  if (token.startsWith("--") || token.startsWith("/*")) return "sql-token-comment";
  if (token.startsWith("'") || token.startsWith('"')) return "sql-token-string";
  if (/^\d/.test(token)) return "sql-token-number";
  if (SQL_KEYWORDS.has(upperToken)) return "sql-token-keyword";
  if (SQL_FUNCTIONS.has(upperToken)) return "sql-token-function";
  if (/^[()[\],.;=<>+\-*/%]$/.test(token)) return "sql-token-operator";
  return "sql-token-identifier";
}

function highlightSql(code) {
  const parts = [];
  let lastIndex = 0;
  let match;

  SQL_TOKEN_PATTERN.lastIndex = 0;
  while ((match = SQL_TOKEN_PATTERN.exec(code)) !== null) {
    if (match.index > lastIndex) {
      parts.push(code.slice(lastIndex, match.index));
    }

    const token = match[0];
    parts.push(
      <span className={tokenClass(token)} key={`${match.index}-${token}`}>
        {token}
      </span>,
    );
    lastIndex = match.index + token.length;
  }

  if (lastIndex < code.length) {
    parts.push(code.slice(lastIndex));
  }

  return parts;
}

export default function CodePreview({ code, language = "sql" }) {
  const normalizedLanguage = language.toLowerCase();
  const previewCode = code || "";
  const highlightedCode = normalizedLanguage === "sql" ? highlightSql(previewCode) : previewCode;

  return (
    <pre className={`code-preview code-preview-${normalizedLanguage}`} data-language={normalizedLanguage}>
      <code>{highlightedCode}</code>
    </pre>
  );
}
