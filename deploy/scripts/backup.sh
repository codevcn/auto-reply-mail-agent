#!/usr/bin/env bash
# ==============================================================================
# Mail Agent — Automated Database Backup Script (VPS 103.147.123.63)
# Invariant R-34: 120-Day Retention Enforcement (backups older than 120 days pruned)
# Invariant R-33: PostgreSQL internal to Docker; dumps via docker exec
# Zero-PII Aggregated Logging: Emits status, byte count, SHA256 only.
# ==============================================================================
set -euo pipefail

# Configuration & Paths
BACKUP_DIR="${BACKUP_DIR:-/opt/mail-agent/backups}"
TIMESTAMP="$(date +"%Y%m%d_%H%M%S")"
CONTAINER_NAME="${CONTAINER_NAME:-mail-agent-db}"
DB_USER="${POSTGRES_USER:-mail_agent_app}"
DB_NAME="${POSTGRES_DB:-mail_agent_db}"

# Enforce Invariant R-34: RETENTION_DAYS must be <= 120
CONFIG_RETENTION_DAYS="${RETENTION_DAYS:-120}"
if [ "${CONFIG_RETENTION_DAYS}" -gt 120 ]; then
    echo "[WARN] RETENTION_DAYS=${CONFIG_RETENTION_DAYS} exceeds maximum permitted 120 days (Invariant R-34). Clamping to 120."
    RETENTION_DAYS=120
else
    RETENTION_DAYS="${CONFIG_RETENTION_DAYS}"
fi

ENCRYPTION_KEY="${BACKUP_ENCRYPTION_KEY:-${ENCRYPTION_MASTER_KEY:-}}"

# Ensure backup directory exists with restricted permissions
mkdir -p "${BACKUP_DIR}"
chmod 700 "${BACKUP_DIR}"

echo "[INFO] [$(date -u +"%Y-%m-%dT%H:%M:%SZ")] Starting Mail Agent backup for database '${DB_NAME}'..."

# Pre-flight Check: Ensure container is running and healthy
if ! docker ps --filter "name=^/${CONTAINER_NAME}$" --format '{{.Names}}' | grep -q "${CONTAINER_NAME}"; then
    echo "[ERROR] Database container '${CONTAINER_NAME}' is not running! Aborting backup." >&2
    exit 1
fi

HEALTH_STATUS="$(docker inspect -f '{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}' "${CONTAINER_NAME}" 2>/dev/null || echo "unknown")"
if [ "${HEALTH_STATUS}" != "healthy" ] && [ "${HEALTH_STATUS}" != "running" ]; then
    echo "[ERROR] Database container '${CONTAINER_NAME}' status is '${HEALTH_STATUS}'. Cannot safely dump." >&2
    exit 1
fi

# Execute pg_dump with compression & optional AES-256-CBC encryption
if [ -n "${ENCRYPTION_KEY}" ]; then
    BACKUP_FILE="${BACKUP_DIR}/mail_agent_backup_${TIMESTAMP}.sql.gz.enc"
    echo "[INFO] Streaming pg_dump -> gzip -> AES-256-CBC encrypted archive..."
    docker exec "${CONTAINER_NAME}" pg_dump -U "${DB_USER}" -d "${DB_NAME}" --clean --if-exists --no-owner --no-privileges \
        | gzip -9 \
        | openssl enc -aes-256-cbc -salt -pbkdf2 -pass "pass:${ENCRYPTION_KEY}" -out "${BACKUP_FILE}"
else
    BACKUP_FILE="${BACKUP_DIR}/mail_agent_backup_${TIMESTAMP}.sql.gz"
    echo "[INFO] Streaming pg_dump -> gzip archive (unencrypted master key)..."
    docker exec "${CONTAINER_NAME}" pg_dump -U "${DB_USER}" -d "${DB_NAME}" --clean --if-exists --no-owner --no-privileges \
        | gzip -9 > "${BACKUP_FILE}"
fi

# Verify non-empty backup archive
if [ ! -s "${BACKUP_FILE}" ]; then
    echo "[ERROR] Backup file is empty or missing: ${BACKUP_FILE}" >&2
    rm -f "${BACKUP_FILE}"
    exit 1
fi

# Compute SHA256 Checksum
SHA256="$(sha256sum "${BACKUP_FILE}" | awk '{print $1}')"
echo "${SHA256}  $(basename "${BACKUP_FILE}")" > "${BACKUP_FILE}.sha256"
FILE_SIZE="$(du -h "${BACKUP_FILE}" | awk '{print $1}')"

echo "[SUCCESS] Backup created successfully: $(basename "${BACKUP_FILE}") (${FILE_SIZE}, sha256: ${SHA256})"

# Invariant R-34: Retention Enforcement — Delete backups older than RETENTION_DAYS
echo "[INFO] Enforcing Invariant R-34 retention policy (${RETENTION_DAYS} days)..."
DELETED_COUNT="$(find "${BACKUP_DIR}" -name "mail_agent_backup_*" -type f -mtime +"${RETENTION_DAYS}" | wc -l || echo "0")"
find "${BACKUP_DIR}" -name "mail_agent_backup_*" -type f -mtime +"${RETENTION_DAYS}" -delete
echo "[INFO] Purged ${DELETED_COUNT} backup archives exceeding ${RETENTION_DAYS} days."

echo "[INFO] [$(date -u +"%Y-%m-%dT%H:%M:%SZ")] Backup workflow completed successfully."
