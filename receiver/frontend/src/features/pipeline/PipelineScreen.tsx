import { Play, Trash2, Zap } from "lucide-react";
import { useEffect, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { errorText } from "../../api/client";
import { getPipeline, listJobs, listSteps, resetAll } from "../../api/jobs";
import { JOB_FINISHED, useJobs } from "../../app/jobs";
import { useSession } from "../../app/session";
import { usePageTitle } from "../../app/title";
import { Button, Card, ConfirmDialog, ErrorBanner, Stat, StatePill, Table, useToast } from "../../components";
import { elapsed, fmtDateTime, fmtNumber } from "../../lib/format";
import { useLoad } from "../../lib/hooks";
import "./pipeline.css";

export function PipelineScreen() {
  usePageTitle("Pipeline");
  const { me, refresh } = useSession();
  const { startStep, openJob } = useJobs();
  const toast = useToast();
  const info = useLoad(getPipeline, []);
  const steps = useLoad(listSteps, []);
  const runs = useLoad(() => listJobs(8), []);
  const [redo, setRedo] = useState<Record<string, boolean>>({});
  const [resetOpen, setResetOpen] = useState(false);
  const [resetting, setResetting] = useState(false);
  const [params, setParams] = useSearchParams();
  const running = me?.job;

  // /more/pipeline?job=<id> (old /jobs/<id> links) opens that log
  useEffect(() => {
    const id = Number(params.get("job"));
    if (id) {
      openJob(id);
      setParams({}, { replace: true });
    }
  }, [params, openJob, setParams]);

  useEffect(() => {
    const on = () => { void info.reload(); void runs.reload(); };
    window.addEventListener(JOB_FINISHED, on);
    return () => window.removeEventListener(JOB_FINISHED, on);
  }, [info.reload, runs.reload]); // eslint-disable-line react-hooks/exhaustive-deps

  const run = async (key: string) => {
    const id = await startStep(key, { redo: redo[key] });
    if (id) void runs.reload();
  };

  const all = steps.data ?? [];
  const routine = all.find((s) => s.key === "chat_routine");
  const c = info.data?.counts;
  const blockedTitle = running ? `${running.name} is running. One job runs at a time.` : undefined;

  return (
    <div className="page pipeline">
      {(info.error || steps.error) && (
        <ErrorBanner onRetry={() => { void info.reload(); void steps.reload(); }}>Couldn't load the pipeline.</ErrorBanner>
      )}
      {c && (
        <div className="stats">
          <Stat value={fmtNumber(c.groups)} label="Chats" />
          <Stat value={fmtNumber(c.in_scope)} label="Inventory chats" />
          <Stat value={fmtNumber(c.messages)} label="Messages" />
          <Stat value={fmtNumber(c.files)} label="Files" />
          <Stat value={fmtNumber(c.suggestions)} label="Open suggestions" />
        </div>
      )}

      {routine && (
        <Card className="routine">
          <div className="routine-body">
            <span className="routine-icon" aria-hidden="true"><Zap size={22} /></span>
            <div className="routine-text">
              <h3>{routine.title}</h3>
              <p className="small muted">{routine.description}</p>
            </div>
            <Button variant="primary" icon={<Play size={16} />} disabled={Boolean(running)} title={blockedTitle}
              onClick={() => void run(routine.key)}>Run chat steps</Button>
          </div>
        </Card>
      )}

      <div className="pipeline-grid">
        <Card eyebrow="Steps" title="Run One Step">
          <p className="small muted steps-help">
            Sync all chats (above) pulls messages and files and reads them. Run these on their own when you need one
            again: each only processes what is new.
          </p>
          <ol className="steps">
            {all.filter((s) => s.key !== "chat_routine").map((s) => (
              <li key={s.key} className="step">
                <div className="step-text">
                  <span className="strong">{s.title}</span>
                  <span className="small muted">{s.description}</span>
                </div>
                <div className="step-actions">
                  {s.redo_label && (
                    <label className="check small">
                      <input type="checkbox" checked={Boolean(redo[s.key])} onChange={(e) => setRedo((r) => ({ ...r, [s.key]: e.target.checked }))} />
                      {s.redo_label}
                    </label>
                  )}
                  <Button size="sm" variant="secondary" icon={<Play size={14} />} disabled={Boolean(running)} title={blockedTitle}
                    aria-label={`Run ${s.title}`} onClick={() => void run(s.key)}>Run</Button>
                </div>
              </li>
            ))}
          </ol>
        </Card>

        <div className="stack">
          <Card eyebrow="History" title="Recent Runs">
            {runs.error && <ErrorBanner onRetry={() => void runs.reload()}>Couldn't load recent runs.</ErrorBanner>}
            {runs.data && runs.data.length === 0 && <p className="small muted">Nothing has run yet.</p>}
            {runs.data && runs.data.length > 0 && (
              <Table head={["Step", "Status", "Started", "Took", ""]} dense label="Recent runs">
                {runs.data.map((j) => (
                  <tr key={j.id} className="is-clickable" onClick={() => openJob(j.id)}>
                    <td>{j.name}</td>
                    <td><StatePill state={j.status} /></td>
                    <td className="tnum nowrap">{fmtDateTime(j.started_at)}</td>
                    <td className="tnum">{j.finished_at ? elapsed(j.started_at, j.finished_at) : "—"}</td>
                    <td><button type="button" className="link-btn" onClick={(e) => { e.stopPropagation(); openJob(j.id); }}>View log</button></td>
                  </tr>
                ))}
              </Table>
            )}
          </Card>

          <Card eyebrow="Danger zone" title="Start Over" className="danger-card">
            <p className="small">
              Deletes every pulled message, downloaded file, suggestion, decision and master upload in this app. WhatsApp and Evolution are not touched.
            </p>
            <div>
              <Button variant="danger" icon={<Trash2 size={16} />} disabled={Boolean(running)} title={blockedTitle}
                onClick={() => setResetOpen(true)}>Delete all app data</Button>
            </div>
          </Card>
        </div>
      </div>

      <ConfirmDialog open={resetOpen} title="Delete all app data?" variant="danger" typeToConfirm="DELETE ALL" busy={resetting}
        confirmLabel="Delete all app data" onCancel={() => setResetOpen(false)}
        onConfirm={async () => {
          setResetting(true);
          try {
            await resetAll("DELETE ALL");
            toast.show({ message: "All app data was deleted. Start again with step 1." });
            setResetOpen(false);
            void info.reload();
            void runs.reload();
            void refresh();
          } catch (e) {
            toast.show({ tone: "error", message: errorText(e, "Couldn't delete the data. Try again.") });
          } finally {
            setResetting(false);
          }
        }}>
        <p>This can't be undone. Messages, files, decisions and the master upload are removed from this app.</p>
      </ConfirmDialog>
    </div>
  );
}
