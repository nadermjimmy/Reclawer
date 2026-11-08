import { ArrowDown, ArrowRight, ArrowUp, Copy, Download, FileSpreadsheet, MessageSquare, Phone, RefreshCw } from "lucide-react";
import { useCallback, useEffect, useRef, useState, type ReactNode } from "react";
import { useNavigate } from "react-router-dom";
import { errorText } from "../../api/client";
import { getInsights, type Insights, type InsightSections, type MissingData, type PriceMove, type SectionKey, type ThroughputDay } from "../../api/insights";
import { useSession } from "../../app/session";
import { usePageTitle } from "../../app/title";
import { Button, ButtonLink, Card, EmptyState, ErrorBanner, InfoBanner, Segmented, Skeleton, Stat, useToast } from "../../components";
import { fmtDate, fmtNumber, fmtPct, plural, relTime } from "../../lib/format";
import { useDelayed } from "../../lib/hooks";
import "./insights.css";

const RANGES = [7, 30, 90] as const;
type Range = (typeof RANGES)[number];

function duration(s: number | null | undefined) {
  if (s === null || s === undefined) return "—";
  if (s < 3600) return `${Math.max(1, Math.round(s / 60))} min`;
  if (s < 86400 * 2) return `${(s / 3600).toFixed(s < 36000 ? 1 : 0)} h`;
  return `${Math.round(s / 86400)} days`;
}

/** One card per section: its own skeleton (after 300 ms), error with Retry, or content. */
function Section<K extends SectionKey>({ k, data, title, eyebrow, actions, className, children, onRetry }: {
  k: K;
  data: Insights | null;
  title: ReactNode;
  eyebrow?: ReactNode;
  actions?: ReactNode;
  className?: string;
  children: (v: InsightSections[K]) => ReactNode;
  onRetry: (k: SectionKey) => void;
}) {
  const v = data?.sections[k] as InsightSections[K] | undefined;
  const err = data?.errors[k];
  const skeleton = useDelayed(!v && !err, 300);
  return (
    <Card title={title} eyebrow={eyebrow} actions={actions} className={`ins-card ${className ?? ""}`}>
      {err ? (
        <ErrorBanner onRetry={() => onRetry(k)}>Couldn't work this out just now.</ErrorBanner>
      ) : v !== undefined ? (
        children(v)
      ) : skeleton ? (
        <div className="stack" aria-busy="true">
          <Skeleton width="60%" />
          <Skeleton height={56} radius={12} />
          <Skeleton width="40%" />
        </div>
      ) : null}
    </Card>
  );
}

function Move({ m, metric, currency }: { m: PriceMove; metric: string; currency: string }) {
  const Icon = m.pct === null ? ArrowRight : m.pct > 0 ? ArrowUp : m.pct < 0 ? ArrowDown : ArrowRight;
  const side = (s: PriceMove["old"], raw: string) =>
    s.price ? (
      <>
        <span className="tnum">{fmtNumber(s.price)} {currency}</span>
        {s.area ? <span className="caption muted tnum"> · {fmtNumber(s.area)} {metric}</span> : null}
      </>
    ) : (
      <span dir="auto">{raw || "Empty"}</span>
    );
  return (
    <tr>
      <td>
        <div className="strong" dir="auto">{m.project}</div>
        <div className="caption muted">{m.unit_type} · {m.sheet}</div>
      </td>
      <td className="ins-old" data-label="Old">{side(m.old, m.old_raw)}</td>
      <td data-label="New">{side(m.new, m.new_raw)}</td>
      <td className="num">
        <span className="ins-delta">
          <Icon size={14} aria-hidden="true" />
          <span className="tnum">{m.pct === null ? (m.new.status || m.old.status || "Status") : fmtPct(m.pct)}</span>
        </span>
      </td>
      <td className="caption muted tnum">{relTime(m.decided_at)}</td>
    </tr>
  );
}

const SERIES = [
  { key: "created", label: "Suggested" },
  { key: "approved", label: "Approved" },
  { key: "rejected", label: "Rejected" },
] as const;
type SeriesKey = (typeof SERIES)[number]["key"];

/** Bars drawn to scale from zero (inline SVG): one orange series, navy baseline, every bar labelled. */
function ThroughputChart({ days }: { days: ThroughputDay[] }) {
  const [series, setSeries] = useState<SeriesKey>("created");
  const [table, setTable] = useState(false);
  const wrap = useRef<HTMLDivElement>(null);
  const [W, setW] = useState(600);
  useEffect(() => {
    const el = wrap.current;
    if (!el) return;
    const ro = new ResizeObserver(([e]) => setW(Math.max(200, Math.floor(e.contentRect.width))));
    ro.observe(el);
    return () => ro.disconnect();
  }, [table]);
  // one bar per day when each gets 18px or more, else one bar per week, so every bar can carry its value
  const weekly = W / Math.max(1, days.length) < 18;
  const bars: ThroughputDay[] = [];
  for (let i = 0; i < days.length; i += weekly ? 7 : 1) {
    const chunk = days.slice(i, i + (weekly ? 7 : 1));
    bars.push({ day: chunk[0].day, created: 0, approved: 0, rejected: 0 });
    chunk.forEach((d) => SERIES.forEach((x) => (bars[bars.length - 1][x.key] += d[x.key])));
  }
  const values = bars.map((d) => d[series]);
  const max = Math.max(1, ...values);
  const n = bars.length;
  const H = 180;
  const top = 18;
  const bottom = 22;
  const gap = n > 14 ? 3 : 8;
  const bw = Math.max(2, (W - gap * (n - 1)) / n);
  const labelEvery = Math.max(1, Math.ceil(52 / (bw + gap)));
  const total = values.reduce((a, b) => a + b, 0);
  return (
    <div className="stack">
      <div className="row">
        <Segmented size="sm" label="Series" value={series} onChange={setSeries} items={SERIES.map((s) => ({ key: s.key, label: s.label }))} />
        <span className="spacer" />
        <Button size="sm" variant="ghost" onClick={() => setTable((t) => !t)}>{table ? "Show chart" : "Show table"}</Button>
      </div>
      {table ? (
        <div className="table-wrap">
          <table className="table table-dense">
            <thead><tr><th>Day</th>{SERIES.map((s) => <th key={s.key} className="num">{s.label}</th>)}</tr></thead>
            <tbody>
              {days.map((d) => (
                <tr key={d.day}><td className="tnum">{d.day}</td>{SERIES.map((s) => <td key={s.key} className="num tnum">{d[s.key]}</td>)}</tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : (
        <div ref={wrap} className="ins-chart-wrap">
        <svg className="ins-chart" width={W} height={H} viewBox={`0 0 ${W} ${H}`} role="img"
          aria-label={`${SERIES.find((s) => s.key === series)?.label} per ${weekly ? "week" : "day"}: ${total} in total`}>
          {bars.map((d, i) => {
            const v = d[series];
            const h = ((H - top - bottom) * v) / max;
            const x = i * (bw + gap);
            const y = H - bottom - h;
            return (
              <g key={d.day}>
                <title>{`${d.day}: ${v}`}</title>
                {v > 0 && <rect x={x} y={y} width={bw} height={h} rx={Math.min(3, bw / 3)} className="ins-rect" />}
                {(
                  <text x={x + bw / 2} y={y - 4} textAnchor="middle" className="ins-bar-label">{v}</text>
                )}
                {i % labelEvery === 0 && (
                  <text x={x + bw / 2} y={H - 6} textAnchor="middle" className="ins-axis-label">
                    {new Date(`${d.day}T00:00:00`).toLocaleDateString("en-GB", weekly || n > 10 ? { day: "numeric", month: "short" } : { weekday: "short", day: "numeric" })}
                  </text>
                )}
              </g>
            );
          })}
          <line x1={0} x2={W} y1={H - bottom} y2={H - bottom} className="ins-baseline" />
        </svg>
        {weekly && <p className="caption muted">One bar per week, starting on the date shown.</p>}
        </div>
      )}
    </div>
  );
}

export function InsightsScreen() {
  usePageTitle("Insights");
  const { me } = useSession();
  const navigate = useNavigate();
  const [range, setRange] = useState<Range>(7);
  const [data, setData] = useState<Insights | null>(null);
  const [error, setError] = useState<string | null>(null);
  const seq = useRef(0);
  const metric = me?.market.metric ?? "sq m";
  const currency = me?.market.currency ?? "EGP";

  const load = useCallback(async () => {
    const my = ++seq.current;
    setData(null);
    setError(null);
    try {
      const d = await getInsights(range);
      if (my === seq.current) setData(d);
    } catch (e) {
      if (my === seq.current) setError(errorText(e, "Couldn't load insights."));
    }
  }, [range]);
  useEffect(() => void load(), [load]);

  const retry = useCallback(async (k: SectionKey) => {
    setData((d) => d && { ...d, errors: { ...d.errors, [k]: undefined } });
    try {
      const d = await getInsights(range, [k]);
      setData((cur) => cur && { ...cur, sections: { ...cur.sections, ...d.sections }, errors: { ...cur.errors, [k]: d.errors[k] } });
    } catch (e) {
      setData((cur) => cur && { ...cur, errors: { ...cur.errors, [k]: errorText(e) } });
    }
  }, [range]);

  const s = data?.sections.summary;
  const openN = data?.exports.open_suggestions ?? me?.counts.open_suggestions ?? 0;
  const hasMaster = data ? data.has_master : me?.has_master;
  const tilesSkeleton = useDelayed(!s && !data?.errors.summary, 300);

  return (
    <div className="page insights">
      <div className="ins-bar">
        <Segmented label="Range" value={String(range)} onChange={(v) => setRange(Number(v) as Range)}
          items={RANGES.map((r) => ({ key: String(r), label: `${r} days` }))} />
        <span className="spacer" />
        {hasMaster && (
          <div className="ins-downloads">
            <a className="btn btn-secondary btn-md btn-link" href="/api/master/export/team" download>
              <span className="btn-icon" aria-hidden="true"><Download size={16} /></span><span className="btn-label">Download team file</span>
            </a>
            <a className="btn btn-ghost btn-md btn-link" href="/api/master/export/sql" download>
              <span className="btn-icon" aria-hidden="true"><Download size={16} /></span><span className="btn-label">Download SQL file</span>
            </a>
          </div>
        )}
      </div>
      {hasMaster && openN > 0 && (
        <InfoBanner action={<ButtonLink to="/updates" size="sm" variant="ghost">Review in Updates</ButtonLink>}>
          {plural(openN, "open suggestion")} {openN === 1 ? "isn't" : "aren't"} in the downloads yet. They're added once approved.
        </InfoBanner>
      )}
      {hasMaster === false && (
        <InfoBanner action={<ButtonLink to="/master" size="sm" variant="primary">Upload master</ButtonLink>}>
          Upload the App Updates workbook to see changes, price moves and stale projects. Chat activity works without it.
        </InfoBanner>
      )}
      {error && <ErrorBanner onRetry={() => void load()}>{error}</ErrorBanner>}

      {data?.errors.summary ? (
        <ErrorBanner onRetry={() => void retry("summary")}>Couldn't work out the totals just now.</ErrorBanner>
      ) : (
        <div className="ins-tiles">
          {s ? (
            <>
              <Stat value={fmtNumber(s.open)} label="Open suggestions" />
              <Stat value={fmtNumber(s.approved)} label={`Approved in ${range} days`} />
              <Stat value={fmtNumber(s.rejected)} label={`Rejected in ${range} days`} />
              <Stat value={duration(s.median_decision_s)} label="Median time to decide" />
              <Stat value={fmtNumber(s.rows_to_place)} label="Rows to place" />
              <Stat value={fmtNumber(s.stale_count)} label="Stale projects (14 days)" />
            </>
          ) : tilesSkeleton ? (
            Array.from({ length: 6 }, (_, i) => <div className="stat" key={i}><Skeleton width="50%" height={24} /><Skeleton width="70%" /></div>)
          ) : null}
        </div>
      )}

      <div className="ins-grid">
        <Section k="missing_data" data={data} eyebrow="Group active, prices quiet for 14 days"
          title={data?.sections.missing_data ? `Data Missing · ${fmtNumber(data.sections.missing_data.length)}` : "Data Missing"}
          onRetry={retry} className="ins-wide">
          {(list) => list.length ? (
            <ul className="ins-missing">
              {list.slice(0, 15).map((m) => <MissingRow key={m.developer} m={m} onOpen={(jid) => navigate(`/chats/${encodeURIComponent(jid)}`)} />)}
              {list.length > 15 && <li className="caption muted">{fmtNumber(list.length - 15)} more developers not shown.</li>}
            </ul>
          ) : <EmptyState title="Nothing missing">Every active developer group sent prices in the last 14 days.</EmptyState>}
        </Section>

        <Section k="price_moves" data={data} eyebrow="Approved unit prices" title="Price Moves" onRetry={retry} className="ins-wide">
          {(rows) => rows.length ? (
            <div className="table-wrap">
              <table className="table table-dense ins-moves">
                <thead><tr><th>Project · unit type</th><th>Old</th><th>New</th><th className="num">Change</th><th>Approved</th></tr></thead>
                <tbody>{rows.slice(0, 50).map((m) => <Move key={m.id} m={m} metric={metric} currency={currency} />)}</tbody>
              </table>
            </div>
          ) : <EmptyState title="No price changes approved in this period" />}
        </Section>

        <Section k="changes" data={data} eyebrow="Approved" title="What Changed" onRetry={retry}>
          {(c) => c.total ? (
            <div className="stack">
              <ul className="ins-list">
                {c.by_sheet_field.slice(0, 8).map((g) => (
                  <li key={`${g.sheet}|${g.field}`}>
                    <span className="ellipsis">{g.field_label} <span className="muted">· {g.sheet || "No sheet"}</span></span>
                    <span className="tnum strong">{g.count}</span>
                  </li>
                ))}
              </ul>
              <div className="eyebrow">Latest</div>
              <ul className="ins-list ins-recent">
                {c.recent.slice(0, 6).map((r) => (
                  <li key={r.id}>
                    <span className="ins-recent-main">
                      <span className="strong" dir="auto">{r.project}</span>
                      <span className="caption muted"> · {r.field_label}: </span>
                      <span className="caption" dir="auto">{r.old_raw || "Empty"} → <b>{r.new_raw}</b></span>
                    </span>
                    <span className="caption muted tnum">{relTime(r.decided_at)}</span>
                  </li>
                ))}
              </ul>
              <ButtonLink to="/updates?state=approved" variant="ghost" size="sm">See all approved</ButtonLink>
            </div>
          ) : <EmptyState title="No approvals in this period" />}
        </Section>

        <Section k="throughput" data={data} eyebrow="Per day" title="Suggestion Throughput" onRetry={retry}>
          {(days) => <ThroughputChart days={days} />}
        </Section>

        <Section k="stale" data={data} eyebrow="No change for 14 days" title={data?.sections.stale ? `Stale Projects · ${fmtNumber(data.sections.stale.count)}` : "Stale Projects"}
          onRetry={retry} className="ins-wide">
          {(st) => st.rows.length ? (
            <ul className="ins-stale">
              {st.rows.slice(0, 12).map((r) => (
                <li key={r.row_id}>
                  <div className="ins-stale-main">
                    <span className="strong" dir="auto">{r.project}</span>
                    <span className="caption muted">
                      <span dir="auto">{r.developer || "Unknown developer"}</span> · {r.sheet}
                      {r.hold ? ` · ${r.hold.charAt(0).toUpperCase()}${r.hold.slice(1)}` : ""}
                    </span>
                    <span className="caption tnum">
                      {r.last_update ? `Last change ${fmtDate(new Date(`${r.last_update}T00:00:00`).getTime() / 1000)} (${r.days} days)` : "No date on the sheet and no approval yet"}
                    </span>
                  </div>
                  <div className="ins-stale-actions">
                    {r.chat_jid ? (
                      <>
                        <Button size="sm" variant="ghost" icon={<MessageSquare size={14} />} onClick={() => navigate(`/chats/${encodeURIComponent(r.chat_jid!)}`)}>
                          Open chat
                        </Button>
                        <Button size="sm" variant="secondary" icon={<RefreshCw size={14} />} onClick={() => navigate(`/chats/${encodeURIComponent(r.chat_jid!)}?sync=1`)}>
                          Sync now
                        </Button>
                      </>
                    ) : <span className="caption muted">No chat linked yet</span>}
                  </div>
                </li>
              ))}
              {st.count > 12 && <li className="caption muted">{fmtNumber(st.count - 12)} more not shown.</li>}
            </ul>
          ) : <EmptyState title="Nothing stale">Every project changed in the last 14 days.</EmptyState>}
        </Section>

        <Section k="chats" data={data} eyebrow={`Last ${range} days`} title="Chat Activity" onRetry={retry}>
          {(ch) => ch.top.length ? (
            <ul className="ins-list">
              {ch.top.map((c) => (
                <li key={c.jid}>
                  <button type="button" className="ins-chat" onClick={() => navigate(`/chats/${encodeURIComponent(c.jid)}`)}>
                    <span className="ellipsis" dir="auto">{c.name}</span>
                    {c.open_suggestions > 0 && <span className="crow-badge tnum" title="Open suggestions">{c.open_suggestions}</span>}
                  </button>
                  <span className="tnum caption">{plural(c.messages, "message")}</span>
                </li>
              ))}
            </ul>
          ) : <EmptyState title="No messages in this period" />}
        </Section>

        <Section k="rows_to_place" data={data} eyebrow="Master" title="Rows to Place" onRetry={retry}
          actions={<ButtonLink to="/master#rows-to-place" size="sm" variant="ghost" icon={<FileSpreadsheet size={14} />}>Open in Master</ButtonLink>}>
          {(rp) => rp.count ? (
            <div className="stack">
              <div className="stat-value tnum">{fmtNumber(rp.count)}</div>
              <ul className="ins-list">
                {rp.first.map((i) => (
                  <li key={i.id}>
                    <span className="ellipsis"><span className="strong" dir="auto">{i.project}</span> <span className="caption muted">· {i.sheet}</span></span>
                    <span className="caption muted ellipsis">{i.reason}</span>
                  </li>
                ))}
              </ul>
            </div>
          ) : <EmptyState title="No rows to place" />}
        </Section>
      </div>
    </div>
  );
}

/** One developer whose group is active but whose price rows went quiet: who to contact and what to send. */
function MissingRow({ m, onOpen }: { m: MissingData; onOpen: (jid: string) => void }) {
  const toast = useToast();
  const copy = () => {
    navigator.clipboard.writeText(m.message).then(
      () => toast.show({ message: "Message copied" }),
      () => toast.show({ tone: "error", message: "Couldn't copy. Select the text instead." }),
    );
  };
  return (
    <li className="ins-missing-row">
      <div className="ins-missing-main">
        <span className="strong" dir="auto">{m.developer}</span>
        <span className="caption muted">
          {plural(m.stale_count, "project")} with no new prices: <span dir="auto">{m.projects.slice(0, 4).map((p) => p.project).join(", ")}</span>
          {m.projects.length > 4 ? ` and ${m.projects.length - 4} more` : ""}
          {m.shares === "starting_prices" ? " · shares starting prices only" : ""}
          {m.on_request.length ? ` · ${m.on_request.join(", ")} on request` : ""}
        </span>
        <p className="ins-missing-msg small" dir="auto">{m.message}</p>
      </div>
      <div className="ins-missing-actions">
        {m.reps.length ? m.reps.slice(0, 2).map((r) => (
          <a key={r.phone} className="btn btn-primary btn-sm" href={`${r.wa_link}?text=${encodeURIComponent(m.message)}`} target="_blank" rel="noreferrer">
            <span className="btn-icon" aria-hidden="true"><Phone size={14} /></span>
            <span className="btn-label">{r.name} · {r.phone}</span>
          </a>
        )) : <span className="caption muted">No sales contact on Sheet 2 for this developer.</span>}
        <Button size="sm" variant="ghost" icon={<Copy size={14} />} onClick={copy}>Copy Message</Button>
        {m.chats[0] && (
          <Button size="sm" variant="ghost" icon={<MessageSquare size={14} />} onClick={() => onOpen(m.chats[0].jid)}>Open Group</Button>
        )}
      </div>
    </li>
  );
}
