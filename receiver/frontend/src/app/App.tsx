import { MotionConfig } from "framer-motion";
import { BrowserRouter, Navigate, Route, Routes, useLocation, useParams, useSearchParams } from "react-router-dom";
import { ToastProvider } from "../components";
import { InventoryScreen, ProjectScreen } from "../features/inventory/InventoryScreen";
import { LoginScreen } from "../features/login/LoginScreen";
import { MasterScreen } from "../features/master/MasterScreen";
import { MoreScreen } from "../features/more/MoreScreen";
import { CommandPalette } from "../features/palette";
import { PipelineScreen } from "../features/pipeline/PipelineScreen";
import { ChatsPlaceholder, InsightsPlaceholder } from "../features/placeholders/Placeholders";
import { ReviewScreen } from "../features/review/ReviewScreen";
import { SourcesScreen } from "../features/sources/SourcesScreen";
import { UpdatesScreen } from "../features/updates/UpdatesScreen";
import { baseTransition } from "../motion";
import { JobsProvider } from "./jobs";
import { SessionProvider, useSession } from "./session";
import { Shell } from "./Shell";
import { TitleProvider } from "./title";

/** Old URLs (as /app/<old path>) land on their new home, so bookmarks keep working (spec §1.1). */
function LegacyRedirect({ to }: { to: (p: Record<string, string | undefined>, q: URLSearchParams) => string }) {
  const params = useParams();
  const [q] = useSearchParams();
  return <Navigate to={to(params, q)} replace />;
}

const enc = encodeURIComponent;
const LEGACY: [string, (p: Record<string, string | undefined>, q: URLSearchParams) => string][] = [
  ["/dashboard", () => "/more/pipeline"],
  ["/feed", () => "/chats"],
  ["/groups", () => "/chats"],
  ["/groups/:jid", (p) => `/chats/${enc(p.jid ?? "")}`],
  ["/sources/message/:waId", (p) => `/chats?around=${enc(p.waId ?? "")}`],
  ["/jobs/:id", (p) => `/more/pipeline?job=${p.id}`],
  ["/projects", () => "/more/inventory"],
  ["/projects/:slug", (p) => `/more/inventory/${enc(p.slug ?? "")}`],
  ["/units", (_, q) => `/more/inventory?tab=units${q.get("q") ? `&q=${enc(q.get("q")!)}` : ""}`],
  ["/units/:id", (p) => `/more/inventory?tab=units&unit=${p.id}`],
  ["/review", (_, q) => `/more/review${q.get("kind") ? `?kind=${enc(q.get("kind")!)}` : ""}`],
  ["/review/mappings", (_, q) => `/more/review/mappings${q.get("state") ? `?state=${enc(q.get("state")!)}` : ""}`],
  ["/review/context", () => "/more/review/mappings"],
  ["/review/images", () => "/more/review/images"],
  ["/review/fact/:id", () => "/more/review/facts"],
  ["/sources", () => "/more/sources"],
  ["/sources/row/:id", () => "/more/sources"],
  ["/reconciliation", () => "/more/sources/reconciliation"],
];

function NotFound() {
  const loc = useLocation();
  return <Navigate to="/updates" replace state={{ from: loc.pathname }} />;
}

function Gate() {
  const { status } = useSession();
  if (status === "loading") return <div className="boot" aria-busy="true" />;
  if (status === "anon") return <LoginScreen />;
  if (status === "offline")
    return (
      <div className="boot boot-offline" role="alert">
        <p>Couldn't reach the server. Check your connection, then reload this page.</p>
      </div>
    );
  return (
    <JobsProvider>
      <Routes>
        <Route element={<Shell />}>
          <Route index element={<Navigate to="/updates" replace />} />
          <Route path="updates" element={<UpdatesScreen />} />
          <Route path="chats" element={<ChatsPlaceholder />} />
          <Route path="chats/:jid" element={<ChatsPlaceholder />} />
          <Route path="insights" element={<InsightsPlaceholder />} />
          <Route path="master" element={<MasterScreen />} />
          <Route path="more" element={<MoreScreen />} />
          <Route path="more/pipeline" element={<PipelineScreen />} />
          <Route path="more/inventory" element={<InventoryScreen />} />
          <Route path="more/inventory/:slug" element={<ProjectScreen />} />
          <Route path="more/review" element={<ReviewScreen />} />
          <Route path="more/review/:tab" element={<ReviewScreen />} />
          <Route path="more/sources" element={<SourcesScreen />} />
          <Route path="more/sources/:tab" element={<SourcesScreen />} />
          {LEGACY.map(([path, to]) => (
            <Route key={path} path={path.slice(1)} element={<LegacyRedirect to={to} />} />
          ))}
          <Route path="*" element={<NotFound />} />
        </Route>
      </Routes>
      <CommandPalette />
    </JobsProvider>
  );
}

export function App() {
  return (
    <MotionConfig reducedMotion="user" transition={baseTransition}>
      <BrowserRouter basename="/app">
        <ToastProvider>
          <SessionProvider>
            <TitleProvider>
              <Gate />
            </TitleProvider>
          </SessionProvider>
        </ToastProvider>
      </BrowserRouter>
    </MotionConfig>
  );
}
