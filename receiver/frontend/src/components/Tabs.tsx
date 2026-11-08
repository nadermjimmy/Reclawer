import type { ReactNode } from "react";

export interface TabItem<K extends string> {
  key: K;
  label: ReactNode;
  count?: number;
}

/** Segmented pill control (state tabs, open/all toggles). Arrow keys move between options. */
export function Segmented<K extends string>({ items, value, onChange, label, size = "md" }: {
  items: TabItem<K>[];
  value: K;
  onChange: (k: K) => void;
  label: string;
  size?: "sm" | "md";
}) {
  return (
    <div className={`segmented segmented-${size}`} role="tablist" aria-label={label}
      onKeyDown={(e) => {
        if (e.key !== "ArrowRight" && e.key !== "ArrowLeft") return;
        const i = items.findIndex((x) => x.key === value);
        const n = items[(i + (e.key === "ArrowRight" ? 1 : items.length - 1)) % items.length];
        onChange(n.key);
        e.preventDefault();
      }}>
      {items.map((it) => (
        <button key={it.key} type="button" role="tab" aria-selected={it.key === value} tabIndex={it.key === value ? 0 : -1}
          className={`seg${it.key === value ? " is-active" : ""}`} onClick={() => onChange(it.key)}>
          {it.label}
          {it.count !== undefined && <span className="seg-count tnum">{it.count.toLocaleString("en-US")}</span>}
        </button>
      ))}
    </div>
  );
}
