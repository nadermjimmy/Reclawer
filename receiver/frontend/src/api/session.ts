import { get, post } from "./client";
import type { RunningJob } from "./jobs";

export interface Market {
  name: string;
  short: string;
  currency: string;
  metric: string;
}

export interface ShellCounts {
  open_suggestions: number;
  rows_to_place: number;
  failed_downloads: number;
  chats_with_suggestions: number;
}

export interface Me {
  market: Market;
  has_master: boolean;
  master: { file_name: string; imported_at: number } | null;
  job: RunningJob | null;
  counts: ShellCounts;
  last_job_finished_at: number | null;
  /** 'team' logins (APP_TEAM_PASSWORD) see the simple app: Sheet and Chats. */
  role: "admin" | "team";
  /** The accuracy gate (More > Brain): Ready / Check / New / Hidden queues and the developer lock. */
  gate: boolean;
}

export const getMe = () => get<Me>("/me");
export const login = (password: string) => post<{ ok: true; role: "admin" | "team" }>("/login", { password });
export const logout = () => post<{ ok: true }>("/logout");
