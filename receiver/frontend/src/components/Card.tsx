import type { ReactNode } from "react";

export function Card({ title, eyebrow, actions, children, className = "", id }: {
  title?: ReactNode;
  eyebrow?: ReactNode;
  actions?: ReactNode;
  children: ReactNode;
  className?: string;
  id?: string;
}) {
  return (
    <section className={`card ${className}`} id={id}>
      {(title || eyebrow || actions) && (
        <header className="card-head">
          <div className="card-titles">
            {eyebrow && <div className="eyebrow">{eyebrow}</div>}
            {title && <h3>{title}</h3>}
          </div>
          {actions && <div className="card-actions">{actions}</div>}
        </header>
      )}
      {children}
    </section>
  );
}

export function Stat({ value, label }: { value: ReactNode; label: ReactNode }) {
  return (
    <div className="stat">
      <div className="stat-value tnum">{value}</div>
      <div className="stat-label">{label}</div>
    </div>
  );
}
