import React, { useState, useEffect } from "react";
import { Button } from "../../components/ui/Button";

export interface SendConfirmModalProps {
  isOpen: boolean;
  recipientEmail: string;
  subject: string;
  versionNumber: number;
  isSending: boolean;
  onConfirm: (idempotencyKey: string) => void;
  onCancel: () => void;
}

export const SendConfirmModal: React.FC<SendConfirmModalProps> = ({
  isOpen,
  recipientEmail,
  subject,
  versionNumber,
  isSending,
  onConfirm,
  onCancel,
}) => {
  const [idempotencyKey, setIdempotencyKey] = useState("");

  useEffect(() => {
    if (isOpen) {
      // Generate a fresh UUIDv4 idempotency key when modal opens
      try {
        setIdempotencyKey(crypto.randomUUID());
      } catch {
        setIdempotencyKey(`idemp-${Date.now()}-${Math.random().toString(36).substring(2, 9)}`);
      }
    }
  }, [isOpen]);

  if (!isOpen) return null;

  const handleConfirmClick = () => {
    if (isSending) return;
    onConfirm(idempotencyKey);
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 backdrop-blur-xs p-4 animate-fade-in">
      <div
        data-testid="send-confirm-modal"
        className="bg-[var(--ds-background-default)] border border-[var(--ds-border)] rounded-lg shadow-xl max-w-lg w-full overflow-hidden flex flex-col"
      >
        {/* Header */}
        <div className="px-5 py-4 border-b border-[var(--ds-border)] bg-[var(--ds-background-subtle)] flex items-center justify-between">
          <div className="flex items-center gap-2">
            <span className="text-base font-bold text-[var(--ds-text)]">Approve & Send Outbound Email</span>
          </div>
          <button
            onClick={onCancel}
            disabled={isSending}
            className="text-[var(--ds-text-subtle)] hover:text-[var(--ds-text)] p-1 rounded disabled:opacity-50"
          >
            ✕
          </button>
        </div>

        {/* Body */}
        <div className="p-5 space-y-4 text-xs">
          {/* Safeguard Warning */}
          <div className="p-3 bg-[var(--ds-background-information)] border border-[var(--ds-border-brand)] rounded text-[var(--ds-text)] space-y-1">
            <div className="font-bold flex items-center gap-1.5 text-[var(--ds-text-brand)]">
              <span>🛡️ Invariant R-01: Explicit Human Authorization</span>
            </div>
            <p className="text-[11px] leading-relaxed text-[var(--ds-text-subtle)]">
              You are explicitly approving this outbound reply. Once confirmed, the message will be dispatched via SMTP
              STARTTLS and an RFC 5322 threaded copy will be saved into the mailbox Sent folder.
            </p>
          </div>

          {/* Message Meta Summary */}
          <div className="bg-[var(--ds-background-subtle)] p-3 rounded border border-[var(--ds-border)] space-y-2">
            <div className="flex items-start justify-between gap-2">
              <span className="font-semibold text-[var(--ds-text-subtle)] w-20">To:</span>
              <span className="font-medium text-[var(--ds-text)] flex-1 truncate">{recipientEmail}</span>
            </div>
            <div className="flex items-start justify-between gap-2">
              <span className="font-semibold text-[var(--ds-text-subtle)] w-20">Subject:</span>
              <span className="font-medium text-[var(--ds-text)] flex-1 truncate">{subject}</span>
            </div>
            <div className="flex items-start justify-between gap-2">
              <span className="font-semibold text-[var(--ds-text-subtle)] w-20">Version:</span>
              <span className="font-medium text-[var(--ds-text-brand)] flex-1">Draft Version {versionNumber}</span>
            </div>
            <div className="flex items-start justify-between gap-2 text-[10px] text-[var(--ds-text-subtlest)] pt-1 border-t border-[var(--ds-border)]/50">
              <span className="w-20">Idempotency:</span>
              <span className="font-mono flex-1 truncate">{idempotencyKey}</span>
            </div>
          </div>
        </div>

        {/* Footer actions */}
        <div className="px-5 py-3 border-t border-[var(--ds-border)] bg-[var(--ds-background-subtle)] flex items-center justify-end gap-3">
          <Button
            type="button"
            variant="subtle"
            data-testid="send-modal-cancel-button"
            disabled={isSending}
            onClick={onCancel}
            className="text-xs py-1.5 px-3"
          >
            Cancel
          </Button>

          <Button
            type="button"
            variant="primary"
            data-testid="send-modal-confirm-button"
            disabled={isSending}
            onClick={handleConfirmClick}
            className="text-xs py-1.5 px-4 font-semibold flex items-center gap-2"
          >
            {isSending ? (
              <>
                <svg
                  data-testid="sending-progress-spinner"
                  className="animate-spin h-3.5 w-3.5 text-white"
                  viewBox="0 0 24 24"
                  fill="none"
                >
                  <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
                  <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8v8H4z" />
                </svg>
                <span>Sending via SMTP...</span>
              </>
            ) : (
              <span>Confirm & Send Reply</span>
            )}
          </Button>
        </div>
      </div>
    </div>
  );
};
