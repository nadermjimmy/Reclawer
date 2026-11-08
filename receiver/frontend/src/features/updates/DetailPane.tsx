import { Check, ExternalLink, FileSpreadsheet, FileText, Image as ImageIcon, MessageSquare, X } from "lucide-react";
import { useEffect, useMemo, useRef, type ReactNode } from "react";
import { fileUrl } from "../../api/legacy";
import type { ContextMessage, Proposal, ProposalDetail } from "../../api/proposals";
import { Button, ErrorBanner, Skeleton, StatePill, WarningChip } from "../../components";
import { fmtDateTime, relTime } from "../../lib/format";
import { Change, displayProject } from "./Change";
import { needsSheet } from "./QueueList";

/** Number-ish tokens of the new value, used to highlight where it appears in the quote and source. */
function tokensOf(p: Proposal) {
  const nums = p.new_raw.match(/\d[\d,.]*\d|\d/g)?.filter((t) => t.length >= 2) ?? [];
  return nums.length ? nums : p.new_raw.trim() ? [p.new_raw.trim()] : [];
}

function escapeRe(s: string) {
  return s.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}

export function Highlight({ text, tokens }: { text: string; tokens: string[] }) {
  if (!tokens.length || !text) return <>{text}</>;
  const re = new RegExp(`(${tokens.map(escapeRe).join("|")})`, "gi");
  const parts = text.split(re);
  return (
    <>
      {parts.map((part, i) => (i % 2 === 1 ? <mark key={i}>{part}</mark> : <span key={i}>{part}</span>))}
    </>
  );
}

function Section({ title, children, aside }: { title: string; children: ReactNode; aside?: ReactNode }) {
  return (
    <section className="dsection">
      <div className="dsection-head">
        <div className="eyebrow">{title}</div>
        {aside}
      </div>
      {children}
    </section>
  );
}

function Messages({ messages, tokens }: { messages: ContextMessage[]; tokens: string[] }) {
  const box = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const el = box.current;
    const hit = el?.querySelector<HTMLElement>(".bubble.is-source");
    if (el && hit) el.scrollTop = hit.offsetTop - el.offsetTop - el.clientHeight / 3;
  }, [messages]);
  return (
    <div className="thread" ref={box}>
      {messages.map((m) => (
        <div key={m.id} className={`bubble${m.is_source ? " is-source" : ""}`}>
          <div className="bubble-meta caption">
            <span className="strong" dir="auto">{m.sender || "Unknown sender"}</span>
            <span className="tnum">{fmtDateTime(m.ts)}</span>
          </div>
          {m.text && (
            <div className="bubble-text" dir="auto">
              {m.is_source ? <Highlight text={m.text} tokens={tokens} /> : m.text}
            </div>
          )}
          {m.file_name && (
            <div className="bubble-file caption">
              {m.file_id ? <a href={fileUrl(m.file_id)} target="_blank" rel="noreferrer" dir="auto">{m.file_name}</a> : <span dir="auto">{m.file_name}</span>}
            </div>
          )}
        </div>
      ))}
    </div>
  );
}

function Source({ d, tokens }: { d: ProposalDetail; tokens: string[] }) {
  const c = d.source_context;
  if (c.missing) return <p className="small muted">The source is no longer stored. The quote above is what was read.</p>;
  if (c.kind === "message")
    return (
      <>
        <p className="caption muted">
          <MessageSquare size={12} aria-hidden="true" /> <span dir="auto">{c.chat.name}</span>, 30 minutes either side
        </p>
        <Messages messages={c.messages} tokens={tokens} />
      </>
    );
  if (c.kind === "image")
    return (
      <>
        <p className="caption muted src-file">
          <ImageIcon size={12} aria-hidden="true" />
          <a href={fileUrl(c.file.id)} target="_blank" rel="noreferrer" dir="auto">{c.file.name || "Image"}</a>
          <span>Read from the image</span>
        </p>
        <a href={fileUrl(c.file.id)} target="_blank" rel="noreferrer">
          <img className="src-image" src={fileUrl(c.file.id)} alt={`Image from the chat: ${c.file.name || "attachment"}`} loading="lazy" />
        </a>
      </>
    );
  const Icon = c.kind === "row" ? FileSpreadsheet : FileText;
  return (
    <>
      <p className="caption muted src-file">
        <Icon size={12} aria-hidden="true" />
        <a href={fileUrl(c.file.id)} target="_blank" rel="noreferrer" dir="auto">{c.file.name}</a>
        <span>{c.kind === "row" ? `${c.sheet}, row ${c.row_number}` : `Page ${c.page}`}</span>
        {c.hidden && <span>Hidden in the workbook</span>}
      </p>
      {c.kind === "row" ? (
        <dl className="cells">
          {(c.cells ?? []).map((cell, i) => {
            const hit = tokens.some((t) => cell.value.toLowerCase().includes(t.toLowerCase()));
            return (
              <div key={i} className={`cell${hit ? " is-hit" : ""}`}>
                <dt className="caption" dir="auto">{cell.header || `Column ${cell.col}`}</dt>
                <dd dir="auto">{cell.value}</dd>
              </div>
            );
          })}
        </dl>
      ) : (
        <>
          <pre className="pdf-text" dir="auto"><Highlight text={c.text ?? ""} tokens={tokens} /></pre>
          <a className="small" href={fileUrl(c.file.id)} target="_blank" rel="noreferrer">
            Open PDF <ExternalLink size={12} aria-hidden="true" />
          </a>
        </>
      )}
    </>
  );
}

export function DetailPane({ p, detail, error, onRetry, readOnly, sheets, sheetChoice, onSheet, onApprove, onReject, onOpenChat,
  metric, footerActions = true }: {
  p: Proposal;
  detail?: ProposalDetail;
  error?: string | null;
  onRetry: () => void;
  readOnly: boolean;
  sheets: string[];
  sheetChoice?: string;
  onSheet: (sheet: string) => void;
  onApprove: () => void;
  onReject: () => void;
  onOpenChat?: () => void;
  metric: string;
  /** false when the actions are rendered in a sticky footer instead (phone / drawer). */
  footerActions?: boolean;
}) {
  const tokens = useMemo(() => tokensOf(p), [p]);
  const blocked = needsSheet(p) && !sheetChoice;
  const target = detail?.row_values.find((v) => v.field === p.field);
  return (
    <div className="detail">
      <header className="detail-head">
        <div className="eyebrow">
          {p.sheet || "No sheet yet"}
          {p.row_number ? ` · Row ${p.row_number}` : p.match_state === "new" ? " · New row" : ""}
        </div>
        <h2 className="detail-project" dir="auto">{displayProject(p)}</h2>
        <div className="small muted">
          {p.developer && <span dir="auto">{p.developer} · </span>}
          {p.field_label}
          {readOnly && <> · <StatePill state={p.state} /></>}
        </div>
      </header>

      <Change p={p} size="lg" metric={metric} />

      {p.warnings.length > 0 && (
        <ul className="dwarnings">
          {p.warnings.map((w) => (
            <li key={w.code}>
              <WarningChip code={w.code} sheet={p.sheet} />
              <span className="small">
                {w.code === "near_match" ? (
                  <>
                    Chat: <b dir="auto">{p.project}</b> → Sheet: <b dir="auto">{p.matched_label}</b>. Approving saves{" "}
                    <span dir="auto">“{p.project}”</span> as a name for <span dir="auto">“{p.matched_label}”</span>.
                  </>
                ) : w.code === "lifts_hold" ? (
                  "Approving writes today's date and removes Hold/Hide on Sheet 2."
                ) : w.code === "bad_format" ? (
                  <>Doesn't follow the sheet's notation. Raw value: <code className="mono">{p.new_raw}</code></>
                ) : w.code === "new_project" ? (
                  `Approving adds ${displayProject(p)} as a new row on ${p.sheet}.`
                ) : (
                  w.text
                )}
              </span>
            </li>
          ))}
        </ul>
      )}

      {!readOnly && needsSheet(p) && (
        <label className="field">
          <span className="field-label">Sheet for this new project (required)</span>
          <select className="select" value={sheetChoice ?? ""} onChange={(e) => onSheet(e.target.value)}>
            <option value="">Choose sheet</option>
            {sheets.map((s, i) => <option key={s} value={s}>{i + 1}. {s}</option>)}
          </select>
        </label>
      )}

      {!readOnly && footerActions && (
        <div className="detail-actions">
          <Button variant="primary" icon={<Check size={16} />} kbd="A" onClick={onApprove} disabled={blocked}
            title={blocked ? "Choose a sheet first" : p.warning_codes.includes("lifts_hold") ? "Writes today's date and removes Hold" : undefined}>
            Approve
          </Button>
          <Button variant="ghost" icon={<X size={16} />} kbd="R" onClick={onReject}>Reject</Button>
          {onOpenChat && p.source.chat_jid && (
            <Button variant="ghost" icon={<MessageSquare size={16} />} kbd="O" onClick={onOpenChat}>Open in chat</Button>
          )}
        </div>
      )}

      {p.brain && (
        <Section title="Brain">
          <p className="brain-note" dir="auto">
            <b>{p.brain.verdict === "update_existing" ? "Existing row" : p.brain.verdict === "new_project" ? "New project"
              : p.brain.verdict === "noise" ? "Noise" : "Unsure"}</b>
            <span className="muted"> · {p.brain.confidence} confidence</span><br />{p.brain.reason}
          </p>
        </Section>
      )}

      {p.quote && (
        <Section title="Quote">
          <blockquote className="quote" dir="auto"><Highlight text={p.quote} tokens={tokens} /></blockquote>
        </Section>
      )}

      {error && !detail && <ErrorBanner onRetry={onRetry}>Couldn't load the source and sheet row.</ErrorBanner>}
      {!detail && !error && (
        <div className="stack" aria-busy="true">
          <Skeleton width="40%" />
          <Skeleton height={64} radius={12} />
          <Skeleton width="60%" />
        </div>
      )}

      {detail && (
        <>
          <Section title="Source" aside={<span className="caption muted tnum">{relTime(p.source.ts)}</span>}>
            <Source d={detail} tokens={tokens} />
          </Section>

          <Section title="Current Sheet Row">
            {detail.row ? (
              <dl className="sheetrow">
                {detail.row_values.map((v) => (
                  <div key={v.field} className={`sheetrow-item${v.field === p.field ? " is-target" : ""}`}>
                    <dt className="caption">{v.label}</dt>
                    <dd dir="auto">{v.raw || <span className="muted">Empty</span>}</dd>
                  </div>
                ))}
                {!target && (
                  <div className="sheetrow-item is-target">
                    <dt className="caption">{p.field_label}</dt>
                    <dd className="muted">Empty</dd>
                  </div>
                )}
              </dl>
            ) : (
              <p className="small muted">
                {p.sheet ? `Not on the sheet yet. Approving adds a new row on ${p.sheet}.` : "Not on any sheet yet. Choose the sheet above, then approve to add the row."}
              </p>
            )}
          </Section>

          {detail.cell_history.length > 0 && (
            <Section title="History of This Cell">
              <ul className="history">
                {detail.cell_history.map((h) => (
                  <li key={h.id}>
                    <StatePill state={h.state} />
                    <span className="small" dir="auto">{h.old_raw || "Empty"} → <b>{h.new_raw}</b></span>
                    <span className="caption muted tnum">{fmtDateTime(h.decided_at ?? h.created_at)}</span>
                  </li>
                ))}
              </ul>
            </Section>
          )}
        </>
      )}
    </div>
  );
}
