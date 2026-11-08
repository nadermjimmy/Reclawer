import { AnimatePresence, motion, useReducedMotion, type Variants } from "framer-motion";
import { Check, FileSpreadsheet, FileText, Image as ImageIcon, MessageSquare, Sparkles, X } from "lucide-react";
import { memo, useState } from "react";
import { BRAIN_CHANGED, reopen, type Proposal } from "../../api/proposals";
import { Button, IconButton, WarningChip } from "../../components";
import { relTime } from "../../lib/format";
import { dur, ease, rowEnter, rowExit } from "../../motion";
import { Change, displayProject } from "./Change";

export interface Group {
  key: string;
  project: string;
  developer: string;
  sheet: string;
  items: Proposal[];
}

export const needsSheet = (p: Proposal) => p.warning_codes.includes("needs_sheet");

/** Rows grouped under project headers, in the order the API returned them. */
export function groupItems(items: Proposal[]): Group[] {
  const out: Group[] = [];
  const by = new Map<string, Group>();
  for (const p of items) {
    const key = `${displayProject(p)}|${p.sheet}`;
    let g = by.get(key);
    if (!g) {
      g = { key, project: displayProject(p), developer: p.developer, sheet: p.sheet, items: [] };
      by.set(key, g);
      out.push(g);
    }
    g.items.push(p);
  }
  return out;
}

export function SourceChip({ p }: { p: Proposal }) {
  const s = p.source;
  const Icon = s.kind === "message" ? MessageSquare : s.kind === "row" ? FileSpreadsheet : s.kind === "image" ? ImageIcon : FileText;
  const what = s.kind === "message" ? "Message" : s.kind === "row" ? "File row" : s.kind === "image" ? "Image" : "PDF page";
  const where = s.chat_name || s.file_name || "Unknown chat";
  return (
    <span className="source-chip" title={`${what}${s.file_name ? ` in ${s.file_name}` : ""}${s.chat_name ? `, ${s.chat_name}` : ""}`}>
      <Icon size={14} aria-label={what} className="source-icon" />
      <span className="ellipsis" dir="auto">{where}</span>
      <span className="source-time tnum">{relTime(s.ts)}</span>
    </span>
  );
}

export interface RowHandlers {
  onFocus: (id: number) => void;
  onOpen: (id: number) => void;
  onToggle: (id: number, range: boolean) => void;
  onApprove: (p: Proposal) => void;
  onReject: (p: Proposal) => void;
  onSheet: (id: number, sheet: string) => void;
}

interface RowProps extends RowHandlers {
  p: Proposal;
  focused: boolean;
  selected: boolean;
  problem?: string;
  readOnly: boolean;
  sheetChoice?: string;
  sheets: string[];
  picking: boolean;
  metric: string;
}

const VERDICT: Record<string, string> = {
  update_existing: "Existing row", new_project: "New project", noise: "Noise", unsure: "Unsure",
};

export function BrainLine({ p }: { p: Proposal }) {
  const b = p.brain!;
  const who = p.decided_by === "brain" ? (p.state === "approved" ? "Approved by brain" : "Rejected by brain") : `Brain: ${VERDICT[b.verdict] ?? b.verdict}`;
  return (
    <span className={`qbrain qbrain-${b.verdict}`} title={`${who} (${b.confidence} confidence)`}>
      <Sparkles size={13} aria-hidden="true" />
      <span className="qbrain-who">{who}</span>
      <span className="qbrain-reason" dir="auto">{b.reason}</span>
    </span>
  );
}

function ReopenButton({ p }: { p: Proposal }) {
  const [busy, setBusy] = useState(false);
  return (
    <button type="button" className="link-btn" disabled={busy} onClick={() => {
      setBusy(true);
      void reopen(p.id).then(() => window.dispatchEvent(new CustomEvent(BRAIN_CHANGED))).finally(() => setBusy(false));
    }}>Reopen</button>
  );
}

const QueueRow = memo(function QueueRow({ p, focused, selected, problem, readOnly, sheetChoice, sheets, picking, metric,
  onFocus, onOpen, onToggle, onApprove, onReject, onSheet }: RowProps) {
  const blocked = needsSheet(p) && !sheetChoice;
  const name = `${p.field_label}, ${displayProject(p)}`;
  return (
    <div className={`qrow${focused ? " is-focused" : ""}${selected ? " is-selected" : ""}${problem ? " has-problem" : ""}`}
      data-pid={p.id} aria-current={focused ? "true" : undefined}
      onClick={() => { onFocus(p.id); onOpen(p.id); }}>
      {!readOnly && (
        <label className="qcheck" onClick={(e) => e.stopPropagation()}>
          <input type="checkbox" checked={selected} aria-label={`Select ${name}`}
            onChange={(e) => onToggle(p.id, (e.nativeEvent as MouseEvent).shiftKey)} />
        </label>
      )}
      <div className="qfield">
        <span className="qfield-label">{p.field_label}</span>
        <span className="qfield-meta caption tnum">
          {p.row_number ? `Row ${p.row_number}` : p.match_state === "new" ? "New row" : p.sheet ? "Added row" : ""}
        </span>
      </div>
      <div className="qchange"><Change p={p} metric={metric} /></div>
      <div className="qmeta">
        <SourceChip p={p} />
        {p.warning_codes.map((c) => <WarningChip key={c} code={c} sheet={p.sheet} />)}
        {p.brain && <BrainLine p={p} />}
        {!readOnly && needsSheet(p) && (
          <label className={`qsheet${picking ? " is-picking" : ""}`} onClick={(e) => e.stopPropagation()}>
            <span className="sr-only">Sheet for {name}</span>
            <select className="select select-sm" value={sheetChoice ?? ""} data-sheet-for={p.id}
              onChange={(e) => onSheet(p.id, e.target.value)}>
              <option value="">Choose sheet</option>
              {sheets.map((s) => <option key={s} value={s}>{s}</option>)}
            </select>
          </label>
        )}
      </div>
      {!readOnly ? (
        <div className="qactions" onClick={(e) => e.stopPropagation()}>
          <IconButton label={blocked ? "Choose a sheet before approving" : `Approve ${name} (A)`} icon={<Check size={18} />}
            variant="ghost" size="sm" className="qbtn qbtn-approve" aria-disabled={blocked}
            onClick={() => onApprove(p)} />
          <IconButton label={`Reject ${name} (R)`} icon={<X size={18} />} variant="ghost" size="sm" className="qbtn qbtn-reject"
            onClick={() => onReject(p)} />
        </div>
      ) : (
        <div className="qactions caption muted tnum" onClick={(e) => e.stopPropagation()}>
          {p.decided_by === "brain" && <ReopenButton p={p} />}
          {p.decided_at ? relTime(p.decided_at) : ""}
        </div>
      )}
      {problem && <div className="qproblem" role="status">{problem}</div>}
    </div>
  );
});

export function QueueList({ groups, focusedId, selected, problems, readOnly, sheetChoice, sheets, pickingFor, direction, bulk,
  metric, onGroupApprove, ...handlers }: RowHandlers & {
  groups: Group[];
  focusedId: number | null;
  selected: Set<number>;
  problems: Map<number, string>;
  readOnly: boolean;
  sheetChoice: Record<number, string>;
  sheets: string[];
  pickingFor: number | null;
  direction: number;
  bulk: boolean;
  metric: string;
  onGroupApprove: (items: Proposal[]) => void;
}) {
  const reduced = Boolean(useReducedMotion());
  const enter = rowEnter(reduced);
  const custom = { direction, bulk };
  // exit reads `custom` from AnimatePresence, so a row leaves in the direction of the decision that removed it
  const rowVariants = {
    initial: enter.initial,
    animate: enter.animate,
    exit: (d: { direction: number; bulk: boolean }) => rowExit(d.bulk ? 0 : d.direction, reduced),
  } as Variants;
  return (
    <div className="queue" role="list" aria-label="Suggestions">
      <AnimatePresence initial={false} custom={custom}>
        {groups.map((g) => (
          <motion.section key={g.key} className="qgroup" role="listitem" custom={custom}
            initial={enter.initial} animate={enter.animate} exit={{ opacity: 0, height: 0, transition: { duration: reduced ? 0 : dur.base, ease: ease.standard, delay: reduced ? 0 : dur.base } }}>
            <header className="qgroup-head">
              <div className="qgroup-titles">
                <span className="qgroup-project" dir="auto">{g.project}</span>
                <span className="qgroup-meta caption">
                  {g.developer && <span dir="auto">{g.developer}</span>}
                  {g.sheet ? <span>{g.sheet}</span> : <span className="qgroup-nosheet">No sheet yet</span>}
                  <span className="tnum">{g.items.length} {g.items.length === 1 ? "suggestion" : "suggestions"}</span>
                </span>
              </div>
              {!readOnly && g.items.length > 1 && (
                <Button size="sm" variant="ghost" onClick={() => onGroupApprove(g.items)}>Approve {g.items.length}</Button>
              )}
            </header>
            <div className="qgroup-rows">
              <AnimatePresence initial={false} custom={custom}>
                {g.items.map((p) => (
                  <motion.div key={p.id} className="qrow-wrap" custom={custom} variants={rowVariants} initial="initial"
                    animate="animate" exit="exit">
                    <QueueRow p={p} focused={p.id === focusedId} selected={selected.has(p.id)} problem={problems.get(p.id)}
                      readOnly={readOnly} sheetChoice={sheetChoice[p.id]} sheets={sheets} picking={pickingFor === p.id}
                      metric={metric} {...handlers} />
                  </motion.div>
                ))}
              </AnimatePresence>
            </div>
          </motion.section>
        ))}
      </AnimatePresence>
    </div>
  );
}
