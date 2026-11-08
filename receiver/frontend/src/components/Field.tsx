import { Search } from "lucide-react";
import { forwardRef, type InputHTMLAttributes, type ReactNode, type SelectHTMLAttributes } from "react";

export const Select = forwardRef<HTMLSelectElement, SelectHTMLAttributes<HTMLSelectElement> & { label: string; hideLabel?: boolean }>(
  function Select({ label, hideLabel, className = "", children, ...rest }, ref) {
    return (
      <label className={`field field-inline ${className}`}>
        <span className={hideLabel ? "sr-only" : "field-label"}>{label}</span>
        <select ref={ref} className="select" {...rest}>{children}</select>
      </label>
    );
  },
);

export const SearchInput = forwardRef<HTMLInputElement, InputHTMLAttributes<HTMLInputElement> & { label: string; hint?: ReactNode }>(
  function SearchInput({ label, hint, className = "", ...rest }, ref) {
    return (
      <label className={`search ${className}`}>
        <span className="sr-only">{label}</span>
        <Search size={16} aria-hidden="true" className="search-icon" />
        <input ref={ref} type="search" className="input input-search" {...rest} />
        {hint && <span className="search-hint" aria-hidden="true">{hint}</span>}
      </label>
    );
  },
);

export function TextField({ label, ...rest }: InputHTMLAttributes<HTMLInputElement> & { label: string }) {
  return (
    <label className="field">
      <span className="field-label">{label}</span>
      <input className="input" {...rest} />
    </label>
  );
}
