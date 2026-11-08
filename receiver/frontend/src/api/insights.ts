/* Insights (docs/ux-revamp.md §3.3, §4.6): one endpoint, every section computed on its own. */
import { get } from "./client";
import type { MasterIssue } from "./master";

/** Owner decision: stale = no change for 14 days (last approval, else the master Date cell). */
export const STALE_DAYS_DEFAULT = 14;

export interface InsightsSummary {
  open: number;
  approved: number;
  rejected: number;
  median_decision_s: number | null;
  rows_to_place: number;
  stale_count: number;
}

export interface ChangeItem {
  id: number;
  project: string;
  sheet: string;
  field_label: string;
  old_raw: string;
  new_raw: string;
  decided_at: number;
  chat_jid: string | null;
  chat_name: string | null;
}

export interface Changes {
  total: number;
  by_sheet_field: { sheet: string; field: string; field_label: string; count: number }[];
  recent: ChangeItem[];
}

export interface UnitSide {
  area: number | null;
  price: number | null;
  status: string | null;
}

export interface PriceMove {
  id: number;
  project: string;
  developer: string;
  sheet: string;
  field: string;
  unit_type: string;
  old_raw: string;
  new_raw: string;
  old: UnitSide;
  new: UnitSide;
  pct: number | null;
  decided_at: number;
  chat_jid: string | null;
  chat_name: string | null;
}

export interface StaleRow {
  row_id: number;
  project: string;
  developer: string;
  sheet: string;
  row_number: number | null;
  last_update: string | null;
  basis: "approval" | "master_date" | "never";
  days: number | null;
  hold: string | null;
  chat_jid: string | null;
  chat_name: string | null;
}

export interface ThroughputDay {
  day: string;
  created: number;
  approved: number;
  rejected: number;
}

export interface ChatActivity {
  total_messages: number;
  active_chats: number;
  top: { jid: string; name: string; messages: number; open_suggestions: number; in_scope: boolean; last_sync_at: number | null }[];
}

export interface MissingData {
  developer: string;
  stale_count: number;
  shares: string;
  on_request: string[];
  chats: { jid: string; name: string; last_message_ts: number | null }[];
  projects: { project: string; sheet: string; row_number: number | null; last_update: string | null; days: number | null }[];
  reps: { name: string; phone: string; wa_link: string; project: string }[];
  message: string;
}

export interface InsightSections {
  missing_data: MissingData[];
  summary: InsightsSummary;
  changes: Changes;
  price_moves: PriceMove[];
  stale: { days: number; count: number; rows: StaleRow[] };
  throughput: ThroughputDay[];
  rows_to_place: { count: number; first: MasterIssue[] };
  chats: ChatActivity;
}

export type SectionKey = keyof InsightSections;

export interface Insights {
  days: number;
  from: number;
  to: number;
  has_master: boolean;
  sections: Partial<InsightSections>;
  errors: Partial<Record<SectionKey, string>>;
  exports: { team: string; sql: string; open_suggestions: number };
}

export const getInsights = (days: number, sections?: SectionKey[]) =>
  get<Insights>("/insights", { days, sections: sections?.join(",") });
