import { get, post } from "./client";

export type JobStatus = "running" | "done" | "failed" | "interrupted";

export interface RunningJob {
  id: number | null;
  name: string;
  status: JobStatus;
  started_at: number | null;
}

export interface JobSummary extends RunningJob {
  id: number;
  finished_at: number | null;
}

/** From the last `PROGRESS {json}` log line. Pipeline steps log {stage: text, done, total}; a chat sync logs
    {stage: n, of: 4, label, done, total} so the Chats stepper knows which of its four stages is active. */
export interface JobProgress {
  stage: string | number;
  done: number;
  total: number;
  of?: number;
  label?: string;
}

export interface JobDetail extends JobSummary {
  log_chunk: string;
  next_offset: number;
  progress: JobProgress | null;
  /** Parsed from a `RESULT {json}` log line (e.g. a chat sync summary), when the job writes one. */
  result: Record<string, unknown> | null;
}

export interface Step {
  key: string;
  title: string;
  description: string;
  redo_label: string | null;
}

export interface PipelineInfo {
  counts: { groups: number; in_scope: number; messages: number; files: number; suggestions: number };
  job: RunningJob | null;
}

export const listSteps = () => get<Step[]>("/steps");
export const listJobs = (limit = 8) => get<JobSummary[]>("/jobs", { limit });
export const startJob = (key: string, redo = false, extra: Record<string, unknown> = {}) =>
  post<{ job_id: number }>(`/jobs/${key}`, { redo, ...extra });
export const getJob = (id: number, since = 0) => get<JobDetail>(`/jobs/${id}`, { since });
export const getPipeline = () => get<PipelineInfo>("/pipeline");
export const resetAll = (confirm: string) => post<{ ok: true }>("/reset", { confirm });
