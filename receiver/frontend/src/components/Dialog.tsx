import { AnimatePresence, motion } from "framer-motion";
import { useRef, useState, type ReactNode } from "react";
import { dialog, scrim } from "../motion";
import { Button, type ButtonVariant } from "./Button";
import { useFocusTrap } from "./focus";

export interface ConfirmProps {
  open: boolean;
  title: string;
  children?: ReactNode;
  /** Verb-first label naming what happens, e.g. "Approve 12". */
  confirmLabel: string;
  cancelLabel?: string;
  variant?: ButtonVariant;
  /** Require typing this exact text first (destructive actions). */
  typeToConfirm?: string;
  busy?: boolean;
  onConfirm: () => void;
  onCancel: () => void;
}

export function ConfirmDialog({ open, title, children, confirmLabel, cancelLabel = "Cancel", variant = "primary",
  typeToConfirm, busy, onConfirm, onCancel }: ConfirmProps) {
  const ref = useRef<HTMLDivElement>(null);
  const confirmRef = useRef<HTMLButtonElement>(null);
  const inputRef = useRef<HTMLInputElement>(null);
  const [typed, setTyped] = useState("");
  useFocusTrap(ref, open, onCancel, typeToConfirm ? inputRef : confirmRef);
  const ok = !typeToConfirm || typed.trim() === typeToConfirm;
  return (
    <AnimatePresence onExitComplete={() => setTyped("")}>
      {open && (
        <div className="overlay overlay-center" data-modal-open="true">
          <motion.div className="scrim" variants={scrim} initial="initial" animate="animate" exit="exit" onClick={onCancel} />
          <motion.div ref={ref} className="dialog" role="alertdialog" aria-modal="true" aria-labelledby="dlg-title"
            variants={dialog} initial="initial" animate="animate" exit="exit">
            <h2 id="dlg-title" className="dialog-title">{title}</h2>
            {children && <div className="dialog-body">{children}</div>}
            {typeToConfirm && (
              <label className="field">
                <span className="field-label">Type <b>{typeToConfirm}</b> to confirm</span>
                <input ref={inputRef} className="input" value={typed} autoComplete="off" spellCheck={false}
                  onChange={(e) => setTyped(e.target.value)}
                  onKeyDown={(e) => { if (e.key === "Enter" && ok && !busy) onConfirm(); }} />
              </label>
            )}
            <div className="dialog-actions">
              <Button variant="ghost" onClick={onCancel}>{cancelLabel}</Button>
              <Button ref={confirmRef} variant={variant} disabled={!ok || busy} onClick={onConfirm}>{confirmLabel}</Button>
            </div>
          </motion.div>
        </div>
      )}
    </AnimatePresence>
  );
}
