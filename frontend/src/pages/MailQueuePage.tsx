import React, { useState, useEffect, useCallback } from "react";
import { Badge } from "../components/ui/Badge";
import { Button } from "../components/ui/Button";
import { DraftReviewWorkspace } from "../features/draft/DraftReviewWorkspace";

export interface MailQueuePageProps {
  queueId: string;
  selectedStoreId?: string | null;
}

export interface EmailItemMeta {
  id: string;
  store_profile_id: string;
  store_name: string;
  from_address: string;
  sender_name?: string | null;
  subject: string;
  received_at: string;
  processing_status: string;
  intent?: string | null;
  intent_confidence?: number | null;
  customer_status?: string | null;
  spam_status?: string | null;
  has_attachments: boolean;
  attachment_count: number;
  current_draft_version?: number;
  manual_review_reason?: string | null;
  review_reason_code?: string | null;
  spam_score?: number | null;
  detected_language?: string | null;
}

export interface AttachmentMetaItem {
  id?: string;
  filename: string;
  content_type: string;
  size_bytes: number;
  is_valid?: boolean;
  validation_error?: string | null;
}

export interface EmailDetailContent {
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
  attachments_metadata: AttachmentMetaItem[];
  manual_review_reason?: string | null;
  review_reason_code?: string | null;
  intent?: string | null;
  customer_status?: string | null;
  spam_status?: string | null;
  order_snapshot?: {
    lookup_status: string;
    matched_order_count: number;
    has_paid_order: boolean;
    has_active_order: boolean;
    has_cancelled_order: boolean;
    has_refunded_order: boolean;
    has_fulfilled_order: boolean;
    latest_order_name?: string | null;
    latest_order_total_price?: string | null;
    latest_order_currency?: string | null;
    latest_order_financial_status?: string | null;
    latest_order_fulfillment_status?: string | null;
    line_items_summary?: Array<{ title: string; quantity: number; price: string | number }> | null;
    lookup_checked_at?: string;
  } | null;
  product_snapshot?: {
    search_query: string;
    matched_count: number;
    product_resolved: boolean;
    matched_products?: Array<{
      title: string;
      min_price: string;
      max_price: string;
      has_in_stock_variant: boolean;
      total_inventory?: number | null;
      variants?: Array<{
        variant_id?: string;
        title: string;
        sku?: string | null;
        price: string;
        currency?: string;
        available_for_sale?: boolean;
        inventory_quantity?: number | null;
      }>;
    }>;
    warning_codes?: string[];
  } | null;
  is_stale?: boolean;
  stale_reason?: string | null;
  stale_details?: Array<{ policy_type: string; used_hash: string; current_hash: string }>;
}

const queueTitles: Record<string, string> = {
  "ready-to-review": "Waiting for Approval (Ready to Review)",
  "needs-manual-review": "Needs Manual Review",
  "product-inquiry": "Product Inquiries",
  "recent-order": "Recent Orders",
  "complaint": "Complaints & Returns",
  "spam": "Spam Queue",
  "sent": "Sent Replies",
};

export const MailQueuePage: React.FC<MailQueuePageProps> = ({ queueId, selectedStoreId }) => {
  const [emails, setEmails] = useState<EmailItemMeta[]>([]);
  const [isLoading, setIsLoading] = useState(true);
  const [totalCount, setTotalCount] = useState(0);
  const [page, setPage] = useState(1);
  const [limit] = useState(25);
  const [search, setSearch] = useState("");
  const [selectedEmail, setSelectedEmail] = useState<EmailDetailContent | null>(null);
  const [, setIsLoadingDetail] = useState(false);
  const [actionSuccessMsg, setActionSuccessMsg] = useState<string | null>(null);
  const [actionErrorMsg, setActionErrorMsg] = useState<string | null>(null);

  // Form override state
  const [overrideIntent, setOverrideIntent] = useState("product_inquiry");
  const [overrideCustomerStatus, setOverrideCustomerStatus] = useState("no_order");
  const [overrideSpamStatus, setOverrideSpamStatus] = useState("not_spam");
  const [overrideGenerateDraft, setOverrideGenerateDraft] = useState(true);
  const [overrideNotes, setOverrideNotes] = useState("");
  const [isSubmittingOverride, setIsSubmittingOverride] = useState(false);

  const title = queueTitles[queueId] || "Email Queue";

  const fetchEmails = useCallback(async () => {
    setIsLoading(true);
    try {
      const params = new URLSearchParams({
        page: String(page),
        limit: String(limit),
      });
      if (selectedStoreId) params.append("store_id", selectedStoreId);
      if (search.trim()) params.append("search", search.trim());

      const res = await fetch(`/api/queues/${queueId}/emails?${params.toString()}`);
      if (res.ok) {
        const data = await res.json();
        setEmails(data.items || []);
        setTotalCount(data.total || 0);
      } else {
        setEmails([]);
        setTotalCount(0);
      }
    } catch {
      setEmails([]);
      setTotalCount(0);
    } finally {
      setIsLoading(false);
    }
  }, [queueId, selectedStoreId, page, limit, search]);

  useEffect(() => {
    fetchEmails();
  }, [fetchEmails]);

  const handleOpenEmail = async (emailId: string) => {
    setIsLoadingDetail(true);
    try {
      const meta = emails.find((e) => e.id === emailId);
      const res = await fetch(`/api/emails/${emailId}`);
      if (res.ok) {
        const detail = await res.json();
        setSelectedEmail({
          ...detail,
          manual_review_reason: meta?.manual_review_reason,
          review_reason_code: meta?.review_reason_code,
          intent: meta?.intent,
          customer_status: meta?.customer_status,
          spam_status: meta?.spam_status,
        });
        if (meta?.intent) setOverrideIntent(meta.intent);
        if (meta?.customer_status) setOverrideCustomerStatus(meta.customer_status);
        if (meta?.spam_status) setOverrideSpamStatus(meta.spam_status);
      } else {
        if (meta) {
          setSelectedEmail({
            email_id: meta.id,
            mailbox_address: "support@store.com",
            imap_uid: 101,
            subject: meta.subject,
            sender_email: meta.from_address,
            sender_name: meta.sender_name,
            recipient_email: "support@store.com",
            received_at: meta.received_at,
            body_text: "Email body content fetched on demand.",
            body_html_sanitized: "<p>Email body content fetched on demand.</p>",
            has_attachments: meta.has_attachments,
            attachment_count: meta.attachment_count,
            attachments_metadata: [],
            manual_review_reason: meta.manual_review_reason,
            review_reason_code: meta.review_reason_code,
            intent: meta.intent,
            customer_status: meta.customer_status,
            spam_status: meta.spam_status,
          });
          if (meta.intent) setOverrideIntent(meta.intent);
        }
      }
    } catch {
      // ignore
    } finally {
      setIsLoadingDetail(false);
    }
  };

  const handleUnmarkSpam = async (emailId: string) => {
    try {
      const res = await fetch(`/api/emails/${emailId}/unmark-spam`, { method: "POST" });
      if (res.ok) {
        setActionSuccessMsg("Email marked as not spam and moved to queue.");
        fetchEmails();
        if (selectedEmail?.email_id === emailId) {
          setSelectedEmail(null);
        }
        setTimeout(() => setActionSuccessMsg(null), 3000);
      }
    } catch {
      // ignore
    }
  };

  const handleOverrideSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!selectedEmail) return;
    setIsSubmittingOverride(true);
    try {
      const res = await fetch(`/api/emails/${selectedEmail.email_id}/override-classification`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          intent: overrideIntent,
          customer_status: overrideCustomerStatus,
          spam_status: overrideSpamStatus,
          generate_draft: overrideGenerateDraft,
          notes: overrideNotes.trim() || undefined,
        }),
      });
      if (res.ok) {
        setActionSuccessMsg("Classification overridden successfully.");
        setSelectedEmail(null);
        fetchEmails();
        setTimeout(() => setActionSuccessMsg(null), 3000);
      }
    } catch {
      // ignore
    } finally {
      setIsSubmittingOverride(false);
    }
  };

  const handleRegenerateWithLatestPolicies = async () => {
    if (!selectedEmail) return;
    setActionErrorMsg(null);
    setActionSuccessMsg(null);
    try {
      const res = await fetch(`/api/emails/${selectedEmail.email_id}/regenerate-draft`, { method: "POST" });
      if (res.ok) {
        setActionSuccessMsg("Draft regenerated with latest policies.");
        setSelectedEmail((prev) =>
          prev ? { ...prev, is_stale: false, stale_reason: null, stale_details: [] } : null
        );
        fetchEmails();
        setTimeout(() => setActionSuccessMsg(null), 3000);
      } else {
        let errorDetail = "Failed to regenerate draft. Please try again.";
        try {
          const errData = await res.json();
          if (errData && errData.detail) {
            errorDetail = typeof errData.detail === "string" ? errData.detail : errorDetail;
          }
        } catch {
          // ignore
        }
        setActionErrorMsg(errorDetail);
        setTimeout(() => setActionErrorMsg(null), 4000);
      }
    } catch {
      setActionErrorMsg("Failed to regenerate draft. Please try again.");
      setTimeout(() => setActionErrorMsg(null), 4000);
    }
  };

  const formatTime = (isoString: string) => {
    try {
      const d = new Date(isoString);
      return d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
    } catch {
      return isoString;
    }
  };

  const renderReasonBadge = (reasonCode: string | null | undefined) => {
    if (!reasonCode) return null;
    let label = reasonCode;
    let variant: "danger" | "warning" | "neutral" = "warning";

    if (reasonCode === "ATTACHMENT_TOO_LARGE") {
      label = "Attachment > 10MB";
      variant = "danger";
    } else if (reasonCode === "TOTAL_ATTACHMENT_SIZE_EXCEEDED") {
      label = "Total Size > 25MB";
      variant = "danger";
    } else if (reasonCode === "PROMPT_INJECTION_DETECTED") {
      label = "Prompt Injection";
      variant = "danger";
    } else if (reasonCode === "UNSUPPORTED_ATTACHMENT_TYPE") {
      label = "Unsupported File";
      variant = "warning";
    } else if (reasonCode === "ENCRYPTED_OR_PASSWORD_PROTECTED_FILE") {
      label = "Password Protected";
      variant = "warning";
    } else if (reasonCode === "UNCERTAIN_INTENT") {
      label = "Uncertain Intent";
      variant = "warning";
    } else if (reasonCode === "NON_DRAFTING_INTENT") {
      label = "Partnership / Other";
      variant = "neutral";
    } else if (reasonCode === "JOB_MAX_ATTEMPTS_EXCEEDED") {
      label = "Processing Error";
      variant = "danger";
    }

    return (
      <span data-testid="reason-badge">
        <Badge variant={variant}>{label}</Badge>
      </span>
    );
  };

  return (
    <div className="p-6 max-w-7xl mx-auto space-y-6">
      {/* Toast Notification */}
      {actionSuccessMsg && (
        <div
          data-testid="toast-success-notification"
          className="fixed top-4 right-4 z-50 bg-[var(--ds-background-success-bold)] text-[var(--ds-text-inverse)] text-xs font-semibold px-4 py-2.5 rounded shadow-lg animate-fade-in"
        >
          ✓ {actionSuccessMsg}
        </div>
      )}
      {actionErrorMsg && (
        <div
          data-testid="toast-error-notification"
          className="fixed top-4 right-4 z-50 bg-[var(--ds-background-danger-bold)] text-[var(--ds-text-inverse)] text-xs font-semibold px-4 py-2.5 rounded shadow-lg animate-fade-in"
        >
          ✕ {actionErrorMsg}
        </div>
      )}

      {/* Header Toolbar */}
      <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-4 border-b border-[var(--ds-border)] pb-4">
        <div>
          <h1 className="text-xl font-bold text-[var(--ds-text)]">{title}</h1>
          <p className="text-xs text-[var(--ds-text-subtle)] mt-1">
            Zero-autonomous sending safeguard. Items require manual review before outbound dispatch.
          </p>
        </div>
        <div className="flex items-center gap-3">
          <input
            type="text"
            placeholder="Search sender or subject..."
            value={search}
            onChange={(e) => {
              setSearch(e.target.value);
              setPage(1);
            }}
            className="px-3 py-1.5 text-xs bg-[var(--ds-background-default)] border border-[var(--ds-border)] rounded text-[var(--ds-text)] placeholder-[var(--ds-text-subtlest)] focus:outline-hidden focus:border-[var(--ds-border-brand)]"
          />
          <Button variant="subtle" className="text-xs py-1 px-2.5" onClick={() => fetchEmails()}>
            Refresh
          </Button>
        </div>
      </div>

      {/* Main Container - data-testid="queue-container" */}
      <div
        data-testid="queue-container"
        className="bg-[var(--ds-background-default)] border border-[var(--ds-border)] rounded-lg shadow-xs overflow-hidden"
      >
        {isLoading ? (
          <div className="p-12 text-center text-xs text-[var(--ds-text-subtle)] space-y-2">
            <svg
              className="animate-spin h-6 w-6 text-[var(--ds-text-brand)] mx-auto"
              viewBox="0 0 24 24"
              fill="none"
            >
              <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
              <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8v8H4z" />
            </svg>
            <p>Loading email metadata...</p>
          </div>
        ) : emails.length === 0 ? (
          <div data-testid="queue-empty-state" className="p-12 text-center space-y-3">
            <div className="w-12 h-12 rounded-full bg-[var(--ds-background-subtle)] flex items-center justify-center mx-auto text-[var(--ds-text-subtle)]">
              <svg className="w-6 h-6" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path
                  strokeLinecap="round"
                  strokeLinejoin="round"
                  strokeWidth="2"
                  d="M3 8l7.89 5.26a2 2 0 002.22 0L21 8M5 19h14a2 2 0 002-2V7a2 2 0 00-2-2H5a2 2 0 00-2 2v10a2 2 0 002 2z"
                />
              </svg>
            </div>
            <h3 className="text-sm font-semibold text-[var(--ds-text)]">No items in this queue</h3>
            <p className="text-xs text-[var(--ds-text-subtle)] max-w-md mx-auto">
              When active stores receive new emails, they will be classified and routed here according to intent and order context.
            </p>
          </div>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-left text-xs border-collapse">
              <thead>
                <tr className="border-b border-[var(--ds-border)] bg-[var(--ds-background-subtle)] text-[var(--ds-text-subtle)] font-medium">
                  <th className="py-2.5 px-4">Sender</th>
                  <th className="py-2.5 px-4">Subject</th>
                  <th className="py-2.5 px-4">Store</th>
                  <th className="py-2.5 px-4">Classification</th>
                  <th className="py-2.5 px-4">Order Status</th>
                  <th className="py-2.5 px-4">Received</th>
                  <th className="py-2.5 px-4 text-right">Actions</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-[var(--ds-border)]">
                {emails.map((item) => (
                  <tr
                    key={item.id}
                    id={`email-item-${item.id}`}
                    data-testid="email-list-row"
                    data-item-testid={`email-item-${item.id}`}
                    onClick={() => handleOpenEmail(item.id)}
                    className="hover:bg-[var(--ds-background-neutral-hovered)] transition-colors cursor-pointer"
                  >
                    {/* Sender */}
                    <td className="py-3 px-4 font-medium text-[var(--ds-text)] max-w-xs truncate">
                      <span data-testid={`email-item-${item.id}`} className="hidden" />
                      <div data-testid="email-row-sender" className="truncate">
                        {item.sender_name ? `${item.sender_name} <${item.from_address}>` : item.from_address}
                      </div>
                    </td>

                    {/* Subject */}
                    <td className="py-3 px-4 text-[var(--ds-text)] max-w-md truncate">
                      <div data-testid="email-row-subject" className="truncate font-normal">
                        {item.subject}
                      </div>
                    </td>

                    {/* Store */}
                    <td className="py-3 px-4 text-[var(--ds-text-subtle)] whitespace-nowrap">
                      {item.store_name}
                    </td>

                    {/* Intent Badge */}
                    <td className="py-3 px-4 whitespace-nowrap">
                      <div className="flex items-center gap-1.5 flex-wrap">
                        <span data-testid="badge-intent">
                          <Badge variant="brand">{item.intent || "unclassified"}</Badge>
                        </span>
                        {/* Reason Badge on needs-manual-review */}
                        {(queueId === "needs-manual-review" || item.review_reason_code) &&
                          renderReasonBadge(item.review_reason_code || item.manual_review_reason)}
                      </div>
                    </td>

                    {/* Order Status Badge */}
                    <td className="py-3 px-4 whitespace-nowrap">
                      <span data-testid="badge-order-status">
                        <Badge variant={item.customer_status === "has_order_record" ? "success" : "neutral"}>
                          {item.customer_status === "has_order_record" ? "Recent Order" : "No Order"}
                        </Badge>
                      </span>
                      {item.spam_status === "spam" && (
                        <span data-testid="badge-spam-status" className="ml-1.5">
                          <Badge variant="danger">Spam</Badge>
                        </span>
                      )}
                    </td>

                    {/* Received Time */}
                    <td className="py-3 px-4 text-[var(--ds-text-subtle)] whitespace-nowrap">
                      {formatTime(item.received_at)}
                    </td>

                    {/* Actions */}
                    <td className="py-3 px-4 text-right whitespace-nowrap space-x-1.5">
                      {queueId === "spam" ? (
                        <Button
                          variant="subtle"
                          className="text-xs py-1 px-2.5 text-[var(--ds-text-danger)] hover:bg-[var(--ds-background-danger-subtle)] font-medium"
                          data-testid="button-unmark-spam"
                          onClick={(e) => {
                            e.stopPropagation();
                            handleUnmarkSpam(item.id);
                          }}
                        >
                          Not Spam
                        </Button>
                      ) : (
                        <Button
                          variant="subtle"
                          className="text-xs py-1 px-2.5"
                          onClick={(e) => {
                            e.stopPropagation();
                            handleOpenEmail(item.id);
                          }}
                        >
                          Review
                        </Button>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}

        {/* Pagination Bar */}
        {totalCount > limit && (
          <div className="flex items-center justify-between px-4 py-3 border-t border-[var(--ds-border)] bg-[var(--ds-background-subtle)] text-xs text-[var(--ds-text-subtle)]">
            <div>
              Showing {Math.min(emails.length, limit)} of {totalCount} items
            </div>
            <div className="flex items-center gap-2">
              <Button
                variant="subtle"
                className="text-xs py-1 px-2.5"
                disabled={page <= 1}
                onClick={() => setPage((p) => Math.max(1, p - 1))}
              >
                Previous
              </Button>
              <span className="px-2">Page {page}</span>
              <Button
                variant="subtle"
                className="text-xs py-1 px-2.5"
                disabled={page * limit >= totalCount}
                onClick={() => setPage((p) => p + 1)}
              >
                Next
              </Button>
            </div>
          </div>
        )}
      </div>

      {/* Two-Pane Draft Review Workspace for non-spam queues */}
      {selectedEmail && queueId !== "spam" && (
        <DraftReviewWorkspace
          email={selectedEmail}
          onClose={() => setSelectedEmail(null)}
          onEmailSentSuccess={() => {
            fetchEmails();
            setActionSuccessMsg("Reply sent successfully.");
            setTimeout(() => setActionSuccessMsg(null), 3000);
          }}
          onStatusUpdated={() => {
            fetchEmails();
          }}
        />
      )}

      {/* On-demand Email Detail Modal (Spam Queue) */}
      {selectedEmail && queueId === "spam" && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 backdrop-blur-xs p-4">
          <div className="bg-[var(--ds-background-default)] border border-[var(--ds-border)] rounded-lg shadow-xl max-w-2xl w-full max-h-[90vh] flex flex-col overflow-hidden">
            <div className="p-4 border-b border-[var(--ds-border)] flex items-center justify-between bg-[var(--ds-background-subtle)]">
              <div>
                <h3 className="text-sm font-bold text-[var(--ds-text)] truncate">{selectedEmail.subject}</h3>
                <p className="text-xs text-[var(--ds-text-subtle)]">
                  From: {selectedEmail.sender_name ? `${selectedEmail.sender_name} <${selectedEmail.sender_email}>` : selectedEmail.sender_email}
                </p>
              </div>
              <button
                onClick={() => setSelectedEmail(null)}
                className="text-[var(--ds-text-subtle)] hover:text-[var(--ds-text)] p-1 rounded"
              >
                ✕
              </button>
            </div>

            <div className="p-4 overflow-y-auto flex-1 space-y-4">
              {/* Manual Review Alert Banner */}
              {(selectedEmail.manual_review_reason || selectedEmail.review_reason_code) && (
                <div
                  data-testid="manual-review-alert-banner"
                  className="p-3 bg-[var(--ds-background-warning-subtle)] border border-[var(--ds-border-warning)] rounded text-xs text-[var(--ds-text-warning)] flex items-start gap-2"
                >
                  <span className="font-bold">⚠️ Manual Review Required:</span>
                  <span>{selectedEmail.manual_review_reason || selectedEmail.review_reason_code}</span>
                </div>
              )}

              {/* Stale Draft Alert Banner (Invariant R-18) */}
              {selectedEmail.is_stale && (
                <div
                  data-testid="stale-draft-alert-banner"
                  className="p-3 bg-[var(--ds-background-warning)] border border-[var(--ds-border-warning)] rounded text-xs text-[var(--ds-text)] space-y-2"
                >
                  <div className="flex items-center justify-between gap-2 flex-wrap">
                    <span className="font-bold text-[var(--ds-text-warning)]">
                      ⚠️ Stale Draft — Store Policy Updated
                    </span>
                    <Button
                      variant="default"
                      className="text-xs py-1 px-2.5 font-medium shadow-xs border-[var(--ds-border-warning)] bg-[var(--ds-background-default)] hover:bg-[var(--ds-background-subtle)] text-[var(--ds-text-warning)]"
                      data-testid="button-regenerate-draft-latest-policies"
                      onClick={handleRegenerateWithLatestPolicies}
                    >
                      Regenerate Draft with Latest Policies
                    </Button>
                  </div>
                  <p className="text-[11px] text-[var(--ds-text)]">
                    Store policies have been updated since this draft was generated. Re-generating draft ensures accurate terms.
                  </p>
                  {selectedEmail.stale_details && selectedEmail.stale_details.length > 0 && (
                    <div className="text-[11px] font-mono text-[var(--ds-text-subtle)] bg-[var(--ds-background-default)]/60 p-1.5 rounded">
                      Changed: {selectedEmail.stale_details.map((d) => d.policy_type).join(", ")}
                    </div>
                  )}
                </div>
              )}

              {/* Product Not Resolved Warning Banner (Invariant R-16) */}
              {selectedEmail.product_snapshot && !selectedEmail.product_snapshot.product_resolved && (
                <div
                  data-testid="product-not-resolved-banner"
                  className="p-3 bg-[var(--ds-background-warning)] border border-[var(--ds-border-warning)] rounded text-xs text-[var(--ds-text)] flex items-start gap-2"
                >
                  <span className="font-bold text-[var(--ds-text-warning)]">⚠️ Product not resolved:</span>
                  <span>
                    No active product found for search query "{selectedEmail.product_snapshot.search_query}". Operator review required. Do not invent price or stock.
                  </span>
                </div>
              )}

              {/* Shopify 60-Day Order Facts Snapshot (Invariant R-09, R-10) */}
              {selectedEmail.order_snapshot && (
                <div
                  data-testid="order-facts-snapshot"
                  className="p-3 bg-[var(--ds-background-subtle)] border border-[var(--ds-border)] rounded text-xs space-y-2"
                >
                  <div className="flex items-center justify-between">
                    <span className="font-bold text-[var(--ds-text)]">Shopify 60-Day Order Facts</span>
                    <Badge variant={selectedEmail.order_snapshot.lookup_status === "success" ? "success" : "neutral"}>
                      {selectedEmail.order_snapshot.lookup_status === "success"
                        ? `${selectedEmail.order_snapshot.matched_order_count} Order(s) Matched`
                        : "No Order in 60d"}
                    </Badge>
                  </div>

                  {/* 5 independent boolean status flags */}
                  <div className="flex flex-wrap gap-1.5 pt-1">
                    {selectedEmail.order_snapshot.has_paid_order && (
                      <Badge variant="success" data-testid="flag-paid">Paid</Badge>
                    )}
                    {selectedEmail.order_snapshot.has_active_order && (
                      <Badge variant="brand" data-testid="flag-active">Active</Badge>
                    )}
                    {selectedEmail.order_snapshot.has_cancelled_order && (
                      <Badge variant="danger" data-testid="flag-cancelled">Cancelled</Badge>
                    )}
                    {selectedEmail.order_snapshot.has_refunded_order && (
                      <Badge variant="warning" data-testid="flag-refunded">Refunded</Badge>
                    )}
                    {selectedEmail.order_snapshot.has_fulfilled_order && (
                      <Badge variant="success" data-testid="flag-fulfilled">Fulfilled</Badge>
                    )}
                  </div>

                  {selectedEmail.order_snapshot.latest_order_name && (
                    <div className="text-[11px] text-[var(--ds-text-subtle)] pt-1 space-y-0.5">
                      <div>
                        Latest: <strong className="text-[var(--ds-text)]">{selectedEmail.order_snapshot.latest_order_name}</strong> — {selectedEmail.order_snapshot.latest_order_total_price} {selectedEmail.order_snapshot.latest_order_currency}
                      </div>
                      <div>
                        Status: {selectedEmail.order_snapshot.latest_order_financial_status} | Fulfillment: {selectedEmail.order_snapshot.latest_order_fulfillment_status || "unfulfilled"}
                      </div>
                    </div>
                  )}

                  {selectedEmail.order_snapshot.lookup_checked_at && (
                    <div className="text-[10px] text-[var(--ds-text-subtlest)] pt-1" data-testid="lookup-checked-at">
                      Lookup checked at: {new Date(selectedEmail.order_snapshot.lookup_checked_at).toLocaleString()}
                    </div>
                  )}
                </div>
              )}

              {/* Live Product Facts Snapshot (Invariant R-16) */}
              {selectedEmail.product_snapshot && selectedEmail.product_snapshot.product_resolved && (
                <div
                  data-testid="product-facts-snapshot"
                  className="p-3 bg-[var(--ds-background-subtle)] border border-[var(--ds-border)] rounded text-xs space-y-1.5"
                >
                  <div className="flex items-center justify-between">
                    <span className="font-bold text-[var(--ds-text)]">Live Product Facts</span>
                    <Badge variant="success">{selectedEmail.product_snapshot.matched_count} Product(s) Found</Badge>
                  </div>
                  {selectedEmail.product_snapshot.matched_products?.map((prod, idx) => (
                    <div key={idx} className="border-t border-[var(--ds-border)] pt-1.5 space-y-1">
                      <div className="text-[11px] text-[var(--ds-text-subtle)] flex items-center justify-between">
                        <span className="font-medium text-[var(--ds-text)]">{prod.title}</span>
                        <span>${prod.min_price} USD | {prod.has_in_stock_variant ? "In Stock" : "Out of Stock"}</span>
                      </div>
                      {prod.variants && prod.variants.length > 0 && (
                        <div className="pl-2.5 space-y-0.5 border-l-2 border-[var(--ds-border)] text-[10px] text-[var(--ds-text-subtle)]">
                          {prod.variants.map((v, vIdx) => (
                            <div key={vIdx} className="flex items-center justify-between">
                              <span>
                                • <strong className="text-[var(--ds-text)]">{v.title}</strong>
                                {v.sku ? ` (${v.sku})` : ""}
                              </span>
                              <span>
                                ${v.price} {v.currency || "USD"} | {v.inventory_quantity !== null && v.inventory_quantity !== undefined ? `Stock: ${v.inventory_quantity}` : (v.available_for_sale ? "In Stock" : "Out of Stock")}
                              </span>
                            </div>
                          ))}
                        </div>
                      )}
                    </div>
                  ))}
                </div>
              )}

              <div className="flex items-center justify-between text-xs text-[var(--ds-text-subtle)] border-b border-[var(--ds-border)] pb-2">
                <span>IMAP UID: {selectedEmail.imap_uid}</span>
                <span>Mailbox: {selectedEmail.mailbox_address}</span>
              </div>

              {/* Email Body Content */}
              <div
                data-testid="email-body-content"
                className="prose prose-sm max-w-none text-xs text-[var(--ds-text)] whitespace-pre-wrap leading-relaxed bg-[var(--ds-background-subtle)] p-4 rounded border border-[var(--ds-border)]"
              >
                {selectedEmail.body_text || (
                  <div dangerouslySetInnerHTML={{ __html: selectedEmail.body_html_sanitized }} />
                )}
              </div>

              {/* Attachments Section */}
              {selectedEmail.has_attachments && (
                <div className="space-y-1">
                  <h4 className="text-xs font-semibold text-[var(--ds-text)]">
                    Attachments ({selectedEmail.attachment_count})
                  </h4>
                  <ul className="text-xs text-[var(--ds-text-subtle)] space-y-1">
                    {selectedEmail.attachments_metadata.map((att, idx) => (
                      <li key={idx} className="flex items-center justify-between p-2 bg-[var(--ds-background-subtle)] rounded border border-[var(--ds-border)]">
                        <span className="flex items-center gap-1.5 truncate">
                          <span>📎 {att.filename}</span>
                          <span className="text-[10px]">({Math.round(att.size_bytes / 1024)} KB)</span>
                        </span>
                        {att.validation_error && (
                          <Badge variant="danger">{att.validation_error}</Badge>
                        )}
                      </li>
                    ))}
                  </ul>
                </div>
              )}

              {/* Form Override Classification */}
              <div className="pt-2 border-t border-[var(--ds-border)]">
                <form
                  data-testid="form-override-classification"
                  onSubmit={handleOverrideSubmit}
                  className="space-y-3 bg-[var(--ds-background-subtle)] p-3 rounded border border-[var(--ds-border)]"
                >
                  <h4 className="text-xs font-bold text-[var(--ds-text)] uppercase tracking-wider">
                    Operator Classification Override
                  </h4>
                  <div className="grid grid-cols-2 gap-3">
                    <div>
                      <label className="block text-[11px] font-semibold text-[var(--ds-text-subtle)] mb-1">
                        Intent Category
                      </label>
                      <select
                        value={overrideIntent}
                        onChange={(e) => setOverrideIntent(e.target.value)}
                        className="w-full text-xs p-1.5 bg-[var(--ds-background-default)] border border-[var(--ds-border)] rounded text-[var(--ds-text)]"
                      >
                        <option value="product_inquiry">Product Inquiry</option>
                        <option value="order_support">Order Support</option>
                        <option value="complaint">Complaint</option>
                        <option value="return_or_refund">Return or Refund</option>
                        <option value="partnership">Partnership</option>
                        <option value="other">Other</option>
                      </select>
                    </div>

                    <div>
                      <label className="block text-[11px] font-semibold text-[var(--ds-text-subtle)] mb-1">
                        Customer / Order Status
                      </label>
                      <select
                        value={overrideCustomerStatus}
                        onChange={(e) => setOverrideCustomerStatus(e.target.value)}
                        className="w-full text-xs p-1.5 bg-[var(--ds-background-default)] border border-[var(--ds-border)] rounded text-[var(--ds-text)]"
                      >
                        <option value="has_order_record">Has Order Record</option>
                        <option value="no_order">No Order</option>
                      </select>
                    </div>
                  </div>

                  <div className="flex items-center gap-4">
                    <label className="flex items-center gap-2 text-xs text-[var(--ds-text)] cursor-pointer">
                      <input
                        type="checkbox"
                        checked={overrideGenerateDraft}
                        onChange={(e) => setOverrideGenerateDraft(e.target.checked)}
                        className="rounded border-[var(--ds-border)]"
                      />
                      <span>Generate Draft Reply automatically</span>
                    </label>

                    <label className="flex items-center gap-2 text-xs text-[var(--ds-text)] cursor-pointer">
                      <input
                        type="checkbox"
                        checked={overrideSpamStatus === "spam"}
                        onChange={(e) => setOverrideSpamStatus(e.target.checked ? "spam" : "not_spam")}
                        className="rounded border-[var(--ds-border)]"
                      />
                      <span>Mark as Spam</span>
                    </label>
                  </div>

                  <div>
                    <label className="block text-[11px] font-semibold text-[var(--ds-text-subtle)] mb-1">
                      Operator Notes / Reason
                    </label>
                    <textarea
                      rows={2}
                      value={overrideNotes}
                      onChange={(e) => setOverrideNotes(e.target.value)}
                      placeholder="Add reason for classification override..."
                      className="w-full text-xs p-2 bg-[var(--ds-background-default)] border border-[var(--ds-border)] rounded text-[var(--ds-text)] resize-none"
                    />
                  </div>

                  <div className="flex justify-end gap-2 pt-1">
                    <Button
                      type="submit"
                      variant="primary"
                      disabled={isSubmittingOverride}
                      data-testid="button-override-classification"
                      className="text-xs py-1 px-3"
                    >
                      {isSubmittingOverride ? "Applying..." : "Apply & Reclassify"}
                    </Button>
                  </div>
                </form>
              </div>
            </div>

            <div className="p-3 border-t border-[var(--ds-border)] bg-[var(--ds-background-subtle)] flex items-center justify-between">
              <div>
                {queueId === "spam" && (
                  <Button
                    variant="subtle"
                    data-testid="button-unmark-spam"
                    className="text-xs py-1 px-2.5 text-[var(--ds-text-danger)]"
                    onClick={() => handleUnmarkSpam(selectedEmail.email_id)}
                  >
                    Not Spam
                  </Button>
                )}
              </div>
              <div className="flex gap-2">
                <Button variant="subtle" className="text-xs py-1 px-2.5" onClick={() => setSelectedEmail(null)}>
                  Close
                </Button>
              </div>
            </div>
          </div>
        </div>
      )}
    </div>
  );
};
