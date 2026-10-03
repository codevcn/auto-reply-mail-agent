import React, { useEffect } from "react";

export interface ModalProps {
  isOpen: boolean;
  onClose: () => void;
  title: string;
  children: React.ReactNode;
}

export const Modal: React.FC<ModalProps> = ({ isOpen, onClose, title, children }) => {
  useEffect(() => {
    const handleKeyDown = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    if (isOpen) {
      window.addEventListener("keydown", handleKeyDown);
    }
    return () => window.removeEventListener("keydown", handleKeyDown);
  }, [isOpen, onClose]);

  if (!isOpen) return null;

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-black/40 backdrop-blur-xs">
      <div className="bg-[var(--ds-background-default)] border border-[var(--ds-border)] rounded-lg shadow-xl max-w-md w-full p-6 text-[var(--ds-text)] animate-in fade-in zoom-in-95 duration-150">
        <div className="flex items-center justify-between pb-3 border-b border-[var(--ds-border)] mb-4">
          <h2 className="text-base font-semibold text-[var(--ds-text)]">{title}</h2>
          <button
            onClick={onClose}
            className="text-[var(--ds-text-subtle)] hover:text-[var(--ds-text)] text-lg leading-none p-1 rounded hover:bg-[var(--ds-background-neutral)]"
            aria-label="Close dialog"
          >
            &times;
          </button>
        </div>
        <div>{children}</div>
      </div>
    </div>
  );
};
