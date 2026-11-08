import { Download, FileSpreadsheet, RefreshCw, Upload } from "lucide-react";
import { useCallback, useEffect, useRef, useState } from "react";
import { ApiError, errorText } from "../../api/client";
import {
  exportUrl, getMaster, listIssues, resolveIssue, uploadMaster, type IssueAction, type MasterInfo, type MasterIssue, type MasterSummary,
} from "../../api/master";
import { JOB_FINISHED, useJobs } from "../../app/jobs";
import { useSession } from "../../app/session";
import { usePageTitle } from "../../app/title";
import {
  Button, Card, ConfirmDialog, EmptyState, ErrorBanner, Segmented, SkeletonRows, Stat, StatePill, useToast,
} from "../../components";
import { fmtDateTime, fmtNumber, plural } from "../../lib/format";
import { useDelayed, useHotkeys, useLoad } from "../../lib/hooks";
import "./master.css";

const XLSX_HELP = "Upload the .xlsx exported from the team sheet.";

function Dropzone({ disabled, reason, onFile }: { disabled: boolean; reason?: string; onFile: (f: File) => void }) {
  const input = useRef<HTMLInputElement>(null);
  const [over, setOver] = useState(false);
  return (
    <div className={`dropzone${over ? " is-over" : ""}${disabled ? " is-disabled" : ""}`}
      onDragOver={(e) => { e.preventDefault(); if (!disabled) setOver(true); }}
      onDragLeave={() => setOver(false)}
      onDrop={(e) => {
        e.preventDefault();
        setOver(false);
        const f = e.dataTransfer.files?.[0];
        if (f && !disabled) onFile(f);
      }}>
      <Upload size={24} aria-hidden="true" className="dropzone-icon" />
      <p className="small">{disabled ? reason : "Drop the App Updates workbook here, or"}</p>
      <Button variant="primary" disabled={disabled} onClick={() => input.current?.click()}>Choose .xlsx file</Button>
      <input ref={input} type="file" accept=".xlsx,application/vnd.openxmlformats-officedocument.spreadsheetml.sheet" hidden
        onChange={(e) => {
          const f = e.target.files?.[0];
          e.target.value = "";
          if (f) onFile(f);
        }} />
    </div>
  );
}

function SummaryDetails({ s }: { s: MasterSummary }) {
  const unknown = Object.entries(s.unknown_headers ?? {});
  return (
    <ul className="summary-notes small">
      <li><span className="muted">Not read (kept as they are in the export):</span> {s.ignored_sheets.length ? s.ignored_sheets.join(", ") : "none"}</li>
      {unknown.length > 0 && (
        <li><span className="muted">Columns not recognised:</span> {unknown.map(([sh, h]) => `${sh}: ${h.join(", ")}`).join("; ")}</li>
      )}
      {s.legacy_duplicates > 0 && (
        <li>{plural(s.legacy_duplicates, "older copy", "older copies")} on Sokhna&amp;new zayed of a New Cairo row; the New Cairo row is used.</li>
      )}
    </ul>
  );
}

export function MasterScreen() {
  usePageTitle("Master");
  const toast = useToast();
  const { me, refresh } = useSession();
  const { startStep } = useJobs();
  const info = useLoad(getMaster, []);
  const [show, setShow] = useState<"open" | "all">("open");
  const issues = useLoad(() => listIssues(show), [show]);
  const [pendingFile, setPendingFile] = useState<File | null>(null);
  const [uploading, setUploading] = useState(false);
  const [uploadError, setUploadError] = useState<string | null>(null);
  const [result, setResult] = useState<{ summary: MasterSummary; previous: MasterSummary | null; superseded: number } | null>(null);
  const [moveTo, setMoveTo] = useState<Record<number, string>>({});
  const [hidden, setHidden] = useState<Set<number>>(new Set());
  const [focus, setFocus] = useState(0);
  const dropTimers = useRef(new Map<number, number>());

  useEffect(() => {
    const on = () => void info.reload();
    window.addEventListener(JOB_FINISHED, on);
    return () => window.removeEventListener(JOB_FINISHED, on);
  }, [info.reload]); // eslint-disable-line react-hooks/exhaustive-deps

  const m: MasterInfo | undefined = info.data;
  const jobRunning = Boolean(me?.job);
  const openSuggestions = m?.counts.proposed ?? 0;

  const doUpload = async (file: File) => {
    setPendingFile(null);
    setUploading(true);
    setUploadError(null);
    try {
      const r = await uploadMaster(file);
      setResult(r);
      toast.show({ tone: "success", message: `${file.name} is the master now.` });
      await Promise.all([info.reload(), issues.reload(), refresh()]);
    } catch (e) {
      setUploadError(e instanceof ApiError && e.code === "unreadable" ? `Couldn't read that file. ${XLSX_HELP}` : errorText(e));
    } finally {
      setUploading(false);
    }
  };
  const onFile = (file: File) => {
    if (!file.name.toLowerCase().endsWith(".xlsx")) {
      setUploadError(`That isn't an .xlsx file. ${XLSX_HELP}`);
      return;
    }
    if (openSuggestions > 0) setPendingFile(file);
    else void doUpload(file);
  };

  const list = (issues.data ?? []).filter((i) => !hidden.has(i.id));
  const act = useCallback(async (i: MasterIssue, action: IssueAction, sheet?: string) => {
    const apply = async () => {
      try {
        await resolveIssue(i.id, action, sheet);
        if (show === "all") void issues.reload();
        else issues.setData((xs) => xs?.filter((x) => x.id !== i.id));
        void refresh();
        void info.reload();
      } catch (e) {
        setHidden((h) => { const n = new Set(h); n.delete(i.id); return n; });
        toast.show({ tone: "error", message: errorText(e, "Couldn't save that. Try again.") });
      }
    };
    if (show === "open") setHidden((h) => new Set([...h, i.id]));
    if (action !== "drop") {
      await apply();
      if (show === "open") setHidden((h) => { const n = new Set(h); n.delete(i.id); return n; });
      toast.show({ message: action === "move" ? `${i.project} moves to ${sheet}.` : `${i.project} stays on ${i.sheet}.` });
      return;
    }
    // Drop waits 5 s so it can be undone
    const t = window.setTimeout(() => { dropTimers.current.delete(i.id); void apply(); }, 5000);
    dropTimers.current.set(i.id, t);
    toast.show({
      id: `drop-${i.id}`, message: `${i.project} dropped from matching and export.`,
      action: { label: "Undo", onClick: () => {
        window.clearTimeout(t);
        dropTimers.current.delete(i.id);
        setHidden((h) => { const n = new Set(h); n.delete(i.id); return n; });
      } },
    });
  }, [show, issues, info, refresh, toast]);

  useEffect(() => () => {
    // leaving the page commits pending drops
    dropTimers.current.forEach((t, id) => { window.clearTimeout(t); void resolveIssue(id, "drop"); });
  }, []);

  const grid = m?.grid_sheets ?? [];
  const target = (i: MasterIssue) => moveTo[i.id] ?? i.target_sheet ?? grid[0] ?? "";
  const cur = list[Math.min(focus, list.length - 1)];
  useHotkeys({
    j: () => setFocus((f) => Math.min(list.length - 1, f + 1)),
    k: () => setFocus((f) => Math.max(0, f - 1)),
    m: () => cur?.state === "open" && void act(cur, "move", target(cur)),
    h: () => cur?.state === "open" && void act(cur, "keep"),
    d: () => cur?.state === "open" && void act(cur, "drop"),
  }, Boolean(list.length));

  const showSkeleton = useDelayed(info.loading && !m);
  if (!m) {
    return (
      <div className="page">
        {info.error ? <ErrorBanner onRetry={() => void info.reload()}>Couldn't load the master details.</ErrorBanner>
          : showSkeleton ? <div className="card"><SkeletonRows rows={4} /></div> : null}
      </div>
    );
  }

  const uploadCard = (
    <Card eyebrow={m.import ? "Replace" : "Start here"} title={m.import ? "Upload a New Master" : "Upload the App Updates Workbook"}>
      <div className="stack">
        {!m.import && (
          <p className="small muted">
            The app reads {m.sheets.join(", ")}. Other sheets are kept untouched in the export.
          </p>
        )}
        <Dropzone disabled={jobRunning || uploading} onFile={onFile}
          reason={uploading ? "Reading the workbook" : `Wait for ${me?.job?.name ?? "the running job"} to finish.`} />
        {uploadError && <p className="form-error" role="alert">{uploadError}</p>}
        {m.import && (
          <p className="caption muted">
            Upload the latest team file after you've applied an export, so new suggestions compare against it.
            {openSuggestions > 0 && ` Replacing closes the ${fmtNumber(openSuggestions)} open suggestions.`}
          </p>
        )}
      </div>
    </Card>
  );

  return (
    <div className="page master">
      {result && (
        <Card eyebrow="Imported" title="New Master Read" className="master-result"
          actions={<Button variant="primary" icon={<RefreshCw size={16} />} disabled={jobRunning}
            onClick={() => void startStep("chat_routine")}>Sync chats to compare</Button>}>
          <div className="table-wrap">
            <table className="table table-dense">
              <thead><tr><th>Sheet</th><th className="num">Before</th><th className="num">Now</th></tr></thead>
              <tbody>
                {Object.entries(result.summary.sheets).map(([s, n]) => (
                  <tr key={s}>
                    <td>{s}</td>
                    <td className="num tnum">{result.previous ? fmtNumber(result.previous.sheets?.[s] ?? 0) : "—"}</td>
                    <td className="num tnum strong">{fmtNumber(n)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <p className="small muted">
            {result.superseded ? `${plural(result.superseded, "open suggestion")} closed as superseded. ` : ""}
            {result.summary.issues ? `${plural(result.summary.issues, "row")} to place below.` : "No rows to place."}
          </p>
        </Card>
      )}

      {!m.import ? (
        <div className="master-onboarding">
          <Card>
            <EmptyState icon={<FileSpreadsheet size={26} />} title="No master yet">
              Upload the team's App Updates workbook. Every suggestion compares chats with this file, and the downloads write
              approved values back into it.
            </EmptyState>
          </Card>
          {uploadCard}
        </div>
      ) : (
        <div className="master-grid">
          <Card eyebrow="Current master" title={<span dir="auto">{m.import.file_name}</span>}
            actions={<span className="caption muted">Imported {fmtDateTime(m.import.imported_at)}</span>}>
            <div className="stack">
              <div className="stats">
                {Object.entries(m.summary?.sheets ?? {}).map(([s, n]) => <Stat key={s} value={fmtNumber(n)} label={`${s} rows`} />)}
                <Stat value={fmtNumber(m.counts.proposed)} label="Open suggestions" />
                <Stat value={fmtNumber(m.counts.approved)} label="Approved" />
              </div>
              {m.summary && <SummaryDetails s={m.summary} />}
            </div>
          </Card>
          <Card eyebrow="Download" title="Files for the Team">
            <div className="downloads">
              <a className="download" href={exportUrl("team")} download
                onClick={() => toast.show({ message: "Team file download started." })}>
                <Download size={20} aria-hidden="true" className="download-icon" />
                <span>
                  <span className="strong">Team file</span>
                  <span className="small muted">Your master with approved changes in the same cells, payment plans as “10% - 8Y”, new projects at the bottom of their sheet.</span>
                </span>
              </a>
              <a className="download" href={exportUrl("sql")} download
                onClick={() => toast.show({ message: "SQL-ready file download started." })}>
                <Download size={20} aria-hidden="true" className="download-icon" />
                <span>
                  <span className="strong">SQL-ready file</span>
                  <span className="small muted">The same rows split into typed columns (area, price, status, down payment %, years, commission, net) plus one row per project and unit type.</span>
                </span>
              </a>
              {openSuggestions > 0 && (
                <p className="caption muted">{plural(openSuggestions, "open suggestion")} {openSuggestions === 1 ? "is" : "are"} not in these files until approved.</p>
              )}
            </div>
          </Card>
          {uploadCard}
        </div>
      )}

      {m.import && (
        <Card eyebrow="Rows to place" title={show === "open" ? `${fmtNumber(list.length)} Open` : `${fmtNumber(list.length)} Rows, ${fmtNumber(m.rows_to_place)} Open`}
          actions={<Segmented label="Show" size="sm" value={show} onChange={setShow}
            items={[{ key: "open", label: "Open" }, { key: "all", label: "All" }]} />}>
          <p className="small muted place-help">
            Until you decide, these rows are not updated from chats. Move sends updates to the chosen sheet (the project is
            added there in the team file); Keep leaves it where it is; Drop ignores it in matching and export.
          </p>
          {issues.error && <ErrorBanner onRetry={() => void issues.reload()}>Couldn't load rows to place.</ErrorBanner>}
          {list.length === 0 && !issues.loading && <p className="small muted">Nothing to place.</p>}
          <div className="places" role="list">
            {list.map((i, idx) => (
              <div key={i.id} role="listitem" className={`place${idx === focus ? " is-focused" : ""}`} onClick={() => setFocus(idx)}>
                <div className="place-where">
                  <span className="strong">{i.sheet}</span>
                  <span className="caption muted tnum">Row {i.row_number ?? "—"}</span>
                </div>
                <div className="place-what">
                  <span className="strong" dir="auto">{i.project}</span>
                  <span className="caption muted" dir="auto">{i.developer || "No developer"} · Location {i.location_code ? i.location_code.toUpperCase() : "none"}</span>
                </div>
                <div className="place-why small">{i.reason}</div>
                <div className="place-actions" onClick={(e) => e.stopPropagation()}>
                  {i.state === "open" ? (
                    <>
                      <label className="sr-only" htmlFor={`mv-${i.id}`}>Move {i.project} to</label>
                      <select id={`mv-${i.id}`} className="select select-sm" value={target(i)}
                        onChange={(e) => setMoveTo((x) => ({ ...x, [i.id]: e.target.value }))}>
                        {grid.map((s) => <option key={s} value={s}>{s}</option>)}
                      </select>
                      <Button size="sm" variant="secondary" kbd="M" onClick={() => void act(i, "move", target(i))}>Move</Button>
                      <Button size="sm" variant="ghost" kbd="H" onClick={() => void act(i, "keep")}>Keep here</Button>
                      <Button size="sm" variant="ghost" kbd="D" onClick={() => void act(i, "drop")}>Drop</Button>
                    </>
                  ) : <StatePill state={i.state} />}
                </div>
              </div>
            ))}
          </div>
        </Card>
      )}

      <ConfirmDialog open={Boolean(pendingFile)} title="Replace the master?" variant="primary"
        confirmLabel={`Replace master and close ${fmtNumber(openSuggestions)} open ${openSuggestions === 1 ? "suggestion" : "suggestions"}`}
        onCancel={() => setPendingFile(null)} onConfirm={() => pendingFile && void doUpload(pendingFile)}>
        <p>
          <span dir="auto">{pendingFile?.name}</span> becomes the master. The {fmtNumber(openSuggestions)} open suggestions were
          compared with the old file, so they close as superseded. Approved changes stay in history.
        </p>
        <p className="muted">Run the chat steps afterwards to compare the chats with the new file.</p>
      </ConfirmDialog>
    </div>
  );
}
