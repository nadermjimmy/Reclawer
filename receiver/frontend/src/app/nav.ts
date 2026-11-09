import { Brain, ChartColumn, FileSpreadsheet, FolderOpen, Inbox, MessagesSquare, Sheet, Workflow, type LucideIcon } from "lucide-react";
import type { ShellCounts } from "../api/session";

export interface NavItem {
  to: string;
  label: string;
  icon: LucideIcon;
  /** Which /api/me count shows as a badge. */
  badge?: keyof ShellCounts;
  /** Rail only: on phones it's listed under More, so the bottom bar keeps five tabs. */
  railOnly?: boolean;
}

/** Primary destinations, in this order (rail on desktop, bottom tabs on phone). */
export const PRIMARY_NAV: NavItem[] = [
  { to: "/chats", label: "Chats", icon: MessagesSquare, badge: "chats_with_suggestions" },
  { to: "/insights", label: "Insights", icon: ChartColumn },
  { to: "/sheet", label: "Sheet", icon: Sheet },
  { to: "/updates", label: "Updates", icon: Inbox, badge: "open_suggestions" },
  { to: "/master", label: "Master", icon: FileSpreadsheet, badge: "rows_to_place", railOnly: true },
];

/** Under More (also listed in the desktop rail). */
export const MORE_NAV: NavItem[] = [
  { to: "/more/pipeline", label: "Pipeline", icon: Workflow },
  { to: "/more/sources", label: "Sources", icon: FolderOpen, badge: "failed_downloads" },
  { to: "/more/brain", label: "Brain", icon: Brain },
];
