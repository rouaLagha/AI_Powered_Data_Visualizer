import React from "react";
import TableCard from "./TableCard.jsx";

const graphPositions = {
  FactResellerSales: { gridColumn: "2", gridRow: "2" },
  DimDate: { gridColumn: "1", gridRow: "1" },
  DimProduct: { gridColumn: "3", gridRow: "1" },
  DimProductSubcategory: { gridColumn: "4", gridRow: "1" },
  DimProductCategory: { gridColumn: "5", gridRow: "1" },
  DimSalesTerritory: { gridColumn: "1", gridRow: "3" },
  DimReseller: { gridColumn: "3", gridRow: "3" },
};

export default function SchemaGraph({ model }) {
  const tables = model.tables || [];
  const fact = tables.find((table) => table.type === "fact");

  return (
    <div className="schema-graph">
      <svg className="schema-lines" viewBox="0 0 100 100" preserveAspectRatio="none" aria-hidden="true">
        <line x1="38" y1="52" x2="18" y2="23" />
        <line x1="48" y1="42" x2="50" y2="23" />
        <line x1="60" y1="23" x2="76" y2="23" />
        <line x1="82" y1="23" x2="94" y2="23" />
        <line x1="36" y1="62" x2="18" y2="78" />
        <line x1="56" y1="62" x2="52" y2="78" />
      </svg>
      {tables.map((table) => (
        <div
          key={table.id}
          className={`schema-node ${table.name === fact?.name ? "center-node" : ""}`}
          style={graphPositions[table.name] || {}}
        >
          <TableCard table={table} compact={table.name !== fact?.name} />
        </div>
      ))}
    </div>
  );
}
