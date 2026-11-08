/* Straight JSON ports of the classic More pages (inventory, review, sources). Loosely typed on purpose. */
import { get, post } from "./client";
import type { ContextMessage } from "./proposals";

export type Row = Record<string, unknown> & { id?: number };

export interface ProjectListItem {
  slug: string;
  name: string;
  developer: string | null;
  phases: number;
  units: number;
}

export interface FactSummary {
  type: string;
  state: string;
  value: string | null;
  items: Row[];
}

export interface ProjectDetail {
  project: Row & { name: string; slug: string; developer: string | null };
  key_facts: FactSummary[];
  phase_facts: Row[];
  other_facts: Row[];
  phases: Row[];
  units: Row[];
  images: Row[];
}

export interface ReconciliationReport {
  report: Record<string, unknown> & { adds_up: boolean };
  per_file: { file_name: string; file_id: number; mapping_state: string; n: number }[];
}

export const listProjects = (q = "") => get<ProjectListItem[]>("/projects", { q });
export const getProject = (slug: string) => get<ProjectDetail>(`/projects/${encodeURIComponent(slug)}`);
export const listUnits = (q = "", status = "", max_price = "") =>
  get<{ units: Row[]; total: number }>("/units", { q, status, max_price });
export const getUnit = (id: number) => get<{ unit: Row; snapshots: Row[] }>(`/units/${id}`);
export const setFact = (id: number, status: string) => post<{ id: number; status: string }>(`/facts/${id}`, { status });

export const reviewIssues = (kind = "") =>
  get<{ kinds: { issue_type: string; severity: string; n: number }[]; issues: Row[]; pending_mappings: number; pending_images: number }>(
    "/review/issues",
    { kind },
  );
export const reviewMappings = (state = "proposed") =>
  get<{ mappings: Row[]; known_projects: string[] }>("/review/mappings", { state });
export const saveMapping = (body: { key: string; action: "approve" | "reject" | "exclude"; project_name?: string; phase_label?: string; developer?: string }) =>
  post<{ ok: true }>("/review/mappings", body);
export const reviewImages = (state = "proposed") => get<Row[]>("/review/images", { state });
export const saveImage = (file_id: number, action: "approve" | "withhold" | "exclude") =>
  post<{ ok: true }>("/review/images", { file_id, action });
export const reviewFacts = (status = "") => get<Row[]>("/review/facts", { status });

export const listSources = () => get<{ files: Row[]; failed: Row[] }>("/sources");
export const sourceMessage = (waId: string) =>
  get<{ chat: { jid: string; name: string }; message: ContextMessage; messages: ContextMessage[] }>(
    `/sources/messages/${encodeURIComponent(waId)}`,
  );
export const getReconciliation = () => get<ReconciliationReport>("/reconciliation");

/** Original file as stored (classic route; same session cookie). */
export const fileUrl = (id: number, download = false) => `/sources/file/${id}${download ? "?download=1" : ""}`;
