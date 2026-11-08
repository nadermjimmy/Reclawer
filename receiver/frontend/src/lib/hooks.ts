import { useCallback, useEffect, useRef, useState } from "react";
import { errorText } from "../api/client";

/** True once `active` has been true for `ms` (skeletons only appear for slow loads). */
export function useDelayed(active: boolean, ms = 300) {
  const [shown, setShown] = useState(false);
  useEffect(() => {
    if (!active) {
      setShown(false);
      return;
    }
    const t = window.setTimeout(() => setShown(true), ms);
    return () => window.clearTimeout(t);
  }, [active, ms]);
  return shown;
}

export interface Loadable<T> {
  data: T | undefined;
  error: string | null;
  loading: boolean;
  reload: () => Promise<void>;
  setData: (fn: (d: T | undefined) => T | undefined) => void;
}

/** Load once (and on deps change) with error text and a reload; keeps the last data on error. */
export function useLoad<T>(fn: () => Promise<T>, deps: unknown[]): Loadable<T> {
  const [data, setDataState] = useState<T>();
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const seq = useRef(0);
  const fnRef = useRef(fn);
  fnRef.current = fn;
  const reload = useCallback(async () => {
    const my = ++seq.current;
    setLoading(true);
    try {
      const d = await fnRef.current();
      if (my === seq.current) {
        setDataState(d);
        setError(null);
      }
    } catch (e) {
      if (my === seq.current) setError(errorText(e));
    } finally {
      if (my === seq.current) setLoading(false);
    }
  }, []);
  useEffect(() => {
    void reload();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, deps);
  const setData = useCallback((f: (d: T | undefined) => T | undefined) => setDataState((d) => f(d)), []);
  return { data, error, loading, reload, setData };
}

/** Re-render every `ms` (elapsed timers, relative times). */
export function useTick(ms = 1000, enabled = true) {
  const [, set] = useState(0);
  useEffect(() => {
    if (!enabled) return;
    const t = window.setInterval(() => set((n) => n + 1), ms);
    return () => window.clearInterval(t);
  }, [ms, enabled]);
}

export function useMediaQuery(query: string) {
  const [match, setMatch] = useState(() => window.matchMedia(query).matches);
  useEffect(() => {
    const m = window.matchMedia(query);
    const on = () => setMatch(m.matches);
    m.addEventListener("change", on);
    on();
    return () => m.removeEventListener("change", on);
  }, [query]);
  return match;
}

export const useIsPhone = () => useMediaQuery("(max-width: 639px)");

/** True when keyboard shortcuts should be ignored (typing in a field, or a dialog is open). */
export function shortcutsBlocked(e: KeyboardEvent) {
  const t = e.target as HTMLElement | null;
  if (e.metaKey || e.ctrlKey || e.altKey) return true;
  const typing = t && (t.isContentEditable || ["TEXTAREA", "SELECT"].includes(t.tagName) ||
    (t.tagName === "INPUT" && !["checkbox", "radio", "button"].includes((t as HTMLInputElement).type)));
  if (typing) return true;
  if ((e.key === "Enter" || e.key === " ") && t && t.closest("button,a,[role='tab']")) return true;
  return Boolean(document.querySelector("[data-modal-open='true']"));
}

/** Bind single-key shortcuts while mounted. Keys are KeyboardEvent.key values; "A" means Shift+a. */
export function useHotkeys(map: Record<string, (e: KeyboardEvent) => void>, enabled = true) {
  const ref = useRef(map);
  ref.current = map;
  useEffect(() => {
    if (!enabled) return;
    const on = (e: KeyboardEvent) => {
      if (shortcutsBlocked(e)) return;
      const fn = ref.current[e.key];
      if (fn) {
        e.preventDefault();
        fn(e);
      }
    };
    window.addEventListener("keydown", on);
    return () => window.removeEventListener("keydown", on);
  }, [enabled]);
}

export function usePageVisible() {
  const [visible, setVisible] = useState(() => document.visibilityState === "visible");
  useEffect(() => {
    const on = () => setVisible(document.visibilityState === "visible");
    document.addEventListener("visibilitychange", on);
    return () => document.removeEventListener("visibilitychange", on);
  }, []);
  return visible;
}
