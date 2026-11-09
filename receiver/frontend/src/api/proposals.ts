import { get, post } from "./client";

export type ProposalState = "proposed" | "approved" | "rejected" | "superseded";
export type MatchState = "exact" | "alias" | "near" | "new";
/** Structured warning codes (receiver/master.py warnings_of). Add new codes there and in WARNING_META. */
export type WarningCode = "needs_sheet" | "near_match" | "lifts_hold" | "bad_format" | "new_project";
export type SourceKind = "message" | "row" | "pdf_page" | "image";

export interface Delta {
  price_from?: number;
  price_to?: number;
  price_pct?: number;
  area_from?: number;
  area_to?: number;
  status_from?: string | null;
  status_to?: string | null;
}

export interface ProposalSource {
  kind: SourceKind;
  ref: string;
  ts: number | null;
  chat_jid: string | null;
  chat_name: string | null;
  file_name: string | null;
}

export interface BrainNote {
  verdict: "update_existing" | "new_project" | "noise" | "unsure";
  confidence: "high" | "medium" | "low";
  reason: string;
  action: string;
}

export interface Proposal {
  id: number;
  state: ProposalState;
  decided_by?: string | null;
  brain?: BrainNote | null;
  sheet: string;
  row_id: number | null;
  row_number: number | null;
  project: string;
  row_project: string | null;
  developer: string;
  field: string;
  field_label: string;
  old_raw: string;
  new_raw: string;
  new_parsed: Record<string, unknown>;
  delta: Delta | null;
  quote: string;
  source: ProposalSource;
  match_state: MatchState;
  matched_label: string;
  location_code: string;
  warning: string;
  warnings: { code: WarningCode; text: string }[];
  warning_codes: WarningCode[];
  created_at: number | null;
  decided_at: number | null;
}

export interface ProposalList {
  items: Proposal[];
  total: number;
  counts: Record<ProposalState, number>;
  warning_counts: Record<WarningCode, number>;
  next_cursor: string | null;
  has_master: boolean;
  sheets: string[];
}

export interface ProposalFilters {
  state?: ProposalState;
  sheet?: string;
  match?: string;
  q?: string;
  chat?: string;
  warning?: string;
  sort?: "project" | "newest" | "warnings";
  cursor?: string;
  limit?: number;
}

export interface ContextMessage {
  id: string;
  ts: number | null;
  sender: string;
  text: string;
  file_name: string | null;
  file_id: number | null;
  is_source: boolean;
}

export type SourceContext =
  | { kind: "message"; missing: true }
  | {
      kind: "message";
      missing?: false;
      chat: { jid: string; name: string };
      message: ContextMessage;
      messages: ContextMessage[];
    }
  | {
      kind: "row" | "pdf_page" | "image";
      missing?: boolean;
      file: { id: number; name: string; kind: string };
      sheet: string;
      row_number: number;
      section_label: string | null;
      hidden: boolean;
      chats: { jid: string; name: string | null }[];
      cells?: { col: string; header: string; value: string }[];
      page?: number;
      text?: string;
      truncated?: boolean;
      image_url?: string;
    };

export interface ProposalDetail extends Proposal {
  row: { id: number; sheet: string; row_number: number | null; developer: string; project: string; state: string } | null;
  row_values: { field: string; label: string; raw: string }[];
  cell_history: {
    id: number;
    state: ProposalState;
    old_raw: string;
    new_raw: string;
    quote: string;
    source_kind: SourceKind;
    source_ts: number | null;
    decided_at: number | null;
    created_at: number | null;
  }[];
  source_context: SourceContext;
}

export interface DecideResult {
  done: number[];
  skipped: { id: number; reason: string; state: ProposalState | null }[];
  counts: Record<ProposalState, number>;
}

export type DecideAction = "approve" | "reject";

export const listProposals = (f: ProposalFilters) => get<ProposalList>("/proposals", { ...f });
export const getProposal = (id: number) => get<ProposalDetail>(`/proposals/${id}`);
/** Undo a decision the brain made (approved or rejected): back on the open list. */
export const reopen = (id: number) => post<{ ok: true }>(`/proposals/${id}/reopen`, {});
export const BRAIN_CHANGED = "brain:changed";

export type RejectReason = "wrong_project" | "wrong_value" | "not_an_offer" | "duplicate";
export const REJECT_REASONS: { key: RejectReason; label: string }[] = [
  { key: "wrong_project", label: "Wrong project" },
  { key: "wrong_value", label: "Wrong value" },
  { key: "not_an_offer", label: "Not an offer" },
  { key: "duplicate", label: "Duplicate" },
];

export const decide = (ids: number[], action: DecideAction, sheetById?: Record<number, string>, reason?: RejectReason) =>
  post<DecideResult>("/proposals/decide", { ids, action, sheet_by_id: sheetById ?? {}, reason });
/** Why a person rejected these: the label the learning layer trains on. */
export const setRejectReason = (ids: number[], reason: RejectReason) => post<{ updated: number }>("/proposals/reason", { ids, reason });
