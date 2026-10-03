# Mail Agent — Implementation Plan

**Status:** Requirements elicitation complete enough to begin implementation  
**Target:** Internal application for team-owned Shopify stores  
**Production URL:** `https://mail-agent.wrydeco.com`  
**Plan date:** 2026-10-03

---

## 1. Objective

Build an internal multi-store application that:

1. Monitors configured support mailboxes in near real time.
2. Classifies new email on multiple independent dimensions.
3. Checks recent Shopify order relationships through a mandatory US proxy.
4. Reads email attachments with Vertex AI Gemini.
5. Looks up current Shopify products and store policies when relevant.
6. Generates reply drafts for approved intent categories.
7. Requires a logged-in user to review and explicitly approve every reply.
8. Sends approved replies through the existing mailserver.
9. Supports multiple store profiles with separate credentials, context and branding.
10. Provides user, proxy, store, AI, workflow and audit management through a web UI.

The system must start with zero store profiles. Users add and activate profiles from
the UI.

---

## 2. Confirmed Product Decisions

### 2.1 Stores and mailboxes

Initial supported mappings:

| Mailbox | Public store domain | Included |
|---|---|---|
| `support@wrydeco.com` | `wrydeco.com` | Yes |
| `support@chillgen.com` | `chillgen.com` | Yes |
| `support@preaureum.com` | `preaureum.com` | Yes |
| `support@jeminise.com` | `jeminise.com` | Yes |
| `support@piezaprint.com` | `piezaprint.com` | No |

A mailbox belongs to one store profile in V1. The schema must still allow one store
to own multiple mailboxes later.

### 2.2 Mail history

- Only email arriving after a store profile is activated is processed.
- Existing mailbox history is not imported or backfilled.
- Disabling and re-enabling a profile resumes from the existing checkpoint; it must
  not silently reset the baseline.
- Original email and attachment binaries remain on the mailserver.

### 2.3 Draft approval

V1 always uses human approval:

```text
new email
  -> classification and enrichment
  -> generated draft
  -> user review/edit
  -> explicit Approve & Send
  -> SMTP delivery
```

There is no fully automatic sending in V1.

### 2.4 Users and permissions

- Every team member has an individual username/password account.
- All authenticated users have full access in V1.
- Every user can manage email, drafts, stores, proxy credentials, Shopify
  credentials, AI settings and other users.
- Every user can create, disable, re-enable and reset the password of another user.
- A user cannot disable their own current account.
- The last active user cannot be disabled.
- There is no automatic brute-force lockout or login rate limiting in V1.
- The authorization layer must still be RBAC-ready for future roles.

### 2.5 Shopify

- No Shopify webhooks are used in V1.
- All server-side communication with Shopify must use the active configured proxy.
- There must be no direct-connection fallback when the proxy fails.
- Each store profile stores its public domain separately from its canonical
  `*.myshopify.com` domain.
- Users enter Client ID and Client Secret; users do not enter an access token.
- The backend obtains and renews access tokens using client credentials.
- A token is renewed before expiry or once after a `401` response.
- Shopify lookups cover the previous 60 days only.
- Test orders are excluded.
- Cancelled/refunded real orders still create an order relationship, with individual
  state flags retained.

### 2.6 AI

- V1 provider: Vertex AI Gemini.
- AI calls do not use the Shopify proxy.
- Business logic depends on an `AIProvider` interface, not directly on Vertex AI.
- Model identifiers are configurable rather than hard-coded.
- Draft language follows the incoming email language.
- If language detection is uncertain, the store default language is used.
- Users can choose another language when regenerating a draft.

### 2.7 UI

- React 18, TypeScript, Vite and Tailwind CSS v4.
- Components are implemented in the application; no Atlaskit component library.
- Visual styling uses Atlassian Design System semantic design tokens.
- V1 exposes light mode only.
- Components must use semantic tokens so dark/system themes can be added later
  without rewriting components.

### 2.8 Retention

Maximum retention is 120 days for:

- email metadata, classification results and draft versions;
- sent-reply metadata and approval audit;
- security, configuration and user audit events.

Email bodies and attachment binaries are not persisted in PostgreSQL. Store
profiles, active configuration, policy cache, users and IMAP checkpoints remain
until intentionally deleted or superseded.

---

## 3. Current Infrastructure and Constraints

The VPS already runs production services. Relevant current state:

- Mail backend: `docker-mailserver` with Postfix and Dovecot.
- IMAPS: `mail.wrydeco.com:993`.
- SMTP submission: `mail.wrydeco.com:587` with STARTTLS.
- Primary public webmail: SnappyMail at `webmail.wrydeco.com`.
- Roundcube remains installed and running; it has not been removed.
- Host Nginx owns public ports 80 and 443.
- Docker and Docker Compose are already used for production workloads.

The new application must not restart, recreate or reconfigure the mailserver,
SnappyMail, Roundcube or unrelated production containers.

---

## 4. Target Architecture

```text
                              INTERNET
                                  |
                                  | HTTPS :443
                                  v
                       mail-agent.wrydeco.com
                                  |
                                  v
                            Host Nginx
                                  |
                                  | localhost only
                                  v
                         Mail Agent API/UI
                          127.0.0.1:8090
                           /            \
                          /              \
                         v                v
                  PostgreSQL         Mail Worker
                                         |
                 +-----------------------+-----------------------+
                 |                       |                       |
                 v                       v                       v
          IMAPS/SMTP              Proxy-enforced HTTP       AI Provider
      mail.wrydeco.com              Detect.Expert US           Vertex AI
           993/587                       |                    direct HTTPS
                 |                       v
                 |               Shopify Admin API
                 v
        docker-mailserver
```

Containers:

```text
mail-agent-api
mail-agent-worker
mail-agent-db
```

The API and worker use the same application image with different startup commands.
PostgreSQL is dedicated to this application and is not exposed publicly.

---

## 5. Technology Stack

### Backend

- Python 3.12
- FastAPI
- Pydantic
- SQLAlchemy 2
- Alembic migrations
- PostgreSQL 16
- Argon2id password hashing
- Server-side opaque sessions stored in PostgreSQL
- A single proxy-enforced HTTP transport for Shopify
- Google GenAI SDK behind the `AIProvider` abstraction
- IMAP client with IDLE support
- SMTP client with STARTTLS and explicit TLS verification

### Frontend

- React 18
- TypeScript
- Vite
- Tailwind CSS v4
- ADS semantic tokens mapped to Tailwind theme utilities
- Application-owned accessible components

### Operations

- Docker Compose
- Host Nginx reverse proxy
- Let's Encrypt certificate for `mail-agent.wrydeco.com`
- Structured JSON logs with mandatory secret redaction
- PostgreSQL-based job queue using transactional row locking and
  `FOR UPDATE SKIP LOCKED`
- No Redis in V1

---

## 6. Repository Layout

Recommended layout:

```text
auto-reply-mail/
├── backend/
│   ├── app/
│   │   ├── api/
│   │   ├── auth/
│   │   ├── core/
│   │   ├── db/
│   │   ├── mail/
│   │   ├── ai/
│   │   ├── shopify/
│   │   ├── proxy/
│   │   ├── workers/
│   │   └── audit/
│   ├── migrations/
│   └── tests/
├── frontend/
│   ├── src/
│   │   ├── components/ui/
│   │   ├── components/domain/
│   │   ├── features/
│   │   ├── layouts/
│   │   ├── pages/
│   │   └── styles/
│   │       ├── ads-tokens.css
│   │       ├── tailwind-theme.css
│   │       └── globals.css
│   └── tests/
├── deploy/
│   ├── nginx/
│   └── scripts/
├── compose.yaml
├── .env.example
└── README.md
```

The copied `admin/` scripts are reference material only. Production code must not
shell out to `admin/get_access_token.cmd` or print tokens to stdout.

---

## 7. Core Data Model

### Identity and authorization

```text
users
roles
permissions
user_roles
role_permissions
sessions
```

V1 creates one `admin` role and assigns it to all users. API routes still check
named permissions through authorization middleware.

### Store configuration

```text
store_profiles
store_profile_versions
mailboxes
shopify_connections
proxy_profiles
ai_provider_configs
store_context_versions
custom_policies
shopify_policy_cache
```

Store context fields:

```text
brand_name
public_domain
industry
brand_description
default_language
tone_of_voice
email_signature
common_faqs
forbidden_claims
escalation_rules
additional_ai_instructions
```

Custom policies, initially empty:

```text
warranty_policy
cancellation_policy
```

### Mail workflow

```text
mailbox_checkpoints
email_work_items
email_attachment_metadata
email_classifications
shopify_order_snapshots
shopify_product_snapshots
reply_drafts
reply_draft_versions
reply_delivery_attempts
background_jobs
audit_events
```

`email_work_items` contains only a reference and workflow metadata:

```text
mailbox_id
folder
uid_validity
imap_uid
message_id
from_address
to_addresses
subject
received_at
processing_status
```

It does not contain the full raw email, HTML body or attachment binary.

---

## 8. Secrets and Encryption

Secrets requiring encryption at rest:

```text
mailbox password
Shopify Client Secret
Shopify access token
proxy username/password
Vertex AI credential material, if stored by the application
```

Use envelope-style application encryption with an application master key stored
outside PostgreSQL as a Docker secret or a root-owned file. Use authenticated
encryption and store key-version metadata to permit later rotation.

Rules:

- Never return an existing secret to the frontend.
- An empty secret field during edit means "retain current value".
- Never log secrets, access tokens, proxy URLs containing credentials or complete
  authorization headers.
- Rotate the credentials currently present in `admin/.env` before production use.
- Remove plaintext production secrets from source-controlled or shared project
  files.

---

## 9. Store Profile Setup Wizard

The Add Profile flow is staged and does not activate partial configuration.

### Step 1 — Identity and context

```text
profile name
brand name
public domain
industry
brand description
default language
tone of voice
email signature
FAQs
forbidden claims
escalation rules
additional AI instructions
custom warranty policy
custom cancellation policy
```

### Step 2 — Mailbox

```text
mailbox address
mailbox password
IMAP host (default mail.wrydeco.com)
IMAP port (default 993)
SMTP host (default mail.wrydeco.com)
SMTP port (default 587)
TLS settings
```

`Test Mail Connection` validates IMAP and SMTP authentication without sending an
email.

### Step 3 — Proxy

Select an existing proxy profile or create one:

```text
profile name
protocol: HTTP or SOCKS5
host
port
username
password
connection timeout
description/country
```

For SOCKS5, remote DNS resolution must be used so Shopify DNS does not bypass the
proxy.

### Step 4 — Shopify

```text
canonical *.myshopify.com domain
Client ID
Client Secret
```

`Test Shopify Connection` must:

1. Connect through the selected proxy.
2. Obtain an access token through the proxy.
3. Query shop identity through the proxy.
4. Confirm the returned shop matches the configured shop.
5. Check required scopes.
6. Display safe diagnostics and never expose the token.

### Step 5 — AI

Select the active AI provider configuration and optional store-level overrides:

```text
classification model
drafting model
temperature
maximum output tokens
```

### Step 6 — Activate

Activation is allowed only after all required connection tests pass. Activation:

1. Connects to IMAP.
2. Reads the current `UIDVALIDITY` and highest UID.
3. Saves this point as the initial baseline.
4. Starts the mailbox IDLE listener.
5. Schedules the five-minute reconciliation job.
6. Synchronizes Shopify policies.

---

## 10. Proxy-Enforced Shopify Client

All Shopify code depends on one application service:

```text
ShopifyService
  -> ProxyEnforcedShopifyTransport
  -> active ProxyProfile
  -> Shopify
```

The following must pass through this transport:

- access-token acquisition and renewal;
- shop identity tests;
- order lookup;
- product and variant lookup;
- policy synchronization;
- every future Shopify API request.

If an active valid proxy is unavailable, client creation fails. There is no code
path for direct fallback.

Token behavior:

1. Reuse an encrypted token while safely before expiry.
2. Acquire a new token before expiry through the proxy.
3. On an API `401`, invalidate the cached token, acquire a new token and retry the
   original request exactly once.
4. Use a per-store database/advisory lock so concurrent workers do not refresh the
   same token simultaneously.
5. Do not refresh repeatedly on `403`; report missing scopes/configuration instead.

Initial required Shopify scopes are expected to include:

```text
read_orders
read_products
read_legal_policies
```

Only the most recent 60 days of orders are queried, so V1 does not require
`read_all_orders`. Exact scopes and protected customer-data requirements must be
verified against the selected Shopify API version during implementation.

---

## 11. Mail Ingestion

### 11.1 IMAP IDLE

Each active mailbox has an IDLE listener:

```text
connect with verified TLS
  -> authenticate
  -> select INBOX
  -> enter IDLE
  -> receive mailbox change notification
  -> fetch new UID(s)
  -> enqueue processing
  -> re-enter IDLE
```

The listener must renew IDLE sessions periodically and reconnect with bounded
exponential backoff after disconnects.

### 11.2 Five-minute reconciliation

Every five minutes:

1. Load the stored `UIDVALIDITY` and last processed UID.
2. Query for higher UIDs.
3. Insert missing work items with a uniqueness constraint.
4. Advance checkpoints only after durable enqueueing.

Uniqueness key:

```text
(mailbox_id, folder, uid_validity, imap_uid)
```

The worker does not set `Seen`, move messages or change mailbox flags.

### 11.3 UIDVALIDITY change

An unexpected UIDVALIDITY change pauses automatic processing for that mailbox and
raises an operator-visible error. It must not silently reprocess the mailbox or
skip messages.

---

## 12. Attachment Processing

V1 supported types:

```text
JPEG
PNG
WebP
PDF
TXT
DOCX
XLSX
```

Default configurable limits:

```text
10 MB per attachment
25 MB total per email
10 attachments per email
```

Pipeline:

1. Fetch the original message and attachment from IMAP only when processing.
2. Verify actual MIME/signature, not only the filename extension.
3. Never execute macros, scripts or embedded active content.
4. Extract or normalize content in an isolated temporary working directory.
5. Send supported content to Vertex AI.
6. Delete temporary data immediately after processing.
7. Persist only filename, MIME type, size and hash.

Failure behavior:

```text
unsupported, oversized, encrypted or unreadable attachment
  -> no draft
  -> Needs manual review
  -> explicit reason code
```

User actions from manual review:

- retry processing;
- view/download the original through an authenticated API backed by IMAP;
- consciously ignore the attachment and regenerate from readable content;
- write a manual draft;
- mark no reply needed.

---

## 13. Classification Model

Classification is multi-dimensional.

### Spam status

```text
spam
not_spam
uncertain
```

### Customer/order status

```text
has_order_record
no_order
lookup_unavailable
not_checked
```

Order state flags:

```text
has_paid_order
has_active_order
has_cancelled_order
has_refunded_order
has_fulfilled_order
```

### Intent

```text
product_inquiry
order_support
complaint
return_or_refund
partnership
other
uncertain
```

Each result contains confidence, reason codes, model ID, prompt version and
classification timestamp. User overrides take precedence over AI output and are
fully audited.

Spam behavior:

- Label spam in the application.
- Hide it from the reply queue.
- Do not move/delete the original or change IMAP flags.
- Provide a Spam view for manual correction and reclassification.

---

## 14. Shopify Enrichment

### 14.1 Order lookup

Normalize the sender email by trimming and lowercasing only. Do not infer that two
different addresses belong to the same person.

Query real, non-test orders created during the previous 60 days. Persist only a
small snapshot:

```text
matched_order_count
latest_order_id
latest_order_name
latest_order_created_at
financial status
fulfillment status
cancelled/refunded flags
lookup_checked_at
```

`lookup_unavailable` is distinct from `no_order`.

Default retry schedule for temporary Shopify/proxy failures:

```text
1 minute
5 minutes
15 minutes
30 minutes
60 minutes
```

After the final failure, transition to `Needs manual review`. Configuration/auth or
scope errors skip repeated retries and surface immediately in Store Settings.

### 14.2 Product lookup

For product inquiries:

1. AI extracts product-identifying details.
2. The backend searches current Shopify products/variants through the proxy.
3. Relevant product facts are passed to the drafting provider.
4. The lookup snapshot used by the draft is recorded for audit.

The application does not mirror the full catalog. If a product cannot be resolved,
generate a draft asking the customer for a product link, name or image and mark the
draft with a `Product not resolved` warning. AI must not invent price, stock,
variants or product claims.

### 14.3 Policy synchronization

Synchronize these Shopify policy types through the proxy:

```text
REFUND_POLICY
PRIVACY_POLICY
TERMS_OF_SERVICE
SHIPPING_POLICY
CONTACT_INFORMATION
LEGAL_NOTICE
```

The Shopify `body` is HTML. Store a sanitized HTML representation, normalized text,
Shopify ID/type/title/URL, Shopify `updatedAt`, fetch time and content hash.

Refresh policies:

- on profile activation;
- when the cache is older than five minutes before relevant drafting;
- when a user selects Sync now.

If a policy changes after a draft was generated, display a warning and require
regeneration or an explicit reviewed override before sending.

Warranty and cancellation remain optional manually entered custom policies and are
empty by default.

---

## 15. AI Provider Architecture

Application interface:

```text
AIProvider
  classify_email(input, store_context)
  generate_reply_draft(input, store_context)
  test_connection()
  get_usage_metadata()
```

V1 implementation:

```text
VertexGeminiProvider
```

Future implementations can include Gemini Developer API, OpenAI or Anthropic
without changing mail workflow code.

AI input is assembled from the minimum required data:

```text
trusted system instructions
store context and current relevant policies
email subject/body
supported attachment content
order snapshot
product snapshot
requested output schema
```

Email and attachments are untrusted content. Prompts must clearly delimit them as
data and instruct the model not to follow embedded attempts to override system or
store instructions.

Classification output must use a validated structured schema. Invalid output is
retried within a strict bound and then moved to manual review.

Draft language:

1. Detect incoming language.
2. Draft in that language when detection is reliable.
3. Fall back to the store default language.
4. Permit language selection during regeneration.

---

## 16. Draft Creation Rules

| Classification | Draft behavior |
|---|---|
| Spam | No draft |
| Product inquiry | Generate draft |
| Order support | Generate draft |
| Complaint | Generate draft with warning |
| Return/refund | Generate draft with warning |
| Partnership | Needs manual review; no draft |
| Other | Needs manual review; no draft |
| Uncertain | Needs manual review; no draft |
| Shopify lookup unavailable | Retry, then manual review; no draft |
| Attachment processing failure | Needs manual review; no draft |

Every generated draft records:

```text
draft version
AI provider/model
prompt/context version
language
classification snapshot
order/product/policy snapshot references
creator (AI or user)
created_at
```

Editing creates a new draft version. Approval targets an immutable version.

---

## 17. Approval and Sending

Available actions:

```text
Edit draft
Regenerate draft
Change regeneration language
Approve & Send
Reject draft
No reply needed
Move to manual review
```

Sending rules:

1. Lock the approved draft version transactionally.
2. Create a unique send operation/idempotency key.
3. Send through the configured SMTP account with STARTTLS and verified TLS.
4. Preserve thread headers including `In-Reply-To` and `References`.
5. Generate and store a unique outgoing `Message-ID`.
6. After SMTP success, append a copy to the mailbox Sent folder through IMAP.
7. Record SMTP and Sent-folder outcomes separately.

If the SMTP server might have accepted the message but the client did not receive a
definitive response, mark `delivery_unknown`. Do not retry automatically; require
manual inspection to avoid a duplicate reply.

---

## 18. Workflow State Machine

Representative states:

```text
detected
queued
fetching
processing_attachments
shopify_lookup
classifying
enriching_product
syncing_policy
generating_draft
pending_approval
needs_manual_review
spam
no_reply_needed
sending
sent
delivery_unknown
failed_retryable
failed_terminal
```

All transitions are validated by the backend and recorded in audit history.

---

## 19. UI Information Architecture

### Authentication

- Login
- Change own password
- Active sessions and logout

### Dashboard

- Counts by store and workflow status
- Connection health for mailbox, Shopify, proxy and AI
- Recent processing failures

### Email queues

- Waiting for approval
- Needs manual review
- Product inquiries
- Customers with recent orders
- Complaints/returns/refunds
- Spam
- Sent
- No reply needed

### Email detail

- Original email rendered safely from IMAP
- Attachment list and secure preview/download
- Multi-dimensional labels and confidence
- Shopify order/product evidence
- Policy/context evidence used
- Draft editor and version history
- Warnings and reason codes
- Approve/Reject/Regenerate/No reply actions
- Audit timeline

### Store profiles

- Profile setup wizard
- Context and custom policies
- Mail connection
- Shopify connection and scopes
- Assigned proxy
- Assigned AI provider/models
- Policy synchronization status
- Enable/disable profile

### Settings

- Proxy profiles and connection tests
- AI provider configurations
- Attachment limits
- Shopify retry schedule
- Retention settings capped at 120 days
- User management
- Audit log

### ADS token implementation

- Define a central semantic token layer.
- Map semantic tokens into Tailwind v4 theme utilities.
- Forbid raw visual values in feature components through linting/conventions.
- Ship light theme only.
- Keep theme selection behind a root `ThemeProvider` so dark/system themes can be
  introduced later.

---

## 20. Authentication and Session Design

- Passwords are hashed with Argon2id.
- Usernames are normalized and unique case-insensitively.
- Browser receives a high-entropy opaque session token in a cookie with:

```text
HttpOnly
Secure
SameSite=Lax
```

- PostgreSQL stores only a hash of the session token.
- Password reset and manual user disable revoke all target-user sessions.
- State-changing requests use CSRF protection.
- The backend enforces self-disable and last-active-user safeguards inside a
  transaction to prevent concurrent lockout.
- No automatic brute-force protection is implemented in V1, per product decision.

---

## 21. Security Boundaries

1. Sanitize inbound email HTML before browser rendering.
2. Sanitize Shopify policy HTML before caching/rendering.
3. Serve attachment downloads only after authentication and authorization.
4. Prevent path traversal and never use sender-provided filenames as local paths.
5. Isolate temporary attachment processing and delete temporary data on success,
   failure and worker startup cleanup.
6. Treat all email/attachment text as untrusted prompt data.
7. Validate proxy configuration without exposing credentials.
8. Enforce TLS verification for IMAP, SMTP, Shopify, proxy tunnels and Vertex AI.
9. Redact secrets and customer content from application logs.
10. Keep PostgreSQL and internal service ports bound to Docker/internal networks or
    localhost only.

---

## 22. Retention and Cleanup

A daily cleanup job deletes workflow records older than 120 days:

```text
email metadata
classification records
drafts and draft versions
order/product snapshots
sent-reply metadata
approval history
security/config/user audit events
completed background-job history
```

The cleanup job must preserve referential integrity and emit aggregate counts, not
deleted content, to logs.

Not automatically removed:

```text
active users
store profiles
active credentials/configuration
current policy cache
custom policies
mailbox checkpoints
retention configuration
```

The original messages remain subject to the mailserver's own retention policy.

---

## 23. Observability and Health

Health endpoints:

```text
/health/live
/health/ready
```

Operational status shown in UI:

```text
API/database health
worker heartbeat
per-mailbox IDLE/reconciliation health
last successful Shopify lookup
proxy connectivity and exit IP
policy sync age
AI provider connectivity
SMTP/IMAP connection health
queue depth and oldest job age
```

Logs use correlation IDs for email workflow, job, Shopify call and send operation.
Logs must not contain email bodies, attachment content or secrets.

---

## 24. Deployment Plan

### Host paths

```text
/opt/mail-agent
/var/www/mail-agent-acme
/etc/nginx/sites-available/mail-agent.wrydeco.com.conf
/etc/nginx/sites-enabled/mail-agent.wrydeco.com.conf
```

### Public topology

```text
mail-agent.wrydeco.com:80/443
  -> host Nginx
  -> 127.0.0.1:8090
  -> mail-agent-api
```

No app database, worker or debug port is publicly exposed.

### Deployment safety sequence

1. Capture baseline container, port, Nginx and mailserver status.
2. Create only the `mail-agent` project directory and dedicated volumes.
3. Build and validate containers without public routing.
4. Run database migrations.
5. Create the first user through a one-time CLI command.
6. Test app locally on `127.0.0.1:8090`.
7. Add only the `mail-agent.wrydeco.com` DNS record.
8. Create an isolated ACME webroot and certificate.
9. Add a dedicated Nginx server block.
10. Run `nginx -t` before graceful reload.
11. Verify HTTPS, login and store-profile setup.
12. Verify existing mailserver, SnappyMail, Roundcube and unrelated services remain
    healthy.

### Rollback

1. Disable the mail-agent Nginx symlink and gracefully reload Nginx.
2. Stop only the mail-agent Compose project.
3. Preserve the database volume and secrets for investigation.
4. Do not restart or alter the production mailserver.
5. Remove only the mail-agent DNS record if full rollback is required.

---

## 25. Implementation Phases

### Phase 0 — Security and repository hygiene

Deliverables:

- Rotate the Client Secret and Shopify access token currently present in
  `admin/.env`.
- Remove plaintext secrets from shared/source-controlled files.
- Add safe `.env.example` files.
- Establish secret redaction tests.
- Create project structure, formatting, linting and CI test commands.

Acceptance:

- Secret scanning finds no real production credentials.
- Application logs cannot print configured secrets.

### Phase 1 — Foundation, database and authentication

Deliverables:

- FastAPI application and PostgreSQL connection.
- Alembic migration framework.
- User/session/RBAC schema.
- Username/password login.
- User management, manual disable/reset and session revocation.
- Self-disable and last-active-user constraints.
- React/Tailwind application shell using ADS tokens.

Acceptance:

- First user can be bootstrapped from CLI.
- All V1 users receive the admin role.
- User disable/reset revokes sessions immediately.
- Concurrent disable operations cannot leave zero active users.

### Phase 2 — Store, proxy and Shopify configuration

Deliverables:

- Store profile setup wizard.
- Encrypted secret storage.
- Proxy profile CRUD and connection test.
- Proxy-enforced Shopify transport.
- Client-credentials token manager.
- Shopify identity/scope test.
- Order/product/policy clients.

Acceptance:

- Network tests prove Shopify requests fail closed when the proxy is unavailable.
- Token acquisition and renewal occur through the proxy.
- A direct Shopify client cannot be instantiated from feature code.

### Phase 3 — Mail connections and ingestion

Deliverables:

- IMAP/SMTP profile tests.
- Activation baseline.
- IMAP IDLE manager.
- Five-minute reconciliation.
- UID checkpointing and deduplication.
- Job queue and worker heartbeat.

Acceptance:

- Historical email is not imported.
- New email appears near real time.
- An IDLE disconnect followed by reconciliation does not lose or duplicate mail.
- Worker activity does not change mailbox read/folder flags.

### Phase 4 — Attachment and AI classification pipeline

Deliverables:

- Safe MIME detection and attachment extraction.
- Vertex AI provider behind `AIProvider`.
- Structured multi-dimensional classification.
- Spam and manual-review behavior.
- Language detection.
- Prompt injection boundaries.

Acceptance:

- Supported attachments are included in AI input.
- Unsupported/failed attachments never produce an automatic draft.
- Invalid AI output cannot bypass schema validation.
- Spam is hidden from reply queue without modifying the mailserver message.

### Phase 5 — Shopify enrichment and store policies

Deliverables:

- 60-day order lookup and order state flags.
- Product/variant search for product inquiries.
- Six Shopify policy types synchronized through the proxy.
- HTML policy sanitization and text normalization.
- Optional warranty/cancellation custom policies.
- Policy/product evidence in the email detail UI.

Acceptance:

- `no_order` is emitted only after a successful lookup.
- Temporary lookup errors retry, then move to manual review.
- Product facts and policy content are traceable to the draft version.
- Changed policy content produces a stale-draft warning.

### Phase 6 — Draft review and delivery

Deliverables:

- Draft generation rules by intent.
- Draft editor and immutable version history.
- Regeneration with language selection.
- Approval workflow.
- SMTP sending, reply threading and Sent-folder append.
- Send idempotency and `delivery_unknown` handling.

Acceptance:

- No generated draft sends without explicit user approval.
- Double-clicks, retries and concurrent users cannot send the same draft twice.
- SMTP ambiguity never triggers a blind automatic retry.
- Sent replies maintain email thread headers.

### Phase 7 — Operations, retention and production deployment

Deliverables:

- Dashboard and health status.
- Redacted structured logging.
- Daily 120-day cleanup job.
- Backup/restore scripts and runbook.
- Docker Compose production definition with resource limits.
- DNS, certificate and isolated Nginx vhost.
- Production smoke and regression checks.

Acceptance:

- HTTPS is valid at `mail-agent.wrydeco.com`.
- Internal ports are not publicly exposed.
- Existing mailserver/webmail/services remain healthy and unrestarted.
- Backup and rollback are exercised before launch.

---

## 26. Test Strategy

### Unit tests

- Email normalization and MIME parsing.
- Classification schema validation.
- Draft eligibility rules.
- Shopify error mapping.
- Proxy fail-closed behavior.
- Token expiry/renewal.
- User lockout invariants.
- Retention calculation.

### Integration tests

- PostgreSQL migrations and job locking.
- IMAP fixture server with IDLE/disconnect scenarios.
- SMTP fixture server with success/failure/ambiguous delivery.
- Mock Shopify server accessed only through a test proxy.
- Mock AI provider plus Vertex staging test.
- HTML sanitization and attachment limits.

### End-to-end tests

- Add and activate a store profile.
- Receive a new product inquiry and approve its draft.
- Receive mail from a recent-order customer.
- Spam classification and manual override.
- Attachment failure to manual review.
- Shopify outage and retry-to-manual-review.
- Two users editing/approving concurrently.
- Password reset and user disable session revocation.

### Production smoke tests

- Login and logout.
- Proxy exit-IP and Shopify connection test.
- Vertex connection test with non-customer fixture data.
- IMAP and SMTP authentication without sending externally.
- Controlled internal test email through the complete approval flow.
- Regression check for ports 25, 587, 993, SnappyMail and Roundcube.

---

## 27. Definition of Done for V1

V1 is complete only when:

- Users can sign in with individual accounts.
- Store profiles start at zero and can be configured entirely through the UI.
- All Shopify calls, including token acquisition, are proven to use the selected
  proxy with no direct fallback.
- Four intended support mailboxes can be configured independently.
- `support@piezaprint.com` is not processed.
- Only post-activation email is ingested.
- IMAP IDLE plus five-minute reconciliation is proven resilient.
- Supported attachment content reaches Vertex AI; failed attachments stop drafting.
- Classification is multi-dimensional and manually overridable.
- Shopify orders use a 60-day window and preserve cancellation/refund flags.
- Product and policy information is current, sourced from Shopify and traceable.
- Drafts are generated only for approved categories.
- Every send requires explicit approval of an immutable draft version.
- Duplicate sends and ambiguous delivery are safely handled.
- No raw email or attachment binary is stored in PostgreSQL.
- Workflow and audit retention does not exceed 120 days.
- The application is available through valid HTTPS at
  `mail-agent.wrydeco.com`.
- Existing VPS production services remain unaffected.

