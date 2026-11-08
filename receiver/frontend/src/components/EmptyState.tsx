import type { ReactNode } from "react";

export function EmptyState({ icon, title, children, actions, tone = "plain" }: {
  icon?: ReactNode;
  title: string;
  children?: ReactNode;
  actions?: ReactNode;
  tone?: "plain" | "blocking";
}) {
  return (
    <div className={`empty empty-${tone}`} role={tone === "blocking" ? "alert" : undefined}>
      {icon && <div className="empty-icon" aria-hidden="true">{icon}</div>}
      <h3>{title}</h3>
      {children && <div className="empty-body">{children}</div>}
      {actions && <div className="empty-actions">{actions}</div>}
    </div>
  );
}

/** Inline error banner with a retry (never blames the person). */
export function ErrorBanner({ children, onRetry }: { children: ReactNode; onRetry?: () => void }) {
  return (
    <div className="banner banner-error" role="alert">
      <span>{children}</span>
      {onRetry && (
        <button type="button" className="banner-action" onClick={onRetry}>
          Retry
        </button>
      )}
    </div>
  );
}

export function InfoBanner({ children, action }: { children: ReactNode; action?: ReactNode }) {
  return (
    <div className="banner banner-info" role="status">
      <span>{children}</span>
      {action}
    </div>
  );
}
