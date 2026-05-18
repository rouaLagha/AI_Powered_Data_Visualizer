import React, { useState } from "react";

export default function Tabs({ tabs }) {
  const [active, setActive] = useState(tabs[0]?.id || "");
  const activeTab = tabs.find((tab) => tab.id === active) || tabs[0];

  return (
    <div className="tabs">
      <div className="tab-list" role="tablist">
        {tabs.map((tab) => (
          <button
            key={tab.id}
            className={`tab-button ${active === tab.id ? "active" : ""}`}
            onClick={() => setActive(tab.id)}
            type="button"
          >
            {tab.label}
          </button>
        ))}
      </div>
      <div className="tab-panel">{activeTab?.content}</div>
    </div>
  );
}
