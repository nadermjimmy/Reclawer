import { ChevronRight, LogOut } from "lucide-react";
import { Link } from "react-router-dom";
import { MORE_NAV, PRIMARY_NAV } from "../../app/nav";
import { useSession } from "../../app/session";
import { usePageTitle } from "../../app/title";
import "./more.css";

const DESCRIPTIONS: Record<string, string> = {
  "/sheet": "The master as a grid, with approved changes and open suggestions",
  "/master": "Upload the master workbook, rows to place, downloads",
  "/more/pipeline": "Sync all chats, sheet updates, brain, eval and retrain; recent runs, Start over",
  "/more/sources": "Files and failed downloads",
  "/more/brain": "Models, eval per source kind, LLM share, cost and flags",
};

export function MoreScreen() {
  usePageTitle("More");
  const { me, logout } = useSession();
  return (
    <div className="page">
      <nav className="more-list card" aria-label="More">
        {[...PRIMARY_NAV.filter((i) => i.railOnly), ...MORE_NAV].map((i) => {
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
