import { Check, Crosshair, FileSpreadsheet, Inbox, RefreshCw, X } from "lucide-react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { chatSuggestions, type ChatSuggestions } from "../../api/chats";
import { errorText } from "../../api/client";
import type { Proposal } from "../../api/proposals";
import {
  Button, ButtonLink, EmptyState, ErrorBanner, IconButton, Segmented, Skeleton, StatePill, WarningChip, useToast,
} from "../../components";
import { relTime } from "../../lib/format";
import { useDelayed } from "../../lib/hooks";
import { Change, displayProject } from "../updates/Change";
import { needsSheet } from "../updates/QueueList";
import { useDecisions, type DecisionsApi } from "../updates/useDecisions";
import "../updates/updates.css";

export type PanelTab = "open" | "approved" | "all";

export interface ChatSuggestionsApi {
  data: ChatSuggestions | undefined;
  error: string | null;
  loading: boolean;
  reload: () => Promise<ChatSuggestions | undefined>;
  visible: Proposal[];
  open: number;
  decisions: DecisionsApi;
  sheetChoice: Record<number, string>;
  chooseSheet: (id: number, sheet: string) => void;
  approve: (p: Proposal) => void;
  reject: (p: Proposal) => void;
  /** New suggestions from the last sync, shown with an accent until the panel is next reloaded. */
  fresh: Set<number>;
  markFresh: (ids: number[]) => void;
}

/** State for "Suggestions from this chat". Lives in the Chats screen so closing the panel (tablet / phone
    slide-over) doesn't commit the decision waiting in its 5 s undo window. */
export function useChatSuggestions(jid: string | null, onDecided: () => void): ChatSuggestionsApi {
  const toast = useToast();
  const [data, setData] = useState<ChatSuggestions>();
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [sheetChoice, setSheetChoice] = useState<Record<number, string>>({});
  const [fresh, setFresh] = useState<Set<number>>(new Set());
  const seq = useRef(0);
  const reload = useCallback(async () => {
    if (!jid) return undefined;
    const my = ++seq.current;
    setLoading(true);
    try {
      const d = await chatSuggestions(jid);
      if (my === seq.current) {
        setData(d);
        setError(null);
      }
      return d;
    } catch (e) {
      if (my === seq.current) setError(errorText(e, "Couldn't load suggestions."));
    } finally {
      if (my === seq.current) setLoading(false);
    }
    return undefined;
  }, [jid]);
  useEffect(() => {
    setData(undefined);
    setSheetChoice({});
    setFresh(new Set());
    void reload();
  }, [reload]);

  const decisions = useDecisions((res, batch) => {
    const done = new Set(res.done);
    setData((d) => d && {
      ...d,
      items: d.items.map((i) => (done.has(i.id) ? { ...i, state: batch.action === "approve" ? "approved" : "rejected", decided_at: Date.now() / 1000 } : i)),
      counts: {
        proposed: Math.max(0, d.counts.proposed - done.size),
        approved: d.counts.approved + (batch.action === "approve" ? done.size : 0),
        rejected: d.counts.rejected + (batch.action === "reject" ? done.size : 0),
      },
    });
    onDecided();
  });
  const visible = useMemo(() => (data?.items ?? []).filter((i) => !decisions.hidden.has(i.id)), [data, decisions.hidden]);
  const open = visible.filter((i) => i.state === "proposed").length;
  const approve = (p: Proposal) => {
    if (needsSheet(p) && !sheetChoice[p.id]) {
      toast.show({ tone: "error", message: "Choose a sheet for this new project first." });
      return;
    }
    decisions.decide([p], "approve", sheetChoice[p.id] ? { [p.id]: sheetChoice[p.id] } : {});
  };
  const reject = (p: Proposal) => decisions.decide([p], "reject");
  return {
    data, error, loading, reload, visible, open, decisions, sheetChoice,
    chooseSheet: (id, sheet) => setSheetChoice((c) => ({ ...c, [id]: sheet })),
    approve, reject, fresh,
    markFresh: (ids) => setFresh(new Set(ids)),
  };
}

function Card({ p, focused, highlighted, fresh, sheets, sheetChoice, onSheet, onFocus, onApprove, onReject, onLocate, metric }: {
  p: Proposal;
  focused: boolean;
  highlighted: boolean;
  fresh: boolean;
  sheets: string[];
  sheetChoice?: string;
  onSheet: (s: string) => void;
  onFocus: () => void;
  onApprove: () => void;
  onReject: () => void;
  onLocate: () => void;
  metric: string;
}) {
  const open = p.state === "proposed";
  const blocked = needsSheet(p) && !sheetChoice;
  const name = `${p.field_label}, ${displayProject(p)}`;
  return (
    <article className={`scard${focused ? " is-focused" : ""}${highlighted ? " is-highlighted" : ""}${fresh ? " is-fresh" : ""}`}
      data-sid={p.id} onClick={onFocus} aria-current={focused ? "true" : undefined}>
      <header className="scard-head">
        <div className="scard-titles">
          <span className="scard-project" dir="auto">{displayProject(p)}</span>
          <span className="caption muted">
            {p.sheet || "No sheet yet"}
            {p.row_number ? ` · Row ${p.row_number}` : p.match_state === "new" ? " · New row" : ""} · {p.field_label}
          </span>
        </div>
        {!open && <StatePill state={p.state} />}
        {fresh && open && <span className="scard-new">New</span>}
      </header>
      <Change p={p} metric={metric} />
      {p.quote && <blockquote className="scard-quote" dir="auto">{p.quote}</blockquote>}
      {(p.warning_codes.length > 0 || p.source.kind !== "message") && (
        <div className="scard-chips">
          {p.source.kind !== "message" && (
            <span className="source-chip" title={p.source.file_name ?? undefined}>
              <FileSpreadsheet size={14} aria-hidden="true" className="source-icon" />
              <span className="ellipsis" dir="auto">{p.source.file_name ?? "File"}</span>
            </span>
          )}
          {p.warning_codes.map((c) => <WarningChip key={c} code={c} sheet={p.sheet} />)}
        </div>
      )}
      {open && needsSheet(p) && (
        <label className="field" onClick={(e) => e.stopPropagation()}>
          <span className="field-label">Sheet for this new project</span>
          <select className="select select-sm" value={sheetChoice ?? ""} onChange={(e) => onSheet(e.target.value)}>
            <option value="">Choose sheet</option>
            {sheets.map((s) => <option key={s} value={s}>{s}</option>)}
          </select>
        </label>
      )}
      <footer className="scard-actions" onClick={(e) => e.stopPropagation()}>
        {open && (
          <>
            <Button size="sm" variant="primary" icon={<Check size={16} />} onClick={onApprove} disabled={blocked}
              aria-label={`Approve ${name}`} title={blocked ? "Choose a sheet first" : "Approve (A)"}>Approve</Button>
            <Button size="sm" variant="ghost" icon={<X size={16} />} onClick={onReject} aria-label={`Reject ${name}`} title="Reject (R)">Reject</Button>
          </>
        )}
        {!open && p.decided_at && <span className="caption muted tnum">{relTime(p.decided_at)}</span>}
        <span className="spacer" />
        <IconButton size="sm" label={`Locate quote in the chat (L)`} icon={<Crosshair size={16} />} onClick={onLocate} />
      </footer>
    </article>
  );
}

/** "Suggestions from this chat": approve / reject in place with the same keys as Updates (a, r, j/k, u) once the
    panel has focus, 5 s undo, and Locate to scroll the conversation to the source message. */
export function SuggestionsPanel({ api, jid, highlight, onLocate, onSync, metric, hasMaster }: {
  api: ChatSuggestionsApi;
  jid: string;
  highlight: Set<number>;
  onLocate: (p: Proposal) => void;
  onSync: () => void;
  metric: string;
  hasMaster: boolean;
}) {
  const [tab, setTab] = useState<PanelTab>("open");
  const [focusedId, setFocusedId] = useState<number | null>(null);
  const box = useRef<HTMLDivElement>(null);
  const { data, error, visible, decisions } = api;
  const shown = useMemo(() => visible.filter((p) => (tab === "open" ? p.state === "proposed" : tab === "approved" ? p.state === "approved" : true)), [visible, tab]);
  const skeleton = useDelayed(!data && !error, 300);

  useEffect(() => {
    if (highlight.size) {
      const hit = (data?.items ?? []).find((i) => highlight.has(i.id));
      if (hit && hit.state !== "proposed" && tab === "open") setTab("all");
      if (hit) {
        setFocusedId(hit.id);
        window.requestAnimationFrame(() => box.current?.querySelector(`[data-sid="${hit.id}"]`)?.scrollIntoView({ block: "nearest" }));
      }
    }
  }, [highlight]); // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => {
    if (focusedId !== null && shown.some((p) => p.id === focusedId)) return;
    setFocusedId(shown[0]?.id ?? null);
  }, [shown, focusedId]);

  // keys work once the panel has focus (Tab or "]" moves focus here)
  useEffect(() => {
    const el = box.current;
    if (!el) return;
    const on = (e: KeyboardEvent) => {
      const t = e.target as HTMLElement;
      if (e.metaKey || e.ctrlKey || e.altKey || ["INPUT", "SELECT", "TEXTAREA"].includes(t.tagName)) return;
      const i = shown.findIndex((p) => p.id === focusedId);
      const cur = shown[i];
      const go = (d: number) => {
        const n = shown[Math.max(0, Math.min(shown.length - 1, (i < 0 ? 0 : i) + d))];
        if (n) {
          setFocusedId(n.id);
          el.querySelector(`[data-sid="${n.id}"]`)?.scrollIntoView({ block: "nearest" });
        }
      };
      const keys: Record<string, () => void> = {
        j: () => go(1), ArrowDown: () => go(1), k: () => go(-1), ArrowUp: () => go(-1),
        a: () => cur?.state === "proposed" && api.approve(cur),
        r: () => cur?.state === "proposed" && api.reject(cur),
        l: () => cur && onLocate(cur),
        u: () => decisions.undo(),
      };
      const fn = keys[e.key];
      if (fn) {
        e.preventDefault();
        e.stopPropagation();
        if ((e.key === "a" || e.key === "r") && cur) go(1);
        fn();
      }
    };
    el.addEventListener("keydown", on);
    return () => el.removeEventListener("keydown", on);
  }, [shown, focusedId, api, decisions, onLocate]);

  const counts = data?.counts;
  return (
    <div className="spanel" ref={box} tabIndex={-1} aria-label="Suggestions from this chat">
      <div className="spanel-tabs">
        <Segmented size="sm" label="Show" value={tab} onChange={setTab} items={[
          { key: "open", label: "Open", count: api.open },
          { key: "approved", label: "Approved", count: visible.filter((p) => p.state === "approved").length },
          { key: "all", label: "All", count: visible.length },
        ]} />
      </div>
      <div className="spanel-list">
        {error && !data && <ErrorBanner onRetry={() => void api.reload()}>Couldn't load suggestions from this chat.</ErrorBanner>}
        {skeleton && (
          <div className="stack">
            {[0, 1, 2].map((i) => <Skeleton key={i} height={120} radius={16} />)}
          </div>
        )}
        {data && !shown.length && (
          !data.has_master ? (
            <EmptyState icon={<FileSpreadsheet size={22} />} title="Upload master to get suggestions"
              actions={<ButtonLink to="/master" size="sm" variant="primary">Go to Master</ButtonLink>}>
              Suggestions compare this chat with the team's master sheet.
            </EmptyState>
          ) : tab === "open" ? (
            <EmptyState icon={<Inbox size={22} />} title={counts?.approved || counts?.rejected ? "No open suggestions" : "No suggestions from this chat"}
              actions={<Button size="sm" variant="secondary" icon={<RefreshCw size={14} />} onClick={onSync}>Sync now</Button>}>
              {hasMaster ? "Sync now reads the latest messages and files and suggests sheet updates." : null}
            </EmptyState>
          ) : (
            <EmptyState title="Nothing here yet">Decisions from the last 30 days show here.</EmptyState>
          )
        )}
        {shown.map((p) => (
          <Card key={p.id} p={p} focused={p.id === focusedId} highlighted={highlight.has(p.id)} fresh={api.fresh.has(p.id)}
            sheets={data?.sheets ?? []} sheetChoice={api.sheetChoice[p.id]} onSheet={(s) => api.chooseSheet(p.id, s)}
            onFocus={() => setFocusedId(p.id)} onApprove={() => api.approve(p)} onReject={() => api.reject(p)}
            onLocate={() => onLocate(p)} metric={metric} />
        ))}
      </div>
      {data && (data.counts.proposed > 0 || visible.length > 0) && (
        <footer className="spanel-foot">
          <ButtonLink to={`/updates?chat=${encodeURIComponent(jid)}`} variant="ghost" size="sm">
            Review all {data.counts.proposed > 0 ? data.counts.proposed : ""} in Updates
          </ButtonLink>
        </footer>
      )}
    </div>
  );
}
