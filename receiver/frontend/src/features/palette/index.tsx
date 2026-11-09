/* Command palette (Ctrl/⌘K): jump to a chat or a message (same /api/search as Chats) and run actions such as
   "Sync <chat>", "Download team file" or "Rows to place". Keyboard: ↑/↓ move, Enter runs, Esc closes. */
import { AnimatePresence, motion } from "framer-motion";
import {
  ArrowRight, Brain, ChartColumn, Sheet, Download, FileSpreadsheet, FolderOpen, Inbox, MessageSquare,
  MessagesSquare, RefreshCw, Search, Upload, Workflow, type LucideIcon,
} from "lucide-react";
import { useCallback, useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { useNavigate } from "react-router-dom";
import { listChats, search as apiSearch, type Chat, type MessageHit } from "../../api/chats";
import { exportUrl } from "../../api/master";
import { useSession } from "../../app/session";
import { Marked } from "../../components";
import { useFocusTrap } from "../../components/focus";
import { fmtDate } from "../../lib/format";
import { matchesAll, rangesIn, tokens } from "../../lib/text";
import { dialog, scrim } from "../../motion";
import "./palette.css";

const OPEN_EVENT = "receiver:open-palette";
const isMac = typeof navigator !== "undefined" && /Mac|iPhone|iPad/.test(navigator.platform);

interface Item {
  id: string;
  group: "Actions" | "Chats" | "Messages";
  icon: LucideIcon;
  label: string;
  ranges?: [number, number][];
  hint?: ReactNode;
  dirAuto?: boolean;
  run: () => void;
}

export function PaletteTrigger() {
  return (
    <button type="button" className="palette-trigger" onClick={() => window.dispatchEvent(new Event(OPEN_EVENT))}
      aria-label="Search chats or run a command" aria-keyshortcuts={isMac ? "Meta+K" : "Control+K"}>
      <Search size={16} aria-hidden="true" />
      <span className="palette-trigger-text">Search or jump to…</span>
      <kbd>{isMac ? "⌘K" : "Ctrl K"}</kbd>
    </button>
  );
}

export function CommandPalette() {
  const [open, setOpen] = useState(false);
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if ((e.ctrlKey || e.metaKey) && !e.shiftKey && !e.altKey && e.key.toLowerCase() === "k") {
        e.preventDefault();
        setOpen((v) => !v);
      }
    };
    const onOpen = () => setOpen(true);
    window.addEventListener("keydown", onKey);
    window.addEventListener(OPEN_EVENT, onOpen);
    return () => {
      window.removeEventListener("keydown", onKey);
      window.removeEventListener(OPEN_EVENT, onOpen);
    };
  }, []);
  return (
    <AnimatePresence>
      {open && <Palette onClose={() => setOpen(false)} />}
    </AnimatePresence>
  );
}

function Palette({ onClose }: { onClose: () => void }) {
  const navigate = useNavigate();
  const { me } = useSession();
  const [q, setQ] = useState("");
  const [chats, setChats] = useState<Chat[]>([]);
  const [hits, setHits] = useState<MessageHit[]>([]);
  const [searchFailed, setSearchFailed] = useState(false);
  const [cursor, setCursor] = useState(0);
  const box = useRef<HTMLDivElement>(null);
  const input = useRef<HTMLInputElement>(null);
  const list = useRef<HTMLDivElement>(null);
  useFocusTrap(box, true, onClose, input);

  useEffect(() => {
    listChats("all").then((r) => setChats(r.chats)).catch(() => undefined);
  }, []);
  useEffect(() => {
    const t = tokens(q);
    if (q.trim().length < 2 || !t.length) {
      setHits([]);
      return;
    }
    let stop = false;
    const timer = window.setTimeout(() => {
      apiSearch(q.trim(), undefined, 8)
        .then((r) => !stop && (setHits(r.messages), setSearchFailed(false)))
        .catch(() => !stop && setSearchFailed(true));
    }, 150);
    return () => {
      stop = true;
      window.clearTimeout(timer);
    };
  }, [q]);

  const go = useCallback((to: string) => {
    onClose();
    navigate(to);
  }, [navigate, onClose]);
  const download = useCallback((which: "team" | "sql") => {
    onClose();
    const a = document.createElement("a");
    a.href = exportUrl(which);
    a.download = "";
    document.body.appendChild(a);
    a.click();
    a.remove();
  }, [onClose]);

  const items = useMemo(() => {
    const toks = tokens(q);
    const actions: Item[] = [
      { id: "go-updates", group: "Actions", icon: Inbox, label: "Go to Updates", run: () => go("/updates") },
      { id: "go-chats", group: "Actions", icon: MessagesSquare, label: "Go to Chats", run: () => go("/chats") },
      { id: "go-insights", group: "Actions", icon: ChartColumn, label: "Go to Insights", run: () => go("/insights") },
      { id: "go-master", group: "Actions", icon: FileSpreadsheet, label: "Go to Master", run: () => go("/master") },
      { id: "rows", group: "Actions", icon: FileSpreadsheet, label: "Rows to place", hint: me?.counts.rows_to_place ? `${me.counts.rows_to_place} open` : undefined, run: () => go("/master#rows-to-place") },
      { id: "upload", group: "Actions", icon: Upload, label: "Upload master", run: () => go("/master#upload") },
      ...(me?.has_master ? [
        { id: "dl-team", group: "Actions" as const, icon: Download, label: "Download team file", run: () => download("team") },
        { id: "dl-sql", group: "Actions" as const, icon: Download, label: "Download SQL file", run: () => download("sql") },
      ] : []),
      { id: "go-pipeline", group: "Actions", icon: Workflow, label: "Go to Pipeline", run: () => go("/more/pipeline") },
      { id: "go-sheet", group: "Actions", icon: Sheet, label: "Go to Sheet", run: () => go("/sheet") },
      { id: "go-brain", group: "Actions", icon: Brain, label: "Go to Brain", run: () => go("/more/brain") },
      { id: "go-sources", group: "Actions", icon: FolderOpen, label: "Go to Sources", run: () => go("/more/sources") },
    ];
    const matchedActions = toks.length ? actions.filter((a) => matchesAll(a.label, toks)) : actions.slice(0, 8);
    const matchedChats = toks.length ? chats.filter((c) => matchesAll(c.name, toks)).slice(0, 5) : [];
    const chatItems: Item[] = matchedChats.flatMap((c) => [
      { id: `open-${c.jid}`, group: "Chats" as const, icon: MessageSquare, label: c.name, ranges: rangesIn(c.name, toks), dirAuto: true,
        hint: c.open_suggestions ? `${c.open_suggestions} open` : undefined, run: () => go(`/chats/${encodeURIComponent(c.jid)}`) },
      { id: `sync-${c.jid}`, group: "Actions" as const, icon: RefreshCw, label: `Sync ${c.name}`, dirAuto: true,
        hint: c.in_scope ? undefined : "Adds it to inventory chats", run: () => go(`/chats/${encodeURIComponent(c.jid)}?sync=1`) },
    ]);
    const msgItems: Item[] = hits.map((m) => ({
      id: `msg-${m.wa_id}`, group: "Messages", icon: ArrowRight, label: m.snippet, ranges: m.match_ranges, dirAuto: true,
      hint: <><bdi>{m.chat_name}</bdi> · {fmtDate(m.ts)}</>,
      run: () => go(`/chats/${encodeURIComponent(m.jid)}?around=${encodeURIComponent(m.wa_id)}`),
    }));
    const syncs = chatItems.filter((i) => i.group === "Actions");
    return [
      ...chatItems.filter((i) => i.group === "Chats"),
      ...matchedActions,
      ...syncs,
      ...msgItems,
    ];
  }, [q, chats, hits, me, go, download]);

  useEffect(() => setCursor(0), [q]);
  useEffect(() => {
    list.current?.querySelector(".pal-item.is-active")?.scrollIntoView({ block: "nearest" });
  }, [cursor]);

  const groups = ["Chats", "Actions", "Messages"] as const;
  let index = -1;
  return (
    <div className="overlay overlay-center palette-overlay" data-modal-open="true">
      <motion.div className="scrim" variants={scrim} initial="initial" animate="animate" exit="exit" onClick={onClose} />
      <motion.div ref={box} className="palette" role="dialog" aria-modal="true" aria-label="Command palette"
        variants={dialog} initial="initial" animate="animate" exit="exit">
        <div className="pal-input">
          <Search size={18} aria-hidden="true" />
          <input ref={input} value={q} onChange={(e) => setQ(e.target.value)} placeholder="Search chats, messages and actions"
            aria-label="Search chats, messages and actions" role="combobox" aria-expanded="true" aria-controls="pal-list"
            aria-activedescendant={items[cursor] ? `pal-${items[cursor].id}` : undefined} dir="auto" autoComplete="off" spellCheck={false}
            onKeyDown={(e) => {
              if (e.key === "ArrowDown" || e.key === "ArrowUp") {
                e.preventDefault();
                setCursor((c) => Math.max(0, Math.min(items.length - 1, c + (e.key === "ArrowDown" ? 1 : -1))));
              } else if (e.key === "Enter") {
                e.preventDefault();
                items[cursor]?.run();
              }
            }} />
          <kbd>Esc</kbd>
        </div>
        <div className="pal-list" id="pal-list" role="listbox" ref={list}>
          {groups.map((g) => {
            const xs = items.filter((i) => i.group === g);
            if (!xs.length) return null;
            return (
              <div key={g} role="group" aria-label={g}>
                <div className="pal-group eyebrow">{g}</div>
                {xs.map((it) => {
                  index = items.indexOf(it);
                  const active = index === cursor;
                  const Icon = it.icon;
                  const i = index;
                  return (
                    <div key={it.id} id={`pal-${it.id}`} role="option" aria-selected={active}
                      className={`pal-item${active ? " is-active" : ""}`} onMouseMove={() => setCursor(i)} onClick={it.run}>
                      <Icon size={16} aria-hidden="true" className="pal-icon" />
                      <span className="pal-label" dir={it.dirAuto ? "auto" : undefined}>
                        {it.ranges ? <Marked text={it.label} ranges={it.ranges} /> : it.label}
                      </span>
                      {it.hint && <span className="pal-hint caption">{it.hint}</span>}
                    </div>
                  );
                })}
              </div>
            );
          })}
          {q.trim().length >= 2 && !items.length && (
            <p className="pal-empty small muted">Nothing matches “{q.trim()}”. Try the Arabic or English spelling.</p>
          )}
          {searchFailed && <p className="pal-empty caption muted">Message search is unavailable right now.</p>}
        </div>
      </motion.div>
    </div>
  );
}
