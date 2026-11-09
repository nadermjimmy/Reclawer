import { AnimatePresence, motion } from "framer-motion";
import { MessagesSquare } from "lucide-react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useNavigate, useParams, useSearchParams } from "react-router-dom";
import { listChats, setChatScope, type ChatFilter } from "../../api/chats";
import { errorText } from "../../api/client";
import { getProposal, type Proposal } from "../../api/proposals";
import { sourceMessage } from "../../api/legacy";
import { search as apiSearch } from "../../api/chats";
import { JOB_FINISHED } from "../../app/jobs";
import { useSession } from "../../app/session";
import { usePageTitle } from "../../app/title";
import { Drawer, EmptyState, KeyboardHelp, useToast, type ShortcutGroup } from "../../components";
import { useHotkeys, useLoad, useMediaQuery } from "../../lib/hooks";
import { drawer as drawerMotion } from "../../motion";
import { Avatar, ChatList } from "./ChatList";
import { useJobs } from "../../app/jobs";
import { CHATS_CHANGED } from "./AssignmentBar";
import { Conversation, type ConversationHandle } from "./Conversation";
import { SuggestionsPanel, useChatSuggestions } from "./SuggestionsPanel";
import { useChatSync } from "./useChatSync";
import "./chats.css";

const SHORTCUTS: ShortcutGroup[] = [
  { title: "Chats", keys: [["/", "Search chats and messages"], ["↑ / ↓", "Move in the list or results"], ["Enter", "Open"], ["Alt+↑ / Alt+↓", "Previous or next chat"], ["Esc", "Clear search, or back on a phone"]] },
  { title: "Conversation", keys: [["s", "Sync now"], ["Ctrl+F", "Search in this chat"], ["i", "Show or hide suggestions"], ["]", "Focus suggestions"], ["[", "Focus the chat list"]] },
  { title: "Suggestions (panel focused)", keys: [["j / k", "Next or previous"], ["a", "Approve"], ["r", "Reject"], ["l", "Locate the quote in the chat"], ["u", "Undo (5 s)"]] },
];

const PANEL_KEY = "receiver.chats.panel";

function readPanelPref() {
  try {
    return window.localStorage.getItem(PANEL_KEY) !== "closed";
  } catch {
    return true;
  }
}

/** WhatsApp-Web-style Chats: list | conversation | suggestions from this chat (desktop). Tablet: the panel is a
    slide-over. Phone: list → conversation → suggestions push, like WhatsApp mobile. */
export function ChatsScreen() {
  const { startStep } = useJobs();
  useEffect(() => {  // an assignment changed in the chat header: refresh the list's developer tags
    const on = () => void chats.reload();
    window.addEventListener(CHATS_CHANGED, on);
    return () => window.removeEventListener(CHATS_CHANGED, on);
  });
  const { jid: jidParam } = useParams();
  const jid = jidParam ?? null;
  const [params, setParams] = useSearchParams();
  const around = params.get("around");
  const navigate = useNavigate();
  const toast = useToast();
  const { me, refresh } = useSession();
  const desktop = useMediaQuery("(min-width: 1024px)");
  const phone = useMediaQuery("(max-width: 639px)");
  const metric = me?.market.metric ?? "sq m";

  const chats = useLoad(() => listChats("all"), []);
  const [filter, setFilter] = useState<ChatFilter>("all");
  const [query, setQuery] = useState("");
  const searchRef = useRef<HTMLInputElement>(null);
  const convo = useRef<ConversationHandle>(null);
  const [help, setHelp] = useState(false);
  const chat = useMemo(() => chats.data?.chats.find((c) => c.jid === jid) ?? null, [chats.data, jid]);
  usePageTitle("Chats");

  // legacy /sources/message/<id> lands here as ?around=<id> without a chat: find its chat
  useEffect(() => {
    if (jid || !around) return;
    sourceMessage(around)
      .then((r) => navigate(`/chats/${encodeURIComponent(r.chat.jid)}?around=${encodeURIComponent(around)}`, { replace: true }))
      .catch(() => toast.show({ tone: "error", message: "That message isn't stored any more." }));
  }, [jid, around]); // eslint-disable-line react-hooks/exhaustive-deps

  // keep the list fresh after jobs and when badges move
  useEffect(() => {
    const on = () => void chats.reload();
    window.addEventListener(JOB_FINISHED, on);
    return () => window.removeEventListener(JOB_FINISHED, on);
  }, [chats]);
  const openCountKey = me?.counts.chats_with_suggestions;
  useEffect(() => {
    if (chats.data) void chats.reload();
  }, [openCountKey]); // eslint-disable-line react-hooks/exhaustive-deps

  // ---------- panel ----------
  const [panelPref, setPanelPref] = useState(readPanelPref);
  const [overlayOpen, setOverlayOpen] = useState(false);
  const panelOpen = desktop ? panelPref : overlayOpen;
  const togglePanel = useCallback(() => {
    if (desktop) {
      setPanelPref((v) => {
        try {
          window.localStorage.setItem(PANEL_KEY, v ? "closed" : "open");
        } catch {
          /* private mode: preference just isn't kept */
        }
        return !v;
      });
    } else setOverlayOpen((v) => !v);
  }, [desktop]);
  useEffect(() => setOverlayOpen(false), [jid]);
  const [highlight, setHighlight] = useState<Set<number>>(new Set());
  const sugg = useChatSuggestions(jid, () => {
    void chats.reload();
    void refresh();
  });
  const panelRef = useRef<HTMLDivElement>(null);

  const showPanel = () => {
    if (desktop) {
      if (!panelPref) togglePanel();
    } else setOverlayOpen(true);
  };
  const onMarker = (ids: number[]) => {
    setHighlight(new Set(ids));
    showPanel();
  };

  // ---------- sync ----------
  const knownIds = useRef<Set<number>>(new Set());
  knownIds.current = new Set((sugg.data?.items ?? []).map((i) => i.id));
  const sync = useChatSync(chat, (r) => {
    const before = knownIds.current;
    void chats.reload();
    convo.current?.reload();
    void sugg.reload().then((d) => {
      if (!d || !r || r.suggestions <= 0) return;
      sugg.markFresh(d.items.filter((i) => !before.has(i.id)).map((i) => i.id));
      showPanel();
    });
  });
  // ?sync=1 (command palette "Sync <chat>", Insights "Sync now")
  useEffect(() => {
    if (chat && params.get("sync") === "1") {
      setParams((p) => {
        const n = new URLSearchParams(p);
        n.delete("sync");
        return n;
      }, { replace: true });
      if (me?.job) sync.queue();
      else sync.start();
    }
  }, [chat, params]); // eslint-disable-line react-hooks/exhaustive-deps

  const locate = async (p: Proposal) => {
    if (!desktop) setOverlayOpen(false);
    if (p.source.kind === "message") {
      convo.current?.jumpTo(p.source.ref);
      return;
    }
    const loaded = convo.current?.messageFor(p.id);
    if (loaded) {
      convo.current?.jumpTo(loaded);
      return;
    }
    try { // a file row or PDF page: jump to the message that posted the file
      const d = await getProposal(p.id);
      const ctx = d.source_context;
      const name = ctx.kind !== "message" && !ctx.missing ? ctx.file.name : p.source.file_name;
      const hits = name && jid ? await apiSearch(name, jid, 5) : null;
      const hit = hits?.messages.find((m) => m.field === "file") ?? hits?.messages[0];
      if (hit) convo.current?.jumpTo(hit.wa_id);
      else toast.show({ message: "The file's message isn't stored in this chat." });
    } catch (e) {
      toast.show({ tone: "error", message: errorText(e, "Couldn't find the source message.") });
    }
  };

  const setScope = async (on: boolean) => {
    if (!chat) return;
    chats.setData((d) => d && { ...d, chats: d.chats.map((c) => (c.jid === chat.jid ? { ...c, in_scope: on } : c)) });
    try {
      await setChatScope(chat.jid, on);
      toast.show({ message: on ? `${chat.name} is an inventory chat now.` : `${chat.name} is no longer an inventory chat.` });
    } catch (e) {
      chats.setData((d) => d && { ...d, chats: d.chats.map((c) => (c.jid === chat.jid ? { ...c, in_scope: !on } : c)) });
      toast.show({ tone: "error", message: errorText(e, "Couldn't change the inventory setting.") });
    }
  };

  const openChat = (j: string) => {
    setHighlight(new Set());
    navigate(`/chats/${encodeURIComponent(j)}`);
  };
  const openMessage = (j: string, waId: string) => {
    if (j === jid) {
      convo.current?.jumpTo(waId);
      setParams({ around: waId }, { replace: true });
    } else navigate(`/chats/${encodeURIComponent(j)}?around=${encodeURIComponent(waId)}`);
  };

  // ---------- keys ----------
  const list = chats.data?.chats ?? [];
  const step = (d: number) => {
    if (!list.length) return;
    const i = list.findIndex((c) => c.jid === jid);
    const n = list[Math.max(0, Math.min(list.length - 1, i + d))];
    if (n) openChat(n.jid);
  };
  useHotkeys({
    "/": () => searchRef.current?.focus(),
    s: () => chat && !sync.running && (sync.busyWith ? sync.queue() : sync.start()),
    i: () => chat && togglePanel(),
    "]": () => {
      if (!desktop) setOverlayOpen(true);
      window.setTimeout(() => panelRef.current?.querySelector<HTMLElement>(".spanel")?.focus(), 0);
    },
    "[": () => (document.querySelector<HTMLElement>(".crow.is-active") ?? searchRef.current)?.focus(),
    "?": () => setHelp(true),
    Escape: () => phone && jid && navigate("/chats"),
  });
  useEffect(() => {
    const on = (e: KeyboardEvent) => {
      if ((e.ctrlKey || e.metaKey) && !e.shiftKey && e.key.toLowerCase() === "f" && chat) {
        e.preventDefault();
        convo.current?.openSearch();
      } else if (e.altKey && (e.key === "ArrowUp" || e.key === "ArrowDown")) {
        e.preventDefault();
        step(e.key === "ArrowDown" ? 1 : -1);
      }
    };
    window.addEventListener("keydown", on);
    return () => window.removeEventListener("keydown", on);
  });

  // ---------- render ----------
  const listPane = (
    <ChatList chats={chats.data?.chats} loading={chats.loading} error={chats.error} onRetry={() => void chats.reload()}
      activeJid={jid} filter={filter} onFilter={setFilter} query={query} onQuery={setQuery}
      onOpenChat={openChat} onOpenMessage={openMessage} searchRef={searchRef}
      onSyncAll={(mode) => void startStep("chat_routine", { body: { mode } })} onAssigned={() => void chats.reload()} />
  );
  const openCount = sugg.open;
  const panelBody = chat && (
    <SuggestionsPanel api={sugg} jid={chat.jid} highlight={highlight} onLocate={(p) => void locate(p)}
      onSync={sync.busyWith ? sync.queue : sync.start} metric={metric} hasMaster={Boolean(me?.has_master)} />
  );
  const top = [...list].filter((c) => c.open_suggestions > 0).sort((a, b) => b.open_suggestions - a.open_suggestions).slice(0, 3);

  let centre;
  if (chat) {
    centre = (
      <Conversation ref={convo} chat={chat} sync={sync} around={around} phone={phone} panelMode={desktop ? "inline" : "overlay"}
        panelOpen={panelOpen} openCount={openCount} onBack={() => navigate("/chats")} onTogglePanel={togglePanel}
        onScope={(on) => void setScope(on)} onMarker={onMarker} onReviewNew={showPanel} />
    );
  } else if (jid && chats.data) {
    centre = (
      <div className="convo convo-placeholder">
        <EmptyState icon={<MessagesSquare size={24} />} title="This chat isn't here">
          It may have been removed, or it hasn't been synced yet. Pick another chat.
        </EmptyState>
      </div>
    );
  } else if (!phone) {
    centre = (
      <div className="convo convo-placeholder">
        <EmptyState icon={<MessagesSquare size={24} />} title="Pick a chat">
          {top.length ? "These chats have the most open suggestions:" : "Choose a chat on the left to read it, sync it or review its suggestions."}
        </EmptyState>
        {top.length > 0 && (
          <div className="pick-list">
            {top.map((c) => (
              <button key={c.jid} type="button" className="pick" onClick={() => openChat(c.jid)}>
                <Avatar name={c.name} kind={c.kind} size={36} />
                <span className="ellipsis" dir="auto">{c.name}</span>
                <span className="crow-badge tnum">{c.open_suggestions}</span>
              </button>
            ))}
          </div>
        )}
      </div>
    );
  }

  const showList = !phone || !jid;
  const showCentre = !phone || Boolean(jid);
  return (
    <div className={`chats${phone ? " is-phone" : ""}${desktop && chat && panelOpen ? " has-panel" : ""}`}>
      {showList && <div className="chats-list">{listPane}</div>}
      {showCentre && (
        phone ? (
          <motion.div className="chats-centre" key={jid ?? "none"} initial={{ x: "100%" }} animate={{ x: 0 }}
            transition={{ duration: 0.36, ease: [0.16, 1, 0.3, 1] }}>
            {centre}
          </motion.div>
        ) : <div className="chats-centre">{centre}</div>
      )}
      {desktop && chat && (
        <AnimatePresence initial={false}>
          {panelOpen && (
            <motion.aside className="chats-panel" ref={panelRef} variants={drawerMotion} initial="initial" animate="animate" exit="exit"
              aria-label="Suggestions from this chat">
              <header className="chats-panel-head">
                <div className="eyebrow">Suggestions from this chat</div>
                <span className="caption muted tnum">{openCount} open</span>
              </header>
              {panelBody}
            </motion.aside>
          )}
        </AnimatePresence>
      )}
      {!desktop && chat && (
        <Drawer open={overlayOpen} onClose={() => setOverlayOpen(false)} width={420} eyebrow={<span dir="auto">{chat.name}</span>}
          title="Suggestions from this chat">
          <div ref={panelRef} className="chats-panel-drawer">{panelBody}</div>
        </Drawer>
      )}
      <KeyboardHelp open={help} onClose={() => setHelp(false)} groups={SHORTCUTS} />
    </div>
  );
}
