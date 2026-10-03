#!/usr/bin/env bash
# ==============================================================================
# Mail Agent — Safe Database Restore Protocol (VPS 103.147.123.63)
# 7-Step Protocol with Outbound Mail Kill-Switch (Invariants R-01, R-25, R-26)
# Prevents accidental double-sending or stale job replaying upon database restoration.
# ==============================================================================
set -euo pipefail

if [ "$#" -lt 1 ]; then
    echo "Usage: $0 <path-to-backup-file> [--force]"
    echo "Example: $0 /opt/mail-agent/backups/mail_agent_backup_20261003_120000.sql.gz.enc"
    exit 1
fi

BACKUP_FILE="$1"
FORCE_MODE="${2:-}"
CONTAINER_NAME="${CONTAINER_NAME:-mail-agent-db}"
DB_USER="${POSTGRES_USER:-mail_agent_app}"
DB_NAME="${POSTGRES_DB:-mail_agent_db}"
ENCRYPTION_KEY="${BACKUP_ENCRYPTION_KEY:-${ENCRYPTION_MASTER_KEY:-}}"

echo "=============================================================================="
echo "          MAIL AGENT — DISASTER RECOVERY DATABASE RESTORATION                 "
echo "=============================================================================="

# STEP 1: Pre-flight Verification
echo "[STEP 1/7] Pre-flight file validation..."
if [ ! -f "${BACKUP_FILE}" ]; then
    echo "[ERROR] Backup file not found: ${BACKUP_FILE}" >&2
    exit 1
fi

# Verify SHA256 if .sha256 file exists alongside backup
if [ -f "${BACKUP_FILE}.sha256" ]; then
    echo "[INFO] Verifying SHA256 checksum..."
    EXPECTED_SHA="$(awk '{print $1}' "${BACKUP_FILE}.sha256")"
    ACTUAL_SHA="$(sha256sum "${BACKUP_FILE}" | awk '{print $1}')"
    if [ "${EXPECTED_SHA}" != "${ACTUAL_SHA}" ]; then
        echo "[ERROR] SHA256 mismatch! File may be corrupt. Expected: ${EXPECTED_SHA}, Got: ${ACTUAL_SHA}" >&2
        exit 1
    fi
    echo "[INFO] SHA256 checksum matched (${ACTUAL_SHA})."
fi

if [ "${FORCE_MODE}" != "--force" ]; then
    read -r -p "WARNING: Restoring will overwrite the current database '${DB_NAME}'. Continue? (y/N) " CONFIRM
    if [[ ! "${CONFIRM}" =~ ^[Yy]$ ]]; then
        echo "[INFO] Restore aborted by operator."
        exit 0
    fi
fi

# STEP 2: Outbound Mail Kill-Switch (Invariants R-01, R-25, R-26)
echo "[STEP 2/7] Activating Outbound Mail Kill-Switch..."
echo "[INFO] Stopping mail-agent-worker and mail-agent-api to halt all outbound SMTP dispatch..."
docker compose stop mail-agent-worker mail-agent-api
echo "[INFO] Worker and API containers stopped. No outbound emails can be sent."

# STEP 3: Database Restore from Decrypted Stream
echo "[STEP 3/7] Replaying database schema & data into '${DB_NAME}'..."
if [[ "${BACKUP_FILE}" == *.enc ]]; then
    if [ -z "${ENCRYPTION_KEY}" ]; then
        echo "[ERROR] Encrypted backup file requires ENCRYPTION_MASTER_KEY or BACKUP_ENCRYPTION_KEY." >&2
        exit 1
    fi
    echo "[INFO] Decrypting AES-256-CBC stream..."
    openssl enc -d -aes-256-cbc -pbkdf2 -pass "pass:${ENCRYPTION_KEY}" -in "${BACKUP_FILE}" \
        | gzip -d \
        | docker exec -i "${CONTAINER_NAME}" psql -U "${DB_USER}" -d "${DB_NAME}"
elif [[ "${BACKUP_FILE}" == *.gz ]]; then
    gzip -dc "${BACKUP_FILE}" \
        | docker exec -i "${CONTAINER_NAME}" psql -U "${DB_USER}" -d "${DB_NAME}"
else
    docker exec -i "${CONTAINER_NAME}" psql -U "${DB_USER}" -d "${DB_NAME}" < "${BACKUP_FILE}"
fi
echo "[INFO] SQL restore stream completed successfully."

# STEP 4: Neutralize Stale Outbound Jobs (Invariants R-01, R-25, R-26)
echo "[STEP 4/7] Neutralizing pending outbound jobs and stale sending states..."
docker exec -i "${CONTAINER_NAME}" psql -U "${DB_USER}" -d "${DB_NAME}" << 'EOF'
-- Neutralize pending/in-flight email jobs from backup so they are never double-sent
UPDATE email_jobs
SET status = 'CANCELLED',
    last_error = 'Neutralized during disaster recovery restore to prevent replay (Invariants R-01/R-25/R-26)'
WHERE status IN ('PENDING', 'IN_PROGRESS', 'RETRY');

-- Reset any drafts caught mid-send to manual review
UPDATE reply_drafts
SET status = 'NEEDS_MANUAL_REVIEW',
    review_notes = COALESCE(review_notes || ' | ', '') || 'Draft sending state neutralized during database restore.'
WHERE status = 'SENDING';
EOF
echo "[INFO] Neutralization complete: All in-flight jobs neutralized to CANCELLED."

# STEP 5: Start API Service & Verify Liveness Probe
echo "[STEP 5/7] Starting mail-agent-api service..."
docker compose start mail-agent-api
echo "[INFO] Waiting for API /health/live probe..."
for i in {1..30}; do
    if curl -s -f http://127.0.0.1:8090/health/live >/dev/null 2>&1; then
        echo "[INFO] API liveness probe OK (attempt ${i})."
        break
    fi
    if [ "$i" -eq 30 ]; then
        echo "[ERROR] API liveness probe timed out after 30 seconds!" >&2
        exit 1
    fi
    sleep 1
done

# STEP 6: Verify System Readiness Probe
echo "[STEP 6/7] Verifying API /health/ready probe..."
for i in {1..15}; do
    if curl -s -f http://127.0.0.1:8090/health/ready >/dev/null 2>&1; then
        echo "[INFO] Database connectivity & Readiness probe OK."
        break
    fi
    if [ "$i" -eq 15 ]; then
        echo "[ERROR] API readiness probe failed!" >&2
        exit 1
    fi
    sleep 1
done

# STEP 7: Safely Start Worker Service
echo "[STEP 7/7] Safely restarting mail-agent-worker..."
docker compose start mail-agent-worker
echo "[INFO] Worker container started."

echo "=============================================================================="
echo "[SUCCESS] Database restoration completed successfully!"
echo "System is operational at http://127.0.0.1:8090"
echo "=============================================================================="
