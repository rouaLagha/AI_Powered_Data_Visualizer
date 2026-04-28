import React from "react";

export default function CodePreview({ code, language = "sql" }) {
  return (
    <pre className="code-preview" data-language={language}>
      <code>{code}</code>
    </pre>
  );
}
