import React from "react";
import Badge from "../shared/Badge.jsx";

export default function MeasuresPanel({ measures }) {
  return (
    <div className="table-wrap">
      <table>
        <thead>
          <tr>
            <th>Measure name</th>
            <th>Expression</th>
            <th>Source table</th>
            <th>Aggregation</th>
            <th>Status</th>
          </tr>
        </thead>
        <tbody>
          {measures.map((measure) => (
            <tr key={measure.name}>
              <td>{measure.name}</td>
              <td><code>{measure.expression}</code></td>
              <td>{measure.sourceTable}</td>
              <td>{measure.aggregation}</td>
              <td><Badge tone={measure.status === "valid" ? "green" : "orange"}>{measure.status}</Badge></td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
