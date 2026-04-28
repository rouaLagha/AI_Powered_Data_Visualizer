import React from "react";

export default function ChangeDiffViewer({ before, after }) {
  return (
    <div className="diff-viewer">
      <div>
        <div className="diff-label">Before</div>
        <pre>{before || "No existing value"}</pre>
      </div>
      <div>
        <div className="diff-label">After</div>
        <pre>{after || "No proposed value"}</pre>
      </div>
    </div>
  );
}
