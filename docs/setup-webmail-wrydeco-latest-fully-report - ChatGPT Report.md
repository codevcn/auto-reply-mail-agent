# WRYDECO Webmail — Deployment, Audit & Operations Runbook

**Overall Status:** PASS — historical Roundcube deployment; live webmail routing updated below  
**Production URL:** https://webmail.wrydeco.com  
**Primary mailbox verified:** `support@wrydeco.com`  
**Current live webmail:** SnappyMail v2.38.2 (`webmail.wrydeco.com`)  
**Legacy webmail still installed:** Roundcube 1.7.3 Non-Root (not removed)  
**Mail backend:** docker-mailserver  
**VPS:** `103.147.123.63`  
**VPS hostname:** `photobooth`  
**VPS FQDN:** `photobooth.encycom.com`  
**OS:** Ubuntu 22.04.5 LTS  
**Original deployment date:** 2026-09-03  
**Latest live-state re-audit:** 2026-10-02

---

## 1. Purpose of This Document

This document is the consolidated technical record for the WRYDECO webmail deployment.

The original sections describe the Roundcube deployment verified on 2026-09-03.
Section 46 records a later read-only re-audit performed on 2026-10-02. That re-audit
confirmed that the public `webmail.wrydeco.com` route had been switched to
SnappyMail. Roundcube remains installed and running; it has **not** been removed.

It is intended to be self-contained enough for a future administrator to understand:

- the original production mail architecture;
- the read-only audit that was performed before any change;
- why Roundcube was selected;
- how Roundcube was isolated from unrelated production services;
- the Docker, Roundcube, Fail2ban, DNS, Certbot and Nginx configuration used;
- the verification evidence collected after deployment;
- the expected steady-state architecture;
- how TLS renewal is intended to work;
- how to diagnose the deployment;
- how to roll back webmail without restarting the production mailserver.

This document intentionally does **not** contain mailbox passwords, the Roundcube encryption key, TLS private-key contents, DKIM private keys, API tokens, or other secrets.

---

# 2. Scope and Safety Constraints

The VPS hosts multiple unrelated production services.

The deployment therefore followed these safety rules:

1. Existing mail data must not be migrated or rewritten.
2. Existing mail accounts must not be recreated.
3. `/opt/mailserver/compose.yaml` must remain unchanged.
4. `/opt/mailserver/mailserver.env` must remain unchanged.
5. Postfix and Dovecot configuration must remain unchanged.
6. Ports `25`, `587`, and `993` must remain available throughout deployment.
7. The production `mailserver` container must not be restarted or recreated.
8. Existing Nginx virtual hosts must not be edited.
9. Nginx changes must use `nginx -t` before every reload.
10. Nginx reloads must be graceful (`systemctl reload nginx`), never a restart for this deployment.
11. Roundcube must not be publicly exposed through its Docker port.
12. Roundcube must use a dedicated project directory and a dedicated container.
13. The Roundcube container must communicate with the mailserver only as a normal IMAP/SMTP client.
14. TLS peer verification must remain enabled.
15. DNS changes must be limited to the new `webmail` A record.
16. Existing MX, SPF, DKIM, DMARC, `mail`, apex, and unrelated records must remain untouched.

---

# 3. Original Roundcube Architecture (Verified 2026-09-03)

> Historical snapshot: this was the active architecture on 2026-09-03. The current
> public `webmail.wrydeco.com` route uses SnappyMail as documented in Section 46.
> Roundcube remains present on the VPS.

```text
                         INTERNET
                            |
                            | HTTPS :443
                            v
                  webmail.wrydeco.com
                            |
                            v
                       Host Nginx
                            |
                            | HTTP localhost only
                            v
                     127.0.0.1:8088
                            |
                            v
                +------------------------+
                |  wrydeco-webmail       |
                |  Roundcube 1.7.3       |
                |  Apache non-root       |
                |  SQLite                |
                |  172.19.0.10           |
                +-----------+------------+
                            |
                            | mailserver_default
                            |
                +-----------+------------+
                |                        |
                v                        v
           IMAPS :993              SMTP :587
                |                        |
                +-----------+------------+
                            |
                            v
                    +---------------+
                    |  mailserver   |
                    |  172.19.0.2   |
                    | docker-mailserver
                    +---------------+
```

Existing mail clients remain independent:

```text
Outlook Mobile
     |
     +---- IMAPS :993
     +---- SMTP Submission :587
     |
     v
mailserver
```

Roundcube is therefore an additional mail client, not a replacement mail server.

---

# 4. Original Production Mail Architecture

## 4.1 VPS

- IP: `103.147.123.63`
- Hostname: `photobooth`
- FQDN: `photobooth.encycom.com`
- OS: Ubuntu 22.04.5 LTS
- Kernel observed during audit: Linux 5.15.0-177-generic x86_64
- RAM observed during audit:
  - Total: ~7.8 GiB
  - Available: ~3.2 GiB
  - Swap: 0
- Root filesystem observed during audit:
  - ~194 GB total
  - ~137 GB used
  - ~58 GB available
  - ~71% used

## 4.2 Mail project

Project path:

```text
/opt/mailserver
```

Primary files:

```text
/opt/mailserver/compose.yaml
/opt/mailserver/mailserver.env
```

Storage:

```text
/opt/mailserver/docker-data/dms/mail-data
/opt/mailserver/docker-data/dms/mail-state
/opt/mailserver/docker-data/dms/mail-logs
/opt/mailserver/docker-data/dms/config
```

Mail container:

```text
mailserver
```

Configured image:

```text
ghcr.io/docker-mailserver/docker-mailserver:latest
```

Observed image ID during audit:

```text
sha256:af51b15dd3fc72153c0e90eb7692bb5e3a463212d87959a80fa7aa89b617d44a
```

Observed image digest:

```text
ghcr.io/docker-mailserver/docker-mailserver@sha256:af51b15dd3fc72153c0e90eb7692bb5e3a463212d87959a80fa7aa89b617d44a
```

Restart policy:

```text
always
```

Mailserver Docker network:

```text
mailserver_default
Subnet: 172.19.0.0/16
Gateway: 172.19.0.1
mailserver: 172.19.0.2
```

Before Roundcube was added, `mailserver` was the only container on this network.

---

# 5. Original docker-mailserver Configuration

Relevant observed settings:

```text
OVERRIDE_HOSTNAME=mail.wrydeco.com
LOG_LEVEL=info
SSL_TYPE=letsencrypt
ENABLE_OPENDKIM=1
ENABLE_OPENDMARC=1
ENABLE_RSPAMD=0
ENABLE_CLAMAV=0
ENABLE_FAIL2BAN=1
SPOOF_PROTECTION=1
POSTMASTER_ADDRESS=support@wrydeco.com
```

Internal services confirmed running:

```text
Postfix
Dovecot
OpenDKIM
OpenDMARC
Fail2ban
Amavis
cron
rsyslog
changedetector
```

Not running / disabled:

```text
Rspamd
ClamAV
```

Host mail listeners:

```text
25/tcp   SMTP
587/tcp  SMTP Submission
993/tcp  IMAPS
```

Not exposed:

```text
110
143
465
995
```

---

# 6. Mailserver Mounts

Observed host-to-container mounts:

```text
/opt/mailserver/docker-data/certbot/certs
    -> /etc/letsencrypt
    read-only

/etc/localtime
    -> /etc/localtime
    read-only

/opt/mailserver/docker-data/dms/mail-data
    -> /var/mail
    read-write

/opt/mailserver/docker-data/dms/mail-state
    -> /var/mail-state
    read-write

/opt/mailserver/docker-data/dms/mail-logs
    -> /var/log/mail
    read-write

/opt/mailserver/docker-data/dms/config
    -> /tmp/docker-mailserver
    read-write
```

---

# 7. Mail Accounts Verified During Audit

The following accounts existed:

```text
support@wrydeco.com
support@chillgen.com
support@preaureum.com
support@jeminise.com
support@piezaprint.com
```

Observed mailbox usage at audit time:

```text
support@wrydeco.com      ~5.3 MB
support@chillgen.com     ~2.0 KB
support@preaureum.com    ~960 KB
support@jeminise.com     ~3.0 MB
support@piezaprint.com   ~129 KB
```

No plaintext passwords or password hashes are stored in this document.

---

# 8. Dovecot Audit

Observed Dovecot state:

```text
protocols = imap lmtp
ssl = required
mail_location = maildir:/var/mail/%d/%n
```

Certificate paths inside mailserver:

```text
/etc/letsencrypt/live/mail.wrydeco.com/fullchain.pem
/etc/letsencrypt/live/mail.wrydeco.com/privkey.pem
```

Target mailbox:

```text
support@wrydeco.com
```

Observed properties:

```text
uid=5000
gid=5000
home=/var/mail/wrydeco.com/support/home
mail=maildir:/var/mail/wrydeco.com/support
```

Folders confirmed:

```text
INBOX
Sent
Drafts
Junk
Trash
```

IMAPS capability probe succeeded on port `993`.

Observed banner included:

```text
IMAP4rev1
SASL-IR
LOGIN-REFERRALS
ID
ENABLE
IDLE
LITERAL+
AUTH=PLAIN
AUTH=LOGIN
```

---

# 9. Postfix Audit

Observed:

```text
myhostname = mail.wrydeco.com
mydomain = wrydeco.com
inet_interfaces = all
inet_protocols = ipv4
mydestination = $myhostname, localhost.$mydomain, localhost
virtual_mailbox_domains = /etc/postfix/vhost
virtual_transport = lmtp:unix:/var/run/dovecot/lmtp
```

SMTP authentication:

```text
Globally: smtpd_sasl_auth_enable = no
Submission service :587: SMTP AUTH enabled through Dovecot
```

Submission TLS:

```text
smtpd_tls_security_level=encrypt
```

Relayhost:

```text
empty
```

Mail is therefore sent directly by the mailserver.

Queue during audit:

```text
Mail queue is empty
```

---

# 10. Existing Mail TLS Certificate

Mail services continued using the existing certificate for:

```text
mail.wrydeco.com
```

Observed certificate:

```text
Subject: CN = mail.wrydeco.com
SAN: DNS:mail.wrydeco.com
Issuer: Let's Encrypt
Valid from: 2026-07-12 14:56:36 GMT
Valid until: 2026-10-10 14:56:35 GMT
```

Observed SHA-256 fingerprint:

```text
6F:0B:53:58:FE:CD:A1:44:2D:AC:7A:83:21:36:25:85:E1:EB:E5:51:D2:1B:EB:AA:4F:04:8B:74:FF:37:F3:AC
```

The same certificate was served on:

```text
IMAPS 993
SMTP Submission 587
```

The mail certificate is separate from the new webmail browser certificate.

---

# 11. DNS State Before Webmail

Existing mail DNS:

```text
mail.wrydeco.com A 103.147.123.63
wrydeco.com MX 10 mail.wrydeco.com
SPF: v=spf1 mx ip4:103.147.123.63 -all
DMARC: v=DMARC1; p=none; fo=1
DKIM selector: mail._domainkey
```

PTR:

```text
103.147.123.63 -> photobooth.encycom.com
```

This PTR does not match:

```text
mail.wrydeco.com
```

This remains a known mail-deliverability / identity issue outside the Roundcube deployment.

Before the deployment:

```text
webmail.wrydeco.com
```

did not resolve.

---

# 12. Existing Nginx State Before Webmail

Host Nginx already owned:

```text
80/tcp
443/tcp
```

No containerized Nginx/Traefik/Caddy proxy was found.

No existing Nginx vhost was found for:

```text
mail.wrydeco.com
webmail.wrydeco.com
```

Nginx master PID at the time of deployment:

```text
2475573
```

It remained preserved through graceful reloads.

---

# 13. Fail2ban State Before Webmail

Active jails:

```text
custom
dovecot
postfix
```

Observed pre-webmail ignore lists:

```text
dovecot: 127.0.0.0/8
postfix: 127.0.0.0/8
```

There was no existing:

```text
/opt/mailserver/docker-data/dms/config/fail2ban-jail.cf
```

This fact is important for rollback because the file was introduced solely for Roundcube.

---

# 14. Webmail Design Decision

Selected solution:

```text
Roundcube 1.7.3
Apache non-root container
SQLite
```

Design requirements:

```text
Dedicated project directory
Dedicated container
Static Docker IP
No public Roundcube Docker port
No privileged mode
No host networking
No Docker socket mount
No mail-data mount
No mailserver restart
No Postfix/Dovecot config change
No existing Nginx vhost change
```

Roundcube backend flow:

```text
Roundcube
   |
   +--> ssl://mailserver:993
   |
   +--> tls://mailserver:587
```

TLS certificate validation uses:

```text
peer_name = mail.wrydeco.com
```

so the Docker service name can be used for routing while the real certificate identity remains validated.

---

# 15. Backup Created Before Deployment

Backup directory:

```text
/root/webmail-roundcube-backup-20260903-150531
```

The deployment process captured / backed up relevant pre-change state including:

```text
Nginx configuration snapshot
nginx.conf
listener snapshot
mailserver state
Fail2ban runtime ignore lists
HTTP-only webmail vhost before HTTPS activation
```

Important HTTP-only vhost backup:

```text
/root/webmail-roundcube-backup-20260903-150531/webmail.wrydeco.com.http-only.conf
```

---

# 16. Roundcube Project Layout

Production project directory:

```text
/opt/webmail-roundcube
```

Layout:

```text
/opt/webmail-roundcube/
├── compose.yaml
├── config/
│   └── zz-wrydeco.inc.php
├── db/
├── hooks/
│   └── nginx-reload-after-webmail-cert.sh
├── php/
│   └── zz-webmail.ini
└── secrets/
    └── roundcube_des_key
```

Permissions established during deployment:

```text
db/
    uid 33
    gid 33
    mode 0750

secrets/
    root:root
    mode 0700
```

The encryption key itself was generated as 24 characters.

Its actual value is intentionally not stored in this document.

---

# 17. Roundcube Docker Compose Configuration

Recorded deployment configuration:

```yaml
services:
  roundcube:
    image: roundcube/roundcubemail:1.7.3-apache-nonroot
    container_name: wrydeco-webmail

    restart: unless-stopped

    ports:
      - "127.0.0.1:8088:8000"

    environment:
      ROUNDCUBEMAIL_DB_TYPE: "sqlite"

    volumes:
      - ./db:/var/roundcube/db
      - ./config/zz-wrydeco.inc.php:/var/roundcube/config/zz-wrydeco.inc.php:ro
      - ./php/zz-webmail.ini:/usr/local/etc/php/conf.d/zz-webmail.ini:ro
      - ./secrets/roundcube_des_key:/run/secrets/roundcube_des_key:ro

    networks:
      mailnet:
        ipv4_address: 172.19.0.10

    security_opt:
      - no-new-privileges:true

    mem_limit: 768m
    cpus: "1.0"
    pids_limit: 256

networks:
  mailnet:
    external: true
    name: mailserver_default
```

Important properties:

```text
Container name: wrydeco-webmail
Container IP: 172.19.0.10
Host bind: 127.0.0.1:8088
Container HTTP port: 8000
External Docker network: mailserver_default
```

Roundcube has no public Docker listener.

---

# 18. Roundcube Application Configuration

File:

```text
/opt/webmail-roundcube/config/zz-wrydeco.inc.php
```

Recorded deployment configuration:

```php
<?php

$config['product_name'] = 'WRYDECO Webmail';

$config['imap_host'] = 'ssl://mailserver:993';

$config['smtp_host'] = 'tls://mailserver:587';
$config['smtp_user'] = '%u';
$config['smtp_pass'] = '%p';

$config['des_key'] = trim(
    file_get_contents('/run/secrets/roundcube_des_key')
);

$config['imap_conn_options'] = [
    'ssl' => [
        'verify_peer'       => true,
        'verify_peer_name'  => true,
        'allow_self_signed' => false,
        'peer_name'         => 'mail.wrydeco.com',
    ],
];

$config['smtp_conn_options'] = [
    'ssl' => [
        'verify_peer'       => true,
        'verify_peer_name'  => true,
        'allow_self_signed' => false,
        'peer_name'         => 'mail.wrydeco.com',
    ],
];

$config['use_https'] = true;

$config['proxy_whitelist'] = [
    '172.19.0.1',
];

$config['trusted_host_patterns'] = [
    '^webmail\.wrydeco\.com$',
];

$config['login_username_filter'] = 'email';
$config['login_lc'] = 2;
$config['login_rate_limit'] = 3;

$config['identities_level'] = 3;

$config['drafts_mbox'] = 'Drafts';
$config['sent_mbox']   = 'Sent';
$config['junk_mbox']   = 'Junk';
$config['trash_mbox']  = 'Trash';

$config['create_default_folders'] = false;

$config['skin'] = 'elastic';

$config['plugins'] = [
    'archive',
    'zipdownload',
];

$config['session_lifetime'] = 30;
$config['session_samesite'] = 'Lax';
$config['log_logins'] = true;

$config['max_message_size'] = '25M';
```

Security properties:

```text
TLS peer verification enabled
TLS hostname verification enabled
Self-signed certificates rejected
Full email address required for login
Roundcube login rate limiting enabled
Sender identity restricted
HTTPS assumed behind trusted reverse proxy
Only Docker host gateway trusted for forwarded proxy information
Only webmail.wrydeco.com accepted as production Host
```

---

# 19. Roundcube PHP Configuration

File:

```text
/opt/webmail-roundcube/php/zz-webmail.ini
```

Configuration:

```ini
upload_max_filesize=15M
post_max_size=18M
memory_limit=256M
max_execution_time=300
```

The upload limit is intentionally below the SMTP total-message limit because MIME/base64 encoding increases final message size.

---

# 20. Roundcube Encryption Key

File:

```text
/opt/webmail-roundcube/secrets/roundcube_des_key
```

Generation procedure:

```bash
openssl rand -hex 12 | tr -d "\n"
```

Required length:

```text
24 bytes / characters
```

Security rule:

```text
DO NOT copy the actual key into this document.
DO NOT store it in Git.
DO NOT send it in chat.
DO NOT print it in deployment reports.
```

If the key is lost while the Roundcube DB still contains data encrypted with it, regenerate only with full understanding of the impact on encrypted Roundcube data/settings.

---

# 21. Fail2ban Configuration Added for Roundcube

Roundcube always reaches Postfix/Dovecot from:

```text
172.19.0.10
```

Without an exception, repeated invalid webmail logins could cause Fail2ban to ban the Roundcube container itself and therefore block webmail access for all users.

Persistent file introduced by this deployment:

```text
/opt/mailserver/docker-data/dms/config/fail2ban-jail.cf
```

Configuration:

```ini
[dovecot]
ignoreip = 127.0.0.0/8 172.19.0.10

[postfix]
ignoreip = 127.0.0.0/8 172.19.0.10
```

The live running Fail2ban instance was updated without restarting the mailserver:

```bash
docker exec mailserver \
  fail2ban-client set dovecot addignoreip 172.19.0.10

docker exec mailserver \
  fail2ban-client set postfix addignoreip 172.19.0.10
```

Only `172.19.0.10` was whitelisted.

The entire `172.19.0.0/16` network was **not** whitelisted.

---

# 22. DNS Change

TenTen DNS management URL:

```text
https://domain.tenten.vn/ApiDnsSetting
```

Exactly one record was added:

```text
Type: A
Host: webmail
Value: 103.147.123.63
Priority: 0
```

Result:

```text
webmail.wrydeco.com -> 103.147.123.63
```

Existing production records were not altered.

Verification:

```text
ns-a1.tenten.vn -> 103.147.123.63
ns-a2.tenten.vn -> 103.147.123.63
1.1.1.1        -> 103.147.123.63
8.8.8.8        -> 103.147.123.63
```

AAAA:

```text
none
```

---

# 23. ACME HTTP-01 Webroot

Webroot:

```text
/var/www/webmail-acme
```

Challenge directory:

```text
/var/www/webmail-acme/.well-known/acme-challenge/
```

A temporary preflight file was used:

```text
preflight.txt
```

Expected body:

```text
webmail-acme-ok
```

The route was verified from:

```text
VPS
external workstation
```

Both returned HTTP 200 before certificate issuance.

The temporary test file was removed after successful HTTPS deployment.

The challenge directory remains available for renewal.

---

# 24. Let's Encrypt Webmail Certificate

Dedicated certificate:

```text
webmail.wrydeco.com
```

Issuance mode:

```text
certbot certonly
webroot authenticator
```

Webroot:

```text
/var/www/webmail-acme
```

Certificate:

```text
/etc/letsencrypt/live/webmail.wrydeco.com/fullchain.pem
```

Private key:

```text
/etc/letsencrypt/live/webmail.wrydeco.com/privkey.pem
```

Observed certificate:

```text
Subject: CN = webmail.wrydeco.com
SAN: DNS:webmail.wrydeco.com
Issuer: Let's Encrypt (CN = YR1)
Valid from: 2026-09-03 14:47:05 GMT
Valid until: 2026-12-02 14:47:04 GMT
```

Certificate status:

```text
Valid
Trusted
No browser warnings
```

This certificate is independent of the existing `mail.wrydeco.com` mail certificate.

---

# 25. Certbot Deploy Hook

File:

```text
/opt/webmail-roundcube/hooks/nginx-reload-after-webmail-cert.sh
```

Configuration:

```sh
#!/bin/sh
set -eu

PATH=/usr/sbin:/usr/bin:/sbin:/bin

EXPECTED="/etc/letsencrypt/live/webmail.wrydeco.com"

if [ "${RENEWED_LINEAGE:-}" != "$EXPECTED" ]; then
    exit 0
fi

/usr/sbin/nginx -t
/bin/systemctl reload nginx
```

Behavior:

1. Do nothing for unrelated certificates.
2. Act only when the renewed lineage is exactly the webmail certificate.
3. Validate Nginx first.
4. Gracefully reload Nginx only after successful validation.
5. Never restart the mailserver.

---

# 26. Final Nginx Configuration

File:

```text
/etc/nginx/sites-available/webmail.wrydeco.com.conf
```

Enabled through:

```text
/etc/nginx/sites-enabled/webmail.wrydeco.com.conf
```

Recorded final configuration:

```nginx
server {
    listen 80;

    server_name webmail.wrydeco.com;

    location ^~ /.well-known/acme-challenge/ {
        root /var/www/webmail-acme;
        default_type text/plain;
        try_files $uri =404;
    }

    location / {
        return 301 https://webmail.wrydeco.com$request_uri;
    }
}

server {
    listen 443 ssl;

    server_name webmail.wrydeco.com;

    ssl_certificate
        /etc/letsencrypt/live/webmail.wrydeco.com/fullchain.pem;

    ssl_certificate_key
        /etc/letsencrypt/live/webmail.wrydeco.com/privkey.pem;

    ssl_protocols TLSv1.2 TLSv1.3;

    server_tokens off;

    client_max_body_size 18M;

    location / {
        proxy_pass http://127.0.0.1:8088;

        proxy_http_version 1.1;

        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $remote_addr;
        proxy_set_header X-Forwarded-Proto https;
        proxy_set_header X-Forwarded-Host $host;
        proxy_set_header X-Forwarded-Port 443;

        proxy_connect_timeout 10s;
        proxy_send_timeout 300s;
        proxy_read_timeout 300s;
    }
}
```

HSTS was intentionally not enabled during initial deployment.

---

# 27. Nginx Change Procedure

Every Nginx change followed:

```bash
sudo nginx -t
```

before:

```bash
sudo systemctl reload nginx
```

No `systemctl restart nginx` was required for this deployment.

Observed final state:

```text
nginx = active
nginx -t = successful
Master PID = 2475573
```

The master PID remained preserved through graceful reloads.

---

# 28. Final Port Topology

Observed final topology:

```text
25     public -> docker-mailserver
587    public -> docker-mailserver
993    public -> docker-mailserver

80     public -> host Nginx
443    public -> host Nginx

8088   127.0.0.1 only -> Roundcube
```

Critical isolation property:

```text
Roundcube is NOT listening on:
0.0.0.0:8088
[::]:8088
```

---

# 29. Roundcube Final Runtime State

Observed final state:

```text
Container: wrydeco-webmail
Status: running
StartedAt: 2026-09-03T15:06:27.27291846Z
RestartCount: 0
IP: 172.19.0.10
Host binding: 127.0.0.1:8088
```

Observed resources:

```text
CPU: ~0.01%
Memory: ~95.75 MiB / 768 MiB
PIDs: 6
```

---

# 30. Mailserver Final Runtime State

Observed after the complete deployment:

```text
Container: mailserver
Status: running
StartedAt: 2026-07-29T07:46:04.212922215Z
RestartCount: 0
```

The original `StartedAt` value was preserved.

This proves the mailserver container was not restarted during deployment.

Ports remained available:

```text
25
587
993
```

Maildir structure remained in place.

---

# 31. Browser Verification

Browser automation used:

```text
Playwright / Chromium
```

Production URL:

```text
https://webmail.wrydeco.com
```

Observed:

```text
Roundcube Elastic login page loaded successfully
HTTPS valid
No SSL certificate warning
No mixed-content warning
HTTP :80 redirected to HTTPS
HTTPS :443 returned HTTP/2 200
```

Session cookie was observed with secure attributes including:

```text
SameSite=Lax
HttpOnly
Secure
```

---

# 32. Authentication Verification

Account:

```text
support@wrydeco.com
```

Authentication through the HTTPS Roundcube login form:

```text
PASS
```

The existing mailbox password was used.

The password itself is intentionally omitted from this report.

Folders verified:

```text
INBOX
Sent
Drafts
Junk
Trash
```

Compose UI:

```text
present
```

Observed Inbox at final verification:

```text
268 emails / unread count reported during verification
```

The session was logged out cleanly after testing.

---

# 33. End-to-End Mail Path

Incoming browser read path:

```text
Browser
  -> HTTPS
  -> Nginx
  -> Roundcube
  -> IMAPS 993
  -> Dovecot
  -> Maildir
```

Outgoing path:

```text
Browser
  -> HTTPS
  -> Roundcube
  -> SMTP Submission 587
  -> Postfix
  -> Internet
```

Existing Outlook path remains:

```text
Outlook Mobile
  -> IMAPS 993
  -> SMTP Submission 587
  -> same mailserver
```

---

# 34. Regression Verification

## 34.1 Mailserver

PASS:

```text
Status running
StartedAt unchanged
RestartCount 0
Ports 25/587/993 intact
```

## 34.2 Nginx

PASS:

```text
active
nginx -t successful
Master PID preserved
graceful reload only
```

## 34.3 Roundcube

PASS:

```text
running
RestartCount 0
127.0.0.1-only binding
172.19.0.10 static IP
login page functional
mailbox authentication functional
```

## 34.4 Fail2ban

PASS:

```text
172.19.0.10 in dovecot ignoreip
172.19.0.10 in postfix ignoreip
172.19.0.10 not banned
external protection remains enabled
```

## 34.5 Other production services

Reported outcome:

```text
No unrelated production service was restarted.
No unrelated Nginx site was modified.
No unrelated certificate was modified.
No unrelated DNS record was modified.
```

---

# 35. Chronological Deployment Journal

The following is a consolidated sequence of the work performed.

## Phase 1 — Read-only audit

1. Verified VPS identity, OS, uptime, memory, disk.
2. Verified `/opt/mailserver`.
3. Identified the running docker-mailserver container.
4. Captured image ID/digest and mounts.
5. Verified listeners `25/587/993`.
6. Identified host Nginx as owner of `80/443`.
7. Verified no existing webmail deployment.
8. Verified no Nginx vhost collision for `webmail.wrydeco.com`.
9. Verified Postfix.
10. Verified Dovecot.
11. Verified existing mailboxes.
12. Verified IMAPS and Submission TLS.
13. Verified current mail TLS certificate.
14. Verified queue state.
15. Verified Fail2ban.
16. Verified DNS.
17. Identified PTR mismatch.
18. Confirmed mailserver was healthy.

## Phase 2 — Webmail preparation

1. Confirmed `mailserver_default = 172.19.0.0/16`.
2. Confirmed only `mailserver` occupied the network.
3. Reserved `172.19.0.10` for Roundcube.
4. Verified Fail2ban ignore list.
5. Verified no persistent custom Fail2ban file existed.
6. Verified Certbot state.
7. Verified Nginx state.
8. Verified port `8088` unused.
9. Created backup directory.
10. Created `/opt/webmail-roundcube`.
11. Generated Roundcube encryption key.
12. Created Roundcube config.
13. Created PHP configuration.
14. Created Compose configuration.
15. Validated Compose.
16. Added Roundcube IP to Fail2ban.
17. Pulled Roundcube image.
18. Started only Roundcube.
19. Verified localhost-only binding.
20. Verified Docker network/DNS.
21. Verified local Roundcube HTTP.
22. Verified TLS connectivity to IMAPS.
23. Reconfirmed mailserver baseline.

## Phase 3 — DNS and HTTPS

1. Added TenTen DNS record:
   `webmail A 103.147.123.63`.
2. Verified authoritative DNS.
3. Verified Cloudflare and Google resolvers.
4. Verified no unintended AAAA.
5. Created ACME webroot.
6. Created HTTP-only Nginx vhost.
7. Validated Nginx.
8. Gracefully reloaded Nginx.
9. Verified ACME route locally.
10. Verified ACME route externally.
11. Created certificate-specific deploy hook.
12. Issued dedicated Let's Encrypt certificate.
13. Verified certificate SAN/validity.
14. Backed up HTTP-only vhost.
15. Activated HTTPS reverse proxy.
16. Validated Nginx.
17. Gracefully reloaded Nginx.
18. Verified HTTP -> HTTPS redirect.
19. Verified HTTPS returns HTTP/2 200.
20. Verified browser certificate trust.

## Phase 4 — Browser acceptance

1. Loaded Roundcube through HTTPS.
2. Authenticated `support@wrydeco.com`.
3. Loaded INBOX.
4. Verified Sent/Drafts/Junk/Trash.
5. Verified Compose UI.
6. Logged out safely.
7. Rechecked Roundcube runtime.
8. Rechecked mailserver runtime.
9. Rechecked Nginx.
10. Rechecked listeners.
11. Rechecked Fail2ban.
12. Confirmed unrelated production services remained intact.

---

# 36. Operations Commands

## 36.1 Check Roundcube

```bash
sudo docker ps \
  --filter 'name=^/wrydeco-webmail$' \
  --format 'table {{.Names}}\t{{.Image}}\t{{.Status}}\t{{.Ports}}'
```

## 36.2 Check Roundcube resources

```bash
sudo docker stats --no-stream wrydeco-webmail
```

## 36.3 Check Roundcube logs

```bash
sudo docker logs --tail 200 wrydeco-webmail
```

## 36.4 Check Roundcube local HTTP

```bash
curl -sSI \
  -H 'Host: webmail.wrydeco.com' \
  http://127.0.0.1:8088/
```

## 36.5 Check Nginx

```bash
systemctl is-active nginx
sudo nginx -t
```

## 36.6 Check public webmail

```bash
curl -sSI https://webmail.wrydeco.com/
```

## 36.7 Check mailserver

```bash
sudo docker inspect mailserver \
  --format 'Status={{.State.Status}} StartedAt={{.State.StartedAt}} RestartCount={{.RestartCount}}'
```

## 36.8 Check mail ports

```bash
sudo ss -ltnp | grep -E ':(25|587|993)\b'
```

## 36.9 Check web ports

```bash
sudo ss -ltnp | grep -E ':(80|443|8088)\b'
```

## 36.10 Check Fail2ban Roundcube exception

```bash
sudo docker exec mailserver \
  fail2ban-client get dovecot ignoreip

sudo docker exec mailserver \
  fail2ban-client get postfix ignoreip
```

---

# 37. Certificate Operations

## 37.1 Inspect webmail certificate

```bash
sudo openssl x509 \
  -in /etc/letsencrypt/live/webmail.wrydeco.com/fullchain.pem \
  -noout \
  -subject \
  -issuer \
  -dates \
  -fingerprint \
  -sha256 \
  -ext subjectAltName
```

## 37.2 Inspect externally served certificate

```bash
echo | \
openssl s_client \
  -connect webmail.wrydeco.com:443 \
  -servername webmail.wrydeco.com \
  2>/dev/null | \
openssl x509 \
  -noout \
  -subject \
  -issuer \
  -dates \
  -ext subjectAltName
```

## 37.3 Renewal dry-run

Approved validation command:

```bash
sudo certbot renew \
  --cert-name webmail.wrydeco.com \
  --dry-run \
  --run-deploy-hooks \
  --no-directory-hooks
```

If a successful renewal dry-run was not explicitly recorded in the execution evidence retained with this document, treat the renewal dry-run as an operational check to confirm rather than assuming it has been proven.

---

# 38. Rollback Procedure

Rollback is designed to remove webmail without restarting the production mailserver.

## 38.1 Disable public webmail first

```bash
sudo rm -f \
  /etc/nginx/sites-enabled/webmail.wrydeco.com.conf

sudo nginx -t && \
sudo systemctl reload nginx
```

## 38.2 Stop Roundcube only

```bash
cd /opt/webmail-roundcube

sudo docker compose \
  -p wrydeco-webmail \
  down
```

Do not remove:

```text
mailserver_default
```

It is an external production network.

## 38.3 Remove Fail2ban runtime exceptions

```bash
sudo docker exec mailserver \
  fail2ban-client set dovecot \
  delignoreip 172.19.0.10

sudo docker exec mailserver \
  fail2ban-client set postfix \
  delignoreip 172.19.0.10
```

## 38.4 Remove persistent Fail2ban override

Because this file did not exist before the deployment:

```bash
sudo rm -f \
  /opt/mailserver/docker-data/dms/config/fail2ban-jail.cf
```

Do not restart the mailserver merely to perform rollback.

## 38.5 Remove Nginx site file

```bash
sudo rm -f \
  /etc/nginx/sites-available/webmail.wrydeco.com.conf

sudo nginx -t
```

## 38.6 Remove Roundcube project if performing full cleanup

Only after the Roundcube container is down:

```bash
sudo rm -rf /opt/webmail-roundcube
```

This removes Roundcube configuration, SQLite data and preferences.

It does not remove Dovecot Maildir mail.

## 38.7 Remove ACME webroot if performing full cleanup

```bash
sudo rm -rf /var/www/webmail-acme
```

## 38.8 DNS rollback

Remove only:

```text
webmail.wrydeco.com A 103.147.123.63
```

Do not modify:

```text
mail.wrydeco.com
MX
SPF
DKIM
DMARC
@
```

## 38.9 Certificate cleanup

Operational rollback does not require certificate deletion.

If complete cleanup is intentionally required, first ensure no active Nginx config references the certificate:

```bash
sudo nginx -T 2>/dev/null | \
grep -n '/etc/letsencrypt/live/webmail.wrydeco.com' \
|| true
```

Then:

```bash
sudo certbot delete \
  --cert-name webmail.wrydeco.com
```

## 38.10 Verify production mail after rollback

```bash
sudo docker inspect mailserver \
  --format 'Status={{.State.Status}} StartedAt={{.State.StartedAt}} RestartCount={{.RestartCount}}'

sudo ss -ltnp | grep -E ':(25|587|993)\b'

systemctl is-active nginx
sudo nginx -t
```

---

# 39. Hard Stop / Safety Rules for Future Maintenance

Do not proceed automatically if any of the following occurs:

```text
nginx -t fails
mailserver RestartCount changes unexpectedly
mailserver StartedAt changes unexpectedly
mailserver stops
port 25 disappears
port 587 disappears
port 993 disappears
Roundcube becomes publicly bound to 0.0.0.0:8088
Roundcube loses static IP 172.19.0.10
mailserver_default contains unexpected unrelated containers
Roundcube TLS requires verify_peer=false
Roundcube TLS requires verify_peer_name=false
an existing production Nginx vhost must be modified
Certbot wants to modify an unrelated certificate
maintenance would require restarting mailserver
maintenance would require modifying Postfix or Dovecot simply to keep Roundcube working
```

Preferred response:

```text
STOP
Collect evidence
Do not improvise
Do not restart unrelated production services
Rollback webmail changes if necessary
```

---

# 40. Known Issues / Remaining Maintenance

## 40.1 PTR / Reverse DNS mismatch

Current PTR:

```text
103.147.123.63 -> photobooth.encycom.com
```

Mail identity:

```text
mail.wrydeco.com
```

These do not match.

This was already present before the webmail project and was not changed by this deployment.

It should be handled as a separate mail-deliverability / server-identity maintenance task.

## 40.2 Existing mail certificate renewal

The existing IMAP/SMTP certificate for:

```text
mail.wrydeco.com
```

was observed expiring:

```text
2026-10-10 14:56:35 GMT
```

Its issuance/renewal workflow is separate from the new browser certificate for `webmail.wrydeco.com`.

Do not assume that renewing the webmail browser certificate also renews the mailserver certificate.

This should be tracked separately.

## 40.3 No HSTS during initial deployment

HSTS was intentionally omitted from the initial Nginx configuration.

This avoids browser-side persistent policy while the new deployment is still relatively new.

It may be considered later after stable operations are confirmed.

---

# 41. Secrets and Sensitive Material Explicitly Excluded

The following are deliberately not stored in this report:

```text
support@wrydeco.com password
passwords for other mailboxes
mailbox password hashes
Roundcube DES/encryption key
TLS private-key contents
DKIM private-key contents
API tokens
browser session cookies
```

Only file paths and regeneration/operational procedures are documented.

---

# 42. Original Roundcube Verification Summary (2026-09-03)

## DNS

```text
PASS
webmail.wrydeco.com -> 103.147.123.63
```

## HTTPS

```text
PASS
HTTP :80 -> 301 HTTPS
HTTPS :443 -> HTTP/2 200
```

## Certificate

```text
PASS
SAN = DNS:webmail.wrydeco.com
Let's Encrypt
Valid until 2026-12-02 14:47:04 GMT
Trusted
```

## Roundcube

```text
PASS
running
RestartCount=0
127.0.0.1:8088 only
172.19.0.10
Elastic UI
```

## Mailbox

```text
PASS
support@wrydeco.com authentication
INBOX
Sent
Drafts
Junk
Trash
Compose
Logout
```

## Mailserver

```text
PASS
running
StartedAt unchanged
RestartCount=0
25/587/993 intact
```

## Nginx

```text
PASS
active
nginx -t successful
Master PID preserved
graceful reload
```

## Fail2ban

```text
PASS
172.19.0.10 whitelisted only where required
not banned
```

## Other production services

```text
PASS
No reported restart or configuration modification caused by this deployment.
```

---

# 43. Original Roundcube Deployment Result (2026-09-03)

```text
OVERALL STATUS: PASS
WEBMAIL DEPLOYMENT: COMPLETE
BROWSER LOGIN: VERIFIED
PRODUCTION MAILSERVER UPTIME: PRESERVED
BLAST RADIUS: CONTAINED
```

Official webmail URL:

```text
https://webmail.wrydeco.com
```

Primary verified account:

```text
support@wrydeco.com
```

---

# 44. Related Planning / Review Session

Planner / Technical Reviewer session used during the deployment:

```text
https://chatgpt.com/c/6a997b23-2fec-83ec-9526-3e9c218677b4
```

The session contains the detailed planner/worker interaction, safety gates, implementation approvals and review decisions.

This runbook is intended to contain the operationally important information so future maintenance should not require the chat session for normal administration.

---

# 45. Documentation Consolidation

This runbook supersedes the shorter completion report that previously existed in
this project. The shorter report and the completed planning/mission documents were
removed during documentation cleanup because their operationally relevant content
is consolidated here.

This document is the authoritative long-term technical reference for the deployed
mailserver and webmail architecture. Sections 1-45 preserve the Roundcube deployment
record verified on 2026-09-03. Section 46 supersedes those sections only where it
describes the newer live webmail routing observed on 2026-10-02.

---

# 46. Live-State Re-Audit — SnappyMail Cutover (2026-10-02)

## 46.1 Scope and Method

A read-only re-audit was performed against VPS `103.147.123.63` on 2026-10-02
(Asia/Ho_Chi_Minh). The purpose was to identify which application currently serves
the production webmail URL.

The re-audit inspected:

- running Docker containers, images, start times, restart counts and published ports;
- Docker Compose labels, working directories, networks and mounts;
- enabled Nginx virtual hosts and their `proxy_pass` targets;
- HTTP responses from the public webmail URL and both local webmail ports;
- the SnappyMail Compose project layout.

No file, container, service, firewall rule, DNS record or Nginx configuration was
changed during this re-audit.

## 46.2 Current Production Webmail

The application currently serving the public production URL is:

```text
Application: SnappyMail
Observed application version label: v2.38.2
Production URL: https://webmail.wrydeco.com
Container: wrydeco-snappymail-test
Image: djmaze/snappymail:latest
Container status: running
StartedAt: 2026-09-29T13:49:25.432476695Z
RestartCount: 0
Host binding: 127.0.0.1:8089
Container HTTP port: 8888
Docker network: mailserver_default
Observed container IP: 172.19.0.3
Compose project: webmail-snappymail
Compose file: /opt/webmail-snappymail/compose.yaml
Compose working directory: /opt/webmail-snappymail
Persistent data: /opt/webmail-snappymail/data
Container data path: /var/lib/snappymail
```

Although the container name ends in `-test`, Nginx currently routes the production
`webmail.wrydeco.com` hostname to this container. Operational behavior, rather than
the container name, therefore establishes SnappyMail as the live production webmail
frontend at the time of the re-audit.

## 46.3 Current Nginx Routing

Enabled virtual host:

```text
/etc/nginx/sites-enabled/webmail.wrydeco.com.conf
    -> /etc/nginx/sites-available/webmail.wrydeco.com.conf
```

Observed active upstream:

```nginx
server_name webmail.wrydeco.com;
proxy_pass http://127.0.0.1:8089;
```

This produces the current request path:

```text
Internet
   |
   | HTTPS :443
   v
webmail.wrydeco.com
   |
   v
Host Nginx
   |
   | HTTP over localhost
   v
127.0.0.1:8089
   |
   v
wrydeco-snappymail-test
   |
   | mailserver_default Docker network
   v
docker-mailserver through IMAP/SMTP
```

An older Nginx backup was also present:

```text
/etc/nginx/sites-available/webmail.wrydeco.com.conf.backup-roundcube
```

That backup still contains the former Roundcube upstream:

```nginx
proxy_pass http://127.0.0.1:8088;
```

The backup file is not the enabled production configuration.

## 46.4 HTTP Evidence

The public URL returned:

```text
https://webmail.wrydeco.com/ -> HTTP/2 200
```

The returned HTML contained SnappyMail application identifiers. A direct local
request to `127.0.0.1:8089` returned:

```text
HTTP/1.1 200 OK
Server: SnappyMail
```

This independently confirms that the active Nginx upstream and the application
served on port `8089` are SnappyMail.

This re-audit did not submit mailbox credentials, open message contents or send a
test email. Authentication, inbox rendering and outbound sending through SnappyMail
were therefore not re-certified in this specific check.

## 46.5 Roundcube Is Still Installed and Running

Roundcube has **not** been removed.

Observed Roundcube state:

```text
Application: Roundcube 1.7.3 Apache non-root
Container: wrydeco-webmail
Image: roundcube/roundcubemail:1.7.3-apache-nonroot
Container status: running
StartedAt: 2026-09-03T15:06:27.27291846Z
RestartCount: 0
Host binding: 127.0.0.1:8088
Container HTTP port: 8000
Compose project: wrydeco-webmail
Compose file: /opt/webmail-roundcube/compose.yaml
Compose working directory: /opt/webmail-roundcube
```

A direct local request to `127.0.0.1:8088` returned the WRYDECO Roundcube login
page and identified Roundcube in the HTML.

Nginx also contained the following separate configuration at the time of the audit:

```text
mail.wrydeco.com -> http://127.0.0.1:8088
```

Therefore the current situation is not a removal or in-place upgrade of Roundcube.
Both webmail clients coexist:

```text
webmail.wrydeco.com -> SnappyMail -> 127.0.0.1:8089
mail.wrydeco.com    -> Roundcube  -> 127.0.0.1:8088
```

Roundcube continues consuming VPS resources and retains its existing project data,
configuration and rollback value. It must not be deleted merely because SnappyMail
now serves the main webmail hostname. Any future removal requires a separate approved
change plan, confirmation that no user depends on `mail.wrydeco.com` browser access,
backup/retention decisions and post-removal mail regression testing.

## 46.6 Mail Backend Is Unchanged

The mail backend remained the existing `docker-mailserver` container:

```text
Container: mailserver
Image: ghcr.io/docker-mailserver/docker-mailserver:latest
Observed product version label: v15.1.0
Status: running
StartedAt: 2026-07-29T07:46:04.212922215Z
RestartCount: 0
Published mail ports: 25, 587, 993
```

SnappyMail and Roundcube are browser clients of the same mail backend. Neither is the
SMTP/IMAP server itself. The addition and activation of SnappyMail did not replace
Postfix, Dovecot or the persistent Maildir data managed by docker-mailserver.

## 46.7 Current Webmail Topology

```text
                         INTERNET
                            |
                 +----------+----------+
                 |                     |
                 v                     v
      webmail.wrydeco.com       mail.wrydeco.com
                 |                     |
                 +----------+----------+
                            |
                            v
                       Host Nginx
                 +----------+----------+
                 |                     |
                 v                     v
       127.0.0.1:8089          127.0.0.1:8088
                 |                     |
                 v                     v
           SnappyMail              Roundcube
          172.19.0.3             legacy client
                 |                     |
                 +----------+----------+
                            |
                            v
                  mailserver_default
                            |
                            v
                   docker-mailserver
                    IMAPS 993 / SMTP 587
```

## 46.8 Operational Observations

The following observations should be tracked but were not changed during the
read-only re-audit:

1. The SnappyMail container is named `wrydeco-snappymail-test` even though it serves
   the production hostname. This can confuse future operators and monitoring.
2. SnappyMail uses the mutable image tag `djmaze/snappymail:latest`. A pinned version
   or digest should be considered through a separately reviewed maintenance change.
3. SnappyMail and Roundcube are both running. Ownership, intended fallback behavior,
   monitoring and eventual lifecycle policy should be documented explicitly.
4. Several items under `/opt/webmail-snappymail/data` were observed with broadly
   writable permissions. The exact security and runtime requirements should be
   reviewed before changing ownership or modes; no permission was changed here.
5. The 2026-09-03 Roundcube verification remains valid as a historical deployment
   record, but it no longer describes the application behind the primary production
   hostname.

## 46.9 Current Status Summary

```text
PUBLIC WEBMAIL FRONTEND: SnappyMail v2.38.2
PUBLIC WEBMAIL URL: https://webmail.wrydeco.com
ACTIVE NGINX UPSTREAM: 127.0.0.1:8089
SNAPPYMAIL CONTAINER: running, RestartCount=0

ROUNDCUBE: still installed and running
ROUNDCUBE REMOVED: NO
ROUNDCUBE LOCAL UPSTREAM: 127.0.0.1:8088
ROUNDCUBE NGINX HOST: mail.wrydeco.com

MAIL BACKEND: docker-mailserver
MAIL BACKEND STATUS: running, RestartCount=0
READ-ONLY RE-AUDIT CHANGES: none
```
