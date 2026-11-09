import { RefreshCw } from "lucide-react";
import { useState } from "react";
import { errorText } from "../../api/client";
import { getMetrics, type BrainMetrics, type KindMetrics, type ModelInfo } from "../../api/brain";
import { usePageTitle } from "../../app/title";
import { Button, Card, ErrorBanner, Pill, SkeletonRows, Stat, Table, useToast } from "../../components";
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
