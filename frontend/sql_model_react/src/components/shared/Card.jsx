import React from "react";

export default function Card({ title, eyebrow, actions, children, className = "" }) {
  return (
    <section className={`card ${className}`.trim()}>
      {(title || eyebrow || actions) && (
        <header className="card-header">
          <div>
            {eyebrow && <div className="eyebrow">{eyebrow}</div>}
            {title && <h2>{title}</h2>}
          </div>
          {actions && <div className="card-actions">{actions}</div>}
        </header>
      )}
      <div className="card-body">{children}</div>
    </section>
  );
}
