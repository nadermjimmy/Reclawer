import { Building2, Check, ChevronDown, FolderKanban, SlidersHorizontal } from "lucide-react";
import { useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { errorText } from "../../api/client";
import { getAssignment, saveAssignment, type Assignment, type Chat } from "../../api/chats";
import { Button, useToast } from "../../components";
import { matchesAll, tokens } from "../../lib/text";

export const CHATS_CHANGED = "chats:changed";

/** A small popover anchored under its trigger; closes on outside click and Escape. */
function Popover({ open, onClose, children, label }: { open: boolean; onClose: () => void; children: ReactNode; label: string }) {
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!open) return;
    const down = (e: MouseEvent) => { if (ref.current && !ref.current.parentElement?.contains(e.target as Node)) onClose(); };
    const key = (e: KeyboardEvent) => { if (e.key === "Escape") { e.stopPropagation(); onClose(); } };
    document.addEventListener("mousedown", down);
    document.addEventListener("keydown", key, true);
    return () => { document.removeEventListener("mousedown", down); document.removeEventListener("keydown", key, true); };
  }, [open, onClose]);
  if (!open) return null;
  return <div ref={ref} className="assign-pop" role="dialog" aria-label={label}>{children}</div>;
}

/** The team's assignment of this chat: developer (from the master's developers) and that developer's projects.
 * Every ingestion from this chat uses it, and each change is stored as a team label. */
export function AssignmentBar({ chat, onChanged, onHabits }: {
  chat: Chat;
  onChanged: (developer: string) => void;
  onHabits: () => void;
}) {
  const toast = useToast();
  const [data, setData] = useState<Assignment | null>(null);
  const [devOpen, setDevOpen] = useState(false);
  const [projOpen, setProjOpen] = useState(false);
  const [query, setQuery] = useState("");
  const [picked, setPicked] = useState<Set<string>>(new Set());
  const [busy, setBusy] = useState(false);

  const load = (dev?: string) => getAssignment(chat.jid, dev).then((a) => {
    setData(a);
    setPicked(new Set(a.assigned.filter((x) => x.status === "ok").map((x) => x.project_key)));
  }).catch(() => undefined);
  useEffect(() => { setData(null); void load(); }, [chat.jid]); // eslint-disable-line react-hooks/exhaustive-deps

  const team = data?.profile?.source === "team" ? data.profile.developer : "";
  const suggested = data?.suggested ?? "";
  const toks = useMemo(() => tokens(query), [query]);
  const devs = useMemo(() => (data?.developers ?? []).filter((d) => !toks.length || matchesAll(d, toks)).slice(0, 80),
    [data, toks]);

  const save = async (developer: string, projectKeys?: string[], newDev = false) => {
    setBusy(true);
    try {
      await saveAssignment(chat.jid, { developer, project_keys: projectKeys, new_developer: newDev });
      await load(developer);
      onChanged(developer);
      window.dispatchEvent(new CustomEvent(CHATS_CHANGED));
      toast.show({ message: projectKeys ? "Projects saved for this group" : developer ? `Assigned to ${developer}` : "Assignment cleared" });
    } catch (e) {
      toast.show({ tone: "error", message: errorText(e, "Couldn't save. Try again.") });
    } finally {
      setBusy(false);
    }
  };

  const nAssigned = data?.assigned.filter((a) => a.status === "ok").length ?? 0;
  const missing = data?.assigned.filter((a) => a.status === "missing") ?? [];
  if (chat.kind !== "group") return null;
  return (
    <div className="assign-bar">
      <div className="assign-slot">
        <button type="button" className={`assign-btn${team ? "" : " is-empty"}`} aria-haspopup="dialog" aria-expanded={devOpen}
          onClick={() => { setDevOpen((o) => !o); setProjOpen(false); setQuery(""); }} disabled={!data}>
          <Building2 size={14} aria-hidden="true" />
          <span className="assign-k caption">Developer</span>
          <span className="assign-v ellipsis" dir="auto">{team || (suggested ? `Suggested: ${suggested}` : "Assign developer")}</span>
          <ChevronDown size={14} aria-hidden="true" />
        </button>
        <Popover open={devOpen} onClose={() => setDevOpen(false)} label="Choose developer">
          {suggested && !team && (
            <button type="button" className="assign-opt assign-suggest" disabled={busy}
              onClick={() => { setDevOpen(false); void save(suggested, undefined, !data?.developers.includes(suggested)); }}>
              <Check size={14} aria-hidden="true" /> Confirm <b dir="auto">{suggested}</b>
              {!data?.developers.includes(suggested) && <span className="caption muted">not on the master yet</span>}
            </button>
          )}
          <input className="input input-sm" autoFocus placeholder="Search developers on the master" value={query}
            onChange={(e) => setQuery(e.target.value)} dir="auto" aria-label="Search developers" />
          <div className="assign-list" role="listbox" aria-label="Developers">
            {devs.map((d) => (
              <button type="button" role="option" aria-selected={d === team} key={d} className={`assign-opt${d === team ? " is-on" : ""}`}
                disabled={busy} onClick={() => { setDevOpen(false); void save(d); }}>
                <span dir="auto">{d}</span>{d === team && <Check size={14} aria-hidden="true" />}
              </button>
            ))}
            {!devs.length && <p className="caption muted assign-note">No developer on the master matches.</p>}
          </div>
          <div className="assign-foot">
            {query.trim() && !devs.some((d) => d.toLowerCase() === query.trim().toLowerCase()) && (
              <Button size="sm" variant="ghost" disabled={busy} onClick={() => { setDevOpen(false); void save(query.trim(), undefined, true); }}>
                Not on the sheet yet: use “{query.trim()}”
              </Button>
            )}
            {team && <Button size="sm" variant="ghost" disabled={busy} onClick={() => { setDevOpen(false); void save(""); }}>Clear</Button>}
          </div>
        </Popover>
      </div>

      <div className="assign-slot">
        <button type="button" className="assign-btn" aria-haspopup="dialog" aria-expanded={projOpen}
          disabled={!team || !data} title={team ? undefined : "Assign a developer first"}
          onClick={() => { setProjOpen((o) => !o); setDevOpen(false); }}>
          <FolderKanban size={14} aria-hidden="true" />
          <span className="assign-k caption">Projects</span>
          <span className="assign-v ellipsis">{nAssigned ? `${nAssigned} assigned` : "All of the developer's"}</span>
          {missing.length > 0 && <span className="pill pill-orange caption">{missing.length} not on sheet</span>}
          <ChevronDown size={14} aria-hidden="true" />
        </button>
        <Popover open={projOpen} onClose={() => setProjOpen(false)} label="Choose projects">
          <p className="caption muted assign-note">Suggestions from this group go to these projects first. Leave all
            unticked to use any of {team}'s projects.</p>
          <div className="assign-list">
            {(data?.projects ?? []).map((p) => (
              <label key={p.project_key} className="assign-check">
                <input type="checkbox" checked={picked.has(p.project_key)} onChange={(e) => setPicked((s) => {
                  const n = new Set(s); if (e.target.checked) n.add(p.project_key); else n.delete(p.project_key); return n;
                })} />
                <span dir="auto">{p.project}</span><span className="caption muted">{p.sheets.join(", ")}</span>
              </label>
            ))}
            {!data?.projects.length && <p className="caption muted assign-note">No rows for this developer on the master.</p>}
            {missing.map((m) => (
              <p key={m.project_key} className="caption warn-text assign-note" dir="auto">{m.project_label}: no longer on the master</p>
            ))}
          </div>
          <div className="assign-foot">
            <Button size="sm" variant="primary" disabled={busy} onClick={() => { setProjOpen(false); void save(team, [...picked]); }}>
              Save Projects
            </Button>
          </div>
        </Popover>
      </div>

      <button type="button" className="assign-btn assign-habits" onClick={onHabits} title="How this developer shares data">
        <SlidersHorizontal size={14} aria-hidden="true" /><span className="assign-v">Habits</span>
      </button>
    </div>
  );
}
