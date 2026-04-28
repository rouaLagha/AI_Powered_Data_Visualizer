import React, { useRef, useState } from "react";
import Button from "../shared/Button.jsx";
import Card from "../shared/Card.jsx";
import Badge from "../shared/Badge.jsx";

function formatSize(size) {
  if (!size) return "0 KB";
  return `${(size / 1024).toFixed(1)} KB`;
}

export default function RdlUploadStep({ uploadedFile, setUploadedFile, markStepComplete, resetPipeline }) {
  const inputRef = useRef(null);
  const [dragging, setDragging] = useState(false);

  function acceptFile(file) {
    if (!file) return;
    setUploadedFile({
      name: file.name,
      size: file.size,
      file,
      status: file.name.toLowerCase().endsWith(".rdl") ? "ready" : "invalid_format",
    });
  }

  function onDrop(event) {
    event.preventDefault();
    setDragging(false);
    acceptFile(event.dataTransfer.files?.[0]);
  }

  return (
    <Card title="1. RDL upload" eyebrow="Source report">
      <div className="dropzone-wrap">
        <div
          className={`dropzone ${dragging ? "dragging" : ""}`}
          onDragOver={(event) => {
            event.preventDefault();
            setDragging(true);
          }}
          onDragLeave={() => setDragging(false)}
          onDrop={onDrop}
        >
          <input
            ref={inputRef}
            type="file"
            accept=".rdl"
            hidden
            onChange={(event) => acceptFile(event.target.files?.[0])}
          />
          <h3>Drop an RDL file here</h3>
          <p>SSRS report definition, `.rdl` only.</p>
          <Button onClick={() => inputRef.current?.click()}>Browse</Button>
        </div>

        {uploadedFile && (
          <div className="file-summary">
            <div>
              <strong>{uploadedFile.name}</strong>
              <span>{formatSize(uploadedFile.size)}</span>
            </div>
            <Badge tone={uploadedFile.status === "ready" ? "green" : "red"}>
              {uploadedFile.status === "ready" ? "Ready" : "Invalid file"}
            </Badge>
          </div>
        )}
      </div>

      <div className="button-row">
        <Button variant="primary" onClick={markStepComplete} disabled={!uploadedFile || uploadedFile.status !== "ready"}>
          Parse RDL
        </Button>
        <Button onClick={resetPipeline}>Reset</Button>
      </div>
    </Card>
  );
}
