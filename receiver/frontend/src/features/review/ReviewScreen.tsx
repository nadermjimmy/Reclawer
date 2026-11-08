import { useState } from "react";
import { useNavigate, useParams, useSearchParams } from "react-router-dom";
import { errorText } from "../../api/client";
import { fileUrl, reviewFacts, reviewImages, reviewIssues, reviewMappings, saveImage, saveMapping, setFact, type Row } from "../../api/legacy";
import { usePageTitle } from "../../app/title";
import { Button, Card, Chip, EmptyState, ErrorBanner, Pill, Segmented, Select, SkeletonRows, Table, useToast } from "../../components";
import { fmtDateTime, titleCase } from "../../lib/format";
import { useDelayed, useLoad } from "../../lib/hooks";
import "./review.css";

type Tab = "issues" | "mappings" | "images" | "facts";
const s = (v: unknown) => (v === null || v === undefined || v === "" ? "—" : String(v));
const SEV_TONE: Record<string, "negative" | "orange" | "muted"> = { high: "negative", medium: "orange", low: "muted", info: "muted" };

function Issues() {
  const [params, setParams] = useSearchParams();
  const kind = params.get("kind") ?? "";
  const data = useLoad(() => reviewIssues(kind), [kind]);
  const skeleton = useDelayed(data.loading && !data.data);
  return (
    <div className="stack">
      {data.error && <ErrorBanner onRetry={() => void data.reload()}>Couldn't load issues.</ErrorBanner>}
      {data.data && (
        <div className="row">
          <Chip selected={!kind} onClick={() => setParams({}, { replace: true })}>All</Chip>
          {data.data.kinds.map((k) => (
            <Chip key={k.issue_type + k.severity} selected={kind === k.issue_type} count={k.n}
              onClick={() => setParams({ kind: k.issue_type }, { replace: true })}>{titleCase(k.issue_type)}</Chip>
          ))}
        </div>
      )}
      <Card>
        {skeleton && <SkeletonRows rows={6} />}
        {data.data && (data.data.issues.length ? (
          <Table dense label="Issues" head={["Severity", "Type", "Reason"]}>
            {data.data.issues.map((i) => (
              <tr key={String(i.id)}>
                <td><Pill tone={SEV_TONE[String(i.severity)] ?? "muted"}>{titleCase(String(i.severity))}</Pill></td>
                <td className="nowrap">{titleCase(String(i.issue_type))}</td>
                <td dir="auto">{s(i.reason)}</td>
              </tr>
            ))}
          </Table>
        ) : <EmptyState title="No issues">Nothing needs review.</EmptyState>)}
      </Card>
    </div>
  );
}

function Mappings() {
  const [state, setState] = useState("proposed");
  const data = useLoad(() => reviewMappings(state), [state]);
  const toast = useToast();
  const act = async (m: Row, action: "approve" | "reject" | "exclude") => {
    try {
      await saveMapping({ key: String(m.key), action, project_name: String(m.project_name ?? ""), phase_label: String(m.phase_label ?? ""), developer: String(m.developer ?? "") });
      toast.show({ message: action === "approve" ? "Mapping approved." : action === "reject" ? "Mapping rejected." : "Marked outside the market." });
      void data.reload();
    } catch (e) {
      toast.show({ tone: "error", message: errorText(e) });
    }
  };
  return (
    <div className="stack">
      <Select label="Show" value={state} onChange={(e) => setState(e.target.value)}>
        {["proposed", "approved", "rejected", "unmapped", "all"].map((x) => <option key={x} value={x}>{titleCase(x)}</option>)}
      </Select>
      {data.error && <ErrorBanner onRetry={() => void data.reload()}>Couldn't load mappings.</ErrorBanner>}
      <Card>
        {data.data && (data.data.mappings.length ? (
          <Table dense label="Mappings" head={["File / sheet", "Rows", "In the file", "Proposed project", "Why", ""]}>
            {data.data.mappings.map((m) => (
              <tr key={String(m.key)}>
                <td><span dir="auto">{s(m.file_name)}</span><div className="caption muted" dir="auto">{s(m.sheet)}{m.section_label ? ` · ${m.section_label}` : ""}</div></td>
                <td className="tnum">{s(m.row_count)}</td>
                <td dir="auto">{s(m.raw_project)}{m.raw_phase ? <div className="caption muted">{String(m.raw_phase)}</div> : null}</td>
                <td dir="auto"><span className="strong">{s(m.project_name)}</span>{m.phase_label ? <div className="caption muted">{String(m.phase_label)}</div> : null}
                  {m.warning ? <div className="caption warn-text">{String(m.warning)}</div> : null}</td>
                <td className="small muted" dir="auto">{s(m.rationale)}</td>
                <td>
                  <div className="row nowrap-row">
                    <Button size="sm" variant="secondary" onClick={() => void act(m, "approve")}>Approve</Button>
                    <Button size="sm" variant="ghost" onClick={() => void act(m, "reject")}>Reject</Button>
                    <Button size="sm" variant="ghost" onClick={() => void act(m, "exclude")}>Exclude</Button>
                    <a className="small" href={`/review/context?key=${encodeURIComponent(String(m.key))}`}>Edit</a>
                  </div>
                </td>
              </tr>
            ))}
          </Table>
        ) : <EmptyState title="Nothing here">No mappings in this state.</EmptyState>)}
      </Card>
    </div>
  );
}

function Images() {
  const [state, setState] = useState("proposed");
  const data = useLoad(() => reviewImages(state), [state]);
  const toast = useToast();
  const act = async (id: number, action: "approve" | "withhold" | "exclude") => {
    try {
      await saveImage(id, action);
      void data.reload();
    } catch (e) {
      toast.show({ tone: "error", message: errorText(e) });
    }
  };
  return (
    <div className="stack">
      <Select label="Show" value={state} onChange={(e) => setState(e.target.value)}>
        {["proposed", "approved", "withheld", "excluded"].map((x) => <option key={x} value={x}>{titleCase(x)}</option>)}
      </Select>
      {data.error && <ErrorBanner onRetry={() => void data.reload()}>Couldn't load images.</ErrorBanner>}
      {data.data && (data.data.length ? (
        <div className="review-images">
          {data.data.map((i) => (
            <Card key={String(i.file_id)} className="review-image">
              <img src={fileUrl(Number(i.file_id))} alt={titleCase(String(i.classification ?? "image"))} loading="lazy" />
              <div className="small strong">{titleCase(String(i.classification ?? ""))}</div>
              <div className="caption muted" dir="auto">{s(i.project_name)}{i.phase_label ? ` · ${i.phase_label}` : ""}</div>
              <div className="caption muted" dir="auto">{s(i.rationale)}</div>
              <div className="row">
                <Button size="sm" variant="secondary" onClick={() => void act(Number(i.file_id), "approve")}>Approve</Button>
                <Button size="sm" variant="ghost" onClick={() => void act(Number(i.file_id), "withhold")}>Withhold</Button>
                <Button size="sm" variant="ghost" onClick={() => void act(Number(i.file_id), "exclude")}>Exclude</Button>
              </div>
            </Card>
          ))}
        </div>
      ) : <Card><EmptyState title="No images">No images in this state.</EmptyState></Card>)}
    </div>
  );
}

const FACT_STATES = ["verified_from_source", "needs_validation", "source_stated_unverified", "rejected"];

function Facts() {
  const [status, setStatus] = useState("");
  const data = useLoad(() => reviewFacts(status), [status]);
  const toast = useToast();
  return (
    <div className="stack">
      <Select label="Status" value={status} onChange={(e) => setStatus(e.target.value)}>
        <option value="">Any status</option>
        {FACT_STATES.map((x) => <option key={x} value={x}>{titleCase(x)}</option>)}
      </Select>
      {data.error && <ErrorBanner onRetry={() => void data.reload()}>Couldn't load facts.</ErrorBanner>}
      <Card>
        {data.data && (data.data.length ? (
          <Table dense label="Facts" head={["Project", "Fact", "Value", "Seen", "Status"]}>
            {data.data.map((f) => (
              <tr key={String(f.id)}>
                <td dir="auto">{s(f.raw_project_label)}{f.phase_label ? <div className="caption muted">{String(f.phase_label)}</div> : null}</td>
                <td className="nowrap">{titleCase(String(f.fact_type))}</td>
                <td dir="auto">{s(f.parsed_value ?? f.raw_value)}<div className="caption muted">“{s(f.excerpt)}”</div></td>
                <td className="tnum nowrap">{fmtDateTime(Number(f.observed_ts) || null)}</td>
                <td>
                  <select className="select select-sm" aria-label="Status" value={String(f.validation_status)} onChange={async (e) => {
                    try {
                      await setFact(Number(f.id), e.target.value);
                      void data.reload();
                    } catch (err) {
                      toast.show({ tone: "error", message: errorText(err) });
                    }
                  }}>
                    {FACT_STATES.map((x) => <option key={x} value={x}>{titleCase(x)}</option>)}
                  </select>
                </td>
              </tr>
            ))}
          </Table>
        ) : <EmptyState title="No facts">Run step 6, Extract facts, in More &gt; Pipeline.</EmptyState>)}
      </Card>
    </div>
  );
}

export function ReviewScreen() {
  usePageTitle("Review");
  const { tab: t } = useParams();
  const tab: Tab = (["mappings", "images", "facts"] as const).find((x) => x === t) ?? "issues";
  const navigate = useNavigate();
  return (
    <div className="page stack">
      <Segmented label="Review sections" value={tab} onChange={(k) => navigate(k === "issues" ? "/more/review" : `/more/review/${k}`)}
        items={[{ key: "issues", label: "Issues" }, { key: "mappings", label: "Mappings" }, { key: "images", label: "Images" }, { key: "facts", label: "Facts" }]} />
      {tab === "issues" && <Issues />}
      {tab === "mappings" && <Mappings />}
      {tab === "images" && <Images />}
      {tab === "facts" && <Facts />}
    </div>
  );
}
