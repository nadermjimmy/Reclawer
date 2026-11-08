import { Check, RotateCcw, Sparkles, X } from "lucide-react";
import { useCallback, useEffect, useMemo, useState } from "react";
import { errorText } from "../../api/client";
import { decide, reopen } from "../../api/proposals";
import { getSheet, type SheetCell, type SheetChange, type SheetData, type SheetRow, type SheetView } from "../../api/sheet";
import { Button, ButtonLink, Drawer, EmptyState, ErrorBanner, SearchInput, Segmented, Skeleton, useToast } from "../../components";
import { relTime } from "../../lib/format";
import { usePageTitle } from "../../app/title";
import "./sheet.css";

type Shown = "current" | "master";
interface Picked { row: SheetRow; field: string; label: string; cell: SheetCell }

const VIEWS: { key: SheetView; label: string }[] = [
  { key: "all", label: "All rows" },
  { key: "changed", label: "With changes" },
  { key: "pending", label: "Open suggestions" },
  { key: "approved", label: "Approved" },
];

function shownValue(cell: SheetCell, shown: Shown) {
  if (shown === "current" && cell.approved) return cell.approved.new_raw;
  return cell.master;
}

function cellClass(cell: SheetCell, shown: Shown) {
  const c = ["sv-cell"];
  if (shown === "current" && cell.approved) c.push(cell.approved.decided_by === "brain" ? "is-brain" : "is-approved");
  if (cell.pending) c.push("is-pending");
  return c.join(" ");
}

/** The master workbook as a grid, with approved changes and open suggestions on top, to check them against the
 * team's own sheet. Click a highlighted cell for the source, the brain's reason and the actions. */
export function SheetScreen() {
  usePageTitle("Sheet");
  const toast = useToast();
  const [sheet, setSheet] = useState("");
  const [view, setView] = useState<SheetView>("changed");
  const [shown, setShown] = useState<Shown>("current");
  const [query, setQuery] = useState("");
  const [q, setQ] = useState("");
  const [data, setData] = useState<SheetData | null>(null);
  const [rows, setRows] = useState<SheetRow[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [picked, setPicked] = useState<Picked | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    const t = window.setTimeout(() => setQ(query.trim()), 200);
    return () => window.clearTimeout(t);
  }, [query]);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const d = await getSheet(sheet, view, q);
      setData(d);
      setRows(d.rows);
      if (!sheet) setSheet(d.sheet);
      setError(null);
    } catch (e) {
      setError(errorText(e, "Couldn't load the sheet."));
    } finally {
      setLoading(false);
    }
  }, [sheet, view, q]);
  useEffect(() => { void load(); }, [load]);

  const more = async () => {
    if (!data?.next_offset) return;
    const d = await getSheet(data.sheet, view, q, data.next_offset);
    setData(d);
    setRows((r) => [...r, ...d.rows]);
  };

  const act = async (fn: () => Promise<unknown>, done: string) => {
    setBusy(true);
    try {
      await fn();
      toast.show({ message: done });
      setPicked(null);
      await load();
    } catch (e) {
      toast.show({ tone: "error", message: errorText(e, "That didn't work. Try again.") });
    } finally {
      setBusy(false);
    }
  };

  const cols = data?.columns ?? [];
  const sticky = useMemo(() => new Set(["developer", "project"]), []);

  if (error && !data) {
    return <div className="page"><ErrorBanner onRetry={() => void load()}>{error}</ErrorBanner></div>;
  }
  if (data === null && !loading) return null;

  return (
    <div className="page sheetview">
      <div className="sv-tabs" role="tablist" aria-label="Sheet">
        {(data?.sheets ?? []).map((s) => (
          <button key={s} type="button" role="tab" aria-selected={s === sheet} className={`sv-tab${s === sheet ? " is-active" : ""}`}
            onClick={() => { setSheet(s); setRows([]); }}>{s}</button>
        ))}
      </div>
      <div className="sv-toolbar">
        <Segmented label="Rows" items={VIEWS} value={view} onChange={setView} size="sm" />
        <Segmented label="Values" size="sm" value={shown} onChange={setShown}
          items={[{ key: "current", label: "After approvals" }, { key: "master", label: "As uploaded" }]} />
        <SearchInput label="Search project or developer" placeholder="Project or developer" value={query}
          onChange={(e) => setQuery(e.target.value)} className="sv-search" />
      </div>
      {data && (
        <div className="sv-legend small">
          <span className="tnum"><b>{data.counts.rows.toLocaleString("en-US")}</b> rows</span>
          <span><i className="sv-key is-approved" /> Approved <b className="tnum">{data.counts.approved_cells}</b></span>
          <span><i className="sv-key is-brain" /> Approved by brain</span>
          <span><i className="sv-key is-pending" /> Open suggestion <b className="tnum">{data.counts.pending_cells}</b></span>
          {data.counts.suggested_new_rows > 0 && <span><i className="sv-key is-newrow" /> Suggested new row <b className="tnum">{data.counts.suggested_new_rows}</b></span>}
        </div>
      )}
      {error && data && <ErrorBanner onRetry={() => void load()}>Couldn't refresh. Showing the last view.</ErrorBanner>}
      {loading && !rows.length ? (
        <div className="card"><Skeleton height={320} /></div>
      ) : !rows.length ? (
        <div className="card"><EmptyState title={view === "all" ? "No rows on this sheet" : "No rows with changes here"}
          actions={view !== "all" ? <Button variant="secondary" onClick={() => setView("all")}>Show all rows</Button> : undefined} /></div>
      ) : (
        <div className="sv-wrap card card-flush">
          <table className="sv-grid">
            <thead>
              <tr>
                <th className="sv-rownum" scope="col">#</th>
                {cols.map((c) => <th key={c.field} scope="col" className={sticky.has(c.field) ? `sv-sticky sv-${c.field}` : ""}>{c.label}</th>)}
              </tr>
            </thead>
            <tbody>
              {rows.map((r, i) => (
                <tr key={r.row_id ?? `new-${i}`} className={r.state === "suggested_new" ? "is-newrow" : r.state !== "active" ? "is-muted" : ""}>
                  <th scope="row" className="sv-rownum tnum">{r.row_number ?? (r.state === "suggested_new" ? "new?" : "new")}</th>
                  {cols.map((c) => {
                    const cell = r.cells[c.field] ?? { master: "" };
                    const has = Boolean(cell.pending || (shown === "current" && cell.approved));
                    const value = shownValue(cell, shown);
                    return (
                      <td key={c.field} className={`${cellClass(cell, shown)}${sticky.has(c.field) ? ` sv-sticky sv-${c.field}` : ""}`} dir="auto">
                        {has ? (
                          <button type="button" className="sv-hit" onClick={() => setPicked({ row: r, field: c.field, label: c.label, cell })}
                            aria-label={`${c.label}, ${r.cells.project?.master ?? ""}: details`}>
                            <span className="sv-val">{value || " "}</span>
                            {cell.pending && <span className="sv-sugg">→ {cell.pending.new_raw}{cell.pending.count > 1 ? ` (+${cell.pending.count - 1})` : ""}</span>}
                          </button>
                        ) : <span className="sv-val">{value}</span>}
                      </td>
                    );
                  })}
                </tr>
              ))}
            </tbody>
          </table>
          {data?.next_offset != null && (
            <div className="load-more"><Button variant="ghost" onClick={() => void more()}>Load more ({data.total - rows.length} left)</Button></div>
          )}
        </div>
      )}
      <Drawer open={Boolean(picked)} onClose={() => setPicked(null)} eyebrow={picked ? `${data?.sheet} · ${picked.row.row_number ? `Row ${picked.row.row_number}` : "New row"}` : ""}
        title={picked ? `${picked.row.cells.project?.master ?? ""} · ${picked.label}` : ""}>
        {picked && (
          <div className="sv-detail">
            <dl className="sv-values">
              <div><dt className="caption">Master (as uploaded)</dt><dd dir="auto">{picked.cell.master || "Empty"}</dd></div>
              {picked.cell.approved && <div><dt className="caption">After approvals</dt><dd dir="auto" className="strong">{picked.cell.approved.new_raw}</dd></div>}
              {picked.cell.pending && <div><dt className="caption">Suggested</dt><dd dir="auto" className="strong">{picked.cell.pending.new_raw}</dd></div>}
            </dl>
            {picked.cell.approved && (
              <ChangeBlock title={picked.cell.approved.decided_by === "brain" ? "Approved by brain" : "Approved"} ch={picked.cell.approved}>
                {picked.cell.approved.decided_by === "brain" && (
                  <Button variant="ghost" size="sm" icon={<RotateCcw size={14} />} disabled={busy}
                    onClick={() => void act(() => reopen(picked.cell.approved!.proposal_id), "Reopened: it's back on Updates")}>Reopen</Button>
                )}
              </ChangeBlock>
            )}
            {picked.cell.pending && (
              <ChangeBlock title={`Open suggestion${picked.cell.pending.count > 1 ? ` (newest of ${picked.cell.pending.count})` : ""}`} ch={picked.cell.pending}>
                <Button variant="primary" size="sm" icon={<Check size={14} />} disabled={busy || picked.cell.pending.row_new}
                  title={picked.cell.pending.row_new ? "New rows are approved on Updates, where you choose the sheet" : undefined}
                  onClick={() => void act(() => decide([picked.cell.pending!.proposal_id], "approve"), "Approved")}>Approve</Button>
                <Button variant="ghost" size="sm" icon={<X size={14} />} disabled={busy}
                  onClick={() => void act(() => decide([picked.cell.pending!.proposal_id], "reject"), "Rejected")}>Reject</Button>
                <ButtonLink to={`/updates?q=${encodeURIComponent(picked.row.cells.project?.master ?? "")}`} variant="ghost" size="sm">Open in Updates</ButtonLink>
              </ChangeBlock>
            )}
          </div>
        )}
      </Drawer>
    </div>
  );
}

function ChangeBlock({ title, ch, children }: { title: string; ch: SheetChange; children?: React.ReactNode }) {
  return (
    <section className="sv-change">
      <h3 className="eyebrow">{title}</h3>
      <p className="small"><span className="muted">{ch.old_raw || "Empty"}</span> → <b dir="auto">{ch.new_raw}</b></p>
      {ch.brain_reason && <p className="small sv-brain"><Sparkles size={13} aria-hidden="true" /> <span dir="auto">{ch.brain_reason}</span></p>}
      {ch.quote && <blockquote className="quote" dir="auto">{ch.quote}</blockquote>}
      <p className="caption muted">{ch.chat_name || "Unknown chat"} · {ch.source_kind === "message" ? "message" : ch.source_kind.replace("_", " ")} · {relTime(ch.source_ts)}
        {ch.warning ? <><br />{ch.warning}</> : null}</p>
      <div className="row">{children}</div>
    </section>
  );
}
