import { AnimatePresence, motion } from "framer-motion";
import { X } from "lucide-react";
import { useRef } from "react";
import { dialog, scrim } from "../motion";
import { IconButton } from "./Button";
import { useFocusTrap } from "./focus";

export interface ShortcutGroup {
  title: string;
  keys: [string, string][];
}

/** "?" sheet listing a screen's shortcuts. */
export function KeyboardHelp({ open, onClose, groups }: { open: boolean; onClose: () => void; groups: ShortcutGroup[] }) {
  const ref = useRef<HTMLDivElement>(null);
  useFocusTrap(ref, open, onClose);
  return (
    <AnimatePresence>
      {open && (
        <div className="overlay overlay-center" data-modal-open="true">
          <motion.div className="scrim" variants={scrim} initial="initial" animate="animate" exit="exit" onClick={onClose} />
          <motion.div ref={ref} className="dialog dialog-wide" role="dialog" aria-modal="true" aria-label="Keyboard shortcuts"
            variants={dialog} initial="initial" animate="animate" exit="exit">
            <div className="row">
              <h2 className="dialog-title">Keyboard Shortcuts</h2>
              <span className="spacer" />
              <IconButton label="Close" icon={<X size={20} />} onClick={onClose} />
            </div>
            <div className="kbd-groups">
              {groups.map((g) => (
                <div key={g.title}>
                  <div className="eyebrow">{g.title}</div>
                  <dl className="kbd-list">
                    {g.keys.map(([k, what]) => (
                      <div key={k + what} className="kbd-item">
                        <dt>{k.split(" ").map((p, i) => (p === "/" && k !== "/" ? <span key={i} className="muted"> or </span> : <kbd key={i}>{p}</kbd>))}</dt>
                        <dd>{what}</dd>
                      </div>
                    ))}
                  </dl>
                </div>
              ))}
            </div>
          </motion.div>
        </div>
      )}
    </AnimatePresence>
  );
}
