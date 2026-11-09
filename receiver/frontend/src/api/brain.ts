import { get, request } from "./client";

/** Phase 7: More > Brain. Read-only view of models, eval, LLM share, cost and flags. */
export interface ModelInfo {
  current: string | null;
  meta: Record<string, unknown> | null;
  versions: string[];
}
export interface KindMetrics { n: number; precision: number | null; recall: number | null; new_vs_existing_acc?: number | null }
export interface EvalSummary {
  generated_at: number;
  gold: { total: number; by_source_kind: Record<string, number>; by_label: Record<string, number> };
  matching: Record<string, KindMetrics>;
  matching_v2: Record<string, KindMetrics>;
  noise: Record<string, KindMetrics>;
  extraction: { by_source_kind: Record<string, { n: number; accepted: number | null }> };
  auto_approve: { brain_approved: number; reopened_by_person: number; precision_upper_bound: number | null };
  noise_filter: { model: string; threshold: number; false_skip_rate: number | null; offers: number; false_skips: number } | null;
  matcher_model: string;
  shadow_disagreements: number;
  cross_script_misses: number;
  cross_script_misses_v2: number;
}
export interface Flag { name: string; value: string | number | boolean | null; set: boolean }
export interface ModelRun { id: number; ts: number; kind: string; version: string; promoted: number; reason: string; trigger: string; labels: number }
export interface RuleSuggestions {
  file: string;
  generated_at: number;
  not_project_names: { name: string; rejections: number }[];
  locations: { name: string; rejections: number }[];
  blocklist_terms: { term: string; in_rejected: number; cluster_size: number; example: string }[];
}
export interface BrainMetrics {
  models: { matcher: ModelInfo; noise: ModelInfo };
  eval: EvalSummary | null;
  llm: {
    suggestions: number; reviewed_by_brain_llm: number; placed_by_matcher: number; share_reaching_brain_llm: number | null;
    extraction_share_reaching_llm: number | null; noise_skipped_messages: number; extraction_cache_hits: number;
    template_sheets_read: number; usd_last_days: number; usd_per_day: number | null;
  };
  cost_by_day: { day: string; model: string; batch: boolean; calls: number; usd: number }[];
  runs: ModelRun[];
  labels: number;
  retrain: Record<string, string>;
  flags: Flag[];
  rule_suggestions: RuleSuggestions | null;
}
export const getMetrics = (refresh = false) => get<BrainMetrics>("/brain/metrics", { refresh: refresh || undefined });

/** Phase 5: spreadsheet templates ("Confirm Columns"). */
export interface SheetTemplate {
  fingerprint: string;
  header_row: number | null;
  headers: string[];
  developer: string;
  mapping: Record<string, string>;
  confirmed: boolean;
  confirmed_by: string | null;
  roles: string[];
  enabled?: boolean;
}
export const getTemplate = (fileId: number, sheet: string) => get<SheetTemplate>(`/sources/files/${fileId}/template`, { sheet });
export const saveTemplate = (fileId: number, sheet: string, mapping: Record<string, string>) =>
  request<SheetTemplate>(`/sources/files/${fileId}/template`, {
    method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ sheet, mapping }),
  });
