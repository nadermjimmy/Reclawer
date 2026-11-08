import { ArrowDown, ArrowRight, ArrowUp } from "lucide-react";
import type { Proposal } from "../../api/proposals";
import { fmtNumber, fmtPct, label } from "../../lib/format";

/** Old -> new value for a suggestion, with neutral price/area deltas (owner decision: arrows and +/- only,
    no good/bad colouring). Reused by part 2 in the Chats insights panel. */
export function Change({ p, size = "md", metric = "sq m" }: { p: Proposal; size?: "md" | "lg"; metric?: string }) {
  const badFormat = p.warning_codes.includes("bad_format");
  return (
    <div className={`change change-${size}`}>
      <div className="change-values">
        {p.old_raw ? <span className="change-old" dir="auto">{p.old_raw}</span> : <span className="change-empty">Empty</span>}
        <ArrowRight size={size === "lg" ? 18 : 14} className="change-arrow" aria-label="becomes" />
        <span className={`change-new${badFormat ? " mono" : ""}`} dir="auto">{p.new_raw}</span>
      </div>
      <Delta p={p} metric={metric} />
    </div>
  );
}

export function Delta({ p, metric }: { p: Proposal; metric: string }) {
  const d = p.delta;
  if (!d) return null;
  const parts = [];
  if (d.price_pct !== undefined && d.price_pct !== null) {
    const Icon = d.price_pct > 0 ? ArrowUp : d.price_pct < 0 ? ArrowDown : null;
    parts.push(
      <span key="price" className="delta">
        {Icon && <Icon size={12} aria-hidden="true" />}
        Price <span className="tnum">{fmtPct(d.price_pct)}</span>
      </span>,
    );
  }
  if (d.area_from !== undefined && d.area_to !== undefined) {
    parts.push(
      <span key="area" className="delta">
        Area <span className="tnum">{fmtNumber(d.area_from)} → {fmtNumber(d.area_to)} {metric}</span>
      </span>,
    );
  }
  if (d.status_from !== undefined && d.status_to !== undefined && (d.status_from || d.status_to)) {
    parts.push(
      <span key="status" className="delta">
        Status {label(d.status_from) || "none"} → {label(d.status_to) || "none"}
      </span>,
    );
  }
  return parts.length ? <div className="deltas">{parts}</div> : null;
}

export const displayProject = (p: Proposal) => p.row_project || p.project;
