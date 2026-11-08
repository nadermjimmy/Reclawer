import type { ReactNode } from "react";

export type Tone = "neutral" | "navy" | "orange" | "positive" | "negative" | "muted";

/** Small pill with an optional dot: states, counts, metadata. Label always present (colour is never the only cue). */
export function Pill({ tone = "neutral", dot, children, title, className = "" }: {
  tone?: Tone;
  dot?: boolean;
  children: ReactNode;
  title?: string;
  className?: string;
}) {
  return (
    <span className={`pill pill-${tone} ${className}`} title={title}>
      {dot && <span className="pill-dot" aria-hidden="true" />}
      {children}
    </span>
  );
}

const STATE_TONE: Record<string, Tone> = {
  proposed: "navy",
  approved: "positive",
  rejected: "muted",
  superseded: "muted",
  running: "orange",
  done: "positive",
  failed: "negative",
  interrupted: "negative",
  open: "orange",
};

const STATE_LABEL: Record<string, string> = {
  proposed: "Open",
  approved: "Approved",
  rejected: "Rejected",
  superseded: "Superseded",
  running: "Running",
  done: "Done",
  failed: "Failed",
  interrupted: "Interrupted",
  open: "Open",
  move: "Moved",
  keep: "Kept",
  drop: "Dropped",
};

export function StatePill({ state }: { state: string }) {
  return (
    <Pill tone={STATE_TONE[state] ?? "neutral"} dot>
      {STATE_LABEL[state] ?? state}
    </Pill>
  );
}
