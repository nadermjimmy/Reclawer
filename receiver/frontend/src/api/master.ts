import { get, post, upload } from "./client";
import type { ProposalState } from "./proposals";
import type { RunningJob } from "./jobs";

export interface MasterSummary {
  sheets: Record<string, number>;
  ignored_sheets: string[];
  unknown_headers: Record<string, string[]>;
  issues: number;
  legacy_duplicates: number;
}

export interface MasterInfo {
  import: { file_name: string; imported_at: number } | null;
  summary: MasterSummary | null;
  counts: Record<ProposalState, number>;
  rows_to_place: number;
  grid_sheets: string[];
  sheets: string[];
  job: RunningJob | null;
}

export interface MasterIssue {
  id: number;
  issue_type: "unknown_location" | "legacy_location" | string;
  reason: string;
  state: "open" | "move" | "keep" | "drop";
  decided_at: number | null;
  sheet: string;
  row_number: number | null;
  developer: string;
  project: string;
  location_code: string;
  target_sheet: string | null;
}

export type IssueAction = "move" | "keep" | "drop";

export const getMaster = () => get<MasterInfo>("/master");
export const uploadMaster = (file: File) =>
  upload<{ summary: MasterSummary; superseded: number; previous: MasterSummary | null }>("/master", file);
export const listIssues = (show: "open" | "all") => get<MasterIssue[]>("/master/issues", { show });
export const resolveIssue = (id: number, action: IssueAction, sheet?: string) =>
  post<{ id: number; state: string }>(`/master/issues/${id}`, { action, sheet });
export const exportUrl = (which: "team" | "sql") => `/api/master/export/${which}`;
