import React from "react";

export interface BadgeProps {
  children: React.ReactNode;
  variant?: "neutral" | "brand" | "success" | "danger" | "warning";
  className?: string;
  "data-testid"?: string;
}

export const Badge: React.FC<BadgeProps> = ({
  children,
  variant = "neutral",
  className = "",
  "data-testid": dataTestId,
}) => {
  let styleClasses = "bg-[var(--ds-background-neutral)] text-[var(--ds-text)]";
  if (variant === "brand") {
    styleClasses = "bg-[var(--ds-background-selected)] text-[var(--ds-text-brand)]";
  } else if (variant === "success") {
    styleClasses = "bg-[var(--ds-background-success)] text-[var(--ds-text-success)]";
  } else if (variant === "danger") {
    styleClasses = "bg-[var(--ds-background-danger)] text-[var(--ds-text-danger)]";
  } else if (variant === "warning") {
    styleClasses = "bg-[var(--ds-background-warning)] text-[var(--ds-text-warning)]";
  }

  return (
    <span
      data-testid={dataTestId}
      className={`inline-flex items-center px-2 py-0.5 rounded-full text-xs font-medium ${styleClasses} ${className}`}
    >
      {children}
    </span>
  );
};
