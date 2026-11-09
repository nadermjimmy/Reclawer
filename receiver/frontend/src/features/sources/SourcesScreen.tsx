import { useNavigate, useParams } from "react-router-dom";
import { fileUrl, listSources } from "../../api/legacy";
import { usePageTitle } from "../../app/title";
import { Card, EmptyState, ErrorBanner, Pill, Segmented, SkeletonRows, Table } from "../../components";
import { fmtDateTime, fmtNumber, titleCase } from "../../lib/format";
import { useDelayed, useLoad } from "../../lib/hooks";

type Tab = "files" | "failed";
const s = (v: unknown) => (v === null || v === undefined || v === "" ? "—" : String(v));

export function SourcesScreen() {
  usePageTitle("Sources");
  const { tab: t } = useParams();
  const tab: Tab = t === "failed" ? t : "files";
  const navigate = useNavigate();
  const data = useLoad(listSources, []);
  const skeleton = useDelayed(data.loading && !data.data);
  return (
    <div className="page stack">
      <Segmented label="Sources sections" value={tab} onChange={(k) => navigate(k === "files" ? "/more/sources" : `/more/sources/${k}`)}
        items={[{ key: "files", label: "Files", count: data.data?.files.length }, { key: "failed", label: "Failed Downloads", count: data.data?.failed.length }]} />
      {data.error && <ErrorBanner onRetry={() => void data.reload()}>Couldn't load sources.</ErrorBanner>}
      {(
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
          ) : <EmptyState title="No files yet">Run Sync all chats in More &gt; Pipeline.</EmptyState>)}
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
