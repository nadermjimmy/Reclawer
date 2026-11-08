import { forwardRef, type ButtonHTMLAttributes, type ReactNode } from "react";
import { Link, type LinkProps } from "react-router-dom";

export type ButtonVariant = "primary" | "secondary" | "ghost" | "danger" | "positive";

export interface ButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: ButtonVariant;
  size?: "sm" | "md";
  icon?: ReactNode;
  /** Shortcut shown inside the button on desktop, e.g. "A". */
  kbd?: string;
  block?: boolean;
}

/** Pill button. Primary = orange (the subject/action), secondary = navy, ghost = navy wash on hover. */
export const Button = forwardRef<HTMLButtonElement, ButtonProps>(function Button(
  { variant = "secondary", size = "md", icon, kbd, block, className = "", children, type = "button", ...rest },
  ref,
) {
  return (
    <button
      ref={ref}
      type={type}
      className={`btn btn-${variant} btn-${size}${block ? " btn-block" : ""} ${className}`}
      {...rest}
    >
      {icon && <span className="btn-icon" aria-hidden="true">{icon}</span>}
      {children !== undefined && <span className="btn-label">{children}</span>}
      {kbd && <kbd className="btn-kbd" aria-hidden="true">{kbd}</kbd>}
    </button>
  );
});

export interface IconButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  label: string;
  icon: ReactNode;
  variant?: ButtonVariant;
  size?: "sm" | "md";
}

/** Round icon-only button with a required label (aria-label + tooltip). */
export const IconButton = forwardRef<HTMLButtonElement, IconButtonProps>(function IconButton(
  { label, icon, variant = "ghost", size = "md", className = "", type = "button", title, ...rest },
  ref,
) {
  return (
    <button
      ref={ref}
      type={type}
      aria-label={label}
      title={title ?? label}
      className={`icon-btn btn-${variant} icon-btn-${size} ${className}`}
      {...rest}
    >
      {icon}
    </button>
  );
});

/** A link that looks like a Button (navigation, not an action). */
export function ButtonLink({ to, variant = "secondary", size = "md", icon, children, className = "", ...rest }: {
  to: string;
  variant?: ButtonVariant;
  size?: "sm" | "md";
  icon?: ReactNode;
  children: ReactNode;
  className?: string;
} & Omit<LinkProps, "to" | "children" | "className">) {
  return (
    <Link to={to} className={`btn btn-${variant} btn-${size} btn-link ${className}`} {...rest}>
      {icon && <span className="btn-icon" aria-hidden="true">{icon}</span>}
      <span className="btn-label">{children}</span>
    </Link>
  );
}
