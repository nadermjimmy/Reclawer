import type { SheetCell, SheetColumn, SheetRow } from "../../api/sheet";

/** "current" = after approvals (what the export writes), "master" = as uploaded. */
export type Shown = "current" | "master";

export interface Pos { r: number; c: number }

// Google Sheets metrics: 21px rows and headers, 100px default column.
export const ROW_H = 21;
export const LETTER_H = 21;
export const HEADER_H = 21;
export const FILL_COL_W = 100;
export const HEAD_H = LETTER_H + HEADER_H;

const EMPTY: SheetCell = { master: "" };
const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

export const isDateCol = (c: SheetColumn) => c.field === "date" || /^\s*date\s*$/i.test(c.label);

export const cellOf = (row: SheetRow, field: string): SheetCell => row.cells[field] ?? EMPTY;

/** ISO 'YYYY-MM-DD' -> '29-Oct', like the team's sheet. Anything else (e.g. 'Hold') is shown as is. */
export function fmtDate(v: string) {
  const m = /^(\d{4})-(\d{2})-(\d{2})(?:[T ]|$)/.exec(v.trim());
  if (!m || +m[2] < 1 || +m[2] > 12) return v;
  return `${+m[3]}-${MONTHS[+m[2] - 1]}`;
}

/** Full date for the formula bar: '29-Oct-2026'. */
export function fmtDateLong(v: string) {
  const m = /^(\d{4})-(\d{2})-(\d{2})(?:[T ]|$)/.exec(v.trim());
  if (!m || +m[2] < 1 || +m[2] > 12) return v;
  return `${+m[3]}-${MONTHS[+m[2] - 1]}-${m[1]}`;
}

/** The raw value of a cell in the chosen mode. In 'After approvals' the date column carries the approval date. */
export function rawValue(cell: SheetCell, shown: Shown, dateCol: boolean) {
  if (shown === "current") {
    if (dateCol && cell.current) return cell.current;
    if (cell.approved) return cell.approved.new_raw;
  }
  return cell.master;
}

export const displayValue = (cell: SheetCell, shown: Shown, dateCol: boolean) => {
  const v = rawValue(cell, shown, dateCol);
  return dateCol ? fmtDate(v) : v;
};

/** A cell with something to look at in the details drawer. */
export const hasDetails = (cell: SheetCell) => Boolean(cell.pending || cell.approved);

/** Fixed column widths from the content (like Sheets' "fit to data", capped), so they don't jump between modes. */
export function columnWidths(cols: SheetColumn[], rows: SheetRow[]): number[] {
  return cols.map((col) => {
    const dateCol = isDateCol(col);
    if (dateCol) return 76;
    let longest = 0;
    for (const r of rows) {
      const cell = r.cells[col.field];
      if (!cell) continue;
      longest = Math.max(longest, cell.master.length, cell.approved?.new_raw.length ?? 0, cell.pending?.new_raw.length ?? 0);
      if (longest > 40) break;
    }
    const max = col.field === "project" ? 220 : col.field === "developer" ? 170 : 200;
    const fromData = longest * 7.2 + 14;
    const fromLabel = col.label.length * 7.4 + 14;
    return Math.round(Math.min(max, Math.max(60, fromData, Math.min(fromLabel, 150))));
  });
}

/** 'A' -> 0, 'Z' -> 25, 'AA' -> 26. */
export function letterIndex(l: string) {
  return [...l.toUpperCase()].reduce((n, ch) => n * 26 + (ch.charCodeAt(0) - 64), 0) - 1;
}

/** 0 -> 'A', 26 -> 'AA'. */
export function letterAt(i: number) {
  let s = "";
  for (let n = i + 1; n > 0; n = Math.floor((n - 1) / 26)) s = String.fromCharCode(65 + ((n - 1) % 26)) + s;
  return s;
}

/** Ctrl/Cmd+Arrow like Sheets: to the end of the current block of filled cells, else to the next filled cell,
 * else to the edge. */
export function jump(pos: Pos, dr: number, dc: number, rowsN: number, colsN: number, empty: (r: number, c: number) => boolean): Pos {
  const inb = (r: number, c: number) => r >= 0 && r < rowsN && c >= 0 && c < colsN;
  let { r, c } = pos;
  if (!inb(r + dr, c + dc)) return pos;
  if (!empty(r, c) && !empty(r + dr, c + dc)) {
    while (inb(r + dr, c + dc) && !empty(r + dr, c + dc)) { r += dr; c += dc; }
    return { r, c };
  }
  r += dr; c += dc;
  while (empty(r, c) && inb(r + dr, c + dc)) { r += dr; c += dc; }
  return { r, c };
}

/** 'G12' for a real row; 'G+' for a row that isn't in the workbook yet. */
export const address = (col: SheetColumn | undefined, row: SheetRow | undefined) =>
  col && row ? `${col.letter}${row.row_number ?? "+"}` : "";

export const STATE_NOTE: Record<string, string> = {
  moved: "Moved to another sheet",
  new: "New row (added after the upload)",
  legacy_duplicate: "Older copy of a row on another sheet",
  needs_review: "Needs review on Master",
  suggested_new: "Suggested new row (not in the workbook yet)",
};
