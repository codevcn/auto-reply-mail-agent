import React from "react";
import { Button } from "../components/ui/Button";

export interface HeaderProps {
  currentUser?: {
    username: string;
    roles?: string[];
  } | null;
  onLogout: () => void;
  onNavigateHome?: () => void;
}

export const Header: React.FC<HeaderProps> = ({ currentUser, onLogout, onNavigateHome }) => {
  return (
    <header className="h-14 border-b border-[var(--ds-border)] bg-[var(--ds-background-default)] px-4 flex items-center justify-between shrink-0">
      <div className="flex items-center gap-3 cursor-pointer" onClick={onNavigateHome}>
        <div className="w-8 h-8 rounded bg-[var(--ds-background-brand-bold)] flex items-center justify-center text-[var(--ds-text-inverse)] font-bold text-sm">
          MA
        </div>
        <div>
          <span className="font-semibold text-sm tracking-tight text-[var(--ds-text)]">
            Mail Agent
          </span>
          <span className="ml-2 text-xs px-1.5 py-0.5 rounded bg-[var(--ds-background-subtle)] text-[var(--ds-text-subtle)] font-medium">
            Shopify Auto-Reply
          </span>
        </div>
      </div>

      <div className="flex items-center gap-4">
        {currentUser && (
          <div className="flex items-center gap-2 text-sm text-[var(--ds-text-subtle)]">
            <span>Logged in as:</span>
            <span className="font-medium text-[var(--ds-text)] bg-[var(--ds-background-neutral)] px-2 py-0.5 rounded">
              {currentUser.username}
            </span>
          </div>
        )}
        <Button
          data-testid="header-logout-button"
          variant="subtle"
          onClick={onLogout}
          className="text-xs"
        >
          Logout
        </Button>
      </div>
    </header>
  );
};
