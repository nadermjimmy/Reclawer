import { AnimatePresence, motion } from "framer-motion";
import { X } from "lucide-react";
import { createContext, useCallback, useContext, useMemo, useRef, useState, type ReactNode } from "react";
import { toast as toastMotion } from "../motion";

export interface ToastOptions {
  message: ReactNode;
  tone?: "neutral" | "error" | "success";
  /** One action, e.g. Undo or Retry. */
  action?: { label: string; onClick: () => void };
  /** ms; 0 = stays until closed. Default 5000. */
  duration?: number;
  /** Replace an existing toast with the same id (e.g. the undo toast). */
  id?: string;
  onExpire?: () => void;
}

interface ToastItem extends ToastOptions {
  id: string;
}

interface ToastApi {
  show: (t: ToastOptions) => string;
  dismiss: (id: string) => void;
}

const Ctx = createContext<ToastApi | null>(null);

export function useToast() {
  const c = useContext(Ctx);
  if (!c) throw new Error("useToast outside ToastProvider");
  return c;
}

let seq = 0;

/** Bottom toasts, at most 3 stacked; y 8 -> 0 + fade. */
export function ToastProvider({ children }: { children: ReactNode }) {
  const [items, setItems] = useState<ToastItem[]>([]);
  const timers = useRef(new Map<string, number>());

  const dismiss = useCallback((id: string) => {
    const t = timers.current.get(id);
    if (t) window.clearTimeout(t);
    timers.current.delete(id);
    setItems((xs) => xs.filter((x) => x.id !== id));
  }, []);

  const show = useCallback(
    (opts: ToastOptions) => {
      const id = opts.id ?? `t${++seq}`;
      const old = timers.current.get(id);
      if (old) window.clearTimeout(old);
      setItems((xs) => [...xs.filter((x) => x.id !== id), { ...opts, id }].slice(-3));
      const d = opts.duration ?? 5000;
      if (d > 0)
        timers.current.set(
          id,
          window.setTimeout(() => {
            timers.current.delete(id);
            setItems((xs) => xs.filter((x) => x.id !== id));
            opts.onExpire?.();
          }, d),
        );
      return id;
    },
    [],
  );

  const api = useMemo(() => ({ show, dismiss }), [show, dismiss]);
  return (
    <Ctx.Provider value={api}>
      {children}
      <div className="toasts" aria-live="polite" aria-atomic="false">
        <AnimatePresence initial={false}>
          {items.map((t) => (
            <motion.div key={t.id} layout="position" variants={toastMotion} initial="initial" animate="animate" exit="exit"
              className={`toast toast-${t.tone ?? "neutral"}`} role={t.tone === "error" ? "alert" : "status"}>
              <span className="toast-msg">{t.message}</span>
              {t.action && (
                <button type="button" className="toast-action" onClick={() => { t.action!.onClick(); dismiss(t.id); }}>
                  {t.action.label}
                </button>
              )}
              <button type="button" className="toast-close" aria-label="Close" onClick={() => dismiss(t.id)}>
                <X size={16} />
              </button>
            </motion.div>
          ))}
        </AnimatePresence>
      </div>
    </Ctx.Provider>
  );
}
