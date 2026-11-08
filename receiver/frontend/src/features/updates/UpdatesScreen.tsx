import { AnimatePresence, motion } from "framer-motion";
import { Check, CircleCheck, FileSpreadsheet, Filter, RefreshCw, Sparkles, X } from "lucide-react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useJobs } from "../../app/jobs";
import { useNavigate, useSearchParams } from "react-router-dom";
import { errorText } from "../../api/client";
import { BRAIN_CHANGED, getProposal, listProposals, type Proposal, type ProposalDetail, type ProposalList, type ProposalState } from "../../api/proposals";
import { useSession } from "../../app/session";
import { usePageTitle } from "../../app/title";
import {
  Button, ButtonLink, ConfirmDialog, Drawer, EmptyState, ErrorBanner, InfoBanner, KeyboardHelp, SkeletonRows, useToast,
  type ShortcutGroup,
} from "../../components";
import { relTime } from "../../lib/format";
import { useDelayed, useHotkeys, useMediaQuery } from "../../lib/hooks";
import { bulkBar } from "../../motion";
import { DetailPane } from "./DetailPane";
import { FilterBar, STATE_TABS, type Filters } from "./FilterBar";
import { QueueList, groupItems, needsSheet } from "./QueueList";
import { useDecisions } from "./useDecisions";
import "./updates.css";

const SHORTCUTS: ShortcutGroup[] = [
  { title: "Move", keys: [["j / ↓", "Next suggestion"], ["k / ↑", "Previous suggestion"], ["Enter", "Open details (narrow screens)"]] },
  {
    title: "Decide",
    keys: [["a", "Approve"], ["r", "Reject"], ["s", "Choose sheet, then 1–5"], ["u / Ctrl+Z", "Undo the last decision (5 s)"], ["o", "Open source in Chats"]],
  },
  {
    title: "Select",
    keys: [["x", "Select or clear"], ["Shift+X", "Select a range"], ["g", "Select the whole project group"], ["Shift+A", "Approve selected"], ["Shift+R", "Reject selected"], ["Esc", "Clear selection"]],
  },
  { title: "Find", keys: [["/", "Search"], ["f", "Filters"], ["1–4", "Open, Approved, Rejected, Superseded"], ["?", "This sheet"]] },
];

const PAGE = 200;

interface BulkConfirm {
  items: Proposal[];
  aliases: number;
  holds: number;
  skipped: number;
}

export function UpdatesScreen() {
  const { startStep } = useJobs();
  usePageTitle("Updates");
  const { me } = useSession();
  const toast = useToast();
  const navigate = useNavigate();
  const wide = useMediaQuery("(min-width: 1024px)");
  const metric = me?.market.metric ?? "sq m";

  // ---------- filters live in the URL (part 2 links here with ?chat=<jid>) ----------
  const [params, setParams] = useSearchParams();
  const filters: Filters = useMemo(() => ({
    state: (params.get("state") as ProposalState) || "proposed",
    sheet: params.get("sheet") ?? "",
    match: params.get("match") ?? "",
    q: params.get("q") ?? "",
    chat: params.get("chat") ?? "",
    warning: params.get("warning") ?? "",
    sort: (params.get("sort") as Filters["sort"]) || "project",
  }), [params]);
  const filterKey = JSON.stringify(filters);
  const setFilter = useCallback((patch: Partial<Filters>) => {
    setParams((prev) => {
      const n = new URLSearchParams(prev);
      for (const [k, v] of Object.entries(patch)) {
        if (v && !(k === "state" && v === "proposed") && !(k === "sort" && v === "project")) n.set(k, String(v));
        else n.delete(k);
      }
      return n;
    }, { replace: true });
  }, [setParams]);
  const [query, setQuery] = useState(filters.q);
  useEffect(() => {
    if (query === filters.q) return;
    const t = window.setTimeout(() => setFilter({ q: query }), 250);
    return () => window.clearTimeout(t);
  }, [query]); // eslint-disable-line react-hooks/exhaustive-deps

  // ---------- data ----------
  const [list, setList] = useState<ProposalList | null>(null);
  const [items, setItems] = useState<Proposal[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [moreBusy, setMoreBusy] = useState(false);
  const seq = useRef(0);
  const load = useCallback(async () => {
    const my = ++seq.current;
    setLoading(true);
    try {
      const res = await listProposals({ ...filters, limit: PAGE });
      if (my !== seq.current) return;
      setList(res);
      setItems(res.items);
      setError(null);
    } catch (e) {
      if (my === seq.current) setError(errorText(e, "Couldn't load suggestions."));
    } finally {
      if (my === seq.current) setLoading(false);
    }
  }, [filterKey]); // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => {
    void load();
  }, [load]);
  useEffect(() => {  // a brain decision was reopened from the list
    const on = () => void load();
    window.addEventListener(BRAIN_CHANGED, on);
    return () => window.removeEventListener(BRAIN_CHANGED, on);
  }, [load]);
  const loadMore = async () => {
    if (!list?.next_cursor) return;
    setMoreBusy(true);
    try {
      const res = await listProposals({ ...filters, limit: PAGE, cursor: list.next_cursor });
      setList(res);
      setItems((xs) => [...xs, ...res.items.filter((r) => !xs.some((x) => x.id === r.id))]);
    } catch (e) {
      toast.show({ tone: "error", message: errorText(e, "Couldn't load more suggestions.") });
    } finally {
      setMoreBusy(false);
    }
  };

  // ---------- decisions ----------
  const decisions = useDecisions((res) => {
    const gone = new Set([...res.done, ...res.skipped.filter((s) => s.state !== "proposed").map((s) => s.id)]);
    setItems((xs) => xs.filter((x) => !gone.has(x.id)));
    setList((l) => (l ? { ...l, counts: res.counts, total: Math.max(0, l.total - gone.size) } : l));
    setSelected((s) => {
      const n = new Set(s);
      gone.forEach((i) => n.delete(i));
      return n;
    });
  });
  const readOnly = filters.state !== "proposed";
  const visible = useMemo(() => items.filter((i) => !decisions.hidden.has(i.id)), [items, decisions.hidden]);
  const groups = useMemo(() => groupItems(visible), [visible]);
  const flat = useMemo(() => groups.flatMap((g) => g.items), [groups]);

  // ---------- focus & selection ----------
  const [focusedId, setFocusedId] = useState<number | null>(null);
  const lastIndex = useRef(0);
  const [selected, setSelected] = useState<Set<number>>(new Set());
  const anchor = useRef<number | null>(null);
  const [sheetChoice, setSheetChoice] = useState<Record<number, string>>({});
  const [pickingFor, setPickingFor] = useState<number | null>(null);
  const [detailOpen, setDetailOpen] = useState(false);
  const [help, setHelp] = useState(false);
  const [confirm, setConfirm] = useState<BulkConfirm | null>(null);
  const searchRef = useRef<HTMLInputElement>(null);
  const filtersRef = useRef<HTMLSelectElement>(null);

  useEffect(() => {
    const i = flat.findIndex((x) => x.id === focusedId);
    if (i >= 0) {
      lastIndex.current = i;
      return;
    }
    const next = flat[Math.min(lastIndex.current, flat.length - 1)];
    setFocusedId(next ? next.id : null);
  }, [flat, focusedId]);
  useEffect(() => {
    setSelected(new Set());
    lastIndex.current = 0;
    setFocusedId(null);
  }, [filterKey]);

  const focused = flat.find((x) => x.id === focusedId) ?? null;
  const scrollToFocused = (id: number) =>
    window.requestAnimationFrame(() => document.querySelector(`[data-pid="${id}"]`)?.scrollIntoView({ block: "nearest" }));
  const move = (d: number) => {
    if (!flat.length) return;
    const i = flat.findIndex((x) => x.id === focusedId);
    const n = flat[Math.max(0, Math.min(flat.length - 1, (i < 0 ? 0 : i) + d))];
    setFocusedId(n.id);
    setPickingFor(null);
    scrollToFocused(n.id);
  };

  // ---------- detail (debounced so j/k stays fast) ----------
  const cache = useRef(new Map<number, ProposalDetail>());
  const [detail, setDetail] = useState<ProposalDetail | undefined>();
  const [detailError, setDetailError] = useState<string | null>(null);
  const [detailSeq, setDetailSeq] = useState(0);
  useEffect(() => {
    if (focusedId === null) return;
    const hit = cache.current.get(focusedId);
    setDetail(hit);
    setDetailError(null);
    if (hit) return;
    let stop = false;
    const t = window.setTimeout(() => {
      getProposal(focusedId).then((d) => {
        cache.current.set(d.id, d);
        if (!stop) setDetail(d);
      }).catch((e) => !stop && setDetailError(errorText(e)));
    }, 120);
    return () => {
      stop = true;
      window.clearTimeout(t);
    };
  }, [focusedId, detailSeq]);
  useEffect(() => cache.current.clear(), [filterKey]);

  // ---------- actions ----------
  const nextFocusAfter = (ids: Set<number>) => {
    const i = flat.findIndex((x) => x.id === focusedId);
    if (i < 0 || !ids.has(flat[i].id)) return;
    const after = flat.slice(i + 1).find((x) => !ids.has(x.id)) ?? [...flat.slice(0, i)].reverse().find((x) => !ids.has(x.id));
    setFocusedId(after ? after.id : null);
    if (after) scrollToFocused(after.id);
  };
  const run = (batch: Proposal[], action: "approve" | "reject") => {
    const ids = new Set(batch.map((b) => b.id));
    nextFocusAfter(ids);
    const sheets: Record<number, string> = {};
    batch.forEach((b) => { if (sheetChoice[b.id]) sheets[b.id] = sheetChoice[b.id]; });
    decisions.decide(batch, action, sheets);
    setSelected((s) => new Set([...s].filter((x) => !ids.has(x))));
    setPickingFor(null);
    if (!wide) setDetailOpen(false);
  };
  const approveOne = (p: Proposal) => {
    if (needsSheet(p) && !sheetChoice[p.id]) {
      setFocusedId(p.id);
      setPickingFor(p.id);
      toast.show({ tone: "error", message: wide ? "Choose a sheet for this new project first (press 1–5 or use the menu)." : "Choose a sheet for this new project first." });
      if (!wide) setDetailOpen(true); // keyboard: the row's picker is highlighted and 1–5 choose
      return;
    }
    run([p], "approve");
  };
  const approveMany = (batch: Proposal[]) => {
    const blocked = batch.filter((p) => needsSheet(p) && !sheetChoice[p.id]);
    const ok = batch.filter((p) => !blocked.includes(p));
    if (!ok.length) {
      toast.show({ tone: "error", message: "Choose a sheet for the new projects first." });
      return;
    }
    const aliases = ok.filter((p) => p.warning_codes.includes("near_match")).length;
    const holds = ok.filter((p) => p.warning_codes.includes("lifts_hold")).length;
    if (aliases || holds) {
      setConfirm({ items: ok, aliases, holds, skipped: blocked.length });
      return;
    }
    run(ok, "approve");
    if (blocked.length) toast.show({ tone: "error", message: `${blocked.length} skipped: choose a sheet for new projects.` });
  };
  const selectedItems = flat.filter((x) => selected.has(x.id));
  const toggle = (id: number, range: boolean) => {
    setSelected((s) => {
      const n = new Set(s);
      if (range && anchor.current !== null) {
        const a = flat.findIndex((x) => x.id === anchor.current);
        const b = flat.findIndex((x) => x.id === id);
        if (a >= 0 && b >= 0) flat.slice(Math.min(a, b), Math.max(a, b) + 1).forEach((x) => n.add(x.id));
      } else if (n.has(id)) n.delete(id);
      else n.add(id);
      return n;
    });
    anchor.current = id;
  };
  const chooseSheet = (id: number, sheet: string) => {
    setSheetChoice((c) => ({ ...c, [id]: sheet }));
    setPickingFor(null);
  };
  const openChat = (p: Proposal | null) => {
    if (!p?.source.chat_jid) return;
    navigate(`/chats/${encodeURIComponent(p.source.chat_jid)}${p.source.kind === "message" ? `?around=${encodeURIComponent(p.source.ref)}` : ""}`);
  };

  const sheets = list?.sheets ?? [];
  useHotkeys({
    j: () => move(1), ArrowDown: () => move(1), k: () => move(-1), ArrowUp: () => move(-1),
    a: () => !readOnly && focused && approveOne(focused),
    r: () => !readOnly && focused && run([focused], "reject"),
    x: () => !readOnly && focused && toggle(focused.id, false),
    X: () => !readOnly && focused && toggle(focused.id, true),
    A: () => !readOnly && selectedItems.length && approveMany(selectedItems),
    R: () => !readOnly && selectedItems.length && run(selectedItems, "reject"),
    g: () => {
      if (readOnly || !focused) return;
      const g = groups.find((gr) => gr.items.some((x) => x.id === focused.id));
      if (g) setSelected((s) => new Set([...s, ...g.items.map((x) => x.id)]));
    },
    s: () => !readOnly && focused && needsSheet(focused) && setPickingFor(focused.id),
    o: () => openChat(focused),
    u: () => decisions.undo(),
    "/": () => searchRef.current?.focus(),
    f: () => filtersRef.current?.focus(),
    "?": () => setHelp(true),
    Enter: () => focused && !wide && setDetailOpen(true),
    Escape: () => {
      setPickingFor(null);
      setSelected(new Set());
    },
    ...Object.fromEntries([1, 2, 3, 4, 5].map((n) => [String(n), () => {
      if (pickingFor !== null) {
        if (sheets[n - 1]) chooseSheet(pickingFor, sheets[n - 1]);
      } else if (n <= 4) setFilter({ state: STATE_TABS[n - 1].key });
    }])),
  }, !confirm && !help);
  useEffect(() => {
    const on = (e: KeyboardEvent) => {
      const t = e.target as HTMLElement;
      if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "z" && !["INPUT", "TEXTAREA"].includes(t.tagName)) {
        if (decisions.undo()) e.preventDefault();
      }
    };
    window.addEventListener("keydown", on);
    return () => window.removeEventListener("keydown", on);
  }, [decisions]);

  // ---------- states ----------
  const showSkeleton = useDelayed(loading && !list, 300);
  const liveOpen = me?.counts.open_suggestions ?? 0;
  const knownOpen = list?.counts.proposed ?? 0;
  const newCount = filters.state === "proposed" && list ? liveOpen - knownOpen : 0;
  const jobAdding = Boolean(me?.job && /propose|chat steps|sync/i.test(me.job.name));
  const filtered = Boolean(filters.sheet || filters.match || filters.q || filters.chat || filters.warning);
  const chatName = filters.chat ? items.find((i) => i.source.chat_jid === filters.chat)?.source.chat_name : null;

  const detailPane = focused && (
    <DetailPane p={focused} detail={detail?.id === focused.id ? detail : undefined} error={detailError}
      onRetry={() => setDetailSeq((n) => n + 1)} readOnly={readOnly} sheets={sheets} sheetChoice={sheetChoice[focused.id]}
      onSheet={(s) => chooseSheet(focused.id, s)} onApprove={() => approveOne(focused)} onReject={() => run([focused], "reject")}
      onOpenChat={() => openChat(focused)} metric={metric} footerActions={wide} />
  );

  let body;
  if (list && !list.has_master && filters.state === "proposed" && !items.length) {
    body = (
      <div className="card">
        <EmptyState tone="blocking" icon={<FileSpreadsheet size={26} />} title="Upload the App Updates workbook to start"
          actions={<ButtonLink to="/master" variant="primary">Go to Master</ButtonLink>}>
          Suggestions compare chats with the team's master sheet, so the app needs the workbook first.
        </EmptyState>
      </div>
    );
  } else if (!list && showSkeleton) {
    body = <div className="card card-flush"><SkeletonRows rows={8} /></div>;
  } else if (!list && error) {
    body = <ErrorBanner onRetry={() => void load()}>Couldn't load suggestions.</ErrorBanner>;
  } else if (list && !visible.length) {
    body = filtered ? (
      <div className="card">
        <EmptyState icon={<Filter size={24} />} title="No suggestions match these filters"
          actions={<Button variant="secondary" onClick={() => { setQuery(""); setFilter({ sheet: "", match: "", q: "", chat: "", warning: "" }); }}>Clear filters</Button>} />
      </div>
    ) : filters.state === "proposed" ? (
      <div className="card">
        <EmptyState icon={<CircleCheck size={26} />} title="All caught up"
          actions={<>
            <ButtonLink to="/chats" variant="primary">Sync a chat</ButtonLink>
            <ButtonLink to="/insights" variant="ghost">See this week's changes</ButtonLink>
          </>}>
          {me?.last_job_finished_at ? `Last sync finished ${relTime(me.last_job_finished_at)}.` : "No sync has run yet."}
        </EmptyState>
      </div>
    ) : (
      <div className="card"><EmptyState title={`Nothing ${STATE_TABS.find((t) => t.key === filters.state)?.label.toLowerCase()} yet`} /></div>
    );
  } else if (list) {
    body = (
      <div className={`updates-split${wide ? "" : " is-single"}`}>
        <div className="updates-list">
          <div className="card card-flush">
            <QueueList key={filterKey} groups={groups} focusedId={focusedId} selected={selected} problems={decisions.problems}
              readOnly={readOnly} sheetChoice={sheetChoice} sheets={sheets} pickingFor={pickingFor}
              direction={decisions.direction} bulk={decisions.bulk} metric={metric}
              onFocus={setFocusedId} onOpen={() => !wide && setDetailOpen(true)} onToggle={toggle}
              onApprove={approveOne} onReject={(p) => run([p], "reject")} onSheet={chooseSheet} onGroupApprove={approveMany} />
            {list.next_cursor && (
              <div className="load-more">
                <Button variant="ghost" disabled={moreBusy} onClick={() => void loadMore()}>
                  Load more ({list.total - items.length} left)
                </Button>
              </div>
            )}
          </div>
          <AnimatePresence>
            {!readOnly && selected.size > 0 && (
              <motion.div className="bulkbar" variants={bulkBar} initial="initial" animate="animate" exit="exit" role="region" aria-label="Selected suggestions">
                <span className="strong tnum">{selected.size} selected</span>
                <span className="spacer" />
                <Button variant="primary" icon={<Check size={16} />} kbd="⇧A" onClick={() => approveMany(selectedItems)}>Approve {selected.size}</Button>
                <Button variant="ghost" icon={<X size={16} />} kbd="⇧R" onClick={() => run(selectedItems, "reject")}>Reject {selected.size}</Button>
                <Button variant="ghost" size="sm" onClick={() => setSelected(new Set())}>Clear</Button>
              </motion.div>
            )}
          </AnimatePresence>
        </div>
        {wide && (
          <aside className="updates-detail card" aria-label="Suggestion details">
            {detailPane || <p className="muted small">Pick a suggestion to see its source and the sheet row.</p>}
          </aside>
        )}
      </div>
    );
  }

  return (
    <div className="page updates">
      <FilterBar filters={filters} query={query} onQuery={setQuery} counts={list?.counts} warningCounts={list?.warning_counts}
        sheets={list?.sheets ?? []} onChange={setFilter} searchRef={searchRef} filtersRef={filtersRef} onHelp={() => setHelp(true)}
        chatName={chatName} />
      {(jobAdding || newCount > 0) && (
        <InfoBanner action={newCount > 0 ? (
          <button type="button" className="refresh-pill" onClick={() => void load()}>
            <RefreshCw size={14} aria-hidden="true" /> Refresh (+{newCount})
          </button>
        ) : undefined}>
          {jobAdding ? "New suggestions are being added." : `${newCount} new ${newCount === 1 ? "suggestion" : "suggestions"} arrived.`}
        </InfoBanner>
      )}
      {error && list && <ErrorBanner onRetry={() => void load()}>Couldn't refresh suggestions. Showing the last list.</ErrorBanner>}
      {filters.state === "proposed" && items.length > 0 && (
        <div className="brain-bar">
          <Sparkles size={15} aria-hidden="true" />
          <span className="small">The brain checks every sync: it moves false new rows onto existing rows, approves safe
            updates and rejects clear noise. What's left here needs you.</span>
          <Button size="sm" variant="secondary" onClick={() => void startStep("brain_review")}>Review Backlog Now</Button>
        </div>
      )}
      {body}
      {!wide && (
        <Drawer open={detailOpen && Boolean(focused)} onClose={() => setDetailOpen(false)}
          title="Suggestion"
          footer={focused && !readOnly ? (
            <div className="detail-sticky">
              <Button variant="primary" block icon={<Check size={18} />} onClick={() => approveOne(focused)}
                disabled={needsSheet(focused) && !sheetChoice[focused.id]}>Approve</Button>
              <Button variant="ghost" block icon={<X size={18} />} onClick={() => run([focused], "reject")}>Reject</Button>
            </div>
          ) : undefined}>
          {detailPane}
        </Drawer>
      )}
      <ConfirmDialog open={Boolean(confirm)} title={`Approve ${confirm?.items.length ?? 0} suggestions?`}
        confirmLabel={`Approve ${confirm?.items.length ?? 0}`}
        onCancel={() => setConfirm(null)}
        onConfirm={() => {
          if (!confirm) return;
          run(confirm.items, "approve");
          if (confirm.skipped) toast.show({ tone: "error", message: `${confirm.skipped} skipped: choose a sheet for new projects.` });
          setConfirm(null);
        }}>
        {confirm && (
          <ul className="confirm-list">
            {confirm.aliases > 0 && (
              <li><b className="tnum">{confirm.aliases}</b> {confirm.aliases === 1 ? "chat name is" : "chat names are"} saved as another name for a sheet project (near {confirm.aliases === 1 ? "match" : "matches"}).</li>
            )}
            {confirm.holds > 0 && (
              <li><b className="tnum">{confirm.holds}</b> Hold/Hide {confirm.holds === 1 ? "flag is" : "flags are"} lifted on Sheet 2 (today's date is written).</li>
            )}
            {confirm.skipped > 0 && (
              <li><b className="tnum">{confirm.skipped}</b> new {confirm.skipped === 1 ? "project has" : "projects have"} no sheet yet and {confirm.skipped === 1 ? "is" : "are"} left out.</li>
            )}
            <li className="muted">You can undo for 5 seconds after approving.</li>
          </ul>
        )}
      </ConfirmDialog>
      <KeyboardHelp open={help} onClose={() => setHelp(false)} groups={SHORTCUTS} />
    </div>
  );
}
