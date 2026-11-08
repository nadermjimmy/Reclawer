import { useCallback, useEffect, useRef, useState } from "react";
import { ApiError, errorText } from "../../api/client";
import { syncChat, type SyncResult } from "../../api/chats";
import { getJob, type JobDetail } from "../../api/jobs";
import { useJobs } from "../../app/jobs";
import { useSession } from "../../app/session";
import { useToast } from "../../components";
import { usePageVisible } from "../../lib/hooks";

/** Fired when a chat sync job starts (also from a queued "Sync when free"), so an open conversation can watch it. */
export const CHAT_SYNC_STARTED = "receiver:chat-sync-started";

export const syncJobName = (name: string) => `Sync chat: ${name}`;

export interface ChatSyncState {
  /** The sync job of this chat (running or just finished), if any. */
  job: JobDetail | null;
  running: boolean;
  /** 1..4 = active stage, 5 = finished. */
  stage: number;
  result: SyncResult | null;
  /** Another job holds the one-job lock. */
  busyWith: { id: number | null; name: string; started_at: number | null } | null;
  queued: boolean;
  confirming: boolean;
  start: () => void;
  confirm: () => void;
  cancelConfirm: () => void;
  queue: () => void;
  cancelQueue: () => void;
  openLog: () => void;
  dismiss: () => void;
}

/** Sync now for one chat: confirm when it isn't an inventory chat, "Sync when free" while another job runs,
    then poll the job and expose its stage (PROGRESS lines) and summary (RESULT line). */
export function useChatSync(chat: { jid: string; name: string; in_scope: boolean } | null, onFinished: (r: SyncResult | null) => void): ChatSyncState {
  const { me, refresh } = useSession();
  const { queueWhenFree, queued, cancelQueued, openJob } = useJobs();
  const toast = useToast();
  const visible = usePageVisible();
  const [jobId, setJobId] = useState<number | null>(null);
  const [job, setJob] = useState<JobDetail | null>(null);
  const [confirming, setConfirming] = useState(false);
  const finishedRef = useRef(onFinished);
  finishedRef.current = onFinished;
  const jid = chat?.jid ?? null;

  useEffect(() => {
    setJobId(null);
    setJob(null);
    setConfirming(false);
  }, [jid]);

  // a sync of this chat already running (started elsewhere, or before this screen opened)
  const runningJob = me?.job ?? null;
  useEffect(() => {
    if (chat && runningJob?.id && runningJob.name === syncJobName(chat.name) && jobId !== runningJob.id) setJobId(runningJob.id);
  }, [chat, runningJob?.id, runningJob?.name]); // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    const on = (e: Event) => {
      const d = (e as CustomEvent<{ jid: string; jobId: number }>).detail;
      if (d.jid === jid) setJobId(d.jobId);
    };
    window.addEventListener(CHAT_SYNC_STARTED, on);
    return () => window.removeEventListener(CHAT_SYNC_STARTED, on);
  }, [jid]);

  // poll the job while it runs
  useEffect(() => {
    if (jobId === null) return;
    let stop = false;
    let timer = 0;
    const poll = async () => {
      try {
        const j = await getJob(jobId);
        if (stop) return;
        setJob(j);
        if (j.status === "running") timer = window.setTimeout(poll, visible ? 1000 : 5000);
        else {
          void refresh();
          finishedRef.current((j.result as SyncResult | null) ?? null);
        }
      } catch {
        if (!stop) timer = window.setTimeout(poll, 3000);
      }
    };
    void poll();
    return () => {
      stop = true;
      window.clearTimeout(timer);
    };
  }, [jobId, visible]); // eslint-disable-line react-hooks/exhaustive-deps

  const launch = useCallback(async (addToScope: boolean): Promise<number | null> => {
    if (!jid) return null;
    try {
      const { job_id } = await syncChat(jid, addToScope);
      window.dispatchEvent(new CustomEvent(CHAT_SYNC_STARTED, { detail: { jid, jobId: job_id } }));
      void refresh();
      return job_id;
    } catch (e) {
      if (e instanceof ApiError && e.code === "busy") {
        const name = (e.body.job as { name?: string } | null)?.name ?? "Another job";
        toast.show({ tone: "error", message: `${name} is running. Use Sync when free to start after it.` });
        void refresh();
      } else if (e instanceof ApiError && e.code === "not_in_scope") {
        setConfirming(true);
      } else toast.show({ tone: "error", message: errorText(e, "Couldn't start the sync. Try again.") });
      return null;
    }
  }, [jid, refresh, toast]);

  const isQueued = Boolean(queued && jid && queued.label === `Sync ${chat?.name}`);
  const start = useCallback(() => {
    if (!chat) return;
    if (!chat.in_scope) setConfirming(true);
    else void launch(false);
  }, [chat, launch]);
  const confirm = useCallback(() => {
    setConfirming(false);
    if (me?.job) {
      queueWhenFree({ label: `Sync ${chat?.name}`, start: () => launch(true) });
      return;
    }
    void launch(true);
  }, [chat?.name, launch, me?.job, queueWhenFree]);
  const queue = useCallback(() => {
    if (!chat) return;
    if (!chat.in_scope) {
      setConfirming(true); // confirm first; confirm() queues because a job is running
      return;
    }
    queueWhenFree({ label: `Sync ${chat.name}`, start: () => launch(false) });
    toast.show({ message: `${chat.name} will sync when the running job ends.` });
  }, [chat, launch, queueWhenFree, toast]);

  const running = job?.status === "running";
  const p = job?.progress;
  const stage = !job ? 0 : running ? (typeof p?.stage === "number" ? p.stage : 1) : 5;
  const busyWith = runningJob && !(jobId && runningJob.id === jobId) ? runningJob : null;
  return {
    job,
    running,
    stage,
    result: (job?.result as SyncResult | null) ?? null,
    busyWith,
    queued: isQueued,
    confirming,
    start,
    confirm,
    cancelConfirm: () => setConfirming(false),
    queue,
    cancelQueue: cancelQueued,
    openLog: () => job && openJob(job.id),
    dismiss: () => {
      setJobId(null);
      setJob(null);
    },
  };
}
