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
  approved?: SheetChange;
  pending?: SheetChange;
}

export interface SheetRow {
  row_id: number | null;
  row_number: number | null;
  state: string;
  cells: Record<string, SheetCell>;
  changes: number;
  pending: number;
}

export interface SheetData {
  sheet: string;
  sheets: string[];
  columns: { field: string; label: string; col: number; proposable: boolean }[];
  counts: { rows: number; changed: number; approved_cells: number; pending_cells: number; suggested_new_rows: number };
  total: number;
  offset: number;
  rows: SheetRow[];
  next_offset: number | null;
}

export type SheetView = "all" | "changed" | "pending" | "approved";

export const getSheet = (sheet: string, view: SheetView, q: string, offset = 0) =>
  get<SheetData>("/sheet", { sheet, view, q, offset, limit: 300 });
