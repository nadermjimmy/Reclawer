import { Check, Download, FileSpreadsheet, RotateCcw, Sparkles, X } from "lucide-react";
import { useCallback, useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { useSearchParams } from "react-router-dom";
import { errorText } from "../../api/client";
import { REJECT_REASONS, decide, reopen } from "../../api/proposals";
import { approveReady, getSheet, type SheetCell, type SheetChange, type SheetColumn, type SheetData, type SheetRow, type SheetView } from "../../api/sheet";
import { Button, ButtonLink, ConfirmDialog, Drawer, EmptyState, ErrorBanner, Pill, SearchInput, Segmented, Skeleton, useToast } from "../../components";
import { useIsPhone } from "../../lib/hooks";
import { fmtDateTime, relTime } from "../../lib/format";
import { useSession } from "../../app/session";
import { usePageTitle } from "../../app/title";
import { SheetGrid } from "./SheetGrid";
import { STATE_NOTE, address, cellOf, columnWidths, displayValue, fmtDateLong, hasDetails, isDateCol, rawValue, type Pos, type Shown } from "./model";
import "./sheet.css";

interface Picked { row: SheetRow; col: SheetColumn; cell: SheetCell }

const READY_VIEW: { key: SheetView; label: string } = { key: "review", label: "Ready to approve" };
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
  const { me } = useSession();
  const [params, setParams] = useSearchParams();
  const sheet = params.get("sheet") ?? "";
  const [view, setView] = useState<SheetView>(me?.gate ? "review" : "all");
  const chosen = useRef(false);
  useEffect(() => {  // the gate's Ready view is the default once we know the gate is on
    if (me?.gate && !chosen.current) setView("review");
  }, [me?.gate]);
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
        <EmptyState title={q ? `No rows match "${q}"` : view === "review" ? "Nothing ready to approve on this sheet"
          : view === "all" ? "No rows on this sheet" : "No rows with changes here"}
          actions={filtered ? (
            <Button variant="secondary" onClick={() => { setView("all"); setQuery(""); }}>Show all rows</Button>
          ) : undefined} />
      </div>
    );
  } else {
    body = (
      <SheetGrid rows={rows} cols={cols} widths={widths} shown={shown} phone={phone} sel={sel} resetKey={data.sheet}
        label={`${data.sheet} sheet`} gridRef={gridRef} onSelect={setSel} onOpen={open} onCopy={copy} showSuggested={view === "review"}
        onFind={() => { searchRef.current?.focus(); searchRef.current?.select(); }} />
    );
  }

  return (
    <div className="sheetview">
      <div className="sg-doc">
        <FileSpreadsheet className="sg-doc-icon" size={28} aria-hidden="true" />
        <div className="sg-doc-text">
          <span className="sg-doc-name" dir="auto">{me?.master?.file_name?.replace(/\.xlsx$/i, "") ?? "Master workbook"}</span>
          <span className="sg-doc-meta">
            {me?.master ? `Uploaded ${fmtDateTime(me.master.imported_at)} · ` : ""}
            {shown === "current" ? "Showing values after approvals" : "Showing values as uploaded"} · read-only
          </span>
        </div>
        {me?.has_master && (
          <a className="sg-doc-btn" href="/api/master/export/team" download>
            <Download size={16} aria-hidden="true" /> Download
          </a>
        )}
      </div>
      <div className="sg-toolbar">
        <Segmented label="Rows" items={me?.gate ? [READY_VIEW, ...VIEWS] : VIEWS} value={view}
          onChange={(v) => { chosen.current = true; setView(v); }} size="sm" />
        <span className="sg-sep" aria-hidden="true" />
        <Segmented label="Values" items={VALUES} value={shown} onChange={setShown} size="sm" />
        <span className="sg-sep" aria-hidden="true" />
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
        {picked && <Details picked={picked} busy={busy} act={act} sheet={activeSheet} />}
      </Drawer>
    </div>
  );
}

function Legend({ data }: { data: SheetData }) {
  return (
    <div className="sg-legend caption" aria-label="Legend">
      <span><i className="sg-key is-approved" /> Approved <b className="tnum">{data.counts.approved_cells}</b></span>
      <span><i className="sg-key is-brain" /> By brain</span>
      {data.gate && <span><i className="sg-key is-ready" /> Ready <b className="tnum">{data.counts.ready_cells ?? 0}</b></span>}
      <span><i className="sg-key is-pending" /> {data.gate ? "Check" : "Open suggestion"} <b className="tnum">
        {data.gate ? data.counts.pending_cells - (data.counts.ready_cells ?? 0) : data.counts.pending_cells}</b></span>
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

function GateInfo({ ch }: { ch: SheetChange }) {
  if (!ch.tier) return null;
  const label = { ready: "Ready", check: "Check", new: "New project", hidden: "Hidden" }[ch.tier];
  const tone = ch.tier === "ready" ? "positive" : ch.tier === "hidden" ? "negative" : "orange";
  return (
    <div className="sv-gate">
      <div className="row">
        <Pill tone={tone} dot>{label}</Pill>
        {ch.p_approve != null && <span className="small">Approval chance <b className="tnum">{Math.round(ch.p_approve * 100)}%</b></span>}
      </div>
      {(ch.gate_reasons ?? []).length > 0 && (
        <ul className="sv-reasons small">{(ch.gate_reasons ?? []).map((r) => <li key={r}>{r}</li>)}</ul>
      )}
      {(ch.gate_signals ?? []).length > 0 && (
        <p className="caption muted">Learned signals: {(ch.gate_signals ?? []).map(([s, v]) => `${v > 0 ? "+" : "−"} ${s}`).join(" · ")}</p>
      )}
    </div>
  );
}

function Details({ picked, busy, act, sheet }: { picked: Picked; busy: boolean; sheet: string;
  act: (fn: () => Promise<unknown>, done: string) => Promise<void> }) {
  const { cell, row, col } = picked;
  const [bulk, setBulk] = useState(false);
  const developer = row.cells.developer?.master ?? "";
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
          <GateInfo ch={cell.pending} />
          <div className="row">
            <Button variant="primary" size="sm" icon={<Check size={14} />} disabled={busy || cell.pending.row_new}
              title={cell.pending.row_new ? "New rows are approved on Updates, where you choose the sheet" : undefined}
              onClick={() => void act(() => decide([cell.pending!.proposal_id], "approve"), "Approved")}>Approve</Button>
            {row.row_id != null && (row.ready ?? 0) > 1 && (
              <Button variant="secondary" size="sm" disabled={busy}
                onClick={() => void act(() => approveReady({ row_id: row.row_id! }), `Approved the row's ${row.ready} ready suggestions`)}>
                Approve Row ({row.ready} Ready)</Button>
            )}
            {developer && cell.pending.tier === "ready" && (
              <Button variant="ghost" size="sm" disabled={busy} onClick={() => setBulk(true)}>Approve All Ready of {developer}</Button>
            )}
          </div>
          <div className="sv-reject" role="group" aria-label="Reject with a reason">
            <span className="caption muted"><X size={12} aria-hidden="true" /> Reject:</span>
            {REJECT_REASONS.map((r) => (
              <Button key={r.key} variant="ghost" size="sm" disabled={busy}
                onClick={() => void act(() => decide([cell.pending!.proposal_id], "reject", undefined, r.key), `Rejected: ${r.label.toLowerCase()}`)}>
                {r.label}</Button>
            ))}
          </div>
          {sheet && developer && (
            <ConfirmDialog open={bulk} title={`Approve every Ready suggestion of ${developer}?`} confirmLabel="Approve All Ready"
              busy={busy} onCancel={() => setBulk(false)}
              onConfirm={() => { setBulk(false); void act(() => approveReady({ developer, sheet }), `Approved ${developer}'s ready suggestions`); }}>
              <p className="small">Only suggestions in the Ready queue on {sheet} are approved. Anything in Check stays for its own look.</p>
            </ConfirmDialog>
          )}
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
