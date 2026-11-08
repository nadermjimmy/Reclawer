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

export interface JobProgress {
  stage: string;
  done: number;
  total: number;
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
  counts: { groups: number; in_scope: number; messages: number; files: number; projects: number; units: number };
  job: RunningJob | null;
}

export const listSteps = () => get<Step[]>("/steps");
export const listJobs = (limit = 8) => get<JobSummary[]>("/jobs", { limit });
export const startJob = (key: string, redo = false) => post<{ job_id: number }>(`/jobs/${key}`, { redo });
export const getJob = (id: number, since = 0) => get<JobDetail>(`/jobs/${id}`, { since });
export const getPipeline = () => get<PipelineInfo>("/pipeline");
export const resetAll = (confirm: string) => post<{ ok: true }>("/reset", { confirm });
