import { memo, useEffect, useLayoutEffect, useMemo, useState, type CSSProperties, type KeyboardEvent, type MouseEvent, type RefObject } from "react";
import type { SheetColumn, SheetRow } from "../../api/sheet";
import { FILL_COL_W, HEAD_H, ROW_H, STATE_NOTE, cellOf, displayValue, hasDetails, isDateCol, jump, letterAt, letterIndex, rawValue, type Pos, type Shown } from "./model";

const OVERSCAN = 14;
const CHUNK = 6;
export const ACTIVE_ID = "sg-active-cell";

interface GridProps {
  rows: SheetRow[];
  cols: SheetColumn[];
  widths: number[];
  shown: Shown;
  phone: boolean;
  sel: Pos;
  label: string;
  /** Scroll back to A1 when this changes (another sheet tab). */
  resetKey: string;
  gridRef: RefObject<HTMLDivElement | null>;
  onSelect: (p: Pos) => void;
  onOpen: (p: Pos) => void;
  onFind: () => void;
  onCopy: (p: Pos) => void;
}

/** A Google-Sheets-like read-only grid: column letters, the header row as row 1, real row numbers, frozen
 * Developer + Project, a selection cursor and keyboard navigation. Rows are virtualized (fixed row height). */
export function SheetGrid({ rows, cols, widths, shown, phone, sel, label, resetKey, gridRef, onSelect, onOpen, onFind, onCopy }: GridProps) {
  const rnW = phone ? 40 : 46;
  const [win, setWin] = useState({ top: 0, h: 800, w: 1200 });
  const R = rows.length;
  const C = cols.length;

  const layout = useMemo(() => {
    const frozen = new Set(phone ? ["project"] : ["developer", "project"]);
    const lefts: (number | null)[] = [];
    const starts: number[] = [];
    let acc = rnW;
    let x = rnW;
    let last = -1;
    cols.forEach((col, i) => {
      starts.push(x);
      x += widths[i];
      if (frozen.has(col.field)) {
        lefts.push(acc);
        acc += widths[i];
        last = i;
      } else lefts.push(null);
    });
    // empty columns after the data, like Sheets (letters continue from the workbook's last column)
    const nFill = Math.max(3, Math.ceil((win.w - x) / FILL_COL_W) + 2);
    const first = cols.length ? letterIndex(cols[cols.length - 1].letter) + 1 : 0;
    const fill = Array.from({ length: nFill }, (_, i) => letterAt(first + i));
    const all = [...widths, ...fill.map(() => FILL_COL_W)];
    return { lefts, starts, last, frozenW: acc, total: x + nFill * FILL_COL_W, fill,
      template: `${rnW}px ${all.map((w) => `${w}px`).join(" ")}` };
  }, [cols, widths, phone, rnW, win.w]);
  const dateCols = useMemo(() => cols.map(isDateCol), [cols]);

  useLayoutEffect(() => {
    const el = gridRef.current;
    if (!el) return;
    const ro = new ResizeObserver(() => setWin((w) => (w.h === el.clientHeight && w.w === el.clientWidth ? w
      : { ...w, h: el.clientHeight, w: el.clientWidth })));
    ro.observe(el);
    return () => ro.disconnect();
  }, [gridRef]);

  useEffect(() => {
    const el = gridRef.current;
    if (!el) return;
    el.scrollTop = 0;
    el.scrollLeft = 0;
  }, [resetKey, gridRef]);

  // Keep the selected cell in view (below the frozen header rows, right of the frozen columns).
  useLayoutEffect(() => {
    const el = gridRef.current;
    if (!el || !R || !C) return;
    const top = sel.r * ROW_H;
    if (top < el.scrollTop) el.scrollTop = top;
    else if (HEAD_H + top + ROW_H > el.scrollTop + el.clientHeight) el.scrollTop = HEAD_H + top + ROW_H - el.clientHeight;
    if (layout.lefts[sel.c] == null) {
      const left = layout.starts[sel.c];
      const right = left + widths[sel.c];
      const frozenW = sel.c > layout.last ? layout.frozenW : rnW;
      if (left < el.scrollLeft + frozenW) el.scrollLeft = Math.max(0, left - frozenW);
      else if (right > el.scrollLeft + el.clientWidth) el.scrollLeft = right - el.clientWidth;
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [sel.r, sel.c]);

  const onScroll = () => {
    const el = gridRef.current;
    if (!el) return;
    const top = Math.floor(el.scrollTop / ROW_H / CHUNK) * CHUNK;
    setWin((w) => (w.top === top ? w : { ...w, top }));
  };

  const empty = (r: number, c: number) => !rawValue(cellOf(rows[r], cols[c].field), shown, dateCols[c]).trim();

  const onKeyDown = (e: KeyboardEvent<HTMLDivElement>) => {
    if (e.altKey || !R || !C) return;
    const mod = e.ctrlKey || e.metaKey;
    const { r, c } = sel;
    const page = Math.max(1, Math.floor(((gridRef.current?.clientHeight ?? 400) - HEAD_H) / ROW_H) - 1);
    let next: Pos;
    switch (e.key) {
      case "ArrowUp": next = mod ? jump(sel, -1, 0, R, C, empty) : { r: r - 1, c }; break;
      case "ArrowDown": next = mod ? jump(sel, 1, 0, R, C, empty) : { r: r + 1, c }; break;
      case "ArrowLeft": next = mod ? jump(sel, 0, -1, R, C, empty) : { r, c: c - 1 }; break;
      case "ArrowRight": next = mod ? jump(sel, 0, 1, R, C, empty) : { r, c: c + 1 }; break;
      case "Tab":
        if (mod || (e.shiftKey ? c === 0 : c === C - 1)) return; // let focus leave the grid at the row's edge
        next = { r, c: c + (e.shiftKey ? -1 : 1) };
        break;
      case "Enter":
        if (!mod && !e.shiftKey && hasDetails(cellOf(rows[r], cols[c].field))) {
          e.preventDefault();
          onOpen(sel);
          return;
        }
        next = { r: r + (e.shiftKey ? -1 : 1), c };
        break;
      case "Home": next = mod ? { r: 0, c: 0 } : { r, c: 0 }; break;
      case "End": next = mod ? { r: R - 1, c: C - 1 } : { r, c: C - 1 }; break;
      case "PageDown": next = { r: r + page, c }; break;
      case "PageUp": next = { r: r - page, c }; break;
      case "f":
      case "F":
        if (!mod) return;
        e.preventDefault();
        onFind();
        return;
      case "c":
      case "C":
        if (!mod || window.getSelection()?.toString()) return;
        e.preventDefault();
        onCopy(sel);
        return;
      default:
        return;
    }
    e.preventDefault();
    next = { r: Math.min(R - 1, Math.max(0, next.r)), c: Math.min(C - 1, Math.max(0, next.c)) };
    if (next.r !== r || next.c !== c) onSelect(next);
  };

  const posOf = (e: MouseEvent): Pos | null => {
    const t = (e.target as HTMLElement).closest<HTMLElement>("[data-c]");
    const rowEl = t?.closest<HTMLElement>("[data-r]");
    if (!t || !rowEl) return null;
    const c = Number(t.dataset.c);
    return { r: Number(rowEl.dataset.r), c: c < 0 ? sel.c : c };
  };

  const start = Math.max(0, win.top - OVERSCAN);
  const end = Math.min(R, win.top + Math.ceil(win.h / ROW_H) + CHUNK + OVERSCAN);
  const visible = rows.slice(start, end);
  const style = { "--sg-cols": layout.template, "--sg-w": `${layout.total}px` } as CSSProperties;
  // empty rows after the data fill the window, numbered on from the last real row
  const fillRows = Math.max(20, Math.ceil(win.h / ROW_H) + 4 - R);
  const lastNo = R ? Math.max(...rows.map((x) => x.row_number ?? 0), R + 1) : 1;

  return (
    <div ref={gridRef} className="sg-scroll" tabIndex={0} role="grid" aria-label={label} aria-readonly="true"
      aria-rowcount={R + 1} aria-colcount={C + 1} aria-activedescendant={R && C ? ACTIVE_ID : undefined}
      onKeyDown={onKeyDown} onScroll={onScroll} style={style}>
      <div className="sg-inner">
        <div className="sg-head" role="rowgroup">
          <div className="sg-letters" aria-hidden="true">
            <div className="sg-corner" />
            {cols.map((col, c) => (
              <div key={col.field} className={`sg-letter${c === sel.c ? " is-hl" : ""}${c === layout.last ? " is-frozen-edge" : ""}`}
                style={layout.lefts[c] != null ? { left: layout.lefts[c]!, position: "sticky", zIndex: 2 } : undefined}>{col.letter}</div>
            ))}
            {layout.fill.map((l) => <div key={l} className="sg-letter">{l}</div>)}
          </div>
          <div className="sg-labels" role="row" aria-rowindex={1}>
            <div className="sg-rn" role="rowheader">1</div>
            {cols.map((col, c) => (
              <div key={col.field} role="columnheader" aria-colindex={c + 2} title={col.label}
                className={`sg-label${c === layout.last ? " is-frozen-edge" : ""}${dateCols[c] ? " is-date" : ""}`}
                style={layout.lefts[c] != null ? { left: layout.lefts[c]!, position: "sticky", zIndex: 2 } : undefined}>
                <span className="sg-txt" dir="auto">{col.label}</span>
              </div>
            ))}
            {layout.fill.map((l) => <div key={l} className="sg-label is-fill" aria-hidden="true" />)}
          </div>
        </div>
        <div className="sg-body" role="rowgroup" style={{ height: (R + fillRows) * ROW_H }}
          onMouseDown={(e) => { if (e.button !== 0) return; const p = posOf(e); if (p) onSelect(p); }}
          onDoubleClick={(e) => { const p = posOf(e); if (p) onOpen(p); }}>
          <div className="sg-window" style={{ top: start * ROW_H }}>
            {visible.map((row, i) => (
              <GridRow key={row.row_id ?? `new-${start + i}`} row={row} r={start + i} cols={cols} dateCols={dateCols}
                lefts={layout.lefts} last={layout.last} shown={shown} selC={sel.r === start + i ? sel.c : -1} fill={layout.fill.length} />
            ))}
          </div>
          <div className="sg-window sg-fill" style={{ top: R * ROW_H }} aria-hidden="true">
            {Array.from({ length: fillRows }, (_, i) => (
              <div key={i} className="sg-row is-fill">
                <div className="sg-rn">{lastNo + 1 + i}</div>
                {cols.map((col, c) => (
                  <div key={col.field} className={`sg-cell${c === layout.last ? " is-frozen-edge" : ""}${layout.lefts[c] != null ? " is-frozen" : ""}`}
                    style={layout.lefts[c] != null ? { left: layout.lefts[c]! } : undefined} />
                ))}
                {layout.fill.map((l) => <div key={l} className="sg-cell" />)}
              </div>
            ))}
          </div>
        </div>
      </div>
    </div>
  );
}

const GridRow = memo(function GridRow({ row, r, cols, dateCols, lefts, last, shown, selC, fill }: {
  row: SheetRow;
  r: number;
  cols: SheetColumn[];
  dateCols: boolean[];
  lefts: (number | null)[];
  last: number;
  shown: Shown;
  selC: number;
  fill: number;
}) {
  const note = STATE_NOTE[row.state];
  const rowCls = row.state === "suggested_new" ? " is-newrow" : row.state === "moved" || row.state === "legacy_duplicate" ? " is-muted" : "";
  return (
    <div className={`sg-row${rowCls}`} role="row" aria-rowindex={r + 2} data-r={r}>
      <div className={`sg-rn${selC >= 0 ? " is-hl" : ""}`} role="rowheader" data-c={-1}
        title={note ? `Row ${row.row_number ?? "(new)"}: ${note}` : undefined}>
        {row.row_number ?? <span className="sg-plus" aria-label="New row">+</span>}
      </div>
      {cols.map((col, c) => {
        const cell = cellOf(row, col.field);
        const dateCol = dateCols[c];
        const text = displayValue(cell, shown, dateCol);
        const approved = shown === "current" && (cell.approved || (dateCol && cell.current));
        let cls = "sg-cell";
        if (approved) cls += cell.approved?.decided_by === "brain" ? " is-brain" : " is-approved";
        if (cell.pending) cls += " is-pending";
        if (dateCol) cls += " is-date";
        if (c === last) cls += " is-frozen-edge";
        if (lefts[c] != null) cls += " is-frozen";
        if (c === selC) cls += " is-sel";
        const tip = cell.pending
          ? `Suggested: ${cell.pending.new_raw}${cell.pending.count > 1 ? ` (newest of ${cell.pending.count})` : ""}`
          : approved && cell.approved ? `Was: ${cell.approved.old_raw || "empty"}` : undefined;
        return (
          <div key={col.field} role="gridcell" className={cls} data-c={c} aria-colindex={c + 2}
            id={c === selC ? ACTIVE_ID : undefined} aria-selected={c === selC} title={tip}
            style={lefts[c] != null ? { left: lefts[c]! } : undefined}>
            {text || !cell.pending
              ? <span className="sg-txt" dir="auto">{text}</span>
              : <span className="sg-txt sg-sugg" dir="auto">{cell.pending.new_raw}</span>}
          </div>
        );
      })}
      {Array.from({ length: fill }, (_, i) => <div key={`f${i}`} className="sg-cell is-fill" />)}
    </div>
  );
});
