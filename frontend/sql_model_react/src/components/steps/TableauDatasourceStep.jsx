import React from "react";
import Card from "../shared/Card.jsx";
import Button from "../shared/Button.jsx";
import Badge from "../shared/Badge.jsx";

export default function TableauDatasourceStep({ datasourceConfig, setDatasourceConfig, datasourcePrepared, onPrepare }) {
  function update(field, value) {
    setDatasourceConfig({ ...datasourceConfig, [field]: value });
  }

  const publishModeLabel = datasourceConfig.connectionType === "extract" ? "Extract" : "Live TDS";
  const usesPat = datasourceConfig.authMode === "pat";
  const sourceLabel = datasourceConfig.sourceDatabase || datasourceConfig.sourceServer || "From RDL/config";
  const secretPlaceholder = datasourceConfig.credentialsReady ? "Already loaded from backend config" : "Required for publish";

  return (
    <Card title="9. Tableau datasource" eyebrow="Publish settings" className="tableau-datasource-card">
      <div className="publish-minimal-strip">
        <div>
          <span>Source</span>
          <strong>{sourceLabel}</strong>
        </div>
        <label>
          <span>Mode</span>
          <select value={datasourceConfig.connectionType} onChange={(event) => update("connectionType", event.target.value)}>
            <option value="live_tds">Live TDS</option>
          </select>
        </label>
        <div>
          <span>Status</span>
          <Badge tone={datasourcePrepared ? "green" : "indigo"}>{datasourcePrepared ? "Prepared" : publishModeLabel}</Badge>
        </div>
      </div>

      <div className="publish-minimal-form">
        <label>
          Tableau server URL
          <input value={datasourceConfig.tableauServerUrl} onChange={(event) => update("tableauServerUrl", event.target.value)} />
        </label>
        <label>
          Site content URL
          <input value={datasourceConfig.siteContentUrl} onChange={(event) => update("siteContentUrl", event.target.value)} />
        </label>
        <label>
          Datasource project
          <input value={datasourceConfig.datasourceProject} onChange={(event) => update("datasourceProject", event.target.value)} />
        </label>
        <label>
          Final workbook project
          <input value={datasourceConfig.workbookProject} onChange={(event) => update("workbookProject", event.target.value)} />
        </label>
        <label>
          Datasource name
          <input value={datasourceConfig.sourceDatasourceName} onChange={(event) => update("sourceDatasourceName", event.target.value)} />
        </label>
        <label>
          Authentication
          <select value={datasourceConfig.authMode} onChange={(event) => update("authMode", event.target.value)}>
            <option value="username_password">Username / password</option>
            <option value="pat">Personal access token</option>
          </select>
        </label>
        {usesPat ? (
          <>
            <label>
              PAT name
              <input value={datasourceConfig.patName} onChange={(event) => update("patName", event.target.value)} />
            </label>
            <label>
              PAT secret
              <input
                type="password"
                placeholder={secretPlaceholder}
                value={datasourceConfig.patSecret}
                onChange={(event) => update("patSecret", event.target.value)}
              />
            </label>
          </>
        ) : (
          <>
            <label>
              Username
              <input value={datasourceConfig.username} onChange={(event) => update("username", event.target.value)} />
            </label>
            <label>
              Password
              <input
                type="password"
                placeholder={secretPlaceholder}
                value={datasourceConfig.password}
                onChange={(event) => update("password", event.target.value)}
              />
            </label>
          </>
        )}
      </div>

      <div className="button-row publish-actions">
        <Button variant="primary" onClick={onPrepare}>Prepare publish</Button>
      </div>
    </Card>
  );
}
