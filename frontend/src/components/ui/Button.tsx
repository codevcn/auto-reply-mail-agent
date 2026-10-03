import React from "react";

export interface ButtonProps extends React.ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: "primary" | "subtle" | "danger" | "default";
  isLoading?: boolean;
}

export const Button: React.FC<ButtonProps> = ({
  children,
  variant = "default",
  isLoading = false,
  className = "",
  disabled,
  ...props
}) => {
  let variantStyles = "bg-[var(--ds-background-neutral)] text-[var(--ds-text)] hover:bg-[var(--ds-background-neutral-hovered)] border border-[var(--ds-border)]";

  if (variant === "primary") {
    variantStyles = "bg-[var(--ds-background-brand-bold)] text-[var(--ds-text-inverse)] hover:bg-[var(--ds-background-brand-bold-hovered)] border-transparent font-medium shadow-sm";
  } else if (variant === "danger") {
    variantStyles = "bg-[var(--ds-background-danger-bold)] text-[var(--ds-text-inverse)] hover:bg-[var(--ds-background-danger-bold-hovered)] border-transparent font-medium";
  } else if (variant === "subtle") {
    variantStyles = "bg-transparent text-[var(--ds-text-subtle)] hover:bg-[var(--ds-background-neutral)] border-transparent";
  }

  return (
    <button
      disabled={disabled || isLoading}
      className={`inline-flex items-center justify-center px-3 py-1.5 text-sm rounded transition-colors focus:outline-none focus:ring-2 focus:ring-[var(--ds-border-focused)] disabled:opacity-50 disabled:cursor-not-allowed ${variantStyles} ${className}`}
      {...props}
    >
      {isLoading ? (
        <span className="inline-flex items-center gap-1.5">
          <svg className="animate-spin h-4 w-4" viewBox="0 0 24 24" fill="none">
            <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
            <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8v8H4z" />
          </svg>
          {children}
        </span>
      ) : (
        children
      )}
    </button>
  );
};
