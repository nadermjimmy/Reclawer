import { CircleCheck, CircleX } from "lucide-react";
import { useNavigate, useParams } from "react-router-dom";
import { fileUrl, getReconciliation, listSources } from "../../api/legacy";
import { useSession } from "../../app/session";
import { usePageTitle } from "../../app/title";
import { Card, EmptyState, ErrorBanner, Pill, Segmented, SkeletonRows, Table } from "../../components";
import { fmtDateTime, fmtNumber, titleCase } from "../../lib/format";
import { useDelayed, useLoad } from "../../lib/hooks";

type Tab = "files" | "failed" | "reconciliation";
const s = (v: unknown) => (v === null || v === undefined || v === "" ? "—" : String(v));

function Reconciliation() {
  const { me } = useSession();
  const data = useLoad(getReconciliation, []);
  const r = data.data?.report;
  const rows: [string, unknown][] = r ? [
    ["In-scope files", r.in_scope_files],
    ["Files by read status", Object.entries((r.files_by_status as Record<string, number>) ?? {}).map(([k, v]) => `${titleCase(k)}: ${v}`).join(", ") || "—"],
    ["Attachments not downloadable", r.attachments_unavailable],
    ["Unit rows in sources", r.unit_rows_in_sources],
    ["Mapped to a project", r.mapped],
    ["Queued for review", r.queued],
    [`Excluded (outside ${me?.market.name ?? "the market"})`, r.excluded_by_scope],
    ["Distinct units (mapped)", r.distinct_units],
    ["Projects", r.projects],
    ["Lowest / highest listed price", `${fmtNumber(r.min_price as number)} / ${fmtNumber(r.max_price as number)}`],
    ["Rows under 1,000,000", `${r.rows_under_1m} (all kept)`],
    ["Prices that aren't plain numbers", `${r.prices_not_plain_numbers} (kept as written)`],
  ] : [];
  return (
    <div className="stack">
      {data.error && <ErrorBanner onRetry={() => void data.reload()}>Couldn't load the reconciliation.</ErrorBanner>}
      {r && (
        <Card title={r.adds_up ? "Adds Up" : "Doesn't Add Up"} eyebrow="Mapped + queued + excluded = rows in sources"
          actions={r.adds_up ? <Pill tone="positive"><CircleCheck size={14} aria-hidden="true" /> Adds up</Pill>
            : <Pill tone="negative"><CircleX size={14} aria-hidden="true" /> Rebuild the inventory</Pill>}>
          <Table dense label="Reconciliation" head={["Check", "Value"]}>
            {rows.map(([k, v]) => <tr key={k}><td>{k}</td><td className="tnum strong">{typeof v === "number" ? fmtNumber(v) : s(v)}</td></tr>)}
          </Table>
        </Card>
      )}
      {data.data && data.data.per_file.length > 0 && (
        <Card title="By File">
          <Table dense label="By file" head={["File", "State", "Rows"]}>
            {data.data.per_file.map((f) => (
              <tr key={`${f.file_id}-${f.mapping_state}`}>
                <td><a href={fileUrl(f.file_id)} target="_blank" rel="noreferrer" dir="auto">{f.file_name}</a></td>
                <td>{titleCase(f.mapping_state)}</td>
                <td className="tnum">{fmtNumber(f.n)}</td>
              </tr>
            ))}
          </Table>
        </Card>
      )}
    </div>
  );
}

export function SourcesScreen() {
  usePageTitle("Sources");
  const { tab: t } = useParams();
  const tab: Tab = t === "failed" || t === "reconciliation" ? t : "files";
  const navigate = useNavigate();
  const data = useLoad(() => (tab === "reconciliation" ? Promise.resolve(undefined) : listSources()), [tab === "reconciliation"]);
  const skeleton = useDelayed(data.loading && !data.data && tab !== "reconciliation");
  return (
    <div className="page stack">
      <Segmented label="Sources sections" value={tab} onChange={(k) => navigate(k === "files" ? "/more/sources" : `/more/sources/${k}`)}
        items={[{ key: "files", label: "Files", count: data.data?.files.length }, { key: "failed", label: "Failed Downloads", count: data.data?.failed.length },
          { key: "reconciliation", label: "Reconciliation" }]} />
      {data.error && <ErrorBanner onRetry={() => void data.reload()}>Couldn't load sources.</ErrorBanner>}
      {tab === "reconciliation" ? <Reconciliation /> : (
        <Card>
          {skeleton && <SkeletonRows rows={6} />}
          {tab === "files" && data.data && (data.data.files.length ? (
            <Table dense label="Files" head={["File", "Kind", "Read", "Posts", "Rows", ""]}>
              {data.data.files.map((f) => (
                <tr key={String(f.id)}>
                  <td dir="auto"><a href={fileUrl(Number(f.id))} target="_blank" rel="noreferrer">{s(f.file_name)}</a>
                    {f.parse_error ? <div className="caption muted">{String(f.parse_error)}</div> : null}</td>
                  <td>{titleCase(String(f.kind ?? ""))}</td>
                  <td><Pill tone={f.parse_status === "parsed" ? "positive" : f.parse_status === "failed" ? "negative" : "muted"}>{titleCase(String(f.parse_status ?? ""))}</Pill></td>
                  <td className="tnum">{fmtNumber(Number(f.posts))}</td>
                  <td className="tnum">{fmtNumber(Number(f.nrows))}</td>
                  <td><a className="small" href={fileUrl(Number(f.id), true)}>Download</a></td>
                </tr>
              ))}
            </Table>
          ) : <EmptyState title="No files yet">Run step 3, Download attachments, in More &gt; Pipeline.</EmptyState>)}
          {tab === "failed" && data.data && (data.data.failed.length ? (
            <>
              <p className="small muted table-note">WhatsApp no longer has these attachments. Ask the chat to send them again.</p>
              <Table dense label="Failed downloads" head={["File", "Chat", "Sent", "Error"]}>
                {data.data.failed.map((f) => (
                  <tr key={String(f.wa_id)}>
                    <td dir="auto">{s(f.file_name)}</td>
                    <td dir="auto">{s(f.chat_name ?? f.group_jid)}</td>
                    <td className="tnum nowrap">{fmtDateTime(Number(f.ts) || null)}</td>
                    <td className="caption muted">{s(f.error).slice(0, 140)}</td>
                  </tr>
                ))}
              </Table>
            </>
          ) : <EmptyState title="No failed downloads">Every attachment was fetched.</EmptyState>)}
        </Card>
      )}
    </div>
  );
}
