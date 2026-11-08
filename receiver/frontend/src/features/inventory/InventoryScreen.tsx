import { ArrowLeft } from "lucide-react";
import { useEffect, useState } from "react";
import { Link, useNavigate, useParams, useSearchParams } from "react-router-dom";
import { errorText } from "../../api/client";
import { fileUrl, getProject, getUnit, listProjects, listUnits, setFact, type Row } from "../../api/legacy";
import { useSession } from "../../app/session";
import { usePageTitle } from "../../app/title";
import { Card, Drawer, EmptyState, ErrorBanner, Pill, SearchInput, Segmented, Select, SkeletonRows, Table, useToast } from "../../components";
import { fmtDateTime, fmtNumber, label, titleCase } from "../../lib/format";
import { useDelayed, useLoad } from "../../lib/hooks";
import "./inventory.css";

const s = (v: unknown) => (v === null || v === undefined || v === "" ? "—" : String(v));

function areaText(r: Row) {
  try {
    const a = JSON.parse(String(r.areas_json ?? "[]")) as { raw: string; unit?: string; label?: string }[];
    return a.map((x) => `${x.raw}${x.unit ? ` ${x.unit.replace("_", " ")}` : ""}`).join(", ") || "—";
  } catch {
    return "—";
  }
}

const STATUS_TONE: Record<string, "positive" | "negative" | "orange" | "muted"> = {
  available: "positive", sold: "negative", reserved: "orange", under_process: "orange", unknown: "muted",
};

function UnitTable({ units, onOpen, showProject }: { units: Row[]; onOpen: (id: number) => void; showProject?: boolean }) {
  const { me } = useSession();
  const metric = me?.market.metric ?? "sq m";
  return (
    <Table dense label="Units" head={["Unit", ...(showProject ? ["Project"] : []), "Phase", "Type", "Area", "Listed price", `Price / ${metric}`, "Status"]}>
      {units.map((u) => (
        <tr key={String(u.id)} className="is-clickable" onClick={() => onOpen(Number(u.unit_id))}>
          <td className="strong" dir="auto">{s(u.unit_code_raw ?? u.unit_code)}</td>
          {showProject && <td dir="auto">{s(u.project)}</td>}
          <td dir="auto">{s(u.phase_key)}</td>
          <td dir="auto">{s(u.unit_type_raw)}</td>
          <td className="tnum">{areaText(u)}</td>
          <td className="tnum nowrap" dir="auto">{s(u.price_raw)}{u.currency ? ` ${u.currency}` : ""}</td>
          <td className="tnum">{u.price_per_sqft ? fmtNumber(Number(u.price_per_sqft)) : "—"}</td>
          <td><Pill tone={STATUS_TONE[String(u.unit_status)] ?? "muted"}>{titleCase(String(u.unit_status ?? "unknown"))}</Pill></td>
        </tr>
      ))}
    </Table>
  );
}

function UnitDrawer({ id, onClose }: { id: number | null; onClose: () => void }) {
  const unit = useLoad(() => (id ? getUnit(id) : Promise.resolve(undefined)), [id]);
  const u = unit.data?.unit;
  return (
    <Drawer open={id !== null} onClose={onClose} eyebrow="Unit history" title={u ? `${s(u.unit_code)} · ${s(u.project)}` : "Unit"} width={640}>
      {unit.error && <ErrorBanner onRetry={() => void unit.reload()}>Couldn't load this unit.</ErrorBanner>}
      {unit.data && (
        <Table dense label="Observations" head={["Seen", "File", "Price", "Area", "Status"]}>
          {unit.data.snapshots.map((x) => (
            <tr key={String(x.id)}>
              <td className="tnum nowrap">{fmtDateTime(Number(x.snapshot_ts) || null)}</td>
              <td><a href={fileUrl(Number(x.file_id))} target="_blank" rel="noreferrer" dir="auto">{s(x.file_name)}</a>
                <div className="caption muted">{s(x.sheet)}, row {s(x.row_number)}</div></td>
              <td className="tnum" dir="auto">{s(x.price_raw)}</td>
              <td className="tnum">{areaText(x)}</td>
              <td>{titleCase(String(x.unit_status ?? ""))}</td>
            </tr>
          ))}
        </Table>
      )}
    </Drawer>
  );
}

export function InventoryScreen() {
  usePageTitle("Inventory");
  const [params, setParams] = useSearchParams();
  const tab = params.get("tab") === "units" ? "units" : "projects";
  const [q, setQ] = useState(params.get("q") ?? "");
  const [debounced, setDebounced] = useState(q);
  const [status, setStatus] = useState("");
  const [maxPrice, setMaxPrice] = useState("");
  const navigate = useNavigate();
  useEffect(() => {
    const t = window.setTimeout(() => setDebounced(q), 250);
    return () => window.clearTimeout(t);
  }, [q]);
  const projects = useLoad(() => (tab === "projects" ? listProjects(debounced) : Promise.resolve(undefined)), [tab, debounced]);
  const units = useLoad(() => (tab === "units" ? listUnits(debounced, status, maxPrice) : Promise.resolve(undefined)), [tab, debounced, status, maxPrice]);
  const unitId = Number(params.get("unit")) || null;
  const skeleton = useDelayed((tab === "projects" ? projects.loading && !projects.data : units.loading && !units.data));
  const setParam = (k: string, v: string | null) => setParams((p) => { const n = new URLSearchParams(p); if (v) n.set(k, v); else n.delete(k); return n; }, { replace: true });

  return (
    <div className="page stack">
      <div className="row toolbar">
        <Segmented label="Show" value={tab} onChange={(t) => setParam("tab", t === "units" ? "units" : null)}
          items={[{ key: "projects", label: "Projects" }, { key: "units", label: "Units" }]} />
        <SearchInput label={tab === "units" ? "Search units, projects or types" : "Search projects"} placeholder={tab === "units" ? "Search units" : "Search projects"}
          value={q} onChange={(e) => setQ(e.target.value)} className="toolbar-search" />
        {tab === "units" && (
          <>
            <Select label="Status" hideLabel value={status} onChange={(e) => setStatus(e.target.value)}>
              <option value="">Any status</option>
              {["available", "reserved", "sold", "under_process", "unknown"].map((x) => <option key={x} value={x}>{titleCase(x)}</option>)}
            </Select>
            <label className="field field-inline">
              <span className="sr-only">Highest price</span>
              <input className="input price-input" inputMode="numeric" placeholder="Max price" value={maxPrice} onChange={(e) => setMaxPrice(e.target.value)} />
            </label>
          </>
        )}
      </div>
      {(projects.error || units.error) && <ErrorBanner onRetry={() => void (tab === "projects" ? projects.reload() : units.reload())}>Couldn't load the inventory.</ErrorBanner>}
      <Card>
        {skeleton && <SkeletonRows rows={6} />}
        {tab === "projects" && projects.data && (projects.data.length ? (
          <Table label="Projects" head={["Project", "Developer", "Phases", "Units"]}>
            {projects.data.map((p) => (
              <tr key={p.slug} className="is-clickable" onClick={() => navigate(`/more/inventory/${encodeURIComponent(p.slug)}`)}>
                <td><Link to={`/more/inventory/${encodeURIComponent(p.slug)}`} className="strong" dir="auto" onClick={(e) => e.stopPropagation()}>{p.name}</Link></td>
                <td dir="auto">{p.developer || <span className="muted">Not supplied</span>}</td>
                <td className="tnum">{fmtNumber(p.phases)}</td>
                <td className="tnum">{fmtNumber(p.units)}</td>
              </tr>
            ))}
          </Table>
        ) : <EmptyState title="No projects yet">Run step 8, Build inventory, in More &gt; Pipeline.</EmptyState>)}
        {tab === "units" && units.data && (
          <>
            <p className="caption muted table-note">{fmtNumber(units.data.total)} units{units.data.total > 2000 ? ", first 2,000 shown" : ""}. Listed price and area exactly as in the source sheet.</p>
            <UnitTable units={units.data.units} showProject onOpen={(id) => setParam("unit", String(id))} />
          </>
        )}
      </Card>
      <UnitDrawer id={unitId} onClose={() => setParam("unit", null)} />
    </div>
  );
}

const FACT_STATES = ["verified_from_source", "needs_validation", "source_stated_unverified", "rejected"];
const FACT_TONE: Record<string, "positive" | "orange" | "negative" | "muted" | "navy"> = {
  verified_from_source: "positive", needs_validation: "orange", conflict: "negative", not_supplied: "muted", source_stated_unverified: "navy", rejected: "muted",
};

function FactItems({ items, onSet }: { items: Row[]; onSet: (id: number, status: string) => void }) {
  return (
    <ul className="fact-items">
      {items.map((f) => (
        <li key={String(f.id)}>
          <div className="small" dir="auto">“{s(f.excerpt ?? f.raw_value)}”</div>
          <div className="row caption muted">
            <span className="tnum">{fmtDateTime(Number(f.observed_ts) || null)}</span>
            <span>{label(String(f.source_kind ?? ""))}</span>
            <select className="select select-sm" aria-label="Fact status" value={String(f.validation_status)} onChange={(e) => onSet(Number(f.id), e.target.value)}>
              {FACT_STATES.map((x) => <option key={x} value={x}>{titleCase(x)}</option>)}
            </select>
          </div>
        </li>
      ))}
    </ul>
  );
}

export function ProjectScreen() {
  const { slug = "" } = useParams();
  const data = useLoad(() => getProject(slug), [slug]);
  const toast = useToast();
  const [tab, setTab] = useState<"facts" | "phases" | "units" | "images">("facts");
  const [unit, setUnit] = useState<number | null>(null);
  const p = data.data;
  usePageTitle(p?.project.name ?? "Project");
  const onSet = async (id: number, status: string) => {
    try {
      await setFact(id, status);
      toast.show({ message: "Fact status saved." });
      void data.reload();
    } catch (e) {
      toast.show({ tone: "error", message: errorText(e) });
    }
  };
  return (
    <div className="page stack">
      <Link to="/more/inventory" className="back-link small"><ArrowLeft size={14} aria-hidden="true" /> All projects</Link>
      {data.error && <ErrorBanner onRetry={() => void data.reload()}>Couldn't load this project.</ErrorBanner>}
      {p && (
        <>
          <div className="stack-tight">
            <h2 dir="auto">{p.project.name}</h2>
            <p className="small muted">Developer: <span dir="auto">{p.project.developer || "Not supplied"}</span> · {fmtNumber(p.phases.length)} phases or buildings · {fmtNumber(p.units.length)} units</p>
          </div>
          <Segmented label="Project sections" value={tab} onChange={setTab}
            items={[{ key: "facts", label: "Key Facts" }, { key: "phases", label: "Phases", count: p.phases.length },
              { key: "units", label: "Units", count: p.units.length }, { key: "images", label: "Images", count: p.images.length }]} />
          <Card>
            {tab === "facts" && (
              <div className="facts">
                {p.key_facts.map((f) => (
                  <div key={f.type} className="fact">
                    <div className="fact-head">
                      <span className="strong">{titleCase(f.type)}</span>
                      <Pill tone={FACT_TONE[f.state] ?? "muted"}>{f.state === "not_supplied" ? "Not supplied" : f.state === "conflict" ? "Conflict" : titleCase(f.state)}</Pill>
                      {f.value && f.state !== "conflict" && <span className="small" dir="auto">{f.value}</span>}
                    </div>
                    {f.items.length > 0 && <FactItems items={f.items} onSet={(id, st) => void onSet(id, st)} />}
                  </div>
                ))}
                {p.other_facts.length > 0 && (
                  <div className="fact"><div className="fact-head"><span className="strong">Other Statements</span></div>
                    <FactItems items={p.other_facts} onSet={(id, st) => void onSet(id, st)} /></div>
                )}
              </div>
            )}
            {tab === "phases" && (
              <div className="stack">
                {p.phases.length ? <ul className="plain-list">{p.phases.map((ph) => <li key={String(ph.id)} dir="auto">{s(ph.display_label ?? ph.raw_label)}</li>)}</ul>
                  : <p className="small muted">No phase labels in the sources.</p>}
                <div className="eyebrow">Phase status statements</div>
                {p.phase_facts.length ? <FactItems items={p.phase_facts} onSet={(id, st) => void onSet(id, st)} />
                  : <p className="small muted">No phase status stated in the chat. Unit status is per unit and doesn't imply a phase status.</p>}
              </div>
            )}
            {tab === "units" && (p.units.length ? <UnitTable units={p.units} onOpen={setUnit} /> : <p className="small muted">No units.</p>)}
            {tab === "images" && (p.images.length ? (
              <div className="image-grid">
                {p.images.map((i) => (
                  <figure key={String(i.fid)}>
                    <img src={fileUrl(Number(i.fid))} alt={titleCase(String(i.classification ?? "image"))} loading="lazy" />
                    <figcaption className="caption">{titleCase(String(i.classification ?? ""))}{i.phase_label ? ` · ${i.phase_label}` : ""}</figcaption>
                  </figure>
                ))}
              </div>
            ) : <p className="small muted">Image withheld / not source-verified.</p>)}
          </Card>
        </>
      )}
      <UnitDrawer id={unit} onClose={() => setUnit(null)} />
    </div>
  );
}
