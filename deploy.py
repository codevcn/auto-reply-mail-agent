#!/usr/bin/env python3
"""
Mail Agent — One-Click Automated Deployment Workflow
Deploy from Local Machine to Production VPS (103.147.123.63)

Usage:
    python deploy.py
    .\\deploy.ps1
"""

import os
import sys
import base64
import tarfile
import tempfile
import time
import subprocess
from pathlib import Path

# Ensure UTF-8 output on Windows consoles
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

import getpass
import secrets

try:
    import paramiko
except ImportError:
    print("[INFO] Installing paramiko for SSH/SFTP transport...")
    subprocess.check_call([sys.executable, "-m", "pip", "install", "paramiko"])
    import paramiko

# Configuration (Sensitive credentials MUST come from environment variables or prompt)
VPS_HOST = os.environ.get("VPS_HOST", "103.147.123.63")
VPS_PORT = int(os.environ.get("VPS_PORT", "22"))
VPS_USER = os.environ.get("VPS_USER", "vmadmin")
VPS_PASS = os.environ.get("VPS_PASS", "")
REMOTE_APP_DIR = "/opt/mail-agent"
DOMAIN = "mail-agent.wrydeco.com"
ADMIN_USER = os.environ.get("DEFAULT_ADMIN_USER", "admin")
ADMIN_PASS = os.environ.get("DEFAULT_ADMIN_PASSWORD", "")

ROOT_DIR = Path(__file__).resolve().parent

def log(msg, level="INFO"):
    icons = {"INFO": "[INFO]", "SUCCESS": "[OK]", "WARN": "[WARN]", "ERROR": "[FAIL]", "STEP": "[STEP]"}
    icon = icons.get(level, "[*]")
    print(f"\n{icon} [{level}] {msg}")

def run_local(cmd, cwd=None):
    log(f"Local exec: {cmd}", "STEP")
    res = subprocess.run(cmd, shell=True, cwd=cwd or ROOT_DIR, capture_output=True, text=True)
    if res.returncode != 0:
        log(f"Command failed: {res.stderr.strip()}", "ERROR")
        raise RuntimeError(f"Local command failed: {cmd}")
    return res.stdout.strip()

def run_remote(ssh, cmd, use_sudo=True, timeout=180, check=True):
    log(f"Remote exec: {cmd[:120]}..." if len(cmd) > 120 else f"Remote exec: {cmd}", "STEP")
    if use_sudo:
        b64_cmd = base64.b64encode(cmd.encode("utf-8")).decode("ascii")
        full_cmd = f"echo '{VPS_PASS}' | sudo -S bash -c 'echo {b64_cmd} | base64 -d | bash'"
    else:
        full_cmd = cmd

    stdin, stdout, stderr = ssh.exec_command(full_cmd, timeout=timeout)
    exit_status = stdout.channel.recv_exit_status()
    out = stdout.read().decode("utf-8", errors="replace").strip()
    err = stderr.read().decode("utf-8", errors="replace").strip()

    # Filter sudo password prompt from stderr
    err_filtered = "\n".join([line for line in err.splitlines() if "[sudo] password for" not in line]).strip()

    if out:
        print(out)
    if err_filtered and "warning" not in err_filtered.lower() and "info" not in err_filtered.lower():
        print(f"[Remote notice]: {err_filtered}")

    if check and exit_status != 0:
        raise RuntimeError(f"Remote command failed with exit code {exit_status}: {err_filtered or out}")

    return out

def step_1_build_frontend():
    log("Step 1: Building latest frontend distribution (npm run build)...", "STEP")
    run_local("npm run build", cwd=ROOT_DIR / "frontend")
    log("frontend/dist built and ready for deployment.", "SUCCESS")

def step_2_package_project():
    log("Step 2: Packaging clean project archive (.tar.gz)...", "STEP")
    tar_path = Path(tempfile.gettempdir()) / "mail-agent-deploy.tar.gz"

    exclude_dirs = {
        "node_modules", ".git", ".venv", "__pycache__", ".pytest_cache",
        ".mypy_cache", ".ruff_cache", ".agents", "archive", "admin"
    }
    exclude_files = {"mail_agent_dev.db", "mail_agent.db", "webmail_login_test.png"}

    with tarfile.open(tar_path, "w:gz") as tar:
        for item in ["backend", "compose.yaml", "pyproject.toml", ".env", "deploy"]:
            p = ROOT_DIR / item
            if not p.exists():
                continue

            def tar_filter(tarinfo):
                name = Path(tarinfo.name).name
                for part in Path(tarinfo.name).parts:
                    if part in exclude_dirs or part in exclude_files:
                        return None
                if name.endswith(".pyc") or name.endswith(".log"):
                    return None
                return tarinfo

            tar.add(p, arcname=item, filter=tar_filter)

        # Include frontend/dist
        dist_dir = ROOT_DIR / "frontend" / "dist"
        if dist_dir.exists():
            tar.add(dist_dir, arcname="frontend/dist")

    log(f"Created deployment archive: {tar_path} ({tar_path.stat().st_size / 1024 / 1024:.2f} MB)", "SUCCESS")
    return tar_path

def step_3_upload_to_vps(tar_path):
    log(f"Step 3: Connecting via SSH & uploading to VPS {VPS_HOST}...", "STEP")
    ssh = paramiko.SSHClient()
    ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())

    max_retries = 3
    for attempt in range(1, max_retries + 1):
        try:
            log(f"SSH connection attempt {attempt}/{max_retries} to {VPS_HOST}:{VPS_PORT}...", "INFO")
            ssh.connect(VPS_HOST, port=VPS_PORT, username=VPS_USER, password=VPS_PASS, timeout=30)
            break
        except Exception as e:
            if attempt == max_retries:
                raise RuntimeError(f"Failed to connect to VPS after {max_retries} attempts: {e}")
            log(f"Connection failed ({e}), retrying in 3s...", "WARN")
            time.sleep(3)

    sftp = ssh.open_sftp()
    remote_tmp = "/tmp/mail-agent-deploy.tar.gz"
    log(f"Uploading {tar_path} -> {remote_tmp}...", "INFO")
    sftp.put(str(tar_path), remote_tmp)
    sftp.close()
    log("Upload complete!", "SUCCESS")
    return ssh

def step_4_setup_remote_app(ssh):
    log("Step 4: Extracting archive into /opt/mail-agent and setting permissions...", "STEP")
    run_remote(ssh, f"mkdir -p {REMOTE_APP_DIR} && chown -R {VPS_USER}:{VPS_USER} {REMOTE_APP_DIR}")
    run_remote(ssh, f"tar -xzf /tmp/mail-agent-deploy.tar.gz -C {REMOTE_APP_DIR}")
    run_remote(ssh, "rm -f /tmp/mail-agent-deploy.tar.gz")
    log("Remote project extracted successfully.", "SUCCESS")

def step_5_setup_nginx_and_ssl(ssh):
    log("Step 5: Configuring Nginx reverse proxy and Let's Encrypt SSL...", "STEP")
    acme_dir = "/var/www/mail-agent-acme"
    run_remote(ssh, f"mkdir -p {acme_dir} && chown -R www-data:www-data {acme_dir}")

    # Check if certificate already exists
    cert_check = run_remote(ssh, f"test -f /etc/letsencrypt/live/{DOMAIN}/fullchain.pem && echo 'EXISTS' || echo 'MISSING'", check=False)

    if "EXISTS" not in cert_check:
        log(f"SSL certificate for {DOMAIN} not found. Acquiring via Certbot webroot...", "INFO")
        http_vhost = f"""server {{
    listen 80;
    listen [::]:80;
    server_name {DOMAIN};

    location /.well-known/acme-challenge/ {{
        root {acme_dir};
        try_files $uri =404;
    }}

    location / {{
        return 200 'Mail Agent ACME verification in progress\\n';
    }}
}}
"""
        run_remote(ssh, f"cat << 'EOF' > /etc/nginx/sites-available/{DOMAIN}.conf\n{http_vhost}\nEOF")
        run_remote(ssh, f"ln -sf /etc/nginx/sites-available/{DOMAIN}.conf /etc/nginx/sites-enabled/{DOMAIN}.conf")
        run_remote(ssh, "nginx -t && systemctl reload nginx")

        # Issue Certbot certificate
        certbot_cmd = f"certbot certonly --webroot -w {acme_dir} -d {DOMAIN} --non-interactive --agree-tos -m support@wrydeco.com --keep-until-expiring"
        run_remote(ssh, certbot_cmd)
        log("Certbot SSL certificate obtained successfully!", "SUCCESS")

    # Install full production HTTPS Nginx config from deploy/nginx/mail-agent.conf
    log(f"Installing full production HTTPS Nginx config for {DOMAIN}...", "INFO")
    run_remote(ssh, f"cp {REMOTE_APP_DIR}/deploy/nginx/mail-agent.conf /etc/nginx/sites-available/{DOMAIN}.conf")
    run_remote(ssh, f"ln -sf /etc/nginx/sites-available/{DOMAIN}.conf /etc/nginx/sites-enabled/{DOMAIN}.conf")
    run_remote(ssh, "nginx -t && systemctl reload nginx")
    log("Nginx configuration verified and reloaded!", "SUCCESS")

def step_6_docker_compose_up(ssh):
    log("Step 6: Building and starting Docker Compose containers on VPS...", "STEP")
    # Build containers
    run_remote(ssh, f"cd {REMOTE_APP_DIR} && docker compose build mail-agent-api mail-agent-worker", timeout=600)
    run_remote(ssh, f"cd {REMOTE_APP_DIR} && docker compose up -d")

    log("Waiting for database container to become healthy...", "INFO")
    for attempt in range(1, 25):
        time.sleep(2)
        ps_out = run_remote(ssh, f"cd {REMOTE_APP_DIR} && docker compose ps", check=False)
        if "mail-agent-db" in ps_out and ("healthy" in ps_out or "Up" in ps_out):
            # Verify postgres is accepting connections
            pg_ready = run_remote(ssh, f"cd {REMOTE_APP_DIR} && docker compose exec -T mail-agent-db pg_isready -U mail_agent_app -d mail_agent_db", check=False)
            if "accepting connections" in pg_ready:
                log("Database is healthy and accepting connections!", "SUCCESS")
                break
        log(f"Waiting for database... ({attempt}/25)", "INFO")

    # Ensure alembic_version table exists and column is VARCHAR(128)
    pre_sql = "CREATE TABLE IF NOT EXISTS alembic_version (version_num VARCHAR(128) NOT NULL, CONSTRAINT alembic_version_pkc PRIMARY KEY (version_num)); ALTER TABLE alembic_version ALTER COLUMN version_num TYPE VARCHAR(128);"
    run_remote(ssh, f'cd {REMOTE_APP_DIR} && docker compose exec -T mail-agent-db psql -U mail_agent_app -d mail_agent_db -c "{pre_sql}"', check=False)

    # Run Alembic migrations inside api container
    log("Running database migrations (alembic upgrade head)...", "INFO")
    run_remote(ssh, f"cd {REMOTE_APP_DIR} && docker compose exec -T mail-agent-api alembic upgrade head")

    # Bootstrap initial admin user (only if ADMIN_PASS is provided)
    if ADMIN_PASS:
        log(f"Bootstrapping default admin user ('{ADMIN_USER}')...", "INFO")
        run_remote(ssh, f"cd {REMOTE_APP_DIR} && docker compose exec -T mail-agent-api python -m app.cli bootstrap-admin -u {ADMIN_USER} -p '{ADMIN_PASS}'")
        log("Admin user bootstrapped successfully.", "SUCCESS")
    else:
        log("No DEFAULT_ADMIN_PASSWORD provided; skipping admin bootstrap.", "INFO")

def step_7_health_verification(ssh):
    log("Step 7: Verifying deployment health probes...", "STEP")
    # Check internal container port
    internal_check = run_remote(ssh, "curl -s http://127.0.0.1:8090/health/live", check=False)
    log(f"Internal health check (127.0.0.1:8090): {internal_check}", "INFO")

    # Check public domain
    public_check = run_remote(ssh, f"curl -s -I https://{DOMAIN}/ | head -n 5", check=False)
    log(f"Public HTTPS response (https://{DOMAIN}):\n{public_check}", "INFO")

def main():
    global VPS_PASS, ADMIN_PASS
    start_time = time.time()
    print("=" * 70)
    print("   MAIL AGENT — AUTOMATED FULL DEPLOYMENT WORKFLOW")
    print(f"   Target VPS: {VPS_USER}@{VPS_HOST} | Domain: https://{DOMAIN}")
    print("=" * 70)

    if not VPS_PASS:
        try:
            VPS_PASS = getpass.getpass(f"Nhập mật khẩu SSH VPS ({VPS_USER}@{VPS_HOST}): ").strip()
        except Exception:
            pass

    if not VPS_PASS:
        log("Vui lòng cấu hình biến môi trường VPS_PASS hoặc nhập mật khẩu khi được hỏi.", "ERROR")
        sys.exit(1)

    try:
        step_1_build_frontend()
        tar_path = step_2_package_project()
        ssh = step_3_upload_to_vps(tar_path)
        step_4_setup_remote_app(ssh)
        step_5_setup_nginx_and_ssl(ssh)
        step_6_docker_compose_up(ssh)
        step_7_health_verification(ssh)
        ssh.close()

        elapsed = time.time() - start_time
        print("\n" + "=" * 70)
        log(f"DEPLOYMENT COMPLETED SUCCESSFULLY IN {elapsed:.1f}s!", "SUCCESS")
        print("=" * 70)
        print(f"\n[URL] Web Application URL : https://{DOMAIN}")
        print(f"[USER] Default Admin User  : {ADMIN_USER}")
        print("\n[NOTE] Sau này khi thay đổi code, bạn chỉ cần chạy:")
        print("         .\\deploy.ps1    (hoặc python deploy.py)")
        print("       Hệ thống sẽ tự động build, sync và restart an toàn trên VPS.\n")

    except Exception as e:
        log(f"Deployment encountered an error: {e}", "ERROR")
        sys.exit(1)

if __name__ == "__main__":
    main()
