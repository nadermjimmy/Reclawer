import {
  ArrowDown, ArrowLeft, ChevronDown, ChevronUp, Download, FileSpreadsheet, FileText, Image as ImageIcon, Lightbulb,
  PanelRightClose, PanelRightOpen, RefreshCw, Search, X,
} from "lucide-react";
import { forwardRef, useCallback, useEffect, useImperativeHandle, useLayoutEffect, useMemo, useRef, useState } from "react";
import { chatMessages, search as apiSearch, type Chat, type ChatMessage, type MessageHit } from "../../api/chats";
import { errorText } from "../../api/client";
import { Button, ConfirmDialog, ErrorBanner, IconButton, Marked, Skeleton, useToast } from "../../components";
import { elapsed, fmtDate, plural, relTime } from "../../lib/format";
import { useDelayed, useTick } from "../../lib/hooks";
import { rangesIn, tokens } from "../../lib/text";
import { Avatar } from "./ChatList";
import { ProfileDrawer } from "./ProfileDrawer";
import { AssignmentBar } from "./AssignmentBar";
import type { ChatSyncState } from "./useChatSync";

const PAGE = 60;
const STAGES = ["Messages", "Text suggestions", "Files", "File suggestions"];

export interface ConversationHandle {
  /** Scroll to a message (loading the page around it when needed) and highlight it for 2 s. */
  jumpTo: (waId: string) => void;
  /** Ids of loaded messages that produced a suggestion. */
  messageFor: (proposalId: number) => string | null;
  reload: () => void;
  openSearch: () => void;
}

function dayLabel(ts: number | null) {
  if (!ts) return "Unknown date";
  const d = new Date(ts * 1000);
  const now = new Date();
  const y = new Date(now);
  y.setDate(now.getDate() - 1);
  if (d.toDateString() === now.toDateString()) return "Today";
  if (d.toDateString() === y.toDateString()) return "Yesterday";
  return fmtDate(ts);
}

const timeOf = (ts: number | null) => (ts ? new Date(ts * 1000).toLocaleTimeString("en-GB", { hour: "2-digit", minute: "2-digit" }) : "");

function fileSize(n: number) {
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${Math.round(n / 1024)} KB`;
  return `${(n / 1024 / 1024).toFixed(1)} MB`;
}

function Attachment({ m }: { m: ChatMessage }) {
  const Icon = m.kind === "image" ? ImageIcon : m.kind === "workbook" || m.kind === "csv" ? FileSpreadsheet : FileText;
  const name = m.file?.file_name || m.file_name || (m.kind === "image" ? "Photo" : m.kind === "video" ? "Video" : "Attachment");
  const note = m.media === "failed" ? "No longer on WhatsApp" : m.media === "pending" ? "Not downloaded yet" : m.file ? fileSize(m.file.size) : "";
  const body = (
    <>
      <Icon size={18} aria-hidden="true" className="att-icon" />
      <span className="att-text">
        <span className="att-name ellipsis" dir="auto">{name}</span>
        {note && <span className="att-note caption">{note}</span>}
      </span>
      {m.file && <Download size={16} aria-hidden="true" className="att-dl" />}
    </>
  );
  return m.file ? (
    <a className="att" href={m.file.url} target="_blank" rel="noreferrer" title={`Open ${name}`}>{body}</a>
  ) : (
    <span className={`att${m.media === "failed" ? " is-failed" : ""}`}>{body}</span>
  );
}

function Bubble({ m, showSender, highlight, hitKey, toks, current, onMarker }: {
  m: ChatMessage;
  showSender: boolean;
  highlight: boolean;
  hitKey: number;
  toks: string[];
  current: boolean;
  onMarker: (ids: number[]) => void;
}) {
  const ranges = toks.length && m.text ? rangesIn(m.text, toks) : [];
  return (
    <div className={`msg${highlight ? " is-jump" : ""}${current ? " is-current-hit" : ""}`} data-wa={m.id}>
      <div className="msg-bubble">
        {highlight && <span key={hitKey} className="msg-flash" aria-hidden="true" />}
        {showSender && <div className="msg-sender ellipsis" dir="auto">{m.sender || "Unknown sender"}</div>}
        {m.kind !== "text" && <Attachment m={m} />}
        {m.text && <div className="msg-text" dir="auto">{ranges.length ? <Marked text={m.text} ranges={ranges} /> : m.text}</div>}
        <div className="msg-foot">
          {m.suggestion_ids.length > 0 && (
            <button type="button" className="msg-marker" onClick={() => onMarker(m.suggestion_ids)}
              aria-label={`${plural(m.suggestion_ids.length, "suggestion")} from this message`}>
              <Lightbulb size={12} aria-hidden="true" /> {plural(m.suggestion_ids.length, "suggestion")}
            </button>
          )}
          <span className="msg-time caption tnum">{timeOf(m.ts)}</span>
        </div>
      </div>
    </div>
  );
}

/** In-chat search (Ctrl/⌘F): "3 of 17", Enter / ↑ older, Shift+Enter / ↓ newer, Esc closes. */
function ChatSearchBar({ jid, onJump, onClose, onTokens }: {
  jid: string;
  onJump: (waId: string) => void;
  onClose: () => void;
  onTokens: (t: string[], current: string | null) => void;
}) {
  const [q, setQ] = useState("");
  const [hits, setHits] = useState<MessageHit[] | null>(null);
  const [i, setI] = useState(0);
  const [failed, setFailed] = useState(false);
  const ref = useRef<HTMLInputElement>(null);
  useEffect(() => ref.current?.focus(), []);
  useEffect(() => {
    const t = tokens(q);
    if (q.trim().length < 2 || !t.length) {
      setHits(null);
      onTokens([], null);
      return;
    }
    let stop = false;
    const timer = window.setTimeout(() => {
      apiSearch(q.trim(), jid, 1000)
        .then((r) => {
          if (stop) return;
          setFailed(false);
          setHits(r.messages);
          setI(0);
          onTokens(t, r.messages[0]?.wa_id ?? null);
          if (r.messages[0]) onJump(r.messages[0].wa_id);
        })
        .catch(() => !stop && setFailed(true));
    }, 150);
    return () => {
      stop = true;
      window.clearTimeout(timer);
    };
  }, [q, jid]); // eslint-disable-line react-hooks/exhaustive-deps
  const step = (d: number) => {
    if (!hits?.length) return;
    const n = (i + d + hits.length) % hits.length;
    setI(n);
    onTokens(tokens(q), hits[n].wa_id);
    onJump(hits[n].wa_id);
  };
  return (
    <div className="csearch" role="search">
      <Search size={16} aria-hidden="true" className="muted" />
      <label className="sr-only" htmlFor="csearch-input">Search in this chat</label>
      <input id="csearch-input" ref={ref} className="csearch-input" value={q} placeholder="Search in this chat" dir="auto"
        autoComplete="off" spellCheck={false} onChange={(e) => setQ(e.target.value)}
        onKeyDown={(e) => {
          if (e.key === "Enter" || e.key === "ArrowUp") {
            e.preventDefault();
            step(e.shiftKey && e.key === "Enter" ? -1 : 1);
          } else if (e.key === "ArrowDown") {
            e.preventDefault();
            step(-1);
          } else if (e.key === "Escape") {
            e.preventDefault();
            e.stopPropagation();
            onClose();
          }
        }} />
      <span className="csearch-count caption tnum" aria-live="polite">
        {failed ? "Search unavailable" : hits ? (hits.length ? `${i + 1} of ${hits.length}` : "No matches") : ""}
      </span>
      <IconButton size="sm" label="Older match (Enter)" icon={<ChevronUp size={18} />} onClick={() => step(1)} disabled={!hits?.length} />
      <IconButton size="sm" label="Newer match (Shift+Enter)" icon={<ChevronDown size={18} />} onClick={() => step(-1)} disabled={!hits?.length} />
      <IconButton size="sm" label="Close search (Esc)" icon={<X size={18} />} onClick={onClose} />
    </div>
  );
}

function SyncStrip({ sync, onRetry }: { sync: ChatSyncState; onRetry: () => void }) {
  useTick(1000, sync.running);
  if (!sync.job) return null;
  const r = sync.result;
  if (sync.job.status === "failed" || sync.job.status === "interrupted") {
    return (
      <div className="sync-strip">
        <ErrorBanner onRetry={onRetry}>
          {r?.error === "bridge_unreachable"
            ? "Couldn't reach the WhatsApp bridge. Messages that arrived live are still here."
            : "The sync stopped before it finished. Retry, or open the log from the job pill."}
        </ErrorBanner>
      </div>
    );
  }
  return (
    <div className="sync-strip" aria-live="polite">
      <ol className="stepper" aria-label="Sync progress">
        {STAGES.map((label, i) => {
          const n = i + 1;
          const state = sync.stage > n ? "done" : sync.stage === n ? "active" : "todo";
          return (
            <li key={label} className={`step step-${state}`}>
              <span className="step-dot" aria-hidden="true" />
              <span className="step-label">{label}</span>
              <span className="sr-only">{state === "done" ? "done" : state === "active" ? "in progress" : "waiting"}</span>
            </li>
          );
        })}
        {sync.running && <li className="step-time caption tnum muted">{elapsed(sync.job.started_at)}</li>}
      </ol>
    </div>
  );
}

function ResultCard({ sync, onReview, onDismiss }: { sync: ChatSyncState; onReview: () => void; onDismiss: () => void }) {
  const r = sync.result;
  if (!r || sync.running || r.error) return null;
  const parts = [
    `+${plural(r.messages, "message")}`,
    `${plural(r.files, "file")}${r.files_failed ? ` (${r.files_failed} unavailable)` : ""}`,
    r.skipped === "no_master" ? "suggestions skipped: no master yet" : plural(r.suggestions, "new suggestion"),
  ];
  const nothing = !r.messages && !r.files && !r.suggestions;
  return (
    <div className="sync-result" role="status">
      <div className="sync-result-body">
        <div className="strong small">{nothing ? "Nothing changed since the last sync" : "Sync finished"}</div>
        <div className="caption muted tnum">{parts.join(" · ")}</div>
        {r.failed_batches > 0 && <div className="caption">{plural(r.failed_batches, "batch", "batches")} couldn't be read. Sync again to retry them.</div>}
      </div>
      {r.suggestions > 0 && <Button size="sm" variant="primary" onClick={onReview}>Review {r.suggestions}</Button>}
      <IconButton size="sm" label="Dismiss" icon={<X size={16} />} onClick={onDismiss} />
    </div>
  );
}

/** Centre pane: header (scope toggle, Sync now, in-chat search), the read-only message stream with date
    separators and upward infinite scroll, and the sync stepper / result card. */
export const Conversation = forwardRef<ConversationHandle, {
  chat: Chat;
  sync: ChatSyncState;
  around: string | null;
  phone: boolean;
  panelMode: "inline" | "overlay";
  panelOpen: boolean;
  openCount: number;
  onBack: () => void;
  onTogglePanel: () => void;
  onScope: (on: boolean) => void;
  onMarker: (ids: number[]) => void;
  onReviewNew: () => void;
}>(function Conversation({ chat, sync, around, phone, panelMode, panelOpen, openCount, onBack, onTogglePanel, onScope, onMarker, onReviewNew }, ref) {
  const toast = useToast();
  const [profileOpen, setProfileOpen] = useState(false);
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [hasMore, setHasMore] = useState(false);
  const [hasNewer, setHasNewer] = useState(false);
  const [loading, setLoading] = useState(true);
  const [loadingOlder, setLoadingOlder] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [jump, setJump] = useState<{ id: string; key: number } | null>(null);
  const [searchOpen, setSearchOpen] = useState(false);
  const [hitToks, setHitToks] = useState<string[]>([]);
  const [currentHit, setCurrentHit] = useState<string | null>(null);
  const [atBottom, setAtBottom] = useState(true);
  const scroller = useRef<HTMLDivElement>(null);
  const restore = useRef<{ height: number; top: number } | null>(null);
  const scrollTarget = useRef<"bottom" | string | null>(null);
  const seq = useRef(0);
  const jid = chat.jid;

  const load = useCallback(async (opts: { around?: string } = {}) => {
    const my = ++seq.current;
    setLoading(true);
    try {
      const page = await chatMessages(jid, opts.around ? { around: opts.around, limit: PAGE } : { limit: PAGE });
      if (my !== seq.current) return;
      setMessages(page.messages);
      setHasMore(page.has_more);
      setHasNewer(page.has_newer);
      setError(null);
      scrollTarget.current = opts.around && page.around ? opts.around : "bottom";
      if (opts.around && page.around) setJump({ id: opts.around, key: Date.now() });
    } catch (e) {
      if (my !== seq.current) return;
      if (opts.around) {
        toast.show({ tone: "error", message: "That message isn't in this chat any more. Showing the latest messages." });
        void load();
        return;
      }
      setError(errorText(e, "Couldn't load messages."));
    } finally {
      if (my === seq.current) setLoading(false);
    }
  }, [jid, toast]);

  useEffect(() => {
    setMessages([]);
    setSearchOpen(false);
    setHitToks([]);
    void load(around ? { around } : {});
  }, [jid]); // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => {
    if (around && !loading) jumpTo(around);
  }, [around]); // eslint-disable-line react-hooks/exhaustive-deps

  const loadOlder = useCallback(async () => {
    if (!hasMore || loadingOlder || !messages.length) return;
    setLoadingOlder(true);
    const el = scroller.current;
    try {
      const page = await chatMessages(jid, { before: messages[0].id, limit: PAGE });
      if (el) restore.current = { height: el.scrollHeight, top: el.scrollTop };
      setMessages((xs) => [...page.messages.filter((m) => !xs.some((x) => x.id === m.id)), ...xs]);
      setHasMore(page.has_more);
    } catch (e) {
      toast.show({ tone: "error", message: errorText(e, "Couldn't load older messages.") });
    } finally {
      setLoadingOlder(false);
    }
  }, [hasMore, loadingOlder, messages, jid, toast]);

  const loadNewer = useCallback(async () => {
    if (!hasNewer || !messages.length) return;
    try {
      const page = await chatMessages(jid, { after: messages[messages.length - 1].id, limit: PAGE });
      setMessages((xs) => [...xs, ...page.messages.filter((m) => !xs.some((x) => x.id === m.id))]);
      setHasNewer(page.has_newer);
    } catch (e) {
      toast.show({ tone: "error", message: errorText(e, "Couldn't load newer messages.") });
    }
  }, [hasNewer, messages, jid, toast]);

  // keep the reading position when older messages are prepended; land on the target after a fresh load
  useLayoutEffect(() => {
    const el = scroller.current;
    if (!el) return;
    if (restore.current) {
      el.scrollTop = restore.current.top + (el.scrollHeight - restore.current.height);
      restore.current = null;
      return;
    }
    const t = scrollTarget.current;
    if (!t) return;
    scrollTarget.current = null;
    if (t === "bottom") el.scrollTop = el.scrollHeight;
    else {
      const hit = el.querySelector<HTMLElement>(`[data-wa="${CSS.escape(t)}"]`);
      if (hit) el.scrollTop = hit.offsetTop - el.clientHeight / 2 + hit.clientHeight / 2;
    }
  }, [messages]);

  const onScroll = () => {
    const el = scroller.current;
    if (!el) return;
    setAtBottom(el.scrollHeight - el.scrollTop - el.clientHeight < 80);
    if (el.scrollTop < 300) void loadOlder();
    if (el.scrollHeight - el.scrollTop - el.clientHeight < 300) void loadNewer();
  };

  const jumpTo = useCallback((waId: string) => {
    const el = scroller.current;
    const hit = el?.querySelector<HTMLElement>(`[data-wa="${CSS.escape(waId)}"]`);
    if (el && hit) {
      el.scrollTo({ top: hit.offsetTop - el.clientHeight / 2 + hit.clientHeight / 2 });
      setJump({ id: waId, key: Date.now() });
    } else void load({ around: waId });
  }, [load]);
  useEffect(() => {
    if (!jump) return;
    const t = window.setTimeout(() => setJump(null), 2000);
    return () => window.clearTimeout(t);
  }, [jump]);

  useImperativeHandle(ref, () => ({
    jumpTo,
    messageFor: (pid) => messages.find((m) => m.suggestion_ids.includes(pid))?.id ?? null,
    reload: () => void load(),
    openSearch: () => setSearchOpen(true),
  }), [jumpTo, messages, load]);

  const toBottom = () => {
    if (hasNewer) void load();
    else scroller.current?.scrollTo({ top: scroller.current.scrollHeight });
  };

  const skeleton = useDelayed(loading && !messages.length, 300);
  let lastDay = "";
  let lastSender: string | null = null;
  const isGroup = chat.kind === "group";
  const syncLabel = sync.running ? "Syncing…" : sync.queued ? "Waiting to sync" : "Sync now";
  const busy = sync.busyWith && !sync.running;
  const lastSync = sync.job?.status === "done" ? sync.job.finished_at : chat.last_sync_at;
  const stream = useMemo(() => messages, [messages]);

  return (
    <section className="convo" aria-label={`Conversation: ${chat.name}`}>
      <header className="convo-head">
        <div className="convo-top">
          {phone && <IconButton label="Back to chats" icon={<ArrowLeft size={20} />} onClick={onBack} />}
          {!phone && <Avatar name={chat.name} kind={chat.kind} size={40} />}
          <div className="convo-titles">
            <h2 className="convo-name ellipsis" dir="auto">{chat.name}</h2>
            <span className="caption muted ellipsis">
              {phone ? "" : chat.kind === "group" ? "Group · " : "Private chat · "}{lastSync ? `Synced ${relTime(lastSync)}` : "Not synced yet"}
            </span>
          </div>
          <div className="convo-actions">
            <IconButton label="Search in this chat (Ctrl+F)" icon={<Search size={18} />} onClick={() => setSearchOpen(true)} />
            {phone ? (
              <IconButton label={busy ? "Sync when free" : syncLabel} icon={<RefreshCw size={18} />} variant="ghost"
                onClick={busy ? sync.queue : sync.start} disabled={sync.running || sync.queued} />
            ) : busy ? (
              <Button size="sm" variant="secondary" icon={<RefreshCw size={16} />} onClick={sync.queued ? sync.cancelQueue : sync.queue}
                title={`${sync.busyWith?.name} is running (${elapsed(sync.busyWith?.started_at)}). One job runs at a time.`}>
                {sync.queued ? "Cancel waiting sync" : "Sync when free"}
              </Button>
            ) : (
              <Button size="sm" variant="primary" icon={<RefreshCw size={16} />} kbd="S" onClick={sync.start} disabled={sync.running || sync.queued}>
                {syncLabel}
              </Button>
            )}
            {panelMode === "inline" ? (
              <IconButton label={panelOpen ? "Hide suggestions (i)" : "Show suggestions (i)"} onClick={onTogglePanel}
                icon={panelOpen ? <PanelRightClose size={18} /> : <PanelRightOpen size={18} />} />
            ) : (
              <button type="button" className="convo-sugg" onClick={onTogglePanel} aria-label={`${plural(openCount, "open suggestion")}. Show suggestions`}>
                <Lightbulb size={16} aria-hidden="true" />
                <span className="tnum">{openCount}</span>
              </button>
            )}
          </div>
        </div>
        <AssignmentBar chat={chat} onChanged={() => undefined} onHabits={() => setProfileOpen(true)} />
        <div className="convo-sub">
          <label className="switch">
            <input type="checkbox" role="switch" checked={chat.in_scope} onChange={(e) => onScope(e.target.checked)} />
            <span className="switch-track" aria-hidden="true"><span className="switch-thumb" /></span>
            <span className="small">Inventory chat</span>
          </label>

          {busy && (
            <span className="caption muted ellipsis" title="One job runs at a time">
              Busy: <span dir="auto">{sync.busyWith?.name}</span> · <span className="tnum">{elapsed(sync.busyWith?.started_at)}</span>
            </span>
          )}
          {sync.queued && !busy && <span className="caption muted">Starting…</span>}
          {chat.new_since_sync ? <span className="caption muted tnum">{plural(chat.new_since_sync, "message")} since the last sync</span> : null}
        </div>
        <SyncStrip sync={sync} onRetry={sync.start} />
        <ProfileDrawer jid={chat.jid} chatName={chat.name} open={profileOpen} onClose={() => setProfileOpen(false)}
          onSaved={() => undefined} />
        {searchOpen && (
          <ChatSearchBar jid={jid} onJump={jumpTo} onClose={() => { setSearchOpen(false); setHitToks([]); setCurrentHit(null); }}
            onTokens={(t, cur) => { setHitToks(t); setCurrentHit(cur); }} />
        )}
      </header>

      <div className="convo-body" ref={scroller} onScroll={onScroll} tabIndex={0} aria-label="Messages">
        {error && !messages.length && <div className="convo-pad"><ErrorBanner onRetry={() => void load()}>Couldn't load messages.</ErrorBanner></div>}
        {skeleton && (
          <div className="convo-skeleton" aria-busy="true">
            {[60, 40, 75, 30, 55].map((w, i) => <Skeleton key={i} width={`${w}%`} height={48} radius={14} />)}
          </div>
        )}
        {!loading && !error && !messages.length && (
          <p className="convo-empty small muted">No messages stored for this chat yet. Sync now pulls its history.</p>
        )}
        {messages.length > 0 && (
          <div className="convo-top-note caption muted">
            {loadingOlder ? "Loading older messages…" : hasMore ? "Scroll up for older messages" : "Start of the stored history"}
          </div>
        )}
        {stream.map((m) => {
          const day = dayLabel(m.ts);
          const sep = day !== lastDay;
          lastDay = day;
          const showSender = isGroup && (sep || m.sender !== lastSender);
          lastSender = m.sender;
          return (
            <div key={m.id} className="msg-wrap">
              {sep && <div className="day-sep"><span>{day}</span></div>}
              <Bubble m={m} showSender={showSender} highlight={jump?.id === m.id} hitKey={jump?.key ?? 0} toks={hitToks}
                current={currentHit === m.id} onMarker={onMarker} />
            </div>
          );
        })}
        <ResultCard sync={sync} onReview={onReviewNew} onDismiss={sync.dismiss} />
        {messages.length > 0 && !hasNewer && <p className="convo-foot caption muted">Read-only. Byit listens to this chat and never posts.</p>}
      </div>
      {(!atBottom || hasNewer) && messages.length > 0 && (
        <button type="button" className="to-latest" onClick={toBottom} aria-label="Jump to the latest message">
          <ArrowDown size={18} aria-hidden="true" />
        </button>
      )}
      <ConfirmDialog open={sync.confirming} title="Add this chat to inventory chats?" confirmLabel={sync.busyWith ? "Add and sync when free" : "Add and sync"}
        onCancel={sync.cancelConfirm} onConfirm={sync.confirm}>
        <p>
          Syncing adds <b dir="auto">{chat.name}</b> to the inventory chats, so its messages and files are read for sheet
          suggestions from now on. You can turn this off with the Inventory chat switch.
        </p>
      </ConfirmDialog>
    </section>
  );
});
