# Mail Agent — Production Operations & Deployment Runbook

**Target Server:** VPS `103.147.123.63`  
**Public Domain:** `https://mail-agent.wrydeco.com`  
**Host Binding:** `127.0.0.1:8090` (FastAPI + React Dashboard)  
**Revision:** Phase 7 Final Production  

---

## 1. Architecture & Production Topology

### 1.1 VPS Host & Network Layout
```
 Internet (HTTPS 443 / HTTP 80)
           │
           ▼
 Host Nginx Reverse Proxy (103.147.123.63)
           │  proxy_pass: http://127.0.0.1:8090
           ▼
 Docker Host Port: 127.0.0.1:8090
           │
 ┌─────────┼────────────────────────────────────────┐
 │ mail-agent-net (Isolated Bridge Network)         │
 │                                                  │
 │   ┌───────────────────────┐                      │
 │   │ mail-agent-api        │                      │
 │   │ (FastAPI & SPA Assets)│                      │
 │   └──────────┬────────────┘                      │
 │              │                                   │
 │              │      ┌───────────────────────┐    │
 │              ├─────►│ mail-agent-db         │    │
 │              │      │ (PostgreSQL 16)       │    │
 │              │      │ [Port 5432 INTERNAL]  │    │
 │              │      └──────────▲────────────┘    │
 │              │                 │                 │
 │   ┌──────────┴────────────┐    │                 │
 │   │ mail-agent-worker     ├────┘                 │
 │   │ (Queue & Retention)   │                      │
 │   └───────────────────────┘                      │
 └──────────────────────────────────────────────────┘
```

### 1.2 Invariant R-33: VPS Non-Disruption Guard
The target VPS hosts pre-existing critical communication infrastructure that **must never be modified, interrupted, or restarted**:
- `docker-mailserver` on ports `25`, `465`, `587`, `993`
- `SnappyMail` Webmail on port `8089`
- `Roundcube` Webmail on port `8088`

**Rules:**
1. Only port `127.0.0.1:8090` is bound to the host network interface.
2. PostgreSQL (port `5432`) is internal to `mail-agent-net` and is **never** published on the host.
3. Systemd commands (`systemctl restart docker`) must **never** be run casually; operate strictly via scoped `docker compose` within `/opt/mail-agent`.

---

## 2. Prerequisites & Initial Host Setup

### 2.1 Dependencies
Ensure the following are installed on the VPS:
- Docker Engine 24.0+ and Docker Compose v2.20+
- Nginx 1.18+
- Certbot (`certbot certonly --webroot`)
- OpenSSL and Gzip

### 2.2 Directory Layout on Host
Create the operational directories:
```bash
sudo mkdir -p /opt/mail-agent/deploy/nginx
sudo mkdir -p /opt/mail-agent/deploy/scripts
sudo mkdir -p /opt/mail-agent/backups
sudo mkdir -p /var/www/mail-agent-acme

sudo chmod 700 /opt/mail-agent/backups
sudo chown -R $USER:$USER /opt/mail-agent
```

---

## 3. Secrets & Environment Management

### 3.1 Environment File (`/opt/mail-agent/.env`)
Populate `/opt/mail-agent/.env` with production credentials:
```bash
ENVIRONMENT=production
LOG_LEVEL=INFO

# Application Ports
APP_HOST=0.0.0.0
APP_PORT=8090

# PostgreSQL (Internal Docker)
POSTGRES_USER=mail_agent_app
POSTGRES_PASSWORD=<SECURE_POSTGRES_PASSWORD>
POSTGRES_DB=mail_agent_db

# Security & Cryptography
SESSION_SECRET=<64_CHAR_HEX_SESSION_SECRET>
ENCRYPTION_MASTER_KEY=<64_CHAR_HEX_AES_GCM_MASTER_KEY>
ENCRYPTION_KEY_VERSION=1
BACKUP_ENCRYPTION_KEY=<SECURE_PASSPHRASE_FOR_BACKUPS>

# Invariant R-34: Retention Policy (Maximum 120 Days)
RETENTION_DAYS=120
RETENTION_BATCH_SIZE=500
RETENTION_SCHEDULER_INTERVAL_HOURS=24

# GCP Vertex AI / Gemini
GCP_PROJECT_ID=my-gcp-project
GCP_LOCATION=us-central1
GOOGLE_APPLICATION_CREDENTIALS=/app/credentials/google-sa.json
```

---

## 4. Zero-Downtime Deployment & Service Lifecycle

### 4.1 Deployment Commands
From `/opt/mail-agent`:
```bash
# Pull latest code or build images
docker compose build --pull

# Launch services in background
docker compose up -d

# Verify container statuses
docker compose ps
```

### 4.2 Probing Health
The API exposes standardized health probes:
- Liveness Probe: `curl -f http://127.0.0.1:8090/health/live`
- Readiness Probe: `curl -f http://127.0.0.1:8090/health/ready`

---

## 5. Nginx & SSL Management

### 5.1 Nginx Setup
Copy the configuration to the Nginx sites directory:
```bash
sudo cp /opt/mail-agent/deploy/nginx/mail-agent.conf /etc/nginx/sites-available/mail-agent.conf
sudo ln -sf /etc/nginx/sites-available/mail-agent.conf /etc/nginx/sites-enabled/mail-agent.conf
sudo nginx -t
sudo systemctl reload nginx
```

### 5.2 Let's Encrypt Certificate Issuance
Issue the certificate via ACME webroot:
```bash
sudo certbot certonly --webroot -w /var/www/mail-agent-acme -d mail-agent.wrydeco.com
sudo systemctl reload nginx
```

### 5.3 Automated Renewal Cron
Add to crontab:
```cron
0 3 * * * certbot renew --post-hook "systemctl reload nginx" --quiet
```

---

## 6. Database Maintenance & Automated Backups

### 6.1 Automated Daily Backups
The backup script `/opt/mail-agent/deploy/scripts/backup.sh` enforces:
1. Docker container health validation.
2. Compressed `pg_dump` stream encrypted with AES-256-CBC.
3. SHA-256 integrity checksum generation.
4. **Invariant R-34 Compliance**: Automatic pruning of backup archives older than 120 days.

Add to system crontab (`crontab -e`):
```cron
# Daily backup at 02:00 UTC
0 2 * * * /opt/mail-agent/deploy/scripts/backup.sh >> /var/log/mail-agent-backup.log 2>&1
```

### 6.2 Manual Backup Execution
```bash
/opt/mail-agent/deploy/scripts/backup.sh
```

---

## 7. Disaster Recovery & 7-Step Restore Protocol

### 7.1 Protocol Overview (Invariants R-01, R-25, R-26)
When restoring a database backup, restoring stale pending email jobs could trigger accidental re-sending or double-replies to customers. The restore protocol enforces an **Outbound Mail Kill-Switch** and **In-flight Job Neutralization**.

### 7.2 Restoration Steps
Execute:
```bash
/opt/mail-agent/deploy/scripts/restore.sh /opt/mail-agent/backups/mail_agent_backup_YYYYMMDD_HHMMSS.sql.gz.enc
```

The script executes 7 steps:
1. **Pre-flight Validation**: Verifies file existence and SHA256 checksum integrity.
2. **Outbound Mail Kill-Switch**: Stops `mail-agent-worker` and `mail-agent-api` so no outbound SMTP traffic can be dispatched.
3. **Database Replay**: Streams decrypted SQL into `mail-agent-db`.
4. **Job Neutralization**: Sets all `PENDING`, `IN_PROGRESS`, and `RETRY` email jobs to `CANCELLED` and resets in-flight drafts.
5. **API Service Start & Liveness Check**: Starts `mail-agent-api` and awaits `/health/live`.
6. **Readiness Verification**: Validates `/health/ready` probe.
7. **Safe Worker Start**: Starts `mail-agent-worker` now that all pending replay jobs have been neutralized.

---

## 8. Monitoring, Health Probes & Operations Dashboard

### 8.1 Operations Dashboard
Access the unified Operations Dashboard at:
`https://mail-agent.wrydeco.com/dashboard`

**Included Real-Time Widgets:**
1. **System Health Banner**: Overall health status, DB status, worker heartbeat, active worker jobs, uptime.
2. **4-Store Multi-Tenant Matrix**: Live status for `wrydeco.com`, `chillgen.com`, `preaureum.com`, `jeminise.com` (Shopify connection, mailbox IMAP/SMTP, active policies count).
3. **Queue Depth & SLA Alert**: 7 queue counters (`ready_to_review`, `needs_manual_review`, `product_inquiry`, `recent_order`, `complaint`, `spam`, `sent`), with warning banner if oldest unreviewed draft exceeds 12 hours.
4. **SOCKS5 Proxy Health Card**: Active proxy host, port, exit IP, and round-trip latency.
5. **Recent System Audit Events**: Log of key system events (`RETENTION_CLEANUP_EXECUTED`, `USER_LOGIN`, `STORE_ACTIVATED`, `DRAFT_APPROVED_AND_SENT`).
6. **Retention Cleanup Modal**: Operator interface to execute dry-run or live cleanup up to 120 days.

### 8.2 API Endpoints for External Monitoring
- `GET /health/live` — 200 OK if process is responding.
- `GET /health/ready` — 200 OK if PostgreSQL database is connected.
- `GET /api/system/health-summary` — Full JSON payload for Prometheus or external pingers.

---

## 9. Troubleshooting & Emergency Procedures

### 9.1 Container Logs
```bash
# Stream API logs
docker compose logs -f --tail=100 mail-agent-api

# Stream Worker logs
docker compose logs -f --tail=100 mail-agent-worker

# Stream DB logs
docker compose logs -f --tail=100 mail-agent-db
```

### 9.2 Immediate Outbound Email Pause (Emergency Kill-Switch)
If an abnormal email loop or third-party outage occurs:
```bash
docker compose stop mail-agent-worker
```
The API and dashboard remain accessible for operator review, but no outbound emails will be dispatched.

### 9.3 Manual Retention Cleanup via CLI
To trigger an immediate retention cleanup from the command line:
```bash
# Dry run (simulation only)
docker compose exec mail-agent-api python -m app.cli retention-cleanup --days 120 --dry-run

# Live execution
docker compose exec mail-agent-api python -m app.cli retention-cleanup --days 120 --batch-size 500
```
