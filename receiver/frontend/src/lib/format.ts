/* Formatting helpers. Numbers carry their unit ("6,900,000 EGP", "125 sq m"); dates are local time. */

const nf = new Intl.NumberFormat("en-US");

export const fmtNumber = (n: number | null | undefined) => (n === null || n === undefined ? "—" : nf.format(n));

export const fmtMoney = (n: number | null | undefined, currency = "EGP") =>
  n === null || n === undefined ? "—" : `${nf.format(Math.round(n))} ${currency}`;

export const fmtArea = (n: number | null | undefined, metric = "sq m") =>
  n === null || n === undefined ? "—" : `${nf.format(n)} ${metric}`;

/** Signed percent with a real minus sign: "+8.7%" / "−3.2%" / "0%". */
export function fmtPct(p: number | null | undefined) {
  if (p === null || p === undefined) return "";
  if (p === 0) return "0%";
  return `${p > 0 ? "+" : "−"}${Math.abs(p).toLocaleString("en-US", { maximumFractionDigits: 1 })}%`;
}

const dtf = new Intl.DateTimeFormat("en-GB", { day: "numeric", month: "short", year: "numeric", hour: "2-digit", minute: "2-digit" });
const df = new Intl.DateTimeFormat("en-GB", { day: "numeric", month: "short", year: "numeric" });

export const fmtDateTime = (ts: number | null | undefined) => (ts ? dtf.format(new Date(ts * 1000)) : "—");
export const fmtDate = (ts: number | null | undefined) => (ts ? df.format(new Date(ts * 1000)) : "—");

/** "just now", "12 min ago", "3 h ago", "2 days ago", then a date. */
export function relTime(ts: number | null | undefined, now = Date.now() / 1000) {
  if (!ts) return "—";
  const s = Math.max(0, now - ts);
  if (s < 60) return "just now";
  if (s < 3600) return `${Math.floor(s / 60)} min ago`;
  if (s < 86400) return `${Math.floor(s / 3600)} h ago`;
  if (s < 86400 * 14) {
    const d = Math.floor(s / 86400);
    return `${d} day${d === 1 ? "" : "s"} ago`;
  }
  return fmtDate(ts);
}

/** Elapsed "m:ss" or "h:mm:ss" since a start time. */
export function elapsed(startTs: number | null | undefined, endTs?: number | null) {
  if (!startTs) return "";
  const s = Math.max(0, Math.floor((endTs ?? Date.now() / 1000) - startTs));
  const h = Math.floor(s / 3600);
  const m = Math.floor((s % 3600) / 60);
  const sec = String(s % 60).padStart(2, "0");
  return h ? `${h}:${String(m).padStart(2, "0")}:${sec}` : `${m}:${sec}`;
}

export const plural = (n: number, one: string, many = `${one}s`) => `${fmtNumber(n)} ${n === 1 ? one : many}`;

export const label = (s: string | null | undefined) => (s ?? "").replace(/_/g, " ");

export const titleCase = (s: string | null | undefined) => label(s).replace(/\b\w/g, (c) => c.toUpperCase());
