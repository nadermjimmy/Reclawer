import { get } from "./client";

export interface SheetChange {
  kind: "approved" | "pending";
  proposal_id: number;
  old_raw: string;
  new_raw: string;
  decided_by: string | null;
  decided_at: number | null;
  quote: string;
  source_kind: string;
  source_ts: number | null;
  chat_name: string;
  chat_jid: string | null;
  brain_reason: string | null;
  brain_verdict: string | null;
  warning: string;
  count: number;
  row_new: boolean;
}

export interface SheetCell {
  master: string;
  /** Date column only: the approval date (ISO) the export writes on a row with approved changes. */
  current?: string;
  approved?: SheetChange;
  pending?: SheetChange;
}

export type SheetRowState = "active" | "moved" | "new" | "legacy_duplicate" | "needs_review" | "suggested_new";

export interface SheetColumn {
  field: string;
  label: string;
  /** 1-based column index in the real workbook. */
  col: number;
  /** Column letter in the real workbook ('A', 'B', ...). */
  letter: string;
  proposable: boolean;
}

export interface SheetRow {
  row_id: number | null;
  row_number: number | null;
  state: SheetRowState;
  cells: Record<string, SheetCell>;
  changes: number;
  pending: number;
}

export interface SheetData {
  sheet: string;
  sheets: string[];
  columns: SheetColumn[];
  counts: { rows: number; changed: number; approved_cells: number; pending_cells: number; suggested_new_rows: number };
  total: number;
  offset: number;
  rows: SheetRow[];
  next_offset: number | null;
}

export type SheetView = "all" | "changed" | "pending" | "approved";

/** The whole sheet in one response (the server caps a page at 3000 rows; the biggest tab is ~1,300). */
export const getSheet = (sheet: string, view: SheetView, q: string, offset = 0) =>
  get<SheetData>("/sheet", { sheet, view, q, offset, limit: 3000 });
