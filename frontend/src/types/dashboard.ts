/**
 * Type definitions for Operations Dashboard, Health Metrics and Audit Feed.
 */

export type OverallStatus = "healthy" | "degraded" | "down";

export interface DatabaseHealth {
  status: "connected" | "disconnected";
  latency_ms: number;
  dialect: string;
}

export interface WorkerHealth {
  status: "alive" | "stale" | "dead";
  last_heartbeat_at: string | null;
  heartbeat_age_seconds: number | null;
  active_jobs_processing: number;
  queued_jobs_pending: number;
  failed_jobs_count: number;
}

export interface ProxyHealth {
  profile_id: string | null;
  profile_name: string | null;
  protocol: string;
  host_masked: string | null;
  status: "passed" | "failed" | "unconfigured" | "untested";
  last_exit_ip: string | null;
  last_detected_country: string | null;
  last_latency_ms: number | null;
  last_tested_at: string | null;
  last_error_code: string | null;
}

export interface MailboxHealth {
  address: string;
  imap_status: "idle" | "active" | "reconciling" | "error" | "unconfigured";
  idle_connected_at: string | null;
  idle_heartbeat_at: string | null;
  last_reconciled_at: string | null;
  reconciliation_age_seconds: number | null;
  smtp_status: "active" | "error" | "unconfigured";
  last_smtp_success_at: string | null;
  last_error_code: string | null;
}

export interface ShopifyConnectionHealth {
  shop_domain: string;
  auth_status: "authenticated" | "expired" | "failed" | "unconfigured";
  last_successful_lookup_at: string | null;
  last_lookup_age_seconds: number | null;
  last_auth_error_code: string | null;
}

export interface PoliciesHealth {
  total_policies: number;
  last_synced_at: string | null;
  sync_age_seconds: number | null;
  is_stale: boolean;
}

export interface StoreHealthItem {
  store_id: string;
  name: string;
  brand_name: string;
  public_domain: string;
  canonical_domain: string | null;
  status: "active" | "paused" | "draft" | "connection_error" | "archived";
  mailbox: MailboxHealth | null;
  shopify: ShopifyConnectionHealth | null;
  policies: PoliciesHealth;
}

export interface QueueMetrics {
  ready_to_review: number;
  needs_manual_review: number;
  product_inquiry: number;
  recent_order: number;
  complaint: number;
  spam: number;
  sent: number;
  total_unprocessed: number;
  oldest_unreviewed_age_seconds: number | null;
  oldest_unreviewed_received_at: string | null;
  is_backlog_critical: boolean;
}

export interface SystemHealthSummary {
  status: OverallStatus;
  timestamp: string;
  uptime_seconds: number;
  database: DatabaseHealth;
  worker: WorkerHealth;
  proxy: ProxyHealth;
  queues: QueueMetrics;
  stores: StoreHealthItem[];
}

export interface AuditEventItem {
  id: string;
  event_type: string;
  actor_user_id: string | null;
  actor_username: string | null;
  store_profile_id: string | null;
  store_name: string | null;
  target_type: string | null;
  target_id: string | null;
  safe_change_summary: Record<string, any> | null;
  created_at: string;
}

export interface AuditEventsResponse {
  items: AuditEventItem[];
  total: number;
  limit: number;
  offset: number;
}
