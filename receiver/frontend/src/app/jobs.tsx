import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { ApiError, errorText } from "../api/client";
import { getJob, startJob as apiStart, type JobDetail } from "../api/jobs";
import { Button, Drawer, Pill, StatePill, useToast } from "../components";
import { elapsed, fmtDateTime } from "../lib/format";
import { usePageVisible, useTick } from "../lib/hooks";
import { useSession } from "./session";

/** A job waiting for the running one to finish ("Sync when free"; one job at a time). */
export interface QueuedJob {
  label: string;
  start: () => Promise<number | null>;
}

interface JobsApi {
  /** Open the log drawer for a job. */
  openJob: (id: number) => void;
  /** Start a pipeline step by key; opens its log. Returns the job id, or null when busy (a toast explains). */
  startStep: (key: string, opts?: { redo?: boolean; title?: string; openLog?: boolean }) => Promise<number | null>;
  /** Run `job.start` as soon as no job is running. Part 2 uses this for "Sync when free" on a chat. */
  queueWhenFree: (job: QueuedJob) => void;
  queued: QueuedJob | null;
  cancelQueued: () => void;
}

const Ctx = createContext<JobsApi | null>(null);

export function useJobs() {
  const c = useContext(Ctx);
  if (!c) throw new Error("useJobs outside JobsProvider");
  return c;
}

/** Fired on window when a job the app watched finishes, so screens can reload ({detail: JobDetail}). */
export const JOB_FINISHED = "receiver:job-finished";

export function JobsProvider({ children }: { children: ReactNode }) {
  const { me, refresh } = useSession();
  const toast = useToast();
  const [openId, setOpenId] = useState<number | null>(null);
  const [queued, setQueued] = useState<QueuedJob | null>(null);
  const lastRunning = useRef<number | null>(null);

  const openJob = useCallback((id: number) => setOpenId(id), []);

  const startStep = useCallback(
    async (key: string, opts: { redo?: boolean; title?: string; openLog?: boolean } = {}) => {
      try {
        const { job_id } = await apiStart(key, opts.redo);
        void refresh();
        if (opts.openLog !== false) setOpenId(job_id);
        return job_id;
      } catch (e) {
        if (e instanceof ApiError && e.code === "busy") {
          const job = e.body.job as { id: number; name: string } | null;
          toast.show({
            tone: "error",
            message: `${job?.name ?? "Another job"} is running. One job runs at a time.`,
            action: job?.id ? { label: "View log", onClick: () => setOpenId(job.id) } : undefined,
          });
        } else toast.show({ tone: "error", message: errorText(e, "Couldn't start that step. Try again.") });
        return null;
      }
    },
    [refresh, toast],
  );

  // Notice jobs that finish (seen through /api/me polling) and run anything queued behind them.
  const runningId = me?.job?.id ?? null;
  useEffect(() => {
    const prev = lastRunning.current;
    lastRunning.current = runningId;
    if (prev && prev !== runningId) {
      void getJob(prev).then((j) => window.dispatchEvent(new CustomEvent(JOB_FINISHED, { detail: j }))).catch(() => undefined);
    }
    if (!me?.job && queued) {
      const q = queued;
      setQueued(null);
      void q.start().then((id) => {
        if (id) {
          toast.show({ message: `${q.label} started`, action: { label: "View log", onClick: () => setOpenId(id) } });
          void refresh();
        }
      });
    }
  }, [runningId, me?.job, queued, refresh, toast]);

  const queueWhenFree = useCallback((job: QueuedJob) => setQueued(job), []);
  const cancelQueued = useCallback(() => setQueued(null), []);

  const api = useMemo(() => ({ openJob, startStep, queueWhenFree, queued, cancelQueued }),
    [openJob, startStep, queueWhenFree, queued, cancelQueued]);
  return (
    <Ctx.Provider value={api}>
      {children}
      <JobDrawer id={openId} onClose={() => setOpenId(null)} onFinished={() => void refresh()} />
    </Ctx.Provider>
  );
}

/** Top-bar pill: the running job (name + elapsed) or a queued one. Opens the log drawer. */
export function JobPill() {
  const { me } = useSession();
  const { openJob, queued } = useJobs();
  const job = me?.job;
  useTick(1000, Boolean(job));
  if (!job && !queued) return null;
  if (!job && queued)
    return (
      <span className="job-pill job-pill-queued" title="Starts when the running job ends">
        <span className="job-pill-dot" aria-hidden="true" />
        <span className="ellipsis">Waiting: {queued.label}</span>
      </span>
    );
  return (
    <button type="button" className="job-pill" onClick={() => job?.id && openJob(job.id)}
      title={`${job!.name} is running. Open the log.`} aria-label={`${job!.name} is running, ${elapsed(job!.started_at)}. Open the log.`}>
      <span className="job-pill-dot" aria-hidden="true" />
      <span className="job-pill-name ellipsis">{job!.name}</span>
      <span className="job-pill-time tnum">{elapsed(job!.started_at)}</span>
    </button>
  );
}

/** Job log drawer: polls every 1 s while running and visible (5 s hidden), appends only new log text. */
function JobDrawer({ id, onClose, onFinished }: { id: number | null; onClose: () => void; onFinished: () => void }) {
  const [job, setJob] = useState<JobDetail | null>(null);
  const [log, setLog] = useState("");
  const [error, setError] = useState<string | null>(null);
  const offset = useRef(0);
  const visible = usePageVisible();
  const logRef = useRef<HTMLDivElement>(null);
  const stick = useRef(true);
  useTick(1000, job?.status === "running");

  useEffect(() => {
    setJob(null);
    setLog("");
    setError(null);
    offset.current = 0;
    stick.current = true;
  }, [id]);

  useEffect(() => {
    if (id === null) return;
    let stop = false;
    let timer = 0;
    const poll = async () => {
      try {
        const j = await getJob(id, offset.current);
        if (stop) return;
        offset.current = j.next_offset;
        setJob(j);
        if (j.log_chunk) setLog((l) => l + j.log_chunk);
        setError(null);
        if (j.status === "running") timer = window.setTimeout(poll, visible ? 1000 : 5000);
        else onFinished();
      } catch (e) {
        if (stop) return;
        setError(errorText(e, "Couldn't load the log. Retrying."));
        timer = window.setTimeout(poll, 5000);
      }
    };
    void poll();
    return () => {
      stop = true;
      window.clearTimeout(timer);
    };
  }, [id, visible]); // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    const el = logRef.current;
    if (el && stick.current) el.scrollTop = el.scrollHeight;
  }, [log]);

  const p = job?.progress;
  const pct = p && p.total ? Math.round((p.done / p.total) * 100) : null;
  return (
    <Drawer open={id !== null} onClose={onClose} width={640} eyebrow={job ? `Job ${job.id}` : "Job"}
      title={job?.name ?? "Loading the log"}
      footer={<Button variant="ghost" onClick={onClose}>Close</Button>}>
      {job && (
        <div className="stack">
          <div className="row">
            <StatePill state={job.status} />
            <span className="small muted">Started {fmtDateTime(job.started_at)}</span>
            <Pill tone="muted"><span className="tnum">{elapsed(job.started_at, job.finished_at)}</span></Pill>
          </div>
          {p && (
            <div className="progress" aria-label="Progress">
              <div className="row small">
                <span className="ellipsis" dir="auto">{p.label ?? p.stage}</span>
                <span className="spacer" />
                <span className="tnum muted">{p.done} / {p.total}</span>
              </div>
              <div className="progress-track" role="progressbar" aria-valuemin={0} aria-valuemax={100} aria-valuenow={pct ?? 0}>
                <div className="progress-fill" style={{ width: `${pct ?? 0}%` }} />
              </div>
            </div>
          )}
          {job.result && (
            <div className="job-result small">
              {Object.entries(job.result).map(([k, v]) => (
                <Pill key={k} tone="navy"><span className="tnum">{String(v)}</span> {k.replace(/_/g, " ")}</Pill>
              ))}
            </div>
          )}
          {error && <div className="banner banner-error">{error}</div>}
          <div className="job-log" ref={logRef} tabIndex={0} aria-label="Job log"
            onScroll={(e) => {
              const el = e.currentTarget;
              stick.current = el.scrollHeight - el.scrollTop - el.clientHeight < 40;
            }}>
            <pre className="log" dir="auto">{log || "No output yet."}</pre>
          </div>
        </div>
      )}
      {!job && error && <div className="banner banner-error">{error}</div>}
    </Drawer>
  );
}
