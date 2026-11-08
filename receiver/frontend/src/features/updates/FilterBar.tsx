import { Keyboard, X } from "lucide-react";
import type { RefObject } from "react";
import type { ProposalState, WarningCode } from "../../api/proposals";
import { Chip, IconButton, SearchInput, Segmented, Select, WARNING_META } from "../../components";

export interface Filters {
  state: ProposalState;
  sheet: string;
  match: string;
  q: string;
  chat: string;
  warning: string;
  sort: "project" | "newest" | "warnings";
}

export const STATE_TABS: { key: ProposalState; label: string }[] = [
  { key: "proposed", label: "Open" },
  { key: "approved", label: "Approved" },
  { key: "rejected", label: "Rejected" },
  { key: "superseded", label: "Superseded" },
];

const WARNING_ORDER: WarningCode[] = ["near_match", "lifts_hold", "needs_sheet", "bad_format", "new_project"];

export function FilterBar({ filters, query, onQuery, counts, warningCounts, sheets, onChange, searchRef, filtersRef, onHelp, chatName }: {
  filters: Filters;
  query: string;
  onQuery: (q: string) => void;
  counts?: Record<ProposalState, number>;
  warningCounts?: Record<WarningCode, number>;
  sheets: string[];
  onChange: (patch: Partial<Filters>) => void;
  searchRef: RefObject<HTMLInputElement | null>;
  filtersRef: RefObject<HTMLSelectElement | null>;
  onHelp: () => void;
  chatName?: string | null;
}) {
  const active = new Set(filters.warning.split(",").filter(Boolean));
  const anyFilter = Boolean(filters.sheet || filters.match || filters.q || filters.chat || filters.warning);
  return (
    <div className="filterbar">
      <div className="filterbar-top">
        <Segmented label="Suggestion state" value={filters.state} onChange={(state) => onChange({ state })}
          items={STATE_TABS.map((t) => ({ ...t, count: counts?.[t.key] }))} />
        <span className="spacer" />
        <SearchInput ref={searchRef} label="Search project, developer or sheet name" placeholder="Search projects"
          className="filter-search" value={query} onChange={(e) => onQuery(e.target.value)} hint={<kbd>/</kbd>}
          onKeyDown={(e) => { if (e.key === "Escape") (e.target as HTMLInputElement).blur(); }} />
        <IconButton label="Keyboard shortcuts (?)" icon={<Keyboard size={18} />} onClick={onHelp} className="hide-touch" />
      </div>
      <div className="filterbar-row" role="group" aria-label="Filters">
        <Select ref={filtersRef} label="Sheet" hideLabel value={filters.sheet} onChange={(e) => onChange({ sheet: e.target.value })}
          className="filter-select">
          <option value="">All sheets</option>
          {sheets.map((s) => <option key={s} value={s}>{s}</option>)}
        </Select>
        <Select label="Match" hideLabel value={filters.match} onChange={(e) => onChange({ match: e.target.value })} className="filter-select">
          <option value="">Any match</option>
          <option value="exact">Exact name</option>
          <option value="alias">Saved name</option>
          <option value="near">Near match</option>
          <option value="new">New project</option>
        </Select>
        <Select label="Sort" hideLabel value={filters.sort} onChange={(e) => onChange({ sort: e.target.value as Filters["sort"] })}
          className="filter-select">
          <option value="project">By project</option>
          <option value="newest">Newest source</option>
          <option value="warnings">Warnings first</option>
        </Select>
        <span className="filter-divider" aria-hidden="true" />
        {WARNING_ORDER.map((code) => {
          const m = WARNING_META[code];
          const on = active.has(code);
          return (
            <Chip key={code} tone="neutral" icon={m.icon} selected={on} count={warningCounts?.[code]}
              title={m.help} onClick={() => {
                const n = new Set(active);
                if (on) n.delete(code);
                else n.add(code);
                onChange({ warning: [...n].join(",") });
              }}>
              {code === "new_project" ? "New project" : m.label}
            </Chip>
          );
        })}
        {filters.chat && (
          <Chip tone="info" selected onClick={() => onChange({ chat: "" })} title="Remove the chat filter">
            <span dir="auto">Chat: {chatName || filters.chat}</span> <X size={12} aria-hidden="true" />
          </Chip>
        )}
        {anyFilter && (
          <button type="button" className="link-btn" onClick={() => { onQuery(""); onChange({ sheet: "", match: "", q: "", chat: "", warning: "" }); }}>
            Clear filters
          </button>
        )}
      </div>
    </div>
  );
}
