import { ChevronRight, LogOut } from "lucide-react";
import { Link } from "react-router-dom";
import { MORE_NAV } from "../../app/nav";
import { useSession } from "../../app/session";
import { usePageTitle } from "../../app/title";
import "./more.css";

const DESCRIPTIONS: Record<string, string> = {
  "/more/pipeline": "Steps 1 to 8 and 6b, recent runs, Start over",
  "/more/inventory": "Projects, units and unit history",
  "/more/review": "Issues, mappings, images and facts",
  "/more/sources": "Files, failed downloads and reconciliation",
};

export function MoreScreen() {
  usePageTitle("More");
  const { me, logout } = useSession();
  return (
    <div className="page">
      <nav className="more-list card" aria-label="More">
        {MORE_NAV.map((i) => {
          const Icon = i.icon;
          const n = i.badge && me ? me.counts[i.badge] : 0;
          return (
            <Link key={i.to} to={i.to} className="more-item">
              <span className="more-icon"><Icon size={20} aria-hidden="true" /></span>
              <span className="more-text">
                <span className="strong">{i.label}</span>
                <span className="small muted">{DESCRIPTIONS[i.to]}</span>
              </span>
              {n ? <span className="pill pill-orange tnum">{n}</span> : null}
              <ChevronRight size={18} aria-hidden="true" className="muted" />
            </Link>
          );
        })}
        <button type="button" className="more-item" onClick={() => void logout()}>
          <span className="more-icon"><LogOut size={20} aria-hidden="true" /></span>
          <span className="more-text"><span className="strong">Log Out</span></span>
        </button>
      </nav>
    </div>
  );
}
