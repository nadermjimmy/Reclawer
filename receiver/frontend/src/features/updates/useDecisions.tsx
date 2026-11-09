import { useCallback, useEffect, useRef, useState } from "react";
import { errorText, postKeepalive } from "../../api/client";
import { decide as apiDecide, REJECT_REASONS, setRejectReason, type DecideAction, type DecideResult, type Proposal,
  type RejectReason } from "../../api/proposals";
import { useSession } from "../../app/session";
import { useToast } from "../../components";

/** Owner decision: undo = a 5-second client-side delayed commit; the backend has no re-open. */
export const UNDO_MS = 5000;

interface Batch {
  key: number;
  action: DecideAction;
  items: Proposal[];
  sheetById: Record<number, string>;
  timer: number;
  reason?: RejectReason;
}

export interface DecisionsApi {
  /** Ids hidden from the list: waiting in the undo window or being committed. */
  hidden: Set<number>;
  /** Rows that came back from the server with a reason (shown with a red outline). */
  problems: Map<number, string>;
  /** +1 approve / -1 reject: direction of the exit animation for the latest batch. */
  direction: number;
  bulk: boolean;
  decide: (items: Proposal[], action: DecideAction, sheetById?: Record<number, string>) => void;
  undo: () => boolean;
  flush: () => void;
}

let seq = 0;

/** Optimistic decisions with a 5 s undo window. A new batch commits the previous one first; leaving the
    screen commits at once; closing the tab commits with a keepalive request. */
export function useDecisions(onCommitted: (res: DecideResult, batch: { items: Proposal[]; action: DecideAction }) => void): DecisionsApi {
  const toast = useToast();
  const { refresh, setPendingDecisions } = useSession();
  const [hidden, setHidden] = useState<Set<number>>(new Set());
  const [problems, setProblems] = useState<Map<number, string>>(new Map());
  const [direction, setDirection] = useState(1);
  const [bulk, setBulk] = useState(false);
  const pending = useRef<Batch | null>(null);
  const committedCb = useRef(onCommitted);
  committedCb.current = onCommitted;

  const unhide = useCallback((ids: number[]) => {
    setHidden((h) => {
      const n = new Set(h);
      ids.forEach((i) => n.delete(i));
      return n;
    });
  }, []);

  const commit = useCallback(
    async (b: Batch) => {
      if (pending.current?.key === b.key) pending.current = null;
      window.clearTimeout(b.timer);
      setPendingDecisions(0);
      const ids = b.items.map((i) => i.id);
      try {
        const res = await apiDecide(ids, b.action, b.sheetById, b.reason);
        const back = res.skipped.filter((s) => s.state === "proposed");
        if (back.length) {
          setProblems((m) => {
            const n = new Map(m);
            back.forEach((s) => n.set(s.id, s.reason));
            return n;
          });
          unhide(back.map((s) => s.id));
          toast.show({
            tone: "error",
            message: `${back.length} skipped: ${back.length === 1 ? back[0].reason : "choose a sheet for new projects"}`,
          });
        }
        committedCb.current(res, b);
        unhide(ids.filter((i) => !back.some((s) => s.id === i)));
      } catch (e) {
        unhide(ids);
        toast.show({
          tone: "error",
          duration: 0,
          message: `Couldn't save ${ids.length === 1 ? "that decision" : `${ids.length} decisions`}. ${errorText(e, "")}`.trim(),
          action: { label: "Retry", onClick: () => decideRef.current(b.items, b.action, b.sheetById) },
        });
      } finally {
        void refresh();
      }
    },
    [refresh, setPendingDecisions, toast, unhide],
  );

  const flush = useCallback(() => {
    const b = pending.current;
    if (b) void commit(b);
  }, [commit]);

  const decide = useCallback(
    (items: Proposal[], action: DecideAction, sheetById: Record<number, string> = {}) => {
      if (!items.length) return;
      flush();
      const ids = items.map((i) => i.id);
      setDirection(action === "approve" ? 1 : -1);
      setBulk(items.length > 10);
      setProblems((m) => {
        if (!ids.some((i) => m.has(i))) return m;
        const n = new Map(m);
        ids.forEach((i) => n.delete(i));
        return n;
      });
      setHidden((h) => new Set([...h, ...ids]));
      const key = ++seq;
      const b: Batch = { key, action, items, sheetById, timer: 0 };
      b.timer = window.setTimeout(() => void commit(b), UNDO_MS);
      pending.current = b;
      setPendingDecisions(items.length);
      const verb = action === "approve" ? "Approved" : "Rejected";
      const text = items.length === 1 ? `${verb}: ${items[0].field_label}, ${items[0].row_project || items[0].project}` : `${verb} ${items.length} suggestions`;
      const why = (r: RejectReason) => {  // before the commit it rides along; after, it's stored on its own
        if (pending.current?.key === key) b.reason = r;
        else void setRejectReason(ids, r).catch(() => undefined);
        toast.show({ id: "undo", duration: 2500, message: `${text} · ${REJECT_REASONS.find((x) => x.key === r)?.label}` });
      };
      toast.show({
        id: "undo",
        duration: action === "reject" ? UNDO_MS + 3000 : UNDO_MS,
        message: action === "reject" ? (
          <span className="toast-why">{text}<span className="toast-why-q">Why?</span>
            {REJECT_REASONS.map((r) => (
              <button key={r.key} type="button" className="toast-why-chip" onClick={() => why(r.key)}>{r.label}</button>
            ))}
          </span>
        ) : text,
        action: { label: "Undo", onClick: () => undoRef.current() },
      });
    },
    [commit, flush, setPendingDecisions, toast],
  );
  const decideRef = useRef(decide);
  decideRef.current = decide;

  const undo = useCallback(() => {
    const b = pending.current;
    if (!b) return false;
    window.clearTimeout(b.timer);
    pending.current = null;
    setPendingDecisions(0);
    unhide(b.items.map((i) => i.id));
    toast.dismiss("undo");
    return true;
  }, [setPendingDecisions, toast, unhide]);
  const undoRef = useRef(undo);
  undoRef.current = undo;

  // leave the screen -> commit now; close the tab -> keepalive request
  useEffect(() => {
    const onHide = () => {
      const b = pending.current;
      if (!b) return;
      window.clearTimeout(b.timer);
      pending.current = null;
      postKeepalive("/proposals/decide", { ids: b.items.map((i) => i.id), action: b.action, sheet_by_id: b.sheetById, reason: b.reason });
    };
    window.addEventListener("pagehide", onHide);
    return () => {
      window.removeEventListener("pagehide", onHide);
      const b = pending.current;
      if (b) {
        void commit(b);
        toast.dismiss("undo");
      }
    };
  }, [commit, toast]);

  return { hidden, problems, direction, bulk, decide, undo, flush };
}
