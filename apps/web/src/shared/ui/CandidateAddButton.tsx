import type { ButtonHTMLAttributes, ReactNode } from "react";

type CandidateAddButtonProps = ButtonHTMLAttributes<HTMLButtonElement> & {
  children: ReactNode;
  compact?: boolean;
};

export function CandidateAddButton({
  children,
  className = "",
  compact = false,
  type = "button",
  ...props
}: CandidateAddButtonProps) {
  const classes = [
    "primary-button",
    "candidate-add-button",
    compact ? "candidate-add-button-compact" : "",
    className,
  ].filter(Boolean).join(" ");

  return (
    <button type={type} className={classes} {...props}>
      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.75" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
        <circle cx="12" cy="12" r="9" />
        <path d="M12 8v8m-4-4h8" />
      </svg>
      {children}
    </button>
  );
}
