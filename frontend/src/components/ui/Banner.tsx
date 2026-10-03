import React from "react";

export interface BannerProps {
  type?: "danger" | "warning" | "success" | "info";
  title?: string;
  children: React.ReactNode;
  "data-testid"?: string;
  className?: string;
}

export const Banner: React.FC<BannerProps> = ({
  type = "danger",
  title,
  children,
  "data-testid": dataTestId,
  className = "",
}) => {
  let styleClasses = "bg-[var(--ds-background-danger)] border-[var(--ds-border-danger)] text-[var(--ds-text-danger)]";
  if (type === "warning") {
    styleClasses = "bg-[var(--ds-background-warning)] border-[var(--ds-border-warning)] text-[var(--ds-text-warning)]";
  } else if (type === "success") {
    styleClasses = "bg-[var(--ds-background-success)] border-[var(--ds-border-success)] text-[var(--ds-text-success)]";
  } else if (type === "info") {
    styleClasses = "bg-[var(--ds-background-information)] border-[var(--ds-border-brand)] text-[var(--ds-text-brand)]";
  }

  return (
    <div
      data-testid={dataTestId}
      role="alert"
      className={`p-3 rounded border text-sm flex flex-col gap-1 ${styleClasses} ${className}`}
    >
      {title && <span className="font-semibold text-xs tracking-wide uppercase">{title}</span>}
      <div>{children}</div>
    </div>
  );
};
