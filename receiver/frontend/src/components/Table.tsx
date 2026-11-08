import type { ReactNode } from "react";

/** Data table that scrolls inside its card (the page never scrolls sideways). */
export function Table({ head, children, dense, label }: { head: ReactNode[]; children: ReactNode; dense?: boolean; label?: string }) {
  return (
    <div className="table-wrap" role="region" aria-label={label} tabIndex={0}>
      <table className={`table${dense ? " table-dense" : ""}`}>
        <thead>
          <tr>
            {head.map((h, i) => (
              <th key={i} scope="col">{h}</th>
            ))}
          </tr>
        </thead>
        <tbody>{children}</tbody>
      </table>
    </div>
  );
}
