import { AnimatePresence, motion } from "framer-motion";
import { X } from "lucide-react";
import { useRef, type ReactNode } from "react";
import { drawer, scrim } from "../motion";
import { IconButton } from "./Button";
import { useFocusTrap } from "./focus";

/** Right-side drawer on desktop, full screen on phone. Esc closes; focus is trapped and restored. */
export function Drawer({ open, onClose, title, eyebrow, children, footer, width = 520 }: {
  open: boolean;
  onClose: () => void;
  title: ReactNode;
  eyebrow?: ReactNode;
  children: ReactNode;
  footer?: ReactNode;
  width?: number;
}) {
  const ref = useRef<HTMLDivElement>(null);
  useFocusTrap(ref, open, onClose);
  return (
    <AnimatePresence>
      {open && (
        <div className="overlay" data-modal-open="true">
          <motion.div className="scrim" variants={scrim} initial="initial" animate="animate" exit="exit" onClick={onClose} />
          <motion.aside ref={ref} className="drawer" style={{ width }} role="dialog" aria-modal="true"
            aria-label={typeof title === "string" ? title : undefined}
            variants={drawer} initial="initial" animate="animate" exit="exit">
            <header className="drawer-head">
              <div className="drawer-titles">
                {eyebrow && <div className="eyebrow">{eyebrow}</div>}
                <h2 className="drawer-title">{title}</h2>
              </div>
              <IconButton label="Close" icon={<X size={20} />} onClick={onClose} />
            </header>
            <div className="drawer-body">{children}</div>
            {footer && <footer className="drawer-foot">{footer}</footer>}
          </motion.aside>
        </div>
      )}
    </AnimatePresence>
  );
}
