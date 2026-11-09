import { MotionConfig } from "framer-motion";
import { BrowserRouter, Navigate, Route, Routes, useLocation, useParams, useSearchParams } from "react-router-dom";
import { ToastProvider } from "../components";
import { LoginScreen } from "../features/login/LoginScreen";
import { SheetScreen } from "../features/sheet/SheetScreen";
import { MasterScreen } from "../features/master/MasterScreen";
import { MoreScreen } from "../features/more/MoreScreen";
import { ChatsScreen } from "../features/chats/ChatsScreen";
import { InsightsScreen } from "../features/insights/InsightsScreen";
import { CommandPalette } from "../features/palette";
import { PipelineScreen } from "../features/pipeline/PipelineScreen";
import { SourcesScreen } from "../features/sources/SourcesScreen";
import { BrainScreen } from "../features/brain/BrainScreen";
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
  ["/sources", () => "/more/sources"],
  ["/sources/row/:id", () => "/more/sources"],
];

function NotFound() {
  const loc = useLocation();
  return <Navigate to="/chats" replace state={{ from: loc.pathname }} />;
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
          <Route index element={<Navigate to="/chats" replace />} />
          <Route path="updates" element={<UpdatesScreen />} />
          <Route path="sheet" element={<SheetScreen />} />
          <Route path="chats" element={<ChatsScreen />} />
          <Route path="chats/:jid" element={<ChatsScreen />} />
          <Route path="insights" element={<InsightsScreen />} />
          <Route path="master" element={<MasterScreen />} />
          <Route path="more" element={<MoreScreen />} />
          <Route path="more/pipeline" element={<PipelineScreen />} />
          <Route path="more/sources" element={<SourcesScreen />} />
          <Route path="more/sources/:tab" element={<SourcesScreen />} />
          <Route path="more/brain" element={<BrainScreen />} />
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
