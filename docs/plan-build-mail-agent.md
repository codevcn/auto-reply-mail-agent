# Mail Agent — Implementation Plan

**Status:** Requirements elicitation complete enough to begin implementation  
**Target:** Internal application for team-owned Shopify stores  
**Production URL:** `https://mail-agent.wrydeco.com`  
**Plan date:** 2026-10-03

> Tài liệu này là bản tổng hợp chính thức các quyết định nghiệp vụ và kỹ thuật đã
> được chốt trong quá trình requirements elicitation. Khi nội dung trong tài liệu
> mâu thuẫn với một prototype cũ trong thư mục `admin/`, tài liệu này là nguồn chuẩn
> cho việc triển khai Mail Agent. Không đưa secret thật vào tài liệu, issue tracker,
> source code hoặc log.

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

When creating a profile, the UI may suggest the public store domain from the support
mailbox domain (for example, `support@wrydeco.com` -> `wrydeco.com`), but the user
must confirm it. This rule must never be used to invent the canonical Shopify
`*.myshopify.com` domain; that value is entered and validated separately.

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
- Every store connection is independently configured and tested. Runtime behavior
  does not depend on all dev apps/stores appearing in one Shopify Dev Dashboard
  organization; organizational grouping is an administrative concern, not a shared
  credential boundary.
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

Retention configuration can be lowered per deployment but can never exceed 120
days. V1 does not hard-delete users; accounts are disabled so historical actor
references remain meaningful until their audit records reach retention expiry.

### 2.9 Architectural decision log and rationale

| Decision | Chosen approach | Rationale |
|---|---|---|
| Source of email truth | Mailserver remains authoritative | Avoid duplicating full email bodies and attachments, database growth and a second retention/security boundary. |
| Database contents | References, metadata, workflow state, drafts and audit only | The application needs durable checkpoints, approvals and send state, but not another mailbox archive. |
| Database engine | Dedicated PostgreSQL | Web and worker need concurrent transactions, uniqueness constraints, row locks, JSON metadata and migrations. SQLite is unsuitable for the target concurrency and operations model. |
| Deployment | Docker Compose | Docker is already present on the VPS; Compose itself is not a persistent RAM-heavy service. Containers give dependency isolation, repeatable rollback and resource limits. |
| Authentication | Opaque server-side sessions in PostgreSQL | Immediate user disable, password-reset revocation and future role changes are simpler than JWT revocation/blacklists. Sessions do not need to be held in RAM. |
| Authorization | RBAC-ready, one all-powerful admin role in V1 | Meets the current full-access requirement while avoiding a rewrite when roles are introduced later. |
| Login abuse protection | No automatic brute-force lock/rate limit in V1 | Explicit product decision. Password hashing, secure cookies, audit and manual disable remain required. |
| Mail detection | IMAP IDLE plus five-minute reconciliation | IDLE gives near-real-time processing; reconciliation recovers from disconnects and worker restarts. |
| Initial mailbox scope | New mail only after activation baseline | Avoids an uncontrolled historical import, unexpected AI cost and database growth. |
| Spam action | Application label only | Avoids moving/deleting a legitimate email due to a classifier false positive. |
| Classification | Multi-dimensional labels | A previous buyer can simultaneously ask about a new product; a single exclusive bucket loses necessary context. |
| Shopify integration | On-demand API calls, no webhook in V1 | The app only needs Shopify data when processing mail; this reduces inbound surface area and webhook lifecycle complexity. |
| Shopify networking | Mandatory fail-closed proxy transport | Satisfies the requirement that every VPS-to-Shopify call, including token acquisition, uses the configured US proxy. |
| Shopify authentication | Client credentials managed by the backend | Users provide Client ID/Secret once; access tokens are short-lived implementation details and are automatically replaced. |
| Order window | Previous 60 days | Matches the chosen business definition and avoids requiring access to complete historical order data. |
| Product data | Live on-demand lookup | Prevents stale prices, variants and product claims without mirroring the full catalog. |
| Store policies | Six Shopify policies synced from GraphQL; warranty/cancellation optional custom text | Shopify remains authoritative for its policy HTML while supporting store-specific information Shopify does not provide by default. |
| AI | `AIProvider` abstraction, Vertex AI Gemini in V1 | Uses the selected Google Cloud provider now while preserving a clean migration path to another provider/model. |
| AI networking | Direct HTTPS, not Shopify proxy | Explicit product decision; only Shopify traffic is proxy-mandatory. |
| Attachment handling | Read every supported attachment; fail closed when unreadable | A draft that ignores customer-provided evidence can be misleading. |
| Sending | Human approval of an immutable draft version | Prevents silent autonomous replies and guarantees the user sees the exact content being sent. |
| UI stack | React/TypeScript/Vite/Tailwind v4 | Matches the selected implementation stack and supports fast application-owned components. |
| Design system | ADS semantic tokens only; no Atlaskit components | Preserves the desired visual language without taking a component-library dependency. |
| Theme | Light only in V1, semantic theme boundary retained | Avoids unnecessary V1 work while allowing later dark/system themes without component rewrites. |
| Queue | PostgreSQL transactional queue, no Redis in V1 | Keeps operations small while still providing durable job claiming and retry state. |
| Retention | Hard maximum 120 days | Meets the chosen storage/privacy limit while retaining enough operational history for review. |

### 2.10 Explicit V1 non-goals

The following are intentionally outside V1 and must not be added implicitly while
implementing another requirement:

- no autonomous sending; every outbound reply requires `Approve & Send`;
- no Shopify webhook endpoint, signature validation or webhook retry pipeline;
- no import/backfill of messages that existed before profile activation;
- no processing of `support@piezaprint.com`;
- no merchant-facing installation, Shopify App Store distribution or external
  tenant self-service;
- no automatic mailbox move, delete, `Seen` flag or server-side spam-folder action;
- no complete copy of raw messages, bodies or attachment binaries in PostgreSQL;
- no full Shopify product/catalog mirror;
- no Atlaskit component dependency and no dark-mode UI in the initial release;
- no JWT access-token architecture, Redis dependency or separate message broker;
- no role differences in the initial UI, despite enforcing named permissions under
  the single V1 `admin` role;
- no shell execution of the copied `admin/get_access_token.cmd` token helper from
  production application code.

Adding one of these capabilities later requires a separate product/security review
and, where relevant, a data-migration and rollback plan.

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

### 7.1 Key columns and constraints

The exact migration syntax is an implementation detail, but the following logical
contract is required.

#### `users`

```text
id UUID primary key
username original display value
normalized_username unique
password_hash Argon2id
status active | disabled
must_change_password boolean
password_changed_at
last_login_at
created_by nullable for bootstrap user
created_at
updated_at
row_version
```

There is no V1 delete operation. Disabling a user revokes sessions in the same
transaction. `normalized_username` is generated consistently so `Tuan`, `tuan` and
`TUAN` cannot become separate accounts.

#### `sessions`

```text
id UUID
user_id foreign key
token_hash unique
csrf_secret_hash or equivalent CSRF binding
created_at
last_seen_at
expires_at
revoked_at
created_ip
last_seen_ip
user_agent_summary
```

The raw session token exists only in the secure browser cookie. Expired/revoked
sessions are cleaned separately and do not need to wait for the 120-day workflow
retention job.

#### `store_profiles`

```text
id UUID
name unique
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
status draft | active | paused | connection_error | archived
active_context_version_id
created_by
created_at
updated_at
row_version
```

Store configuration updates use optimistic concurrency through `row_version` so
two users cannot silently overwrite each other's changes.

#### `mailboxes`

```text
id UUID
store_profile_id foreign key
address normalized and unique among active mailboxes
encrypted_password
secret_key_version
imap_host
imap_port
imap_tls_mode
smtp_host
smtp_port
smtp_tls_mode
status
last_imap_success_at
last_smtp_auth_success_at
last_error_code
created_at
updated_at
```

V1 enforces one active mailbox per store at the service layer while keeping the
foreign-key shape compatible with multiple mailboxes later.

#### `mailbox_checkpoints`

```text
mailbox_id
folder
uid_validity
activation_baseline_uid
last_durably_enqueued_uid
last_reconciled_at
idle_connected_at
idle_heartbeat_at
state
```

Unique key: `(mailbox_id, folder)`. A UIDVALIDITY change never overwrites the old
value automatically.

#### `proxy_profiles`

```text
id UUID
name unique
protocol http | socks5
host
port
encrypted_username nullable
encrypted_password nullable
secret_key_version
connect_timeout_seconds
enabled
last_test_status
last_tested_at
last_exit_ip
last_detected_country
last_error_code
row_version
```

Store profiles reference a proxy profile ID; copying credentials into each store is
not allowed. Editing a shared proxy therefore updates all assigned stores after the
new configuration passes its connection tests and is activated.

#### `shopify_connections`

```text
store_profile_id unique
shop_domain unique canonical *.myshopify.com
client_id
encrypted_client_secret
encrypted_access_token nullable
access_token_expires_at nullable
granted_scopes
auth_status
last_auth_error_code
last_connected_at
proxy_profile_id
secret_key_version
row_version
```

Access tokens are cacheable credentials, not configuration entered by the user.
Changing shop domain, Client ID, Client Secret or proxy invalidates the cached token.

#### `ai_provider_configs`

```text
id UUID
name
provider_type vertex_gemini (V1)
gcp_project_id
gcp_location
credential_reference
classification_model
drafting_model
default_temperature
default_max_output_tokens
enabled
last_test_status
last_tested_at
row_version
```

For V1, `credential_reference` points to a mounted Docker secret/service-account
credential file; the JSON key is not returned by the API or embedded in an image.
The provider boundary must allow a later move to Workload Identity Federation or a
different AI provider.

#### `email_work_items`

```text
id UUID
store_profile_id
mailbox_id
folder
uid_validity
imap_uid
message_id nullable
from_address
reply_to_address nullable
to_addresses structured JSON
cc_addresses structured JSON
subject
received_at
processing_status
current_classification_id nullable
current_draft_version_id nullable
manual_review_reason nullable
first_detected_at
last_transition_at
row_version
```

Required unique constraint:

```text
(mailbox_id, folder, uid_validity, imap_uid)
```

Indexes cover `(store_profile_id, processing_status, received_at)` and queue views.

#### `email_classifications`

```text
id UUID
email_work_item_id
spam_status
customer_status
intent
order-state flags
confidence per dimension
reason codes
source ai | user | rule
provider/model nullable
prompt_version nullable
created_by nullable for AI/rule output
created_at
superseded_at
```

User-created classifications supersede AI results; reprocessing cannot silently
replace a current user override.

#### `reply_draft_versions`

```text
id UUID
email_work_item_id
version_number
subject
body_text
body_html_sanitized
language
source ai | user
provider/model nullable
prompt_version nullable
context_version_id
classification_id
order_snapshot_id nullable
product_snapshot_id nullable
policy_snapshot_hashes
warning_codes
created_by nullable
created_at
content_hash
```

Unique key: `(email_work_item_id, version_number)`. Draft versions are immutable;
editing creates a new version.

#### `reply_delivery_attempts`

```text
id UUID
email_work_item_id
approved_draft_version_id
idempotency_key unique
approved_by
approved_at
status queued | sending | sent | delivery_unknown | failed
outgoing_message_id unique
smtp_started_at
smtp_completed_at
smtp_response_summary redacted
sent_folder_append_status
sent_at
error_code nullable
```

Only one non-failed send operation may exist for an email work item. Database
constraints and transactional state transitions enforce this in addition to UI
button disabling.

#### `background_jobs`

```text
id UUID
job_type
store_profile_id nullable
email_work_item_id nullable
payload_json without secrets/content
status ready | leased | succeeded | retry_waiting | failed_terminal
priority
attempt_count
available_at
leased_by
lease_expires_at
last_error_code
created_at
finished_at
deduplication_key unique where applicable
```

Workers claim jobs with short transactions using `FOR UPDATE SKIP LOCKED`. A lease
expiry returns abandoned work to the queue; idempotent business constraints prevent
duplicate side effects.

#### `audit_events`

```text
id UUID
actor_user_id nullable for system actions
event_type
target_type
target_id
store_profile_id nullable
request_correlation_id
source_ip nullable
safe_change_summary JSON without secrets/customer content
created_at
```

Audit records never contain passwords, tokens, proxy credential URLs, raw email
bodies, attachment content or full Shopify payloads.

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

Proxy changes use a draft/test/activate workflow:

1. Saving edited values creates or updates a non-active candidate revision.
2. `Test Proxy` establishes an authenticated tunnel, verifies remote DNS behavior,
   records the observed exit IP/country from a safe diagnostic service and reports
   latency without revealing credentials.
3. `Test Shopify Connection` then obtains a token and queries shop identity through
   that exact candidate revision.
4. Only a fully passing candidate can replace an active proxy revision.
5. A failed candidate test leaves the currently active revision in service.
6. Editing a shared proxy displays every store that will be affected and requires
   an explicit activation action.

Existing usernames/passwords are never returned to the browser. On edit, blank
secret fields mean retain the current value; an explicit `Clear credentials` action
is required to remove them.

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

Enforcement is an application architecture boundary, not a convention:

- Feature modules may import `ShopifyService` interfaces only; direct Shopify SDK or
  generic HTTP-client construction in feature code is prohibited.
- The transport receives a resolved immutable proxy revision for each request and
  refuses an absent, disabled or untested revision.
- HTTP redirects are either disabled or revalidated so they cannot switch to a
  non-Shopify/unapproved destination outside the proxy-aware transport.
- DNS mode, TLS verification, connection/read timeouts and proxy authentication are
  configured centrally.
- Tests instrument the proxy and target server to prove token, GraphQL and REST
  traffic arrived through it; a blocked proxy must make the request fail.
- Logs record store ID, proxy revision ID, target class, duration, result and
  correlation ID, but never the proxy URL, credentials, access token or customer
  payload.

This design proves there is no application fallback. If defense against accidental
future code bypass is required at the host level, add an egress gateway/firewall in
a later hardening phase after validating that it will not disrupt the existing
mailserver and other VPS workloads.

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

### 11.4 Message parsing and reply identity

The worker fetches the RFC 5322 message from IMAP into memory/temporary storage and
parses:

```text
Message-ID
In-Reply-To
References
From
Reply-To
To/Cc
Subject
Date
Auto-Submitted
Precedence
List-Id/List-Unsubscribe
Content-Type/MIME structure
authentication/spam headers when present
```

Business identity lookup uses the normalized `From` address. Reply delivery uses a
valid `Reply-To` address when one is supplied, otherwise `From`; the selected target
is always displayed to the approving user. Display names or headers never become
trusted HTML.

For AI processing, prefer a valid `text/plain` part. If only HTML exists, sanitize
then convert it into structured text. Separate the newest authored portion from
quoted history and signatures where reliable, but preserve a bounded thread context
when it is required to understand the conversation. Never remove the original from
the mailserver.

### 11.5 Automated-message and mail-loop safety

Before expensive Shopify/AI processing, deterministic rules detect likely automatic
mail such as delivery-status notifications, out-of-office responses, mailing-list
traffic and messages marked `Auto-Submitted`. These messages must not automatically
enter a draftable category.

Default behavior:

```text
delivery failure/bounce -> Needs manual review with bounce reason
auto-reply/out-of-office -> No reply needed candidate
mailing list/bulk mail -> Spam or manual review candidate
message sent by the same configured support address -> suppress loop
```

Rules label evidence but do not delete or move the source message. A user can
override the result. Outgoing messages include appropriate auto-response headers as
configured to reduce reply loops, while still preserving normal conversational
threading.

### 11.6 UI notification of new work

Near-real-time ingestion is surfaced to logged-in browsers through Server-Sent
Events (SSE). SSE is one-way, fits queue/status updates and avoids a WebSocket
dependency. The UI always performs a normal REST refetch after an event; event data
contains identifiers/status only, not full email content. If SSE disconnects, the
browser reconnects and periodic refetch remains the safety net.

### 11.7 Mailserver-source availability contract

Because PostgreSQL intentionally does not store the raw body or attachment binary,
every processing retry and authenticated detail/attachment view resolves the saved
IMAP reference and fetches the source from the mailserver. Consequences are explicit:

- if the message disappears before classification/drafting completes, stop the
  pipeline with `SOURCE_MESSAGE_UNAVAILABLE` and move it to manual review;
- if the message disappears after a draft exists but before approval, show the saved
  metadata/draft but disable `Approve & Send` until a user explicitly verifies the
  case and supplies an appropriate manual resolution;
- if a source attachment disappears, it cannot be reconstructed from PostgreSQL;
- mailbox checkpoint/workflow records are not proof that the source still exists;
- the application never restores or re-creates a source message on the mailserver;
- UI copy must state that original-message retention is controlled by the
  mailserver, independently of the application's 120-day workflow retention.

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

### 15.1 Vertex AI configuration and credentials

V1 uses one or more centrally managed Vertex provider configurations that store:

```text
GCP project ID
Vertex location
classification model identifier
drafting model identifier
temperature/output limits
credential reference
enabled/test status
```

Because the application runs on a non-Google VPS, the initial implementation uses a
least-privilege Google service-account credential mounted read-only into the API and
worker as a Docker secret/root-owned file. The JSON key itself is not stored in a
normal database column, uploaded through a general settings response, built into an
image or logged. `credential_reference` identifies where the runtime can load it.
The provider interface must permit migration to workload identity federation or
another provider without changing email workflow tables.

`Test AI Provider` uses synthetic, non-customer text and a tiny supported fixture;
it verifies authentication, location/model access and structured output. It returns
safe diagnostics only. Store profiles select an enabled tested provider and may
override model parameters within administrator-defined bounds.

### 15.2 AI request and failure contract

- AI requests go directly to the configured provider over verified TLS and do not
  use the Shopify proxy.
- Prompts contain only the current message/thread portion and business context
  needed for the task; unrelated mailbox content is never sent.
- Each call has an explicit timeout, bounded retry count and correlation ID.
- Retry transient provider/network/rate-limit errors with exponential backoff and
  jitter; do not retry invalid credentials, denied model access or invalid config.
- Never retry an invalid structured response indefinitely. One repair attempt is
  allowed, followed by `Needs manual review` with `AI_OUTPUT_INVALID`.
- Persist model ID, prompt/schema version, request timing and usage metadata when
  available, but not the complete prompt or raw provider response.
- If classification cannot complete, no draft is generated. If drafting fails after
  a valid classification, retain the classification and expose a safe retry action.
- Provider/model changes affect only new calls; existing draft versions retain the
  provider/model and context references used to create them.

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

### 18.1 Transactional job model

Canonical job types:

```text
RECONCILE_MAILBOX
PROCESS_EMAIL
LOOKUP_SHOPIFY_ORDER
LOOKUP_SHOPIFY_PRODUCT
SYNC_SHOPIFY_POLICIES
CLASSIFY_EMAIL
GENERATE_DRAFT
SEND_APPROVED_REPLY
RETENTION_CLEANUP
```

Jobs carry identifiers and version numbers only, never passwords, tokens, raw email
bodies or attachment bytes. A worker leases a job transactionally, loads current
state from PostgreSQL/IMAP, performs the external operation, then commits the result
and next transition. Leases expire so another worker can recover work after a crash.

Concurrency and idempotency rules:

- run at most one active IDLE listener per mailbox, protected by an advisory lock or
  renewable mailbox lease;
- deduplicate ingestion with `(mailbox_id, folder, uid_validity, imap_uid)`;
- deduplicate a job by operation type plus the relevant entity/context version;
- re-check current state after leasing so superseded jobs exit successfully without
  performing side effects;
- acquire per-email locking for state transitions and optimistic `row_version`
  checks for user edits;
- serialize token refresh per store and policy sync per store/policy type;
- treat SMTP delivery as the only non-repeatable boundary and use the stricter send
  protocol in Section 17;
- put terminal/configuration errors directly into manual review or configuration
  health instead of repeatedly consuming the queue.

### 18.2 Retry ownership

The service that understands an error owns its retry policy: Shopify lookup follows
Section 14, AI follows Section 15, IMAP reconnect is handled by the listener, and
SMTP ambiguity follows Section 17. The generic queue schedules `available_at` but
must not convert permanent failures into retries. Manual `Retry` creates a new audit
event and new job after confirming the underlying configuration/version is still
applicable.

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

### API surface

All application endpoints are under `/api/v1`. Representative contract:

```text
POST   /auth/login
POST   /auth/logout
GET    /auth/me
POST   /auth/change-password
GET    /auth/sessions
DELETE /auth/sessions/{sessionId}

GET    /users
POST   /users
PATCH  /users/{userId}
POST   /users/{userId}/reset-password
POST   /users/{userId}/disable
POST   /users/{userId}/enable
POST   /users/{userId}/revoke-sessions

GET    /stores
POST   /stores
GET    /stores/{storeId}
PATCH  /stores/{storeId}
POST   /stores/{storeId}/test-mail
POST   /stores/{storeId}/test-shopify
POST   /stores/{storeId}/sync-policies
POST   /stores/{storeId}/activate
POST   /stores/{storeId}/pause
POST   /stores/{storeId}/reset-baseline

GET    /proxies
POST   /proxies
PATCH  /proxies/{proxyId}
POST   /proxies/{proxyId}/test
POST   /proxies/{proxyId}/activate

GET    /ai-providers
POST   /ai-providers
PATCH  /ai-providers/{providerId}
POST   /ai-providers/{providerId}/test

GET    /emails
GET    /emails/{emailId}
GET    /emails/{emailId}/source
GET    /emails/{emailId}/attachments/{attachmentId}
POST   /emails/{emailId}/reclassify
POST   /emails/{emailId}/override-classification
POST   /emails/{emailId}/retry
POST   /emails/{emailId}/mark-no-reply

GET    /emails/{emailId}/drafts
POST   /emails/{emailId}/drafts
POST   /emails/{emailId}/drafts/regenerate
PATCH  /drafts/{draftVersionId}
POST   /drafts/{draftVersionId}/approve-and-send
POST   /drafts/{draftVersionId}/reject

GET    /audit
GET    /system/health-summary
GET    /events
```

Every state-changing endpoint:

- requires an authenticated session and a named permission;
- requires CSRF validation;
- validates optimistic concurrency (`row_version`/ETag) where edits can collide;
- creates an audit event;
- returns stable machine-readable error codes and a safe user-facing message.

List endpoints use cursor pagination and server-side filtering. They do not return
raw secrets, token values or email bodies in list payloads.

### Error code contract

Important stable codes include:

```text
SELF_DISABLE_NOT_ALLOWED
LAST_ACTIVE_USER_REQUIRED
CONCURRENT_MODIFICATION
PROFILE_NOT_READY
MAIL_AUTHENTICATION_FAILED
IMAP_UIDVALIDITY_CHANGED
SOURCE_MESSAGE_UNAVAILABLE
PROXY_CONNECTION_FAILED
PROXY_DNS_RESOLUTION_FAILED
PROXY_REVISION_NOT_TESTED
SHOPIFY_CLIENT_CREDENTIALS_INVALID
SHOPIFY_CLIENT_CREDENTIALS_NOT_PERMITTED
SHOPIFY_SCOPE_MISSING
SHOPIFY_RATE_LIMITED
SHOPIFY_LOOKUP_UNAVAILABLE
SHOP_DOMAIN_MISMATCH
AI_PROVIDER_UNAVAILABLE
AI_OUTPUT_INVALID
ATTACHMENT_TOO_LARGE
UNSUPPORTED_ATTACHMENT_TYPE
ATTACHMENT_DOWNLOAD_FAILED
ATTACHMENT_PARSE_FAILED
VERTEX_ATTACHMENT_PROCESSING_FAILED
ENCRYPTED_OR_PASSWORD_PROTECTED_FILE
DRAFT_VERSION_STALE
STORE_POLICY_CHANGED
SEND_ALREADY_EXISTS
SMTP_DELIVERY_UNKNOWN
```

Error details may include correlation IDs and retry guidance, never credentials or
raw provider payloads that could contain customer data.

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

### Backup, restore and migration discipline

- Take an encrypted PostgreSQL backup before every schema migration and maintain a
  scheduled encrypted backup for normal recovery.
- Store application master keys, runtime secrets and infrastructure configuration
  separately from database backups; a database dump alone must not expose secrets
  and is not sufficient for a complete restore.
- Backup access is limited to VPS operators and backup jobs; application users do
  not receive backup download access through the UI.
- Backup filenames/logs contain no customer address, subject or secret.
- Backup retention must not exceed the same 120-day maximum. Expired backups are
  deleted and restore tests account for the possibility that a restored database
  needs the retention cleanup job before normal service resumes.
- Practice restore into an isolated environment with outbound mail disabled. Verify
  migrations, user authentication, encryption-key access and checkpoint integrity.
- Database migrations are forward-only in production. Rollback normally restores
  the previous application image while preserving a backward-compatible migration;
  a destructive migration requires its own tested restore procedure.
- After restoration, start API access first, validate state, then enable workers.
  This avoids duplicate processing while checkpoints/jobs are being inspected.

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

## 27. Requirement Traceability Matrix

This table is the implementation-review checklist. A ticket that changes one row
must update its linked design, tests and operational documentation together.

| ID | Confirmed requirement/decision | Design location | Required verification |
|---|---|---|---|
| R-01 | Internal app for team-owned stores only | Sections 1, 2.10 | No merchant install/onboarding path exists. |
| R-02 | Start with zero profiles; add profiles through UI | Sections 1, 9, 19 | Fresh database displays empty state and completes setup wizard. |
| R-03 | One initial mailbox per profile; four named stores, excluding Piezaprint | Sections 2.1, 7, 28 | All four can be configured; excluded mailbox is never auto-created. |
| R-04 | Process only mail received after activation | Sections 2.2, 9, 11 | Pre-baseline UID fixtures never create work items. |
| R-05 | Near-real-time mail via IMAP IDLE plus five-minute reconciliation | Sections 11, 18 | Disconnect/restart test loses and duplicates no email. |
| R-06 | Do not copy full mail/attachments into database | Sections 2.2, 7, 11.7, 12 | Schema/content scan finds metadata only; live IMAP fetch works. |
| R-07 | Multi-dimensional classification | Section 13 | One email can have independent spam, order and intent values. |
| R-08 | Spam is labeled and hidden, not moved/deleted | Sections 13, 19 | IMAP flags/folder remain unchanged and Spam view supports override. |
| R-09 | Shopify identifies whether sender ordered in last 60 days | Sections 2.5, 14.1 | Real recent order matches; old/test order does not. |
| R-10 | Cancelled/refunded real order still means prior customer | Sections 2.5, 13, 14.1 | `has_order_record` plus cancellation/refund flags are both retained. |
| R-11 | Shopify unavailable is not the same as no order | Sections 13, 14.1, 16 | Retry schedule runs, then manual review; never emits false `no_order`. |
| R-12 | No Shopify webhooks in V1 | Sections 2.5, 2.10 | No public webhook route or webhook job exists. |
| R-13 | Every Shopify request, including token acquisition, uses proxy | Sections 9, 10 | Instrumented proxy tests prove fail-closed behavior for all clients. |
| R-14 | Proxy settings editable by every logged-in user and future-RBAC-ready | Sections 2.4, 7, 9, 20 | Named permission is assigned to V1 admin; changes are versioned/audited. |
| R-15 | User enters Client ID/Secret; backend gets/refreshes token | Sections 7, 9, 10 | Expiry and `401` tests renew once through proxy without exposing token. |
| R-16 | Current Shopify product data used for inquiries | Section 14.2 | Product facts are traceable; unresolved products are never invented. |
| R-17 | Six Shopify policy types fetched through proxy as current HTML | Section 14.3 | Sanitized HTML/text and hash are synced; stale draft warning works. |
| R-18 | Warranty/cancellation are optional empty custom policies | Sections 7, 9, 14.3 | Fresh profile has both empty and can version edits. |
| R-19 | Vertex AI Gemini behind provider abstraction; AI bypasses Shopify proxy | Sections 2.6, 15 | Provider contract tests and network routing tests pass. |
| R-20 | Gemini reads every supported attachment | Sections 12, 15 | Supported fixtures reach provider; metadata/hash are the only persisted parts. |
| R-21 | Attachment failure stops draft and needs manual review | Sections 12, 16 | Oversized/encrypted/unsupported/unreadable fixtures create no draft. |
| R-22 | Product/order categories generate drafts; risky intents show warnings | Section 16 | Decision-table unit tests cover every row. |
| R-23 | Partnership/other/uncertain do not draft | Section 16 | Each outcome enters manual review with no draft version. |
| R-24 | Draft language follows email; default on uncertainty; regenerate override | Sections 2.6, 15 | Language fixtures and manual regeneration selection pass. |
| R-25 | Every send is individually reviewed and approved | Sections 2.3, 17 | No background path can send an unapproved draft. |
| R-26 | Avoid duplicate reply with send idempotency | Sections 7, 17, 18 | Double-click/crash/concurrency scenarios create at most one delivery operation. |
| R-27 | Individual username/password accounts; every user full access in V1 | Sections 2.4, 20 | Separate sessions work and permission matrix grants V1 admin actions. |
| R-28 | Users manage other users; block self-disable and last-user disable | Sections 2.4, 7, 20 | Transactional concurrent lockout tests pass. |
| R-29 | No brute-force protection in V1 | Sections 2.4, 20 | No implicit lockout/rate-limit changes login behavior; failures remain auditable. |
| R-30 | UI uses ADS semantic tokens, custom components and Tailwind v4 | Sections 2.7, 5, 19 | UI lint/review rejects raw feature colors and Atlaskit components. |
| R-31 | Light only now, theme-ready later | Sections 2.7, 19 | All component colors resolve through the root semantic theme boundary. |
| R-32 | App available at `mail-agent.wrydeco.com` | Sections 3, 4, 24 | Valid HTTPS and localhost-only upstream smoke tests pass. |
| R-33 | SnappyMail remains primary; Roundcube remains installed/running | Sections 3, 24, 26 | Pre/post-deploy webmail regression checks pass. |
| R-34 | Workflow and listed audit data retained at most 120 days | Sections 2.8, 22, 24 | Cleanup and backup-expiry tests prove no retained copy exceeds the cap. |
| R-35 | Docker Compose plus server-side sessions/PostgreSQL/no Redis | Sections 2.9, 4, 5 | Deployment topology and restart/session-revocation tests match the plan. |

---

## 28. Definition of Done for V1

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
