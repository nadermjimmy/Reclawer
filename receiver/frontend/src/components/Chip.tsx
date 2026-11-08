import { Link2, OctagonAlert, SquarePlus, Type, Unlock } from "lucide-react";
import type { ReactNode } from "react";
import type { WarningCode } from "../api/proposals";

export type ChipTone = "blocking" | "caution" | "info" | "neutral";

export function Chip({ tone = "neutral", icon, children, title, selected, onClick, count }: {
  tone?: ChipTone;
  icon?: ReactNode;
  children: ReactNode;
  title?: string;
  selected?: boolean;
  onClick?: () => void;
  count?: number;
}) {
  const body = (
    <>
      {icon && <span className="chip-icon" aria-hidden="true">{icon}</span>}
      <span>{children}</span>
      {count !== undefined && <span className="chip-count tnum">{count}</span>}
    </>
  );
  if (onClick)
    return (
      <button type="button" className={`chip chip-${tone} chip-button${selected ? " is-selected" : ""}`} title={title}
        aria-pressed={selected} onClick={onClick}>
        {body}
      </button>
    );
  return <span className={`chip chip-${tone}`} title={title}>{body}</span>;
}

/** Display metadata for warning codes. Part 2 renders the same chips in the Chats insights panel. */
export const WARNING_META: Record<WarningCode, { label: string; tone: ChipTone; icon: ReactNode; help: string }> = {
  needs_sheet: {
    label: "Choose sheet",
    tone: "blocking",
    icon: <OctagonAlert size={14} />,
    help: "New project with no known location. Pick the sheet before approving.",
  },
  near_match: {
    label: "Near match",
    tone: "caution",
    icon: <Link2 size={14} />,
    help: "The chat name is close to a sheet name. Approving saves the chat name as another name for that project.",
  },
  lifts_hold: {
    label: "Lifts Hold/Hide",
    tone: "caution",
    icon: <Unlock size={14} />,
    help: "Approving writes today's date on Sheet 2 and removes Hold/Hide.",
  },
  bad_format: {
    label: "Check format",
    tone: "caution",
    icon: <Type size={14} />,
    help: "The value doesn't follow the sheet's notation. Check it before approving.",
  },
  new_project: {
    label: "New row",
    tone: "info",
    icon: <SquarePlus size={14} />,
    help: "Approving adds this project as a new row.",
  },
};

export function WarningChip({ code, sheet }: { code: WarningCode; sheet?: string }) {
  const m = WARNING_META[code];
  if (!m) return null;
  const text = code === "new_project" && sheet ? `New row on ${sheet}` : m.label;
  return (
    <Chip tone={m.tone} icon={m.icon} title={m.help}>
      {text}
    </Chip>
  );
}
