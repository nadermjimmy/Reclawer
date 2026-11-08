/* Motion tokens and shared Framer Motion variants (Byit: 120 / 200 / 360 ms, no springs, nothing loops).
   The app is wrapped in <MotionConfig reducedMotion="user">, so transforms fall back to opacity-only for people
   who ask for reduced motion. Height changes are handled explicitly with useReducedMotion() where used. */
import type { Transition, Variants } from "framer-motion";

export const dur = { instant: 0, fast: 0.12, base: 0.2, slow: 0.36 } as const;
export const ease = {
  standard: [0.4, 0, 0.2, 1] as [number, number, number, number],
  out: [0.16, 1, 0.3, 1] as [number, number, number, number],
};

export const baseTransition: Transition = { duration: dur.base, ease: ease.standard };

/** Route change: new page fades in, no slide, no exit wait. */
export const routeFade: Variants = {
  initial: { opacity: 0 },
  animate: { opacity: 1, transition: { duration: dur.fast, ease: ease.standard } },
};

/** Queue row leaving after approve (+1, moves right) or reject (-1, moves left): fade + nudge, then collapse. */
export function rowExit(direction: number, reduced: boolean) {
  if (reduced) return { opacity: 0, height: 0, transition: { duration: 0 } };
  return {
    opacity: 0,
    x: 12 * direction,
    height: 0,
    transition: {
      opacity: { duration: dur.base, ease: ease.standard },
      x: { duration: dur.base, ease: ease.standard },
      height: { duration: dur.base, ease: ease.standard, delay: dur.base },
    },
  };
}

/** Row coming back (undo or failed commit) at its original index. */
export function rowEnter(reduced: boolean) {
  return {
    initial: reduced ? { opacity: 0 } : { opacity: 0, height: 0 },
    animate: {
      opacity: 1,
      height: "auto",
      transition: { duration: reduced ? dur.fast : dur.base, ease: ease.out },
    },
  };
}

/** Drawers and side panels slide in from the right. */
export const drawer: Variants = {
  initial: { x: 24, opacity: 0 },
  animate: { x: 0, opacity: 1, transition: { duration: dur.slow, ease: ease.out } },
  exit: { x: 24, opacity: 0, transition: { duration: dur.base, ease: ease.standard } },
};

export const scrim: Variants = {
  initial: { opacity: 0 },
  animate: { opacity: 1, transition: { duration: dur.base, ease: ease.standard } },
  exit: { opacity: 0, transition: { duration: dur.fast, ease: ease.standard } },
};

export const dialog: Variants = {
  initial: { opacity: 0, y: 8 },
  animate: { opacity: 1, y: 0, transition: { duration: dur.base, ease: ease.out } },
  exit: { opacity: 0, y: 8, transition: { duration: dur.fast, ease: ease.standard } },
};

export const toast: Variants = {
  initial: { opacity: 0, y: 8 },
  animate: { opacity: 1, y: 0, transition: { duration: dur.base, ease: ease.out } },
  exit: { opacity: 0, y: 8, transition: { duration: dur.fast, ease: ease.standard } },
};

/** Bulk bar rises from the bottom of the list when rows are selected. */
export const bulkBar: Variants = {
  initial: { opacity: 0, y: 16 },
  animate: { opacity: 1, y: 0, transition: { duration: dur.base, ease: ease.out } },
  exit: { opacity: 0, y: 16, transition: { duration: dur.fast, ease: ease.standard } },
};

/** Counters and badges cross-fade (no rolling digits). */
export const counter: Variants = {
  initial: { opacity: 0 },
  animate: { opacity: 1, transition: { duration: dur.fast } },
  exit: { opacity: 0, transition: { duration: dur.fast } },
};
