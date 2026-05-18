import React from "react";
import Badge from "../shared/Badge.jsx";

export default function RelationshipEditor({ relationships, onAddRelationship, onDeleteRelationship }) {
  return (
    <div className="table-wrap">
      <div className="table-toolbar">
        <strong>Relationships</strong>
        <button className="btn btn-secondary btn-sm" onClick={onAddRelationship} type="button">
          Add via AI
        </button>
      </div>
      <table>
        <thead>
          <tr>
            <th>From</th>
            <th>To</th>
            <th>Keys</th>
            <th>Cardinality</th>
            <th></th>
          </tr>
        </thead>
        <tbody>
          {relationships.map((relationship) => (
            <tr key={relationship.id}>
              <td>{relationship.fromTable}</td>
              <td>{relationship.toTable}</td>
              <td><code>{relationship.fromColumn} = {relationship.toColumn}</code></td>
              <td><Badge tone="indigo">{relationship.cardinality}</Badge></td>
              <td>
                <button className="link-button danger" type="button" onClick={() => onDeleteRelationship(relationship.id)}>
                  Remove
                </button>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
