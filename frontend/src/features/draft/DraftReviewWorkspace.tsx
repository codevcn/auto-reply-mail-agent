import React, { useState, useEffect, useMemo, useCallback } from "react";
import { Badge } from "../../components/ui/Badge";
import { Button } from "../../components/ui/Button";
import { DiffViewer } from "./DiffViewer";
import { SendConfirmModal } from "./SendConfirmModal";

export interface DraftVersionItem {
  id: string;
  draft_id: string;
  version_number: number;
  subject: string;
  body_text: string;
  body_html: string;
  language: string;
  source: string; // 'ai' | 'user'
  ai_provider?: string | null;
  ai_model?: string | null;
  prompt_version?: string | null;
  warning_codes?: string[];
  content_hash?: string;
  is_current_version: boolean;
  created_at: string;
}

export interface DraftDetailData {
  email_id: string;
  draft_id?: string;
  current_version_id?: string | null;
  current_version_number: number;
  status: string;
  is_stale: boolean;
  stale_reason?: string | null;
  stale_details?: Array<{ policy_type: string; used_hash: string; current_hash: string }>;
  warning_codes: string[];
  versions: DraftVersionItem[];
}

export interface DraftReviewWorkspaceProps {
  email: {
    email_id: string;
    mailbox_address: string;
    imap_uid: number;
    subject: string;
    sender_email: string;
    sender_name?: string | null;
    recipient_email: string;
    received_at: string;
    body_text: string;
    body_html_sanitized: string;
    has_attachments: boolean;
    attachment_count: number;
    attachments_metadata?: Array<{
      filename: string;
      size_bytes: number;
      validation_error?: string | null;
    }>;
    manual_review_reason?: string | null;
    review_reason_code?: string | null;
    intent?: string | null;
    customer_status?: string | null;
    spam_status?: string | null;
    is_stale?: boolean;
    stale_reason?: string | null;
    stale_details?: Array<{ policy_type: string; used_hash: string; current_hash: string }>;
    order_snapshot?: any;
    product_snapshot?: any;
  };
  onClose: () => void;
  onEmailSentSuccess?: () => void;
  onStatusUpdated?: () => void;
}

export const DraftReviewWorkspace: React.FC<DraftReviewWorkspaceProps> = ({
  email,
  onClose,
  onEmailSentSuccess,
  onStatusUpdated,
}) => {
  const [draftDetail, setDraftDetail] = useState<DraftDetailData | null>(null);
  const [selectedVersionNumber, setSelectedVersionNumber] = useState<number>(1);
  const [subjectText, setSubjectText] = useState<string>(`Re: ${email.subject}`);
  const [editorBodyText, setEditorBodyText] = useState<string>("");
  const [isDiffMode, setIsDiffMode] = useState<boolean>(false);
  const [targetLang, setTargetLang] = useState<string>("en");

  // Actions states
  const [isLoadingDraft, setIsLoadingDraft] = useState<boolean>(true);
  const [isSavingVersion, setIsSavingVersion] = useState<boolean>(false);
  const [isRegenerating, setIsRegenerating] = useState<boolean>(false);
  const [isSending, setIsSending] = useState<boolean>(false);
  const [isSendModalOpen, setIsSendModalOpen] = useState<boolean>(false);
  const [toastSuccess, setToastSuccess] = useState<string | null>(null);
  const [toastError, setToastError] = useState<string | null>(null);

  const showSuccess = (msg: string) => {
    setToastSuccess(msg);
    setTimeout(() => setToastSuccess(null), 3500);
  };

  const showError = (msg: string) => {
    setToastError(msg);
    setTimeout(() => setToastError(null), 4000);
  };

  // Fetch or initialize draft details
  const fetchDraftDetails = useCallback(async () => {
    setIsLoadingDraft(true);
    try {
      const res = await fetch(`/api/drafts/email/${email.email_id}`);
      if (res.ok) {
        const data: DraftDetailData = await res.json();
        setDraftDetail(data);

        // Pick current version or latest version
        const currentV =
          data.versions.find((v) => v.id === data.current_version_id) ||
          data.versions[data.versions.length - 1];

        if (currentV) {
          setSelectedVersionNumber(currentV.version_number);
          setSubjectText(currentV.subject);
          setEditorBodyText(currentV.body_text);
          if (currentV.language) setTargetLang(currentV.language);
        }
      } else {
        // Fallback or empty draft state
        setEditorBodyText(
          `Hi ${email.sender_name || "Customer"},\n\nThank you for reaching out to us. We have received your inquiry regarding "${email.subject}".\n\nBest regards,\nCustomer Support Team`
        );
      }
    } catch {
      // Fallback
      setEditorBodyText(
        `Hi ${email.sender_name || "Customer"},\n\nThank you for reaching out to us. We have received your inquiry.\n\nBest regards,\nSupport Team`
      );
    } finally {
      setIsLoadingDraft(false);
    }
  }, [email.email_id, email.sender_name, email.subject]);

  useEffect(() => {
    fetchDraftDetails();
  }, [fetchDraftDetails]);

  // V1 text for diffing
  const v1Text = useMemo(() => {
    if (!draftDetail?.versions || draftDetail.versions.length === 0) return "";
    const v1 = draftDetail.versions.find((v) => v.version_number === 1);
    return v1 ? v1.body_text : draftDetail.versions[0].body_text;
  }, [draftDetail]);

  // Current selected version metadata
  const currentSelectedVersion = useMemo(() => {
    return draftDetail?.versions.find((v) => v.version_number === selectedVersionNumber);
  }, [draftDetail, selectedVersionNumber]);

  // Counters
  const wordCount = useMemo(() => {
    return editorBodyText.trim() ? editorBodyText.trim().split(/\s+/).length : 0;
  }, [editorBodyText]);

  const charCount = useMemo(() => {
    return editorBodyText.length;
  }, [editorBodyText]);

  // Switch versions in timeline
  const handleSelectVersion = (vNum: number) => {
    setSelectedVersionNumber(vNum);
    const ver = draftDetail?.versions.find((v) => v.version_number === vNum);
    if (ver) {
      setSubjectText(ver.subject);
      setEditorBodyText(ver.body_text);
    }
  };

  // Save manual edit as a new immutable version (Invariant R-24)
  const handleSaveVersion = async () => {
    if (!editorBodyText.trim()) {
      showError("Draft content cannot be empty.");
      return;
    }
    setIsSavingVersion(true);
    try {
      const res = await fetch(`/api/emails/${email.email_id}/drafts`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          subject: subjectText.trim(),
          body_text: editorBodyText,
          body_html: `<p>${editorBodyText.replace(/\n\n/g, "</p><p>").replace(/\n/g, "<br/>")}</p>`,
          base_version_number: selectedVersionNumber,
        }),
      });

      if (res.ok) {
        showSuccess("Draft saved as a new version.");
        await fetchDraftDetails();
      } else {
        showError("Failed to save draft version.");
      }
    } catch {
      showError("Network error while saving draft.");
    } finally {
      setIsSavingVersion(false);
    }
  };

  // AI Regenerate Draft
  const handleRegenerateDraft = async () => {
    setIsRegenerating(true);
    try {
      const res = await fetch(`/api/emails/${email.email_id}/drafts/regenerate`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          target_language: targetLang,
        }),
      });

      if (res.ok) {
        showSuccess("Draft regenerated successfully.");
        await fetchDraftDetails();
      } else {
        showError("Failed to regenerate draft.");
      }
    } catch {
      showError("Network error while regenerating draft.");
    } finally {
      setIsRegenerating(false);
    }
  };

  // Mark as No Reply Needed
  const handleNoReplyNeeded = async () => {
    try {
      const res = await fetch(`/api/emails/${email.email_id}/mark-no-reply`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ action_type: "no_reply_needed" }),
      });
      if (res.ok) {
        showSuccess("Email marked as No Reply Needed.");
        if (onStatusUpdated) onStatusUpdated();
        setTimeout(onClose, 1000);
      } else {
        showError("Failed to mark as no reply.");
      }
    } catch {
      showError("Failed to mark as no reply.");
    }
  };

  // Move to Manual Review
  const handleMoveToManualReview = async () => {
    try {
      const res = await fetch(`/api/emails/${email.email_id}/move-manual-review`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ reason_code: "OPERATOR_ESCALATION" }),
      });
      if (res.ok) {
        showSuccess("Moved to manual review.");
        if (onStatusUpdated) onStatusUpdated();
        setTimeout(onClose, 1000);
      } else {
        showError("Failed to move to manual review.");
      }
    } catch {
      showError("Failed to move to manual review.");
    }
  };

  // Approve & Send outbound via SMTP with Idempotency Key (Invariants R-01 & R-26)
  const handleConfirmSend = async (idempotencyKey: string) => {
    setIsSending(true);
    try {
      const draftVersionId = currentSelectedVersion?.id;
      const endpoint = draftVersionId
        ? `/api/drafts/${draftVersionId}/approve-and-send`
        : `/api/queue/emails/${email.email_id}/draft/approve-and-send`;

      const res = await fetch(endpoint, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          "X-Idempotency-Key": idempotencyKey,
        },
        body: JSON.stringify({
          idempotency_key: idempotencyKey,
        }),
      });

      if (res.ok) {
        setIsSendModalOpen(false);
        showSuccess("✓ Reply sent successfully via SMTP.");
        if (onEmailSentSuccess) onEmailSentSuccess();
        setTimeout(onClose, 1200);
      } else {
        const errData = await res.json().catch(() => ({}));
        showError(errData?.detail?.message || "Failed to dispatch email via SMTP.");
      }
    } catch {
      showError("Network error during SMTP delivery dispatch.");
    } finally {
      setIsSending(false);
    }
  };

  // Risk & Context flags
  const isComplaint =
    email.intent === "complaint" ||
    draftDetail?.warning_codes?.includes("COMPLAINT_DETECTED") ||
    false;

  const isReturnRefund =
    email.intent === "return_or_refund" ||
    draftDetail?.warning_codes?.includes("RETURN_REFUND_REQUEST") ||
    false;

  const isStaleDraft = draftDetail?.is_stale || email.is_stale || false;

  const isProductUnresolved =
    email.product_snapshot && !email.product_snapshot.product_resolved;

  return (
    <div
      data-testid="two-pane-container"
      className="fixed inset-0 z-40 bg-[var(--ds-background-default)] flex flex-col overflow-hidden animate-fade-in"
    >
      {/* Toast Notifications */}
      {toastSuccess && (
        <div
          data-testid="toast-success-notification"
          className="fixed top-4 right-4 z-50 bg-[var(--ds-background-success-bold)] text-[var(--ds-text-inverse)] text-xs font-semibold px-4 py-2.5 rounded shadow-lg animate-fade-in"
        >
          {toastSuccess}
        </div>
      )}
      {toastError && (
        <div
          data-testid="toast-error-notification"
          className="fixed top-4 right-4 z-50 bg-[var(--ds-background-danger-bold)] text-[var(--ds-text-inverse)] text-xs font-semibold px-4 py-2.5 rounded shadow-lg animate-fade-in"
        >
          ✕ {toastError}
        </div>
      )}

      {/* Top Workspace Header */}
      <div className="h-12 border-b border-[var(--ds-border)] bg-[var(--ds-background-subtle)] px-4 flex items-center justify-between text-xs shrink-0">
        <div className="flex items-center gap-3 truncate">
          <Button variant="subtle" className="text-xs py-1 px-2.5 font-medium" onClick={onClose}>
            ← Back to Queue
          </Button>
          <div className="h-4 w-px bg-[var(--ds-border)]" />
          <span className="font-semibold text-[var(--ds-text)] truncate">
            {email.subject}
          </span>
          <Badge variant="warning">PENDING_APPROVAL</Badge>
        </div>

        <div className="flex items-center gap-2 text-[var(--ds-text-subtle)]">
          <span>Mailbox: <strong className="text-[var(--ds-text)]">{email.mailbox_address}</strong></span>
          <button
            onClick={onClose}
            className="text-[var(--ds-text-subtle)] hover:text-[var(--ds-text)] p-1.5 rounded ml-2"
          >
            ✕
          </button>
        </div>
      </div>

      {/* Main Two-Pane Split View */}
      <div className="flex-1 flex flex-col md:flex-row overflow-hidden">
        {/* =========================================================================
            LEFT PANE: Context & Evidence (45%)
           ========================================================================= */}
        <div
          data-testid="left-pane-evidence"
          className="w-full md:w-[45%] border-r border-[var(--ds-border)] bg-[var(--ds-background-subtle)] overflow-y-auto p-4 space-y-4"
        >
          {/* 1. Customer Metadata Card */}
          <div className="bg-[var(--ds-background-default)] p-3.5 rounded-lg border border-[var(--ds-border)] shadow-xs space-y-2">
            <div className="flex items-start justify-between gap-2">
              <div>
                <h3 className="font-bold text-xs text-[var(--ds-text)]">{email.subject}</h3>
                <p className="text-[11px] text-[var(--ds-text-subtle)]">
                  From: {email.sender_name ? `${email.sender_name} <${email.sender_email}>` : email.sender_email}
                </p>
              </div>
              <span className="text-[10px] text-[var(--ds-text-subtlest)] font-mono">
                UID #{email.imap_uid}
              </span>
            </div>
            <div className="text-[10px] text-[var(--ds-text-subtlest)] flex items-center justify-between pt-1 border-t border-[var(--ds-border)]/50">
              <span>To: {email.recipient_email}</span>
              <span>{new Date(email.received_at).toLocaleString()}</span>
            </div>
          </div>

          {/* 2. 3D Classification Badges */}
          <div className="flex flex-wrap items-center gap-1.5">
            <span data-testid="badge-intent">
              <Badge variant={email.intent === "complaint" ? "warning" : "brand"}>
                Intent: {email.intent || "uncertain"}
              </Badge>
            </span>
            <span data-testid="badge-order-status">
              <Badge variant={email.customer_status === "has_order_record" ? "success" : "neutral"}>
                Customer: {email.customer_status === "has_order_record" ? "Has Order" : "No Order"}
              </Badge>
            </span>
            <span data-testid="badge-spam-status">
              <Badge variant={email.spam_status === "spam" ? "danger" : "neutral"}>
                Spam: {email.spam_status || "not_spam"}
              </Badge>
            </span>
          </div>

          {/* 3. Context Threat & Risk Alerts */}
          {/* Complaint Alert */}
          {isComplaint && (
            <div
              data-testid="alert-complaint-detected"
              className="p-3 bg-[var(--ds-background-warning)] border border-[var(--ds-border-warning)] rounded-md text-xs text-[var(--ds-text-warning)] flex items-start gap-2 shadow-xs"
            >
              <span className="font-bold shrink-0">⚠️ Complaint Detected:</span>
              <span>
                Customer is expressing dissatisfaction or filing a service complaint. Review tone for empathy and verify
                all statements carefully before sending.
              </span>
            </div>
          )}

          {/* Return / Refund Alert */}
          {isReturnRefund && (
            <div
              data-testid="alert-return-refund-request"
              className="p-3 bg-[var(--ds-background-warning)] border border-[var(--ds-border-warning)] rounded-md text-xs text-[var(--ds-text-warning)] flex items-start gap-2 shadow-xs"
            >
              <span className="font-bold shrink-0">⚠️ Return / Refund Request:</span>
              <span>
                Return or refund request detected. Verify store return policies (within 30 days) and Shopify order status
                prior to approval.
              </span>
            </div>
          )}

          {/* Stale Draft Alert Banner (Invariant R-18) */}
          {isStaleDraft && (
            <div
              data-testid="stale-draft-alert-banner"
              className="p-3 bg-[var(--ds-background-warning)] border border-[var(--ds-border-warning)] rounded-md text-xs text-[var(--ds-text)] space-y-2 shadow-xs"
            >
              <div className="flex items-center justify-between gap-2 flex-wrap">
                <span className="font-bold text-[var(--ds-text-warning)]">
                  ⚠️ Stale Draft — Store Policy Updated
                </span>
                <Button
                  variant="default"
                  data-testid="button-regenerate-draft-latest-policies"
                  disabled={isRegenerating}
                  onClick={handleRegenerateDraft}
                  className="text-xs py-1 px-2.5 font-medium shadow-xs border-[var(--ds-border-warning)] bg-[var(--ds-background-default)] hover:bg-[var(--ds-background-subtle)] text-[var(--ds-text-warning)]"
                >
                  {isRegenerating ? "Regenerating..." : "Regenerate Draft with Latest Policies"}
                </Button>
              </div>
              <p className="text-[11px] text-[var(--ds-text)]">
                Store policies changed after this draft was generated. Regenerate to guarantee compliance with latest terms.
              </p>
            </div>
          )}

          {/* Product Not Resolved Banner (Invariant R-16) */}
          {isProductUnresolved && (
            <div
              data-testid="product-not-resolved-banner"
              className="p-3 bg-[var(--ds-background-warning)] border border-[var(--ds-border-warning)] rounded-md text-xs text-[var(--ds-text)] flex items-start gap-2 shadow-xs"
            >
              <span className="font-bold text-[var(--ds-text-warning)] shrink-0">⚠️ Product Not Resolved:</span>
              <span>
                No matching product found on Shopify for query "{email.product_snapshot?.search_query || ""}". Operator
                review required. Do not invent price, model specifications, or stock levels!
              </span>
            </div>
          )}

          {/* 4. Shopify 60-Day Order Facts Snapshot (Phase 5) */}
          {email.order_snapshot && (
            <div
              data-testid="order-facts-snapshot"
              className="p-3 bg-[var(--ds-background-default)] border border-[var(--ds-border)] rounded-lg text-xs space-y-2 shadow-xs"
            >
              <div className="flex items-center justify-between">
                <span className="font-bold text-[var(--ds-text)]">Shopify 60-Day Order Facts</span>
                <Badge variant={email.order_snapshot.lookup_status === "success" ? "success" : "neutral"}>
                  {email.order_snapshot.lookup_status === "success"
                    ? `${email.order_snapshot.matched_order_count || 1} Order(s) Matched`
                    : "No Order in 60d"}
                </Badge>
              </div>

              {/* 5 Status Flags */}
              <div className="flex flex-wrap gap-1.5 pt-1">
                {email.order_snapshot.has_paid_order && (
                  <Badge variant="success" data-testid="flag-paid">Paid</Badge>
                )}
                {email.order_snapshot.has_active_order && (
                  <Badge variant="brand" data-testid="flag-active">Active</Badge>
                )}
                {email.order_snapshot.has_cancelled_order && (
                  <Badge variant="danger" data-testid="flag-cancelled">Cancelled</Badge>
                )}
                {email.order_snapshot.has_refunded_order && (
                  <Badge variant="warning" data-testid="flag-refunded">Refunded</Badge>
                )}
                {email.order_snapshot.has_fulfilled_order && (
                  <Badge variant="success" data-testid="flag-fulfilled">Fulfilled</Badge>
                )}
              </div>

              {email.order_snapshot.latest_order_name && (
                <div className="text-[11px] text-[var(--ds-text-subtle)] pt-1 space-y-0.5">
                  <div>
                    Latest: <strong className="text-[var(--ds-text)]">{email.order_snapshot.latest_order_name}</strong>
                    {" — "}
                    {email.order_snapshot.latest_order_total_price} {email.order_snapshot.latest_order_currency || "USD"}
                  </div>
                  <div>
                    Financial: {email.order_snapshot.latest_order_financial_status} | Fulfillment: {email.order_snapshot.latest_order_fulfillment_status || "unfulfilled"}
                  </div>
                </div>
              )}

              {email.order_snapshot.lookup_checked_at && (
                <div className="text-[10px] text-[var(--ds-text-subtlest)] pt-1" data-testid="lookup-checked-at">
                  Lookup checked at: {new Date(email.order_snapshot.lookup_checked_at).toLocaleString()}
                </div>
              )}
            </div>
          )}

          {/* 5. Live Product Facts Snapshot (Phase 5) */}
          {email.product_snapshot && email.product_snapshot.product_resolved && (
            <div
              data-testid="product-facts-snapshot"
              className="p-3 bg-[var(--ds-background-default)] border border-[var(--ds-border)] rounded-lg text-xs space-y-1.5 shadow-xs"
            >
              <div className="flex items-center justify-between">
                <span className="font-bold text-[var(--ds-text)]">Live Product Facts</span>
                <Badge variant="success">{email.product_snapshot.matched_count || 1} Product(s) Found</Badge>
              </div>
              {email.product_snapshot.matched_products?.map((prod: any, idx: number) => (
                <div key={idx} className="border-t border-[var(--ds-border)] pt-1.5 space-y-1">
                  <div className="text-[11px] text-[var(--ds-text-subtle)] flex items-center justify-between">
                    <span className="font-medium text-[var(--ds-text)]">{prod.title}</span>
                    <span>${prod.min_price} USD | {prod.has_in_stock_variant ? "In Stock" : "Out of Stock"}</span>
                  </div>
                </div>
              ))}
            </div>
          )}

          {/* 6. Original Email Body Content (Safe render) */}
          <div className="space-y-1.5">
            <h4 className="font-semibold text-xs text-[var(--ds-text-subtle)]">Original Message</h4>
            <div
              data-testid="email-body-content"
              className="bg-[var(--ds-background-default)] border border-[var(--ds-border)] rounded-lg p-3 text-xs text-[var(--ds-text)] whitespace-pre-wrap leading-relaxed shadow-xs overflow-y-auto max-h-[300px]"
            >
              {email.body_text || (
                <div dangerouslySetInnerHTML={{ __html: email.body_html_sanitized }} />
              )}
            </div>
          </div>

          {/* 7. Attachments List */}
          {email.has_attachments && email.attachments_metadata && (
            <div className="space-y-1">
              <h4 className="font-semibold text-xs text-[var(--ds-text-subtle)]">
                Attachments ({email.attachment_count})
              </h4>
              <ul className="text-xs space-y-1">
                {email.attachments_metadata.map((att, idx) => (
                  <li
                    key={idx}
                    className="flex items-center justify-between p-2 bg-[var(--ds-background-default)] rounded border border-[var(--ds-border)]"
                  >
                    <span className="truncate">📎 {att.filename}</span>
                    {att.validation_error && <Badge variant="danger">{att.validation_error}</Badge>}
                  </li>
                ))}
              </ul>
            </div>
          )}
        </div>

        {/* =========================================================================
            RIGHT PANE: Draft Review & Editor (55%)
           ========================================================================= */}
        <div
          data-testid="right-pane-editor"
          className="w-full md:w-[55%] flex flex-col bg-[var(--ds-background-default)] overflow-hidden"
        >
          {/* Version Header Toolbar & Timeline */}
          <div className="p-3.5 border-b border-[var(--ds-border)] bg-[var(--ds-background-subtle)] flex flex-col gap-2 shrink-0">
            <div className="flex items-center justify-between flex-wrap gap-2">
              <div className="flex items-center gap-2">
                <span
                  data-testid="draft-version-badge"
                  className="px-2.5 py-1 bg-[var(--ds-background-selected)] text-[var(--ds-text-brand)] font-bold text-xs rounded border border-[var(--ds-border-brand)]/40"
                >
                  Version {selectedVersionNumber} ({currentSelectedVersion?.source === "user" ? "User Edited" : "AI Generated"})
                </span>
                <span
                  data-testid="draft-version-ai-tag"
                  className="text-[11px] text-[var(--ds-text-subtle)]"
                >
                  {currentSelectedVersion?.ai_model || "Gemini 1.5 Pro"} | Lang: [{targetLang}]
                </span>
              </div>

              {/* Diff Toggle Button */}
              <Button
                variant={isDiffMode ? "primary" : "subtle"}
                data-testid="diff-toggle-button"
                className="text-xs py-1 px-3 font-medium"
                onClick={() => setIsDiffMode(!isDiffMode)}
              >
                {isDiffMode ? "Switch to Editor" : "Diff with Original (AI V1)"}
              </Button>
            </div>

            {/* Version Timeline bar */}
            {draftDetail?.versions && draftDetail.versions.length > 1 && (
              <div
                data-testid="draft-version-timeline"
                className="flex items-center gap-1.5 pt-1 overflow-x-auto text-[11px]"
              >
                <span className="text-[var(--ds-text-subtle)] font-medium mr-1">History:</span>
                {draftDetail.versions.map((ver) => (
                  <button
                    key={ver.id}
                    data-testid="draft-version-item"
                    onClick={() => handleSelectVersion(ver.version_number)}
                    className={`px-2 py-0.5 rounded text-[11px] transition-colors border ${
                      ver.version_number === selectedVersionNumber
                        ? "bg-[var(--ds-background-brand-bold)] text-[var(--ds-text-inverse)] border-[var(--ds-background-brand-bold)] font-semibold"
                        : "bg-[var(--ds-background-default)] text-[var(--ds-text)] border-[var(--ds-border)] hover:bg-[var(--ds-background-subtle)]"
                    }`}
                  >
                    V{ver.version_number} ({ver.source === "ai" ? "AI" : "User"})
                  </button>
                ))}
              </div>
            )}
          </div>

          {/* Subject & Editor Area */}
          <div className="flex-1 p-4 overflow-y-auto space-y-3 flex flex-col">
            {/* Subject Input */}
            <div>
              <label className="block text-[11px] font-semibold text-[var(--ds-text-subtle)] mb-1">
                Outbound Subject:
              </label>
              <input
                type="text"
                data-testid="draft-subject-input"
                value={subjectText}
                onChange={(e) => setSubjectText(e.target.value)}
                className="w-full px-3 py-1.5 text-xs bg-[var(--ds-background-default)] border border-[var(--ds-border)] rounded text-[var(--ds-text)] focus:outline-hidden focus:border-[var(--ds-border-focused)]"
                placeholder="Subject..."
              />
            </div>

            {/* Body: Diff or Textarea */}
            <div className="flex-1 flex flex-col min-h-[280px]">
              <div className="flex items-center justify-between mb-1">
                <label className="text-[11px] font-semibold text-[var(--ds-text-subtle)]">
                  {isDiffMode ? "Visual Difference View:" : "Reply Body Editor:"}
                </label>
                <div className="flex items-center gap-3 text-[11px] text-[var(--ds-text-subtle)] font-mono">
                  <span data-testid="draft-word-count">Words: {wordCount}</span>
                  <span data-testid="draft-char-count">Chars: {charCount}</span>
                </div>
              </div>

              {isLoadingDraft ? (
                <div className="flex-1 flex items-center justify-center p-12 text-xs text-[var(--ds-text-subtle)]">
                  Loading draft content...
                </div>
              ) : isDiffMode ? (
                <div className="flex-1">
                  <DiffViewer
                    originalText={v1Text}
                    modifiedText={editorBodyText}
                    originalLabel="AI V1 Draft"
                    modifiedLabel={`V${selectedVersionNumber} Current Content`}
                  />
                </div>
              ) : (
                <textarea
                  data-testid="draft-editor-textarea"
                  value={editorBodyText}
                  onChange={(e) => setEditorBodyText(e.target.value)}
                  className="flex-1 w-full p-3 text-xs font-mono leading-relaxed bg-[var(--ds-background-default)] border border-[var(--ds-border)] rounded-md text-[var(--ds-text)] resize-none focus:outline-hidden focus:border-[var(--ds-border-focused)] shadow-2xs"
                  placeholder="Type draft reply here..."
                />
              )}
            </div>
          </div>

          {/* Sticky Bottom Action Controls Bar */}
          <div className="p-3 border-t border-[var(--ds-border)] bg-[var(--ds-background-subtle)] flex items-center justify-between flex-wrap gap-2 shrink-0">
            {/* Left-side non-sending actions */}
            <div className="flex items-center gap-2">
              <Button
                variant="subtle"
                data-testid="draft-no-reply-button"
                className="text-xs py-1.5 px-2.5"
                onClick={handleNoReplyNeeded}
              >
                No Reply Needed
              </Button>
              <Button
                variant="subtle"
                data-testid="draft-move-manual-review-button"
                className="text-xs py-1.5 px-2.5"
                onClick={handleMoveToManualReview}
              >
                Move to Manual Review
              </Button>
            </div>

            {/* Right-side drafting & sending actions */}
            <div className="flex items-center gap-2 flex-wrap">
              {/* Language Selector */}
              <select
                data-testid="draft-regenerate-language-select"
                value={targetLang}
                onChange={(e) => setTargetLang(e.target.value)}
                className="text-xs px-2 py-1.5 bg-[var(--ds-background-default)] border border-[var(--ds-border)] rounded text-[var(--ds-text)]"
              >
                <option value="en">English (en)</option>
                <option value="vi">Tiếng Việt (vi)</option>
                <option value="fr">Français (fr)</option>
                <option value="es">Español (es)</option>
                <option value="de">Deutsch (de)</option>
                <option value="ja">日本語 (ja)</option>
                <option value="zh">中文 (zh)</option>
              </select>

              {/* Regenerate Button */}
              <Button
                variant="default"
                data-testid="draft-regenerate-button"
                disabled={isRegenerating}
                onClick={handleRegenerateDraft}
                className="text-xs py-1.5 px-3"
              >
                {isRegenerating ? "Regenerating..." : "Regenerate Draft"}
              </Button>

              {/* Save Version Button (Invariant R-24) */}
              <Button
                variant="default"
                data-testid="draft-save-button"
                disabled={isSavingVersion}
                onClick={handleSaveVersion}
                className="text-xs py-1.5 px-3"
              >
                {isSavingVersion ? "Saving..." : "Save Version"}
              </Button>

              {/* Approve & Send (Invariant R-01) */}
              <Button
                variant="primary"
                data-testid="draft-approve-send-button"
                onClick={() => setIsSendModalOpen(true)}
                className="text-xs py-1.5 px-4 font-bold shadow-xs flex items-center gap-1.5"
              >
                <span>Approve & Send</span>
              </Button>
            </div>
          </div>
        </div>
      </div>

      {/* Send Confirmation Modal with Debounce & Idempotency Safeguard */}
      <SendConfirmModal
        isOpen={isSendModalOpen}
        recipientEmail={email.sender_email}
        subject={subjectText}
        versionNumber={selectedVersionNumber}
        isSending={isSending}
        onConfirm={handleConfirmSend}
        onCancel={() => setIsSendModalOpen(false)}
      />
    </div>
  );
};
