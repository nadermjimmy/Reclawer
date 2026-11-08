import { FileText, Image as ImageIcon, MessagesSquare, RefreshCw, SearchX, Users } from "lucide-react";
import { useEffect, useMemo, useRef, useState, type RefObject } from "react";
import { search as apiSearch, type Chat, type ChatFilter, type SearchResults } from "../../api/chats";
import { Button, Chip, ConfirmDialog, EmptyState, ErrorBanner, Marked, SearchInput, Skeleton } from "../../components";
import { fmtDate } from "../../lib/format";
import { useDelayed } from "../../lib/hooks";
import { initials, matchesAll, rangesIn, textDir, tokens } from "../../lib/text";

export const FILTERS: { key: ChatFilter; label: string }[] = [
  { key: "all", label: "All" },
  { key: "inventory", label: "Inventory" },
  { key: "suggestions", label: "Has suggestions" },
  { key: "groups", label: "Groups" },
  { key: "private", label: "Private" },
];

export function applyFilter(chats: Chat[], f: ChatFilter) {
  if (f === "inventory") return chats.filter((c) => c.in_scope);
  if (f === "suggestions") return chats.filter((c) => c.open_suggestions > 0);
  if (f === "groups") return chats.filter((c) => c.kind === "group");
  if (f === "private") return chats.filter((c) => c.kind === "private");
  return chats;
}

export function Avatar({ name, kind, size = 44 }: { name: string; kind: "group" | "private"; size?: number }) {
  return (
    <span className={`avatar avatar-${kind}`} style={{ width: size, height: size }} aria-hidden="true">
      <span dir="auto">{initials(name)}</span>
    </span>
  );
}

/** Short time for a list row: time today, weekday this week, else a date. */
export function shortTime(ts: number | null) {
  if (!ts) return "";
  const d = new Date(ts * 1000);
  const now = new Date();
  if (d.toDateString() === now.toDateString()) return d.toLocaleTimeString("en-GB", { hour: "2-digit", minute: "2-digit" });
  if (now.getTime() - d.getTime() < 6 * 86400000) return d.toLocaleDateString("en-GB", { weekday: "short" });
  return d.toLocaleDateString("en-GB", { day: "numeric", month: "short", year: d.getFullYear() === now.getFullYear() ? undefined : "2-digit" });
}

function ChatRow({ c, active, cursor, ranges, onOpen }: { c: Chat; active: boolean; cursor: boolean; ranges?: [number, number][]; onOpen: () => void }) {
  return (
    <button type="button" className={`crow${active ? " is-active" : ""}${cursor ? " is-cursor" : ""}`} onClick={onOpen}
      aria-current={active ? "page" : undefined} data-jid={c.jid}>
      <Avatar name={c.name} kind={c.kind} />
      <span className="crow-body">
        <span className="crow-top">
          <span className="crow-name ellipsis" dir="auto">{ranges ? <Marked text={c.name} ranges={ranges} /> : c.name}</span>
          <span className="crow-time caption tnum">{shortTime(c.last_ts)}</span>
        </span>
        <span className="crow-bottom">
          <span className="crow-preview ellipsis" dir={textDir(c.preview)}>
            {c.media_preview && (c.preview.match(/\.(pdf|xlsx?|csv)$/i) ? <FileText size={13} aria-hidden="true" /> : <ImageIcon size={13} aria-hidden="true" />)}
            {c.preview_sender && c.kind === "group" ? <><bdi className="crow-sender">{c.preview_sender}</bdi><span className="crow-sender">: </span></> : null}
            {c.preview ? <bdi>{c.preview}</bdi> : c.count ? "" : <span className="muted">No messages yet</span>}
          </span>
          {c.in_scope && <span className="scope-dot" title="Inventory chat" aria-label="Inventory chat" />}
          {c.open_suggestions > 0 && (
            <span className="crow-badge tnum" title={`${c.open_suggestions} open suggestions`} aria-label={`${c.open_suggestions} open suggestions`}>
              {c.open_suggestions}
            </span>
          )}
        </span>
      </span>
    </button>
  );
}

type Flat = { kind: "chat"; jid: string } | { kind: "message"; jid: string; wa_id: string };

/** Left pane: search (focus with "/"), filter chips and the chat list. With 2+ characters typed, the list shows
    grouped results (chats, then messages) from /api/search; Enter opens the chat at that message. */
export function ChatList({ chats, loading, error, onRetry, activeJid, filter, onFilter, query, onQuery, onOpenChat, onOpenMessage, searchRef,
  onSyncAll }: {
  chats: Chat[] | undefined;
  loading: boolean;
  error: string | null;
  onRetry: () => void;
  activeJid: string | null;
  filter: ChatFilter;
  onFilter: (f: ChatFilter) => void;
  query: string;
  onQuery: (q: string) => void;
  onOpenChat: (jid: string) => void;
  onOpenMessage: (jid: string, waId: string) => void;
  searchRef: RefObject<HTMLInputElement | null>;
  onSyncAll: (mode: "batch" | "live") => void;
}) {
  const [syncAll, setSyncAll] = useState(false);
  const [mode, setMode] = useState<"batch" | "live">("batch");
  const toks = useMemo(() => tokens(query), [query]);
  const searching = query.trim().length >= 2 && toks.length > 0;
  const all = chats ?? [];
  const filtered = useMemo(() => {
    const base = applyFilter(all, filter);
    return toks.length ? base.filter((c) => matchesAll(c.name, toks)) : base;
  }, [all, filter, toks]);

  const [results, setResults] = useState<SearchResults | null>(null);
  const [searchError, setSearchError] = useState(false);
  const [busy, setBusy] = useState(false);
  useEffect(() => {
    if (!searching) {
      setResults(null);
      setSearchError(false);
      return;
    }
    let stop = false;
    setBusy(true);
    const t = window.setTimeout(() => {
      apiSearch(query.trim(), undefined, 50)
        .then((r) => !stop && (setResults(r), setSearchError(false)))
        .catch(() => !stop && setSearchError(true))
        .finally(() => !stop && setBusy(false));
    }, 150);
    return () => {
      stop = true;
      window.clearTimeout(t);
    };
  }, [query, searching]);

  const chatHits = searching ? filtered.slice(0, 5) : filtered;
  const messages = searching ? results?.messages ?? [] : [];
  const flat: Flat[] = useMemo(() => [
    ...chatHits.map((c) => ({ kind: "chat" as const, jid: c.jid })),
    ...messages.map((m) => ({ kind: "message" as const, jid: m.jid, wa_id: m.wa_id })),
  ], [chatHits, messages]);
  const [cursor, setCursor] = useState(-1);
  useEffect(() => setCursor(-1), [query, filter]);
  const listRef = useRef<HTMLDivElement>(null);
  const openFlat = (f: Flat | undefined) => {
    if (!f) return;
    if (f.kind === "chat") onOpenChat(f.jid);
    else onOpenMessage(f.jid, f.wa_id);
  };
  const onKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === "ArrowDown" || e.key === "ArrowUp") {
      e.preventDefault();
      const n = Math.max(0, Math.min(flat.length - 1, cursor + (e.key === "ArrowDown" ? 1 : -1)));
      setCursor(n);
      window.requestAnimationFrame(() => listRef.current?.querySelector(".is-cursor")?.scrollIntoView({ block: "nearest" }));
    } else if (e.key === "Enter") {
      e.preventDefault();
      openFlat(flat[cursor < 0 ? 0 : cursor]);
    } else if (e.key === "Escape" && query) {
      e.preventDefault();
      e.stopPropagation();
      onQuery("");
    }
  };
  const skeleton = useDelayed(loading && !chats, 300);

  return (
    <div className="clist" onKeyDown={onKeyDown}>
      <div className="clist-head">
        <SearchInput ref={searchRef} label="Search chats and messages" placeholder="Search chats and messages" value={query}
          onChange={(e) => onQuery(e.target.value)} hint={<kbd>/</kbd>} autoComplete="off" spellCheck={false} />
        <div className="clist-chips" role="group" aria-label="Show">
          {FILTERS.map((f) => (
            <Chip key={f.key} selected={filter === f.key} onClick={() => onFilter(f.key)}
              count={f.key === "all" ? undefined : applyFilter(all, f.key).length}>{f.label}</Chip>
          ))}
        </div>
        <Button size="sm" variant="secondary" className="clist-syncall" onClick={() => setSyncAll(true)}>
          <RefreshCw size={14} aria-hidden="true" /> Sync All Chats
        </Button>
      </div>
      <ConfirmDialog open={syncAll} title="Sync all chats" confirmLabel="Sync All Chats"
        onCancel={() => setSyncAll(false)} onConfirm={() => { setSyncAll(false); onSyncAll(mode); }}>
        <p>Every group, plus private chats you ticked. Text suggestions come first; photos, PDFs and spreadsheets are
          downloaded and read after, and only fill what the text didn't give.</p>
        <fieldset className="syncall-modes">
          <legend className="sr-only">Speed</legend>
          <label className="syncall-mode">
            <input type="radio" name="syncall-mode" checked={mode === "batch"} onChange={() => setMode("batch")} />
            <span><b>Half price</b><span className="small muted">Claude reads through the batch queue. Suggestions arrive
              stage by stage, usually within an hour.</span></span>
          </label>
          <label className="syncall-mode">
            <input type="radio" name="syncall-mode" checked={mode === "live"} onChange={() => setMode("live")} />
            <span><b>Fast</b><span className="small muted">Read now, at full price. Suggestions appear as each chat's
              text is read.</span></span>
          </label>
        </fieldset>
      </ConfirmDialog>
      <div className="clist-body" ref={listRef} role="list" aria-label={searching ? "Search results" : "Chats"}>
        {error && !chats && <div className="clist-pad"><ErrorBanner onRetry={onRetry}>Couldn't load chats.</ErrorBanner></div>}
        {skeleton && Array.from({ length: 8 }, (_, i) => (
          <div className="crow crow-skeleton" key={i}>
            <Skeleton width={44} height={44} />
            <span className="crow-body"><Skeleton width="55%" /><Skeleton width="80%" /></span>
          </div>
        ))}
        {searching && <div className="clist-group eyebrow">Chats</div>}
        {chatHits.map((c, i) => (
          <ChatRow key={c.jid} c={c} active={c.jid === activeJid} cursor={i === cursor} onOpen={() => onOpenChat(c.jid)}
            ranges={searching ? rangesIn(c.name, toks) : undefined} />
        ))}
        {searching && !chatHits.length && <p className="clist-note small muted">No chat names match.</p>}
        {searching && (
          <>
            <div className="clist-group eyebrow">
              Messages{results ? <span className="tnum"> · {results.messages.length}{results.has_more ? "+" : ""}</span> : null}
            </div>
            {searchError && <p className="clist-note small muted">Message search is unavailable right now. Chat names still filter.</p>}
            {!searchError && busy && !results && <p className="clist-note small muted">Searching messages…</p>}
            {results && !messages.length && !searchError && (
              <EmptyState icon={<SearchX size={22} />} title={`No messages match “${query.trim()}”`}>
                Try fewer words, or the Arabic or English spelling.
              </EmptyState>
            )}
            {messages.map((m, i) => (
              <button type="button" key={m.wa_id} className={`mhit${chatHits.length + i === cursor ? " is-cursor" : ""}`}
                onClick={() => onOpenMessage(m.jid, m.wa_id)}>
                <span className="mhit-top">
                  <span className="mhit-chat ellipsis" dir="auto">{m.chat_name}</span>
                  <span className="caption muted tnum">{fmtDate(m.ts)}</span>
                </span>
                <span className="mhit-snippet" dir={textDir(m.snippet)}>
                  {m.sender && <><bdi className="crow-sender">{m.sender}</bdi><span className="crow-sender">: </span></>}
                  {m.field === "file" && <FileText size={13} aria-hidden="true" />}
                  <bdi><Marked text={m.snippet} ranges={m.match_ranges} /></bdi>
                </span>
              </button>
            ))}
          </>
        )}
        {!searching && chats && !filtered.length && (
          all.length ? (
            <EmptyState icon={<Users size={22} />} title="No chats in this filter">
              <button type="button" className="linkish" onClick={() => onFilter("all")}>Show all chats</button>
            </EmptyState>
          ) : (
            <EmptyState icon={<MessagesSquare size={22} />} title="No chats yet">
              Run Sync chats in More &gt; Pipeline.
            </EmptyState>
          )
        )}
      </div>
    </div>
  );
}
