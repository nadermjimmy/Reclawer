import { Check, RotateCcw, Sparkles, X } from "lucide-react";
import { useCallback, useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { useSearchParams } from "react-router-dom";
import { errorText } from "../../api/client";
import { decide, reopen } from "../../api/proposals";
import { getSheet, type SheetCell, type SheetChange, type SheetColumn, type SheetData, type SheetRow, type SheetView } from "../../api/sheet";
import { Button, ButtonLink, Drawer, EmptyState, ErrorBanner, SearchInput, Segmented, Skeleton, useToast } from "../../components";
import { useIsPhone } from "../../lib/hooks";
import { relTime } from "../../lib/format";
import { usePageTitle } from "../../app/title";
import { SheetGrid } from "./SheetGrid";
import { STATE_NOTE, address, cellOf, columnWidths, displayValue, fmtDateLong, hasDetails, isDateCol, rawValue, type Pos, type Shown } from "./model";
import "./sheet.css";

interface Picked { row: SheetRow; col: SheetColumn; cell: SheetCell }

const VIEWS: { key: SheetView; label: string }[] = [
  { key: "all", label: "All rows" },
  { key: "changed", label: "With changes" },
  { key: "pending", label: "Open suggestions" },
  { key: "approved", label: "Approved" },
];
const VALUES: { key: Shown; label: string }[] = [
  { key: "current", label: "After approvals" },
  { key: "master", label: "As uploaded" },
];
const ORIGIN: Pos = { r: 0, c: 0 };

/** The master workbook as a Google-Sheets-like grid, with approved changes and open suggestions on top, so the team
 * can check them against their own sheet. Select a cell to see it in the formula bar; Enter or double-click a
 * highlighted cell for the source, the brain's reason and the actions. */
export function SheetScreen() {
  usePageTitle("Sheet");
  const toast = useToast();
  const phone = useIsPhone();
  const [params, setParams] = useSearchParams();
  const sheet = params.get("sheet") ?? "";
  const [view, setView] = useState<SheetView>("all");
  const [shown, setShown] = useState<Shown>("current");
  const [query, setQuery] = useState("");
  const [q, setQ] = useState("");
  const [data, setData] = useState<SheetData | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [sel, setSel] = useState<Pos>(ORIGIN);
  const [picked, setPicked] = useState<Picked | null>(null);
  const [busy, setBusy] = useState(false);
  const gridRef = useRef<HTMLDivElement>(null);
  const searchRef = useRef<HTMLInputElement>(null);
  const keep = useRef<{ sheet: string; rowId: number | null; field: string } | null>(null);

  useEffect(() => {
    const t = window.setTimeout(() => setQ(query.trim()), 200);
    return () => window.clearTimeout(t);
  }, [query]);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const d = await getSheet(sheet, view, q);
      setData(d);
      // Keep the cursor on the same row and column across a refresh; a new tab starts at A2.
      const k = keep.current;
      if (k && k.sheet === d.sheet) {
        const r = k.rowId == null ? -1 : d.rows.findIndex((x) => x.row_id === k.rowId);
        const c = d.columns.findIndex((x) => x.field === k.field);
        setSel((s) => ({ r: r >= 0 ? r : Math.min(s.r, Math.max(0, d.rows.length - 1)), c: c >= 0 ? c : 0 }));
      } else setSel(ORIGIN);
      setError(null);
    } catch (e) {
      setError(errorText(e, "Couldn't load the sheet."));
    } finally {
      setLoading(false);
    }
  }, [sheet, view, q]);
  useEffect(() => { void load(); }, [load]);

  const rows = useMemo(() => data?.rows ?? [], [data]);
  const cols = useMemo(() => data?.columns ?? [], [data]);
  // Column widths are fitted once per tab from the fullest set of rows seen, so filtering doesn't make them jump.
  const fitted = useRef(new Map<string, { n: number; widths: number[] }>());
  const widths = useMemo(() => {
    const key = `${data?.sheet}|${cols.map((c) => c.field).join(",")}`;
    const had = fitted.current.get(key);
    if (had && had.n >= rows.length) return had.widths;
    const w = columnWidths(cols, rows);
    fitted.current.set(key, { n: rows.length, widths: w });
    return w;
  }, [data?.sheet, cols, rows]);
  const activeSheet = data?.sheet ?? sheet;
  const selRow = rows[sel.r] as SheetRow | undefined;
  const selCol = cols[sel.c] as SheetColumn | undefined;

  useEffect(() => {
    keep.current = data ? { sheet: data.sheet, rowId: selRow?.row_id ?? null, field: selCol?.field ?? "" } : null;
  }, [data, selRow, selCol]);

  const pickTab = (s: string) => {
    if (s === activeSheet) return;
    setParams((p) => { const n = new URLSearchParams(p); n.set("sheet", s); return n; }, { replace: true });
  };

  const open = useCallback((p: Pos) => {
    const row = rows[p.r];
    const col = cols[p.c];
    if (row && col) setPicked({ row, col, cell: cellOf(row, col.field) });
  }, [rows, cols]);

  const copy = useCallback((p: Pos) => {
    const row = rows[p.r];
    const col = cols[p.c];
    if (!row || !col) return;
    const v = displayValue(cellOf(row, col.field), shown, isDateCol(col));
    navigator.clipboard?.writeText(v).then(
      () => toast.show({ message: `Copied ${address(col, row)}` }),
      () => toast.show({ tone: "error", message: "Couldn't copy. Select the text in the formula bar instead." }),
    );
  }, [rows, cols, shown, toast]);

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

  const focusGrid = () => gridRef.current?.focus();
  const filtered = view !== "all" || q !== "";

  let body: ReactNode;
  if (error && !data) {
    body = <div className="sg-message"><ErrorBanner onRetry={() => void load()}>{error}</ErrorBanner></div>;
  } else if (!data) {
    body = <div className="sg-message" aria-busy="true"><Skeleton height={22} /><Skeleton height={22} /><Skeleton height={22} /><Skeleton height={22} /></div>;
  } else if (!rows.length) {
    body = (
      <div className="sg-message">
        <EmptyState title={q ? `No rows match "${q}"` : view === "all" ? "No rows on this sheet" : "No rows with changes here"}
          actions={filtered ? (
            <Button variant="secondary" onClick={() => { setView("all"); setQuery(""); }}>Show all rows</Button>
          ) : undefined} />
      </div>
    );
  } else {
    body = (
      <SheetGrid rows={rows} cols={cols} widths={widths} shown={shown} phone={phone} sel={sel} resetKey={data.sheet}
        label={`${data.sheet} sheet`} gridRef={gridRef} onSelect={setSel} onOpen={open} onCopy={copy}
        onFind={() => { searchRef.current?.focus(); searchRef.current?.select(); }} />
    );
  }

  return (
    <div className="sheetview">
      <div className="sg-toolbar">
        <Segmented label="Rows" items={VIEWS} value={view} onChange={setView} size="sm" />
        <Segmented label="Values" items={VALUES} value={shown} onChange={setShown} size="sm" />
        <SearchInput ref={searchRef} label="Search project or developer" placeholder="Project or developer"
          value={query} onChange={(e) => setQuery(e.target.value)} className="sg-search"
          onKeyDown={(e) => {
            if (e.key === "Enter" || e.key === "ArrowDown") { e.preventDefault(); focusGrid(); }
            if (e.key === "Escape" && query) { e.preventDefault(); setQuery(""); }
          }} />
        {data && <Legend data={data} />}
        {data && (
          <span className="sg-count caption tnum" aria-live="polite">
            {loading ? "Loading…" : filtered
              ? `${data.total.toLocaleString("en-US")} of ${data.counts.rows.toLocaleString("en-US")} rows`
              : `${data.counts.rows.toLocaleString("en-US")} rows`}
          </span>
        )}
      </div>
      {error && data && <div className="sg-banner"><ErrorBanner onRetry={() => void load()}>Couldn't refresh. Showing the last view.</ErrorBanner></div>}
      <FormulaBar row={rows.length ? selRow : undefined} col={rows.length ? selCol : undefined} shown={shown}
        onOpen={() => open(sel)} />
      <div className="sg-area">{body}</div>
      <div className="sg-bottom">
        <div className="sg-tabs" role="tablist" aria-label="Sheets">
          {(data?.sheets ?? []).map((s) => (
            <button key={s} type="button" role="tab" aria-selected={s === activeSheet}
              className={`sg-tab${s === activeSheet ? " is-active" : ""}`} onClick={() => pickTab(s)}>{s}</button>
          ))}
        </div>
        {data && <Legend data={data} />}
      </div>
      <Drawer open={Boolean(picked)} onClose={() => setPicked(null)}
        eyebrow={picked ? `${activeSheet} · ${address(picked.col, picked.row)} · ${picked.row.row_number ? `Row ${picked.row.row_number}` : "New row"}` : ""}
        title={picked ? `${picked.row.cells.project?.master || "Untitled row"} · ${picked.col.label}` : ""}>
        {picked && <Details picked={picked} busy={busy} act={act} />}
      </Drawer>
    </div>
  );
}

function Legend({ data }: { data: SheetData }) {
  return (
    <div className="sg-legend caption" aria-label="Legend">
      <span><i className="sg-key is-approved" /> Approved <b className="tnum">{data.counts.approved_cells}</b></span>
      <span><i className="sg-key is-brain" /> By brain</span>
      <span><i className="sg-key is-pending" /> Open suggestion <b className="tnum">{data.counts.pending_cells}</b></span>
      {data.counts.suggested_new_rows > 0 && (
        <span><i className="sg-key is-newrow" /> New row <b className="tnum">{data.counts.suggested_new_rows}</b></span>
      )}
    </div>
  );
}

/** Address box + the selected cell's full value; for a changed cell: master → current and the open suggestion. */
function FormulaBar({ row, col, shown, onOpen }: { row?: SheetRow; col?: SheetColumn; shown: Shown; onOpen: () => void }) {
  const cell = row && col ? cellOf(row, col.field) : undefined;
  const dateCol = col ? isDateCol(col) : false;
  const fmt = (v: string) => (dateCol ? fmtDateLong(v) : v);
  let content: ReactNode = null;
  if (cell && col && row) {
    const value = rawValue(cell, shown, dateCol);
    const changedTo = shown === "current" ? cell.approved?.new_raw ?? (dateCol ? cell.current : undefined) : undefined;
    content = (
      <>
        {changedTo !== undefined ? (
          <span className="sg-fx-change">
            <span className="sg-fx-old" dir="auto">{fmt(cell.master) || "empty"}</span>
            <span aria-hidden="true">→</span><span className="sr-only">changed to</span>
            <b dir="auto">{fmt(changedTo)}</b>
            {cell.approved?.decided_by === "brain" && <span className="sg-fx-tag">brain</span>}
            {!cell.approved && dateCol && <span className="sg-fx-tag">approval date</span>}
          </span>
        ) : (
          <span className="sg-fx-value" dir="auto">{fmt(value)}</span>
        )}
        {cell.pending && (
          <span className="sg-fx-sugg">
            <span className="sg-fx-tag is-pending">Suggested</span>
            <b dir="auto">{cell.pending.new_raw}</b>
            {cell.pending.count > 1 && <span className="muted tnum">+{cell.pending.count - 1} more</span>}
          </span>
        )}
        {STATE_NOTE[row.state] && !cell.pending && !changedTo && <span className="sg-fx-note">{STATE_NOTE[row.state]}</span>}
      </>
    );
  }
  return (
    <div className="sg-fx" aria-label="Selected cell">
      <div className="sg-fx-addr tnum" aria-label="Cell">{address(col, row) || " "}</div>
      <div className="sg-fx-sym" aria-hidden="true">fx</div>
      <div className="sg-fx-body">{content}</div>
      {cell && hasDetails(cell) && (
        <button type="button" className="sg-fx-open" onClick={onOpen}>Details<span className="sg-kbd" aria-hidden="true">↵</span></button>
      )}
    </div>
  );
}

function Details({ picked, busy, act }: { picked: Picked; busy: boolean; act: (fn: () => Promise<unknown>, done: string) => Promise<void> }) {
  const { cell, row, col } = picked;
  const dateCol = isDateCol(col);
  const fmt = (v: string) => (dateCol ? fmtDateLong(v) : v);
  return (
    <div className="sv-detail">
      <dl className="sv-values">
        <div><dt className="caption">Master (as uploaded)</dt><dd dir="auto">{fmt(cell.master) || "Empty"}</dd></div>
        {cell.approved && <div><dt className="caption">After approvals</dt><dd dir="auto" className="strong">{cell.approved.new_raw}</dd></div>}
        {dateCol && cell.current && <div><dt className="caption">Approval date</dt><dd className="strong tnum">{fmt(cell.current)}</dd></div>}
        {cell.pending && <div><dt className="caption">Suggested</dt><dd dir="auto" className="strong">{cell.pending.new_raw}</dd></div>}
      </dl>
      {!hasDetails(cell) && (
        <p className="small muted">
          {dateCol && cell.current ? "The export writes the approval date on rows with approved changes." : "No approved change or open suggestion on this cell."}
          {STATE_NOTE[row.state] ? ` ${STATE_NOTE[row.state]}.` : ""}
        </p>
      )}
      {cell.approved && (
        <ChangeBlock title={cell.approved.decided_by === "brain" ? "Approved by brain" : "Approved"} ch={cell.approved}>
          {cell.approved.decided_by === "brain" && (
            <Button variant="ghost" size="sm" icon={<RotateCcw size={14} />} disabled={busy}
              onClick={() => void act(() => reopen(cell.approved!.proposal_id), "Reopened: it's back on Updates")}>Reopen</Button>
          )}
        </ChangeBlock>
      )}
      {cell.pending && (
        <ChangeBlock title={`Open suggestion${cell.pending.count > 1 ? ` (newest of ${cell.pending.count})` : ""}`} ch={cell.pending}>
          <Button variant="primary" size="sm" icon={<Check size={14} />} disabled={busy || cell.pending.row_new}
            title={cell.pending.row_new ? "New rows are approved on Updates, where you choose the sheet" : undefined}
            onClick={() => void act(() => decide([cell.pending!.proposal_id], "approve"), "Approved")}>Approve</Button>
          <Button variant="ghost" size="sm" icon={<X size={14} />} disabled={busy}
            onClick={() => void act(() => decide([cell.pending!.proposal_id], "reject"), "Rejected")}>Reject</Button>
          <ButtonLink to={`/updates?q=${encodeURIComponent(row.cells.project?.master ?? "")}`} variant="ghost" size="sm">Open in Updates</ButtonLink>
        </ChangeBlock>
      )}
    </div>
  );
}

function ChangeBlock({ title, ch, children }: { title: string; ch: SheetChange; children?: ReactNode }) {
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
