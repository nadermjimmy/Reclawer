/* Part 2 (Insights): typed stubs for docs/ux-revamp.md §4.6. Backend routes are not built yet. */
import { get } from "./client";

export interface Range {
  from?: string;
  to?: string;
}

export interface InsightsSummary {
  open: number;
  approved: number;
  rejected: number;
  median_decision_s: number | null;
  rows_to_place: number;
  stale_count: number;
}

/** Owner decision: stale = no change for 14 days (last approval, else the master Date cell). */
export const STALE_DAYS_DEFAULT = 14;

export const insightsSummary = (r: Range) => get<InsightsSummary>("/insights/summary", { ...r });
export const insightsChanges = (r: Range & { sheet?: string }) => get<unknown[]>("/insights/changes", { ...r });
export const insightsPriceMoves = (r: Range & { sheet?: string; field?: string }) =>
  get<unknown[]>("/insights/price-moves", { ...r });
export const insightsStale = (days = STALE_DAYS_DEFAULT, sheet?: string) => get<unknown[]>("/insights/stale", { days, sheet });
export const insightsThroughput = (r: Range) => get<unknown[]>("/insights/throughput", { ...r, bucket: "day" });
export const insightsChats = (r: Range) => get<unknown[]>("/insights/chats", { ...r });
