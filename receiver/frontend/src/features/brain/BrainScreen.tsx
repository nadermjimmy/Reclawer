import { RefreshCw } from "lucide-react";
import { useState } from "react";
import { errorText } from "../../api/client";
import { getAssignmentAudit, getMetrics, setGate, type BrainMetrics, type GateBlock, type KindMetrics, type ModelInfo } from "../../api/brain";
import { useSession } from "../../app/session";
import { usePageTitle } from "../../app/title";
import { Button, Card, ConfirmDialog, ErrorBanner, Pill, SkeletonRows, Stat, Table, useToast } from "../../components";
import { fmtDateTime, fmtNumber } from "../../lib/format";
import { useLoad } from "../../lib/hooks";
import "./brain.css";

const KINDS = ["message", "row", "pdf_page", "image", "all"];
const KIND_LABEL: Record<string, string> = { message: "Text", row: "Sheets", pdf_page: "PDFs", image: "Images", all: "All" };
const pct = (v: number | null | undefined) => (v === null || v === undefined ? "–" : `${(v * 100).toFixed(1)}%`);

function flagValue(v: string | number | boolean | null) {
  if (v === null) return "off";
  if (typeof v === "boolean") return v ? "on" : "off";
  return String(v);
}

function ModelCard({ title, info, metaKeys }: { title: string; info: ModelInfo; metaKeys: [string, string][] }) {
  const meta = (info.meta ?? {}) as Record<string, unknown>;
  return (
    <Card title={title} eyebrow="Model" actions={info.current ? <Pill tone="positive" dot>Promoted</Pill> : <Pill tone="muted">Not trained</Pill>}>
      {info.current ? (
        <dl className="brain-dl">
          <div><dt className="caption">Version</dt><dd className="mono small">{info.current}</dd></div>
          {metaKeys.map(([k, label]) => (
            <div key={k}><dt className="caption">{label}</dt><dd className="tnum">{
              typeof meta[k] === "number" && (meta[k] as number) <= 1 && k.includes("precision") ? pct(meta[k] as number)
                : typeof meta[k] === "number" && k === "trained_at" ? fmtDateTime(meta[k] as number)
                : meta[k] === undefined || meta[k] === null ? "–" : typeof meta[k] === "object" ? JSON.stringify(meta[k]) : String(meta[k])
            }</dd></div>
          ))}
        </dl>
      ) : (
        <p className="small muted">Uses its built-in rules until enough person decisions exist to train it (Pipeline: Retrain).</p>
      )}
    </Card>
  );
}

const TIER_LABEL: Record<string, string> = { ready: "Ready", check: "Check", new: "New projects", hidden: "Hidden",
  unsorted: "Not sorted yet" };

function GateCard({ g, onChanged }: { g: GateBlock; onChanged: () => void }) {
  const toast = useToast();
  const { refresh } = useSession();
  const [confirm, setConfirm] = useState(false);
  const [busy, setBusy] = useState(false);
  const toggle = async () => {
    setBusy(true);
    try {
      const r = await setGate(!g.enabled);
      toast.show({ tone: "success", message: r.enabled
        ? (r.job_id ? "Accuracy gate on. Re-matching the open suggestions now (see Pipeline)." : "Accuracy gate on.")
        : "Accuracy gate off: the team sees every suggestion again." });
      setConfirm(false);
      await refresh();
      onChanged();
    } catch (e) {
      toast.show({ tone: "error", message: errorText(e) });
    } finally {
      setBusy(false);
    }
  };
  return (
    <Card title="Accuracy Gate" eyebrow="What the team sees"
      actions={<Button variant={g.enabled ? "secondary" : "primary"} size="sm" disabled={busy} onClick={() => setConfirm(true)}>
        {g.enabled ? "Turn Off" : "Turn On"}</Button>}>
      <p className="small muted brain-note">
        {g.enabled ? "On. " : "Off: the team sees every suggestion. "}
        With it on, a group's suggestions stay with its own developer, the Sheet opens on Ready suggestions only, and UAE,
        wrong-developer and already-applied ones are hidden. The queues below are always computed so you can compare first.
      </p>
      <div className="stats">
        {["ready", "check", "new", "hidden", "unsorted"].filter((k) => g.tiers[k]).map((k) => (
          <Stat key={k} value={fmtNumber(g.tiers[k])} label={TIER_LABEL[k]} />
        ))}
        <Stat value={g.ready_approval_rate == null ? "–" : pct(g.ready_approval_rate)} label={`Ready approved (${fmtNumber(g.ready_decided)} decided)`} />
        <Stat value={`${fmtNumber(g.catalog.companies)} / ${fmtNumber(g.catalog.projects)}`} label="Catalog developers / projects" />
      </div>
      {g.catalog.companies === 0 && <p className="small">Upload the Byit companies and projects CSVs on the Master page first.</p>}
      <ConfirmDialog open={confirm} title={g.enabled ? "Turn the accuracy gate off?" : "Turn the accuracy gate on?"}
        confirmLabel={g.enabled ? "Turn Off" : "Turn On"} busy={busy} onCancel={() => setConfirm(false)} onConfirm={() => void toggle()}>
        <p className="small">{g.enabled ? "The team will see every suggestion again, and matching goes back to the old rules."
          : "The open suggestions are re-matched with the developer lock (a few minutes, no Claude cost), then sorted. The team's Sheet opens on Ready."}</p>
      </ConfirmDialog>
    </Card>
  );
}

function LearningCard({ g, model }: { g: GateBlock; model: ModelInfo }) {
  const meta = (model.meta ?? {}) as { model?: string; labels?: number; approved?: number; trained_at?: number;
    held_out?: { n: number; ready_threshold_99?: number | null; precision_at?: Record<string, [number | null, number]> } | null };
  return (
    <Card title="What the Brain Learned" eyebrow="Approval model and lessons">
      <p className="small muted brain-note">
        The approval model weighs about 45 signals per suggestion (match, developer, catalog, price, source, age, the
        history of the group, developer and column) and is retrained nightly from the team's decisions. Lessons are
        patterns in those decisions, weighted by how strong the evidence is.
      </p>
      {model.current ? (
        <p className="small">Model <span className="mono">{model.current}</span>: {meta.model}, trained on {fmtNumber(meta.labels ?? 0)} decisions
          ({fmtNumber(meta.approved ?? 0)} approved){meta.trained_at ? `, ${fmtDateTime(meta.trained_at)}` : ""}.
          {meta.held_out?.ready_threshold_99 != null && <> 99% of held-back suggestions above {pct(meta.held_out.ready_threshold_99)} were approved.</>}</p>
      ) : (
        <p className="small">No approval model yet: it starts once the team has made about 30 decisions (at least 5 approvals and 5
          rejections). Until then the Ready queue is decided by the rules alone.</p>
      )}
      {g.lessons.length ? (
        <ul className="brain-list small">
          {g.lessons.map((l, i) => (
            <li key={i}><Pill tone={l.outcome === "rejected" ? "negative" : "positive"}>{l.outcome === "rejected" ? "Watch" : "Reliable"}</Pill>{" "}
              <span dir="auto">{l.text}</span> <span className="muted">(weight {l.weight.toFixed(2)})</span></li>
          ))}
        </ul>
      ) : <p className="small muted">No lessons yet: they need at least 5 decisions with the same pattern.</p>}
    </Card>
  );
}

function AssignmentAudit({ n }: { n: number }) {
  const data = useLoad(getAssignmentAudit, [n]);
  const list = data.data?.doubtful ?? [];
  return (
    <Card title="Group Assignments to Check" eyebrow={`${fmtNumber(list.length)} doubtful`}>
      {list.length ? (
        <Table label="Doubtful assignments" head={["Group", "Assigned developer", "Why"]} dense>
          {list.map((a) => (
            <tr key={a.jid}><td dir="auto">{a.group}</td><td dir="auto">{a.developer}</td><td className="small">{a.why.join("; ")}</td></tr>
          ))}
        </Table>
      ) : <p className="small muted">Every assigned group matches the catalog and its name.</p>}
    </Card>
  );
}

function MatchRows({ m }: { m: Record<string, KindMetrics> }) {
  return (
    <>
      {KINDS.map((k) => (
        <td key={k} className="tnum">{pct(m[k]?.precision)} / {pct(m[k]?.recall)}</td>
      ))}
    </>
  );
}

export function BrainScreen() {
  usePageTitle("Brain");
  const toast = useToast();
  const [busy, setBusy] = useState(false);
  const data = useLoad(() => getMetrics(false), []);
  const refresh = async () => {
    setBusy(true);
    try {
      const fresh = await getMetrics(true);
      data.setData(() => fresh);
    } catch (e) {
      toast.show({ tone: "error", message: `Couldn't re-run the eval. ${errorText(e)}` });
    } finally {
      setBusy(false);
    }
  };
  const d: BrainMetrics | null = data.data ?? null;
  const ev = d?.eval;
  return (
    <div className="page brain">
      {data.error && <ErrorBanner onRetry={() => void data.reload()}>Couldn't load the brain metrics.</ErrorBanner>}
      {!d && !data.error && <SkeletonRows rows={10} />}
      {d && (
        <>
          <div className="stats">
            <Stat value={fmtNumber(d.labels)} label="Person labels" />
            <Stat value={pct(d.llm.share_reaching_brain_llm)} label="Suggestions reaching the brain LLM" />
            <Stat value={fmtNumber(d.llm.placed_by_matcher)} label="Placed by the matcher" />
            <Stat value={`$${d.llm.usd_last_days.toFixed(2)}`} label="API cost, last 7 days" />
          </div>

          <GateCard g={d.gate} onChanged={() => void data.reload()} />
          <LearningCard g={d.gate} model={d.models.acceptance} />
          {d.gate.doubtful_assignments > 0 && <AssignmentAudit n={d.gate.doubtful_assignments} />}

          <Card title="Eval per source kind" eyebrow={ev ? `Gold: ${fmtNumber(ev.gold.total)} decisions · ${fmtDateTime(ev.generated_at)}` : "Not run yet"}
            actions={<Button size="sm" icon={<RefreshCw size={14} />} disabled={busy} onClick={() => void refresh()}>
              {busy ? "Running Eval…" : "Re-run Eval"}</Button>}>
            {ev ? (
              <>
                <Table label="Match precision / recall" head={["Matcher", ...KINDS.map((k) => KIND_LABEL[k])]} dense>
                  <tr><th scope="row">Legacy (in use unless MATCHER says otherwise)</th><MatchRows m={ev.matching} /></tr>
                  <tr><th scope="row">Unified ({ev.matcher_model})</th><MatchRows m={ev.matching_v2} /></tr>
                </Table>
                <p className="small muted brain-note">
                  Precision / recall of the row each suggestion was matched to. Cross-script misses: legacy {ev.cross_script_misses},
                  unified {ev.cross_script_misses_v2}. Shadow disagreements logged: {ev.shadow_disagreements}.
                  Brain auto-approve precision at most {pct(ev.auto_approve.precision_upper_bound)} ({ev.auto_approve.brain_approved} approved,{" "}
                  {ev.auto_approve.reopened_by_person} reopened).
                  {ev.noise_filter && <> Noise filter false skips: {pct(ev.noise_filter.false_skip_rate)} of {ev.noise_filter.offers} gold offers at {ev.noise_filter.threshold} (must stay under 1% before NOISE_SKIP is set).</>}
                </p>
              </>
            ) : (
              <p className="small muted">Run the eval to see match precision and recall per source kind. It makes no Claude calls.</p>
            )}
          </Card>

          <div className="brain-grid">
            <ModelCard title="Matcher" info={d.models.matcher} metaKeys={[["trained_at", "Trained"], ["model", "Kind"], ["labels", "Labelled items"],
              ["test_top1_precision", "Held-out precision"], ["current_test_top1_precision", "Previous model, same items"], ["suggested", "Suggested thresholds"]]} />
            <ModelCard title="Noise filter" info={d.models.noise} metaKeys={[["trained_at", "Trained"], ["labels", "Labelled messages"],
              ["offers", "Offers"], ["noise", "Noise"]]} />
          </div>

          <Card title="What reaches an LLM" eyebrow="Extraction and brain">
            <ul className="brain-list small">
              <li>Extraction: {d.llm.extraction_share_reaching_llm !== null ? `${pct(d.llm.extraction_share_reaching_llm)} of new sources go to Claude`
                : `${fmtNumber(d.llm.noise_skipped_messages)} messages skipped as noise, ${fmtNumber(d.llm.extraction_cache_hits)} cache hits, ${fmtNumber(d.llm.template_sheets_read)} sheets read from a template`}</li>
              <li>Brain: {fmtNumber(d.llm.reviewed_by_brain_llm)} of {fmtNumber(d.llm.suggestions)} suggestions reviewed by an LLM; {fmtNumber(d.llm.placed_by_matcher)} placed by the matcher alone</li>
            </ul>
            {d.cost_by_day.length > 0 && (
              <Table label="Cost by day" head={["Day", "Model", "Batch", "Calls", "USD"]} dense>
                {d.cost_by_day.map((r, i) => (
                  <tr key={i}><td>{r.day}</td><td className="mono small">{r.model}</td><td>{r.batch ? "Yes" : "No"}</td>
                    <td className="tnum">{fmtNumber(r.calls)}</td><td className="tnum">${r.usd.toFixed(2)}</td></tr>
                ))}
              </Table>
            )}
          </Card>

          <Card title="Flags" eyebrow="Environment variables on the receiver service (Railway)">
            <Table label="Flags" head={["Flag", "Value", "Source"]} dense>
              {d.flags.map((f) => (
                <tr key={f.name}><td className="mono small">{f.name}</td><td className="mono small">{flagValue(f.value)}</td>
                  <td>{f.set ? <Pill tone="navy">Set</Pill> : <span className="small muted">Default</span>}</td></tr>
              ))}
            </Table>
          </Card>

          <Card title="Retrain runs" eyebrow={d.retrain.retrain_at ? `Last: ${fmtDateTime(Number(d.retrain.retrain_at))}` : "Nightly when 25+ new labels"}>
            {d.runs.length ? (
              <Table label="Retrain runs" head={["When", "Model", "Version", "Result", "Why", "Trigger"]} dense>
                {d.runs.map((r) => (
                  <tr key={r.id}><td>{fmtDateTime(r.ts)}</td><td>{r.kind}</td><td className="mono small">{r.version}</td>
                    <td>{r.promoted ? <Pill tone="positive">Promoted</Pill> : <Pill tone="muted">Kept current</Pill>}</td>
                    <td className="small">{r.reason}</td><td className="small">{r.trigger}</td></tr>
                ))}
              </Table>
            ) : <p className="small muted">No retrain yet.</p>}
          </Card>

          <Card title="Rule suggestions" eyebrow="Read-only: copy what you agree with into owner_rules_eg.json">
            {d.rule_suggestions ? (
              <div className="brain-rules small">
                <p className="muted">{d.rule_suggestions.file}</p>
                <h4>Not a project</h4>
                <p>{d.rule_suggestions.not_project_names.map((x) => `${x.name} (${x.rejections})`).join(", ") || "None"}</p>
                <h4>Locations</h4>
                <p>{d.rule_suggestions.locations.map((x) => x.name).join(", ") || "None"}</p>
                <h4>Blocklist terms</h4>
                <p>{d.rule_suggestions.blocklist_terms.map((x) => `${x.term} (${x.in_rejected}/${x.cluster_size})`).join(", ") || "None"}</p>
              </div>
            ) : <p className="small muted">None yet. They are built weekly from rejections with a reason.</p>}
          </Card>
        </>
      )}
    </div>
  );
}
