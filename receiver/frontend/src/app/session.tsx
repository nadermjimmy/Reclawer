import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { onUnauthorized } from "../api/client";
import { getMe, login as apiLogin, logout as apiLogout, type Me } from "../api/session";
import { usePageVisible } from "../lib/hooks";

type Status = "loading" | "anon" | "ready" | "offline";

interface SessionApi {
  status: Status;
  me: Me | null;
  refresh: () => Promise<void>;
  login: (password: string) => Promise<void>;
  logout: () => Promise<void>;
  /** Decisions waiting in the undo window; badges subtract them so counters move at once. */
  pendingDecisions: number;
  setPendingDecisions: (n: number) => void;
}

const Ctx = createContext<SessionApi | null>(null);

export function useSession() {
  const c = useContext(Ctx);
  if (!c) throw new Error("useSession outside SessionProvider");
  return c;
}

/** Shell state from GET /api/me, polled every 15 s (5 s while a job runs, paused while the tab is hidden). */
export function SessionProvider({ children }: { children: ReactNode }) {
  const [status, setStatus] = useState<Status>("loading");
  const [me, setMe] = useState<Me | null>(null);
  const [pendingDecisions, setPendingDecisions] = useState(0);
  const visible = usePageVisible();
  const inflight = useRef<Promise<void> | null>(null);

  const refresh = useCallback(() => {
    if (inflight.current) return inflight.current;
    const p = getMe()
      .then((m) => {
        setMe(m);
        setStatus("ready");
      })
      .catch((e: { status?: number }) => {
        if (e?.status === 401) setStatus("anon");
        else setStatus((s) => (s === "loading" ? "offline" : s));
      })
      .finally(() => {
        inflight.current = null;
      });
    inflight.current = p;
    return p;
  }, []);

  useEffect(() => {
    void refresh();
    return onUnauthorized(() => {
      setStatus("anon");
      setMe(null);
    });
  }, [refresh]);

  const running = Boolean(me?.job);
  useEffect(() => {
    if (status !== "ready" || !visible) return;
    const t = window.setInterval(() => void refresh(), running ? 5000 : 15000);
    return () => window.clearInterval(t);
  }, [status, visible, running, refresh]);

  useEffect(() => {
    if (visible && status === "ready") void refresh();
  }, [visible]); // eslint-disable-line react-hooks/exhaustive-deps

  const login = useCallback(
    async (password: string) => {
      await apiLogin(password);
      await refresh();
    },
    [refresh],
  );
  const logout = useCallback(async () => {
    await apiLogout().catch(() => undefined);
    setMe(null);
    setStatus("anon");
  }, []);

  const api = useMemo(
    () => ({ status, me, refresh, login, logout, pendingDecisions, setPendingDecisions }),
    [status, me, refresh, login, logout, pendingDecisions],
  );
  return <Ctx.Provider value={api}>{children}</Ctx.Provider>;
}
