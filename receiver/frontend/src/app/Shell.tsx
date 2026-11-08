import { AnimatePresence, motion } from "framer-motion";
import { Ellipsis, LogOut } from "lucide-react";
import { NavLink, Outlet, useLocation } from "react-router-dom";
import { PaletteTrigger } from "../features/palette";
import { counter, routeFade } from "../motion";
import { JobPill } from "./jobs";
import { MORE_NAV, PRIMARY_NAV, type NavItem } from "./nav";
import { useSession } from "./session";
import { usePageTitleValue } from "./title";

/** Route fade key: a new screen fades in; moving between chats keeps the Chats screen mounted. */
const routeKey = (path: string) => (path.startsWith("/chats") ? "/chats" : path.split("/").slice(0, 3).join("/"));

function useBadge(item: NavItem) {
  const { me, pendingDecisions } = useSession();
  if (!item.badge || !me) return 0;
  const n = me.counts[item.badge] ?? 0;
  return item.badge === "open_suggestions" ? Math.max(0, n - pendingDecisions) : n;
}

function Badge({ n, max = 99 }: { n: number; max?: number }) {
  return (
    <AnimatePresence initial={false} mode="popLayout">
      {n > 0 && (
        <motion.span key={n} className="nav-badge tnum" variants={counter} initial="initial" animate="animate" exit="exit">
          {n > max ? `${max}+` : n}
        </motion.span>
      )}
    </AnimatePresence>
  );
}

function RailLink({ item }: { item: NavItem }) {
  const n = useBadge(item);
  const Icon = item.icon;
  return (
    <NavLink to={item.to} className={({ isActive }) => `rail-link${isActive ? " is-active" : ""}`}
      aria-label={n ? `${item.label}, ${n}` : item.label} data-tip={item.label}>
      <Icon size={22} aria-hidden="true" />
      <Badge n={n} />
    </NavLink>
  );
}

function TabLink({ item }: { item: NavItem }) {
  const n = useBadge(item);
  const Icon = item.icon;
  return (
    <NavLink to={item.to} className={({ isActive }) => `tab-link${isActive ? " is-active" : ""}`}>
      <span className="tab-icon">
        <Icon size={22} aria-hidden="true" />
        <Badge n={n} />
      </span>
      <span className="tab-label">{item.label}</span>
    </NavLink>
  );
}

/** Desktop/tablet: 72px navy icon rail + top bar. Phone (<640px): top bar + bottom tab bar. */
export function Shell() {
  const { me, logout } = useSession();
  const title = usePageTitleValue();
  const location = useLocation();
  const moreActive = location.pathname.startsWith("/more");
  const moreBadge = useBadge(MORE_NAV[2]) + useBadge(MORE_NAV[3]);
  return (
    <div className="shell">
      <nav className="rail" aria-label="Main">
        <div className="rail-brand" aria-hidden="true">Byit</div>
        <div className="rail-group">
          {PRIMARY_NAV.map((i) => <RailLink key={i.to} item={i} />)}
        </div>
        <div className="rail-sep" />
        <div className="rail-group">
          {MORE_NAV.map((i) => <RailLink key={i.to} item={i} />)}
        </div>
        <div className="spacer" />
        <button type="button" className="rail-link" onClick={() => void logout()} aria-label="Log out" data-tip="Log out">
          <LogOut size={20} aria-hidden="true" />
        </button>
      </nav>
      <div className="main">
        <header className="topbar">
          <h1 className="topbar-title ellipsis">{title}</h1>
          <PaletteTrigger />
          <div className="topbar-right">
            <JobPill />
            {me && <span className="market" title={`${me.market.name} · ${me.market.currency}`}>{me.market.short}</span>}
          </div>
        </header>
        <main className="content" id="content">
          <motion.div key={routeKey(location.pathname)} className="route" variants={routeFade}
            initial="initial" animate="animate">
            <Outlet />
          </motion.div>
        </main>
      </div>
      <nav className="tabbar" aria-label="Main">
        {PRIMARY_NAV.filter((i) => !i.railOnly).map((i) => <TabLink key={i.to} item={i} />)}
        <NavLink to="/more" className={`tab-link${moreActive ? " is-active" : ""}`}>
          <span className="tab-icon">
            <Ellipsis size={22} aria-hidden="true" />
            <Badge n={moreBadge} />
          </span>
          <span className="tab-label">More</span>
        </NavLink>
      </nav>
    </div>
  );
}
