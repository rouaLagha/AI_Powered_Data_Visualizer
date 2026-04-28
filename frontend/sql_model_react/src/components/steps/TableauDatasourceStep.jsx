import React from "react";
import Card from "../shared/Card.jsx";
import Button from "../shared/Button.jsx";
import Badge from "../shared/Badge.jsx";

export default function TableauDatasourceStep({ datasourceConfig, setDatasourceConfig, datasourcePrepared, onPrepare }) {
  function update(field, value) {
    setDatasourceConfig({ ...datasourceConfig, [field]: value });
  }

  return (
    <Card title="8. Tableau datasource" eyebrow="Publish settings">
      <div className="source-context">
        <div><span>Source server</span><strong>{datasourceConfig.sourceServer || "From RDL/config"}</strong></div>
        <div><span>Source database</span><strong>{datasourceConfig.sourceDatabase || "From RDL/config"}</strong></div>
        <div><span>Credentials</span><strong>{datasourceConfig.credentialsReady ? "Loaded" : "Enter below"}</strong></div>
      </div>

      <div className="form-grid">
        <label>
          Tableau server URL
          <input value={datasourceConfig.tableauServerUrl} onChange={(event) => update("tableauServerUrl", event.target.value)} />
        </label>
        <label>
          Site content URL
          <input value={datasourceConfig.siteContentUrl} onChange={(event) => update("siteContentUrl", event.target.value)} />
        </label>
        <label>
          Project
          <input value={datasourceConfig.project} onChange={(event) => update("project", event.target.value)} />
        </label>
        <label>
          Publish mode
          <select value={datasourceConfig.connectionType} onChange={(event) => update("connectionType", event.target.value)}>
            <option value="live_tds">Live TDS</option>
            <option value="extract">Extract</option>
          </select>
        </label>
        <label>
          Source datasource name
          <input value={datasourceConfig.sourceDatasourceName} onChange={(event) => update("sourceDatasourceName", event.target.value)} />
        </label>
        <label>
          Authentication
          <select value={datasourceConfig.authMode} onChange={(event) => update("authMode", event.target.value)}>
            <option value="username_password">Username / password</option>
            <option value="pat">Personal access token</option>
          </select>
        </label>
        <label>
          Username
          <input value={datasourceConfig.username} onChange={(event) => update("username", event.target.value)} />
        </label>
        <label>
          Password
          <input
            type="password"
            placeholder={datasourceConfig.credentialsReady ? "Already loaded" : "Required for publish"}
            value={datasourceConfig.password}
            onChange={(event) => update("password", event.target.value)}
          />
        </label>
      </div>

      <div className="button-row">
        <Button variant="primary" onClick={onPrepare}>Prepare publish</Button>
        {datasourcePrepared && <Badge tone="green">Prepared</Badge>}
      </div>
    </Card>
  );
}
