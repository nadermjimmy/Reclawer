import { Boxes, ChartColumn, ClipboardCheck, FileSpreadsheet, FolderOpen, Inbox, MessagesSquare, Workflow, type LucideIcon } from "lucide-react";
import type { ShellCounts } from "../api/session";

export interface NavItem {
  to: string;
  label: string;
  icon: LucideIcon;
  /** Which /api/me count shows as a badge. */
  badge?: keyof ShellCounts;
}

/** Primary destinations (rail on desktop, bottom tabs on phone). Part 2 owns Chats and Insights. */
export const PRIMARY_NAV: NavItem[] = [
  { to: "/updates", label: "Updates", icon: Inbox, badge: "open_suggestions" },
  { to: "/chats", label: "Chats", icon: MessagesSquare, badge: "chats_with_suggestions" },
  { to: "/insights", label: "Insights", icon: ChartColumn },
  { to: "/master", label: "Master", icon: FileSpreadsheet, badge: "rows_to_place" },
];

/** Under More (also listed in the desktop rail). */
export const MORE_NAV: NavItem[] = [
  { to: "/more/pipeline", label: "Pipeline", icon: Workflow },
  { to: "/more/inventory", label: "Inventory", icon: Boxes },
  { to: "/more/review", label: "Review", icon: ClipboardCheck, badge: "open_issues" },
  { to: "/more/sources", label: "Sources", icon: FolderOpen, badge: "failed_downloads" },
];
