import React from "react";

export interface InputProps extends React.InputHTMLAttributes<HTMLInputElement> {
  label?: string;
  error?: string;
  helperText?: string;
}

export const Input: React.FC<InputProps> = ({
  label,
  error,
  helperText,
  className = "",
  id,
  ...props
}) => {
  const inputId = id || (label ? label.toLowerCase().replace(/\s+/g, "-") : undefined);

  return (
    <div className="w-full flex flex-col gap-1">
      {label && (
        <label htmlFor={inputId} className="text-xs font-semibold text-[var(--ds-text-subtle)]">
          {label}
        </label>
      )}
      <input
        id={inputId}
        className={`w-full px-2.5 py-1.5 text-sm rounded bg-[var(--ds-background-input)] text-[var(--ds-text)] border transition-colors outline-none focus:ring-2 ${
          error
            ? "border-[var(--ds-border-danger)] focus:ring-[var(--ds-border-danger)]"
            : "border-[var(--ds-border-input)] focus:ring-[var(--ds-border-focused)] focus:border-transparent"
        } disabled:bg-[var(--ds-background-subtle)] disabled:cursor-not-allowed ${className}`}
        {...props}
      />
      {error ? (
        <p className="text-xs text-[var(--ds-text-danger)]">{error}</p>
      ) : helperText ? (
        <p className="text-xs text-[var(--ds-text-subtle)]">{helperText}</p>
      ) : null}
    </div>
  );
};
