import React from "react";

export type NavItem =
  | "dashboard"
  | "ready-to-review"
  | "needs-manual-review"
  | "product-inquiry"
  | "recent-order"
  | "complaint"
  | "spam"
  | "sent"
  | "settings";

export interface SidebarProps {
  currentTab: NavItem;
  onSelectTab: (tab: NavItem) => void;
  queueCounts?: Record<string, number>;
}

export const Sidebar: React.FC<SidebarProps> = ({
  currentTab,
  onSelectTab,
  queueCounts = {},
}) => {
  const queues = [
    {
      id: "ready-to-review" as const,
      label: "Waiting for Approval",
      testId: "queue-nav-ready-to-review",
      badgeKey: "ready",
    },
    {
      id: "needs-manual-review" as const,
      label: "Needs Manual Review",
      testId: "queue-nav-needs-manual-review",
      badgeKey: "manual",
    },
    {
      id: "product-inquiry" as const,
      label: "Product Inquiries",
      testId: "queue-nav-product-inquiry",
      badgeKey: "product",
    },
    {
      id: "recent-order" as const,
      label: "Recent Orders",
      testId: "queue-nav-recent-order",
      badgeKey: "recent_order",
    },
    {
      id: "complaint" as const,
      label: "Complaints & Returns",
      testId: "queue-nav-complaint",
      badgeKey: "complaint",
    },
    {
      id: "spam" as const,
      label: "Spam",
      testId: "queue-nav-spam",
      badgeKey: "spam",
    },
    {
      id: "sent" as const,
      label: "Sent",
      testId: "queue-nav-sent",
      badgeKey: "sent",
    },
  ];

  return (
    <aside className="w-64 border-r border-[var(--ds-border)] bg-[var(--ds-background-subtle)] flex flex-col justify-between shrink-0 h-[calc(100vh-3.5rem)]">
      <div className="p-3 space-y-4">
        <div>
          <button
            data-testid="nav-dashboard"
            onClick={() => onSelectTab("dashboard")}
            className={`w-full flex items-center gap-2 px-3 py-2 text-xs font-medium rounded transition-colors text-left mb-3 ${
              currentTab === "dashboard"
                ? "bg-[var(--ds-background-selected)] text-[var(--ds-text-selected)] font-semibold"
                : "text-[var(--ds-text)] hover:bg-[var(--ds-background-neutral-hovered)]"
            }`}
          >
            <svg
              className="w-4 h-4 text-[var(--ds-text-subtle)]"
              fill="none"
              stroke="currentColor"
              viewBox="0 0 24 24"
            >
              <path
                strokeLinecap="round"
                strokeLinejoin="round"
                strokeWidth="2"
                d="M9 19v-6a2 2 0 00-2-2H5a2 2 0 00-2 2v6a2 2 0 002 2h2a2 2 0 002-2zm0 0V9a2 2 0 012-2h2a2 2 0 012 2v10m-6 0a2 2 0 002 2h2a2 2 0 002-2m0 0V5a2 2 0 012-2h2a2 2 0 012 2v14a2 2 0 01-2 2h-2a2 2 0 01-2-2z"
              />
            </svg>
            <span>Dashboard &amp; Health</span>
          </button>
          <h3 className="px-3 text-xs font-semibold text-[var(--ds-text-subtle)] uppercase tracking-wider mb-2">
            Email Queues
          </h3>
          <nav className="space-y-0.5">
            {queues.map((q) => {
              const count =
                queueCounts[q.badgeKey] ??
                queueCounts[q.id.replace(/-/g, "_")] ??
                queueCounts[q.id] ??
                0;
              const active = currentTab === q.id;
              const isWarning = q.id === "needs-manual-review";
              const isDanger = q.id === "complaint";
              return (
                <button
                  key={q.id}
                  data-testid={q.testId}
                  onClick={() => onSelectTab(q.id)}
                  className={`w-full flex items-center justify-between px-3 py-2 text-xs font-medium rounded transition-colors text-left ${
                    active
                      ? "bg-[var(--ds-background-selected)] text-[var(--ds-text-selected)] font-semibold"
                      : "text-[var(--ds-text)] hover:bg-[var(--ds-background-neutral-hovered)]"
                  }`}
                >
                  <span className="truncate">{q.label}</span>
                  {count > 0 && (
                    <span
                      className={`ml-2 px-1.5 py-0.5 text-[10px] rounded-full font-bold ${
                        active
                          ? "bg-[var(--ds-background-brand-bold)] text-[var(--ds-text-inverse)]"
                          : isDanger
                          ? "bg-[var(--ds-background-danger)] text-[var(--ds-text-danger)]"
                          : isWarning
                          ? "bg-[var(--ds-background-warning)] text-[var(--ds-text-warning)]"
                          : "bg-[var(--ds-background-neutral)] text-[var(--ds-text-subtle)]"
                      }`}
                    >
                      {count}
                    </span>
                  )}
                </button>
              );
            })}
          </nav>
        </div>
      </div>

      <div className="p-3 border-t border-[var(--ds-border)] space-y-1">
        <button
          data-testid="wizard-add-store-button"
          onClick={() => {
            if (typeof window !== "undefined") {
              window.history.pushState(null, "", "/settings/stores/new");
              window.dispatchEvent(new PopStateEvent("popstate"));
            }
          }}
          className="w-full flex items-center gap-2 px-3 py-2 text-xs font-medium rounded transition-colors text-left text-[var(--ds-text)] hover:bg-[var(--ds-background-neutral-hovered)]"
        >
          <svg className="w-4 h-4 text-[var(--ds-text-subtle)]" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M12 4v16m8-8H4" />
          </svg>
          <span>Add Store</span>
        </button>
        <button
          data-testid="nav-settings"
          onClick={() => onSelectTab("settings")}
          className={`w-full flex items-center gap-2 px-3 py-2 text-xs font-medium rounded transition-colors text-left ${
            currentTab === "settings"
              ? "bg-[var(--ds-background-selected)] text-[var(--ds-text-selected)] font-semibold"
              : "text-[var(--ds-text)] hover:bg-[var(--ds-background-neutral-hovered)]"
          }`}
        >
          <svg
            className="w-4 h-4 text-[var(--ds-text-subtle)]"
            fill="none"
            stroke="currentColor"
            viewBox="0 0 24 24"
          >
            <path
              strokeLinecap="round"
              strokeLinejoin="round"
              strokeWidth="2"
              d="M10.325 4.317c.426-1.756 2.924-1.756 3.35 0a1.724 1.724 0 002.573 1.066c1.543-.94 3.31.826 2.37 2.37a1.724 1.724 0 001.065 2.572c1.756.426 1.756 2.924 0 3.35a1.724 1.724 0 00-1.066 2.573c.94 1.543-.826 3.31-2.37 2.37a1.724 1.724 0 00-2.572 1.065c-.426 1.756-2.924 1.756-3.35 0a1.724 1.724 0 00-2.573-1.066c-1.543.94-3.31-.826-2.37-2.37a1.724 1.724 0 00-1.065-2.572c-1.756-.426-1.756-2.924 0-3.35a1.724 1.724 0 001.066-2.573c-.94-1.543.826-3.31 2.37-2.37.996.608 2.296.07 2.572-1.065z"
            />
            <path
              strokeLinecap="round"
              strokeLinejoin="round"
              strokeWidth="2"
              d="M15 12a3 3 0 11-6 0 3 3 0 016 0z"
            />
          </svg>
          <span>User Management</span>
        </button>
      </div>
    </aside>
  );
};
