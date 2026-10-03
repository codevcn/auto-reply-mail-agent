# 📬 Shopify Mail Agent — Intelligent Auto-Reply & Customer Support System

> **Hệ thống AI thông minh hỗ trợ thu thập, phân loại, làm giàu dữ liệu Shopify và tự động soạn thảo phản hồi email chăm sóc khách hàng với cơ chế Phê duyệt 100% bởi con người (100% Human Approval / Zero Autonomous Sending).**

---

## 📌 Mục Lục

- [1. Giới Thiệu Dự Án](#1-giới-thiệu-dự-án)
- [2. 5 Nguyên Tắc Bất Biến & An Toàn Cốt Lõi (Critical Invariants)](#2-5-nguyên-tắc-bất-biến--an-toàn-cốt-lõi-critical-invariants)
- [3. Tính Năng Nổi Bật](#3-tính-năng-nổi-bật)
- [4. Kiến Trúc Hệ Thống](#4-kiến-trúc-hệ-thống)
- [5. Công Nghệ Sử Dụng (Tech Stack)](#5-công-nghệ-sử-dụng-tech-stack)
- [6. Cấu Trúc Thư Mục Codebase](#6-cấu-trúc-thư-mục-codebase)
- [7. Hướng Dẫn Cài Đặt & Chạy Cục Bộ (Local Development)](#7-hướng-dẫn-cài-đặt--chạy-cục-bộ-local-development)
- [8. Quy Trình Triển Khai 1-Click Lên VPS (Automated Deployment)](#8-quy-trình-triển-khai-1-click-lên-vps-automated-deployment)
- [9. Kiểm Thử & Đảm Bảo Chất Lượng (Testing & QA)](#9-kiểm-thử--đảm-bảo-chất-lượng-testing--qa)
- [10. Vận Hành, Bảo Trì & Sao Lưu (Ops & Runbook)](#10-vận-hành-bảo-trì--sao-lưu-ops--runbook)
- [11. Tài Liệu Kèm Theo](#11-tài-liệu-kèm-theo)

---

## 1. Giới Thiệu Dự Án

**Shopify Mail Agent** là giải pháp trợ lý AI chuyên nghiệp phục vụ dịch vụ khách hàng (CSKH) cho các cửa hàng thương mại điện tử Shopify (hiện đang vận hành cho 4 cửa hàng: `wrydeco.com`, `chillgen.com`, `preaureum.com`, `jeminise.com`). 

Hệ thống kết nối trực tiếp với máy chủ mail qua giao thức **IMAP IDLE** để nhận diện email mới theo thời gian thực (near real-time), sử dụng mô hình ngôn ngữ lớn **Google Gemini (Vertex AI)** để phân loại ý định khách hàng, tự động tra cứu đơn hàng và chính sách từ **Shopify Admin API** qua **SOCKS5 Proxy**, sau đó sinh bản nháp phản hồi thông minh, chính xác và bám sát chính sách của từng store. 

Đặc biệt, hệ thống loại bỏ hoàn toàn rủi ro gửi nhầm của AI bằng quy trình **Duyệt thủ công 100% bởi con người (Zero Autonomous Sending)** trước khi thực hiện gửi qua SMTP.

---

## 2. 5 Nguyên Tắc Bất Biến & An Toàn Cốt Lõi (Critical Invariants)

Hệ thống được thiết kế và thực thi nghiêm ngặt theo 5 quy tắc bất biến để bảo vệ uy tín thương hiệu và an toàn hạ tầng:

| # | Invariant | Ý Nghĩa Kỹ Thuật & Ranh Giới An Toàn |
|---|-----------|---------------------------------------|
| **1** | **Zero Autonomous Sending** | **Tuyệt đối không gửi email tự động**. 100% email phản hồi bắt buộc phải được người dùng đăng nhập kiểm duyệt và nhấn nút `Approve & Send` trên giao diện web. Hệ thống áp dụng **Idempotency Key** để chặn đứng hoàn toàn việc gửi đúp do click đúp hoặc worker retry. |
| **2** | **Fail-Closed Shopify Proxy** | 100% HTTP request gửi tới Shopify Admin API (bao gồm lấy token và truy vấn dữ liệu) **bắt buộc phải đi qua SOCKS5 Proxy** chuyên dụng. Nếu proxy lỗi hoặc ngắt kết nối, request bị từ chối ngay lập tức (fail-closed), tuyệt đối không fallback ra kết nối trực tiếp để bảo vệ IP của máy chủ VPS. |
| **3** | **Zero Email Duplication in DB** | Cơ sở dữ liệu PostgreSQL **chỉ lưu trữ metadata, UID tham chiếu, bản nháp, context snapshots và audit log**. Nội dung email gốc và tệp đính kèm được đọc trực tiếp từ mailserver khi cần, không nhân bản vào database. |
| **4** | **Bảo Tồn Tuyệt Đối Hạ Tầng VPS** | Ứng dụng chạy trong mạng Docker Bridge riêng biệt (`mail-agent-net`), không chiếm dụng cổng `5432` trên VPS host (tránh xung đột với database của hệ thống khác), không gây gián đoạn hay khởi động lại dịch vụ `docker-mailserver` và SnappyMail đang hoạt động. |
| **5** | **120-Day Retention Policy** | Toàn bộ dữ liệu workflow metadata, lịch sử nháp và audit logs tự động được dọn dẹp sau **120 ngày** bằng tiến trình cron scheduler chạy định kỳ. |

---

## 3. Tính Năng Nổi Bật

### 🏢 Quản Lý Đa Cửa Hàng (Store Profile Wizard)
- Hỗ trợ thêm và quản lý nhiều cửa hàng Shopify độc lập.
- **Setup Wizard 4 bước**: Cấu hình Thông tin chung -> Kết nối Mailserver (IMAP/SMTP TLS) -> Cấu hình Shopify & Proxy -> Thiết lập Persona AI.
- Cơ chế **Envelope Encryption** (chuẩn AES-GCM-256) bảo vệ toàn bộ thông tin nhạy cảm (mật khẩu email, API token, proxy credentials).

### ⚡ Thu Thập Email Real-Time & Hàng Đợi Giao Dịch
- **IMAP IDLE Listener**: Lắng nghe và tiếp nhận email mới trong vòng vài giây.
- **5-Minute Reconciliation Engine**: Quét đối soát định kỳ 5 phút/lần chống sót lọt email khi mất kết nối mạng.
- Hàng đợi giao dịch **Transactional Row-Locked Queue** (`SELECT ... FOR UPDATE SKIP LOCKED`), đảm bảo an toàn tuyệt đối khi nhiều worker chạy song song.

### 🧠 Phân Loại Đa Chiều Bằng AI (Vertex AI Gemini)
- **Safe MIME Parser**: Trích xuất an toàn các file đính kèm hợp lệ (`.png`, `.jpg`, `.webp`, `.pdf`, `.txt`, `.docx`, `.xlsx`), tự động chuyển email có file lỗi/quá dung lượng sang hàng đợi `Needs manual review`.
- Phân loại 3 chiều độc lập:
  - **Spam / Phishing Detection**: Tự động nhận diện thư rác và ẩn khỏi hàng đợi duyệt (không xóa khỏi mailserver).
  - **Customer & Order Status**: Nhận diện khách hàng mới, khách hàng cũ, có mã đơn hàng hay không.
  - **Intent Classification**: Phân loại chính xác các ý định: `product_inquiry`, `order_support`, `complaint`, `return_or_refund`, `feedback`, `spam`, `other`.

### 🛍️ Làm Giàu Ngữ Cảnh Shopify (Shopify Deep Enrichment)
- Tự động tra cứu lịch sử đơn hàng của khách hàng trong **60 ngày gần nhất** qua Shopify Admin API.
- Tự động tìm kiếm sản phẩm liên quan theo từ khóa hoặc SKU khi khách hỏi về sản phẩm.
- Đồng bộ tự động **6 loại chính sách cửa hàng** từ Shopify (`Refund Policy`, `Privacy Policy`, `Terms of Service`, `Shipping Policy`, `Legal Notice`, `Contact Information`).
- **Stale Policy Draft Warning**: Cảnh báo tức thì trên giao diện nếu bản nháp được sinh dựa trên phiên bản chính sách cũ vừa bị cập nhật.

### ✍️ Không Gian Duyệt Nháp Chuyên Nghiệp (Draft Review Workspace)
- Giao diện 2 cột chuẩn **Atlassian Design System (ADS Tokens)**: Cột trái xem luồng email gốc & context đơn hàng/sản phẩm; Cột phải là trình soạn thảo phản hồi.
- **Diff Viewer**: So sánh trực quan sự khác biệt giữa bản nháp do AI sinh ra và nội dung do con người chỉnh sửa.
- **Immutable Versioning**: Lưu vết lịch sử chỉnh sửa không thể ghi đè.
- **Nút Regenerate**: Yêu cầu AI sinh lại bản nháp với ghi chú/chỉ đạo bổ sung từ nhân viên CSKH.
- **Gửi Mail An Toàn (SMTP STARTTLS)**: Tự động gắn header luồng email chuẩn (`In-Reply-To`, `References`) và sao chép email đã gửi vào thư mục `Sent` trên IMAP.

### 🛡️ Xác Thực & Phân Quyền An Toàn
- Đăng nhập bảo mật với thuật toán băm mật khẩu **Argon2id**.
- Quản lý phiên làm việc qua **Opaque Server-side Session** lưu trong PostgreSQL, Cookie chuẩn `HttpOnly; Secure; SameSite=Lax`.
- Phân quyền người dùng (Role-Based Access Control): `Admin` và `Agent`.
- Cơ chế bảo vệ **Lockout Safeguard**: Ngăn chặn người dùng tự vô hiệu hóa chính mình hoặc vô hiệu hóa tài khoản quản trị viên cuối cùng trong hệ thống.

### 📊 Bảng Điều Khiển Vận Hành (Operations Dashboard)
- Giám sát trạng thái kết nối thời gian thực: Mailbox (IMAP/SMTP), Shopify API, SOCKS5 Proxy, AI Gemini Provider.
- Thống kê phân bổ email theo trạng thái hàng đợi và tỷ lệ phân loại ý định khách hàng.

---

## 4. Kiến Trúc Hệ Thống

```
                                INTERNET (HTTPS 443 / HTTP 80)
                                              │
                                              ▼
                    VPS HOST NGINX REVERSE PROXY (103.147.123.63)
                                              │
                     proxy_pass: http://127.0.0.1:8090 (Host Loopback)
                                              ▼
┌────────────────────────────────────────────────────────────────────────────────────────┐
│ MẠNG CÁCH LY DOCKER BRIDGE: mail-agent-net                                             │
│                                                                                        │
│  ┌──────────────────────────────────────────────────────────────────────────────────┐  │
│  │ Container: mail-agent-api (FastAPI + React SPA Static Build)                     │  │
│  │ - Phục vụ RESTful API & Static UI Assets                                        │  │
│  │ - Quản lý Auth, Store Wizard, Email Queue, Draft Review, SSE Realtime            │  │
│  │ - Health Probes (/health/live, /health/ready)                                     │  │
│  └───────────────────┬──────────────────────────────────────────────────────────────┘  │
│                      │                                                                 │
│                      ├──────────────────────────┐                                      │
│                      ▼                          ▼                                      │
│  ┌──────────────────────────────────┐ ┌──────────────────────────────────────────────┐ │
│  │ Container: mail-agent-db         │ │ Container: mail-agent-worker                 │ │
│  │ - PostgreSQL 16                  │ │ - IMAP IDLE Listener & Ingestion Service     │ │
│  │ - Port nội bộ: 5432              │ │ - 5-Minute Reconciliation Scheduler          │ │
│  │ - KHÔNG map port ra VPS Host     │ │ - AI Classification & Draft Generator        │ │
│  │ - Volume dữ liệu bền vững        │ │ - 120-Day Retention Cleanup Task             │ │
│  └──────────────────────────────────┘ └──────────────────┬───────────────────────────┘ │
└──────────────────────────────────────────────────────────┼─────────────────────────────┘
                                                           │
                        ┌──────────────────────────────────┴──────────────────┐
                        │                                                     │
                        ▼                                                     ▼
     ┌──────────────────────────────────────┐             ┌──────────────────────────────────────┐
     │ Dịch Vụ Mailserver Trên VPS Host     │             │ Mạng Internet Ngoài (Qua Proxy)      │
     │ - docker-mailserver                  │             │ - Fail-Closed SOCKS5 Proxy           │
     │ - IMAP (993) / SMTP (587 TLS)        │             │ - Shopify Admin GraphQL / REST API   │
     │ - Hòm thư: support@wrydeco.com...   │             │ - Google Vertex AI Gemini API        │
     └──────────────────────────────────────┘             └──────────────────────────────────────┘
```

---

## 5. Công Nghệ Sử Dụng (Tech Stack)

### Backend
- **Ngôn ngữ & Runtime**: Python 3.12, Quản lý gói bằng `uv` / `pip`.
- **Framework**: FastAPI (Asynchronous REST API).
- **Cơ sở dữ liệu & ORM**: PostgreSQL 16, SQLAlchemy 2 (Async), Alembic (Migrations), AsyncPG connection pool.
- **Bảo mật & Mã hóa**: Argon2id (`argon2-cffi`), Cryptography (`AES-GCM-256 Envelope Encryption`), Secrets.
- **Giao thức Mạng & Email**: `aioimaplib`, `aiosmtplib`, `httpx` (hỗ trợ SOCKS5 proxy via `httpx-socks`).
- **Trí tuệ nhân tạo**: Google Gemini API / Vertex AI Gemini 1.5 Pro/Flash.

### Frontend
- **Framework**: React 18, TypeScript, Vite.
- **Styling**: Tailwind CSS v4, Atlassian Design System (ADS) Semantic Design Tokens (`light mode V1`).
- **Icons & UI Utilities**: Lucide React, Server-Sent Events (SSE) cho cập nhật realtime trạng thái queue.

### DevOps & Triển Khai
- **Containerization**: Docker & Docker Compose (`compose.yaml`).
- **Reverse Proxy & SSL**: Nginx (Host-level), Let's Encrypt Certbot SSL.
- **Workflow tự động**: Python script (`deploy.py`) đóng gói, upload SFTP và thực thi lệnh từ xa an toàn qua SSH.

---

## 6. Cấu Trúc Thư Mục Codebase

```text
auto-reply-mail/
├── backend/
│   ├── alembic/                      # Database migration scripts (Alembic)
│   ├── app/
│   │   ├── auth/                     # Xác thực, Argon2id, Session manager, RBAC
│   │   ├── cli/                      # CLI tools: bootstrap-admin, retention-cleanup
│   │   ├── core/                     # Config pydantic-settings, database session, logging
│   │   ├── crypto/                   # Envelope encryption AES-GCM-256
│   │   ├── db/                       # SQLAlchemy models & schema definitions
│   │   ├── delivery/                 # SMTP Delivery service, Idempotency check
│   │   ├── draft/                    # Sinh nháp AI, prompt templates, versioning
│   │   ├── ingestion/                # IMAP IDLE listener, 5-min reconciliation
│   │   ├── mail/                     # IMAP/SMTP low-level client handlers
│   │   ├── proxy/                    # SOCKS5 proxy transport, Fail-closed client
│   │   ├── queue/                    # Transactional row-locked queue logic
│   │   ├── retention/                # Dọn dẹp dữ liệu 120 ngày
│   │   ├── shopify/                  # Shopify API client, Order lookup, Policy sync
│   │   ├── store/                    # Quản lý Store Profile, Setup Wizard API
│   │   ├── system/                   # Health check endpoints, Diagnostics
│   │   ├── workers/                  # Worker tiến trình nền (Background workers)
│   │   └── main.py                   # FastAPI Application Entrypoint
│   └── tests/                        # Backend unit & integration test suites
├── frontend/
│   ├── src/
│   │   ├── components/ui/            # Button, Input, Modal, Badge, Banner (ADS tokenized)
│   │   ├── features/
│   │   │   ├── draft/                # DraftReviewWorkspace, DiffViewer, SendConfirmModal
│   │   │   └── stores/               # SetupWizard, StoreManagement
│   │   ├── layouts/                  # Header, Sidebar (Navigation)
│   │   ├── pages/                    # LoginPage, MailQueuePage, OperationsDashboardPage, UsersPage
│   │   ├── styles/                   # ads-tokens.css, tailwind-theme.css, globals.css
│   │   ├── App.tsx                   # React root router & application shell
│   │   └── main.tsx
│   ├── package.json
│   └── vite.config.ts
├── deploy/
│   ├── nginx/
│   │   └── mail-agent.conf           # Cấu hình Nginx reverse proxy cho domain
│   ├── scripts/
│   │   ├── backup.sh                 # Kịch bản sao lưu PostgreSQL hàng ngày
│   │   └── restore.sh                # Kịch bản khôi phục database
│   └── RUNBOOK.md                    # Tài liệu cẩm nang vận hành chi tiết
├── docs/
│   ├── plan-build-mail-agent.md      # Tài liệu đặc tả kỹ thuật chuẩn của dự án
│   ├── BAO_CAO_TRIEN_KHAI_MAIL_AGENT.md # Báo cáo chi tiết quá trình triển khai & kết quả
│   └── QUY_TRINH_DEPLOY_VPS.md       # Hướng dẫn chi tiết quy trình deploy tự động
├── tests/
│   ├── adversarial/                  # Bộ test kiểm tra tình huống biên & tấn công giả lập
│   └── e2e/                          # Bộ test E2E 4 Tiers & Playwright UI testing
├── compose.yaml                      # Docker compose production definition
├── deploy.cmd                        # Lệnh 1-click deploy dành cho Windows CMD
├── deploy.ps1                        # Lệnh 1-click deploy dành cho PowerShell
├── deploy.py                         # Trình điều khiển deploy tự động A-Z bằng Python
├── pyproject.toml                    # Cấu hình Python project & dependencies
└── README.md                         # Tài liệu hướng dẫn tổng quan dự án
```

---

## 7. Hướng Dẫn Cài Đặt & Chạy Cục Bộ (Local Development)

### Yêu Cầu Tiên Quyết
- **Python**: Phiên bản `>= 3.12`
- **Node.js**: Phiên bản `>= 20.x` và `npm`
- **Docker & Docker Compose** (để chạy PostgreSQL local)

### Bước 1: Clone Kho Mã Nguồn & Cài Đặt Thư Viện

```bash
git clone https://github.com/codevcn/auto-reply-mail-agent.git
cd auto-reply-mail-agent

# Cài đặt Python dependencies
uv pip install -e .
# Hoặc dùng pip: pip install -e .

# Cài đặt Frontend dependencies
cd frontend
npm install
cd ..
```

### Bước 2: Thiết Lập Biến Môi Trường (`.env`)

Sao chép file `.env.example` thành `.env` và điền các thông tin:

```bash
cp .env.example .env
```

Các biến môi trường quan trọng:
```ini
ENVIRONMENT=development
DATABASE_URL=postgresql+asyncpg://mailagent:your_secure_password@localhost:5432/mailagent_db
SECRET_KEY=generate_a_random_32_bytes_hex_string
ENCRYPTION_MASTER_KEY=generate_another_random_32_bytes_hex_key
GEMINI_API_KEY=your_gemini_api_key_here
```

### Bước 3: Khởi Chạy Cơ Sở Dữ Liệu & Migrations

```bash
# Chạy database PostgreSQL local
docker run --name mail-agent-local-db -e POSTGRES_USER=mailagent -e POSTGRES_PASSWORD=your_secure_password -e POSTGRES_DB=mailagent_db -p 5432:5432 -d postgres:16-alpine

# Chạy migration nâng cấp DB schema
alembic upgrade head

# Khởi tạo tài khoản quản trị viên đầu tiên
python -m backend.app.cli bootstrap-admin --email admin@example.com --password YourStrongPassword123!
```

### Bước 4: Khởi Chạy Hệ Thống

**Cửa sổ 1: Backend API**
```bash
uvicorn backend.app.main:app --reload --host 0.0.0.0 --port 8090
```

**Cửa sổ 2: Worker Tiến Trình Nền (IMAP Ingestion + Queue)**
```bash
python -m backend.app.workers.main
```

**Cửa sổ 3: Frontend Web Development Server**
```bash
cd frontend
npm run dev
```

Truy cập giao diện tại: `http://localhost:5173` (được proxy tới backend tại port `8090`).

---

## 8. Quy Trình Triển Khai 1-Click Lên VPS (Automated Deployment)

Dự án trang bị workflow tự động hóa triển khai **"1-Click Deployment"** từ máy cá nhân lên VPS `103.147.123.63` (`mail-agent.wrydeco.com`) mà không cần thao tác thủ công.

### Cách Thực Thi

Tại thư mục gốc dự án trên máy tính của bạn, chỉ cần chạy **đúng 1 lệnh**:

- **Trên Windows Command Prompt:**
  ```cmd
  deploy.cmd
  ```
- **Trên Windows PowerShell:**
  ```powershell
  .\deploy.ps1
  ```
- **Hoặc chạy trực tiếp với Python:**
  ```bash
  python deploy.py
  ```

### Quy Trình Tự Động Hóa 7 Bước Của `deploy.py`

```text
[BƯỚC 1/7] Kiểm tra kết nối SSH tới VPS 103.147.123.63...
[BƯỚC 2/7] Biên dịch Frontend SPA (npm run build)...
[BƯỚC 3/7] Đóng gói mã nguồn (Loại trừ node_modules, .venv, git history)...
[BƯỚC 4/7] Tải gói mã nguồn lên VPS qua giao thức SFTP an toàn...
[BƯỚC 5/7] Thiết lập cấu hình Host Nginx & Cấp chứng chỉ SSL Let's Encrypt...
[BƯỚC 6/7] Khởi tạo Docker Network & Khởi chạy Docker Compose (API, Worker, DB)...
[BƯỚC 7/7] Thực thi Alembic Migrations & Kiểm tra sức khỏe hệ thống (Health Check)...
```

Sau khi hoàn tất, hệ thống tự động sẵn sàng tại: **[https://mail-agent.wrydeco.com](https://mail-agent.wrydeco.com)**.

---

## 9. Kiểm Thử & Đảm Bảo Chất Lượng (Testing & QA)

Dự án sở hữu bộ kiểm thử đa tầng bao phủ toàn diện từ unit test, adversarial test cho đến E2E UI Playwright:

### 1. Chạy Toàn Bộ Test Suite Backend
```bash
pytest backend/tests -v
```

### 2. Chạy Bộ Test Tình Huống Biên & Đối Kháng (Adversarial Tests)
Kiểm tra khả năng chịu lỗi, proxy fail-closed, xung đột tài khoản, xử lý mail hỏng:
```bash
pytest tests/adversarial -v
```

### 3. Chạy Toàn Bộ Test E2E 4 Tiers
Bộ kiểm thử tích hợp 4 tầng (Tier 1: Features, Tier 2: Boundary, Tier 3: Pairwise, Tier 4: Real-world Scenarios):
```bash
python tests/e2e/run_e2e_tests.py
```

### 4. Chạy Kiểm Thử Giao Diện Người Dùng Bằng Playwright
```bash
pytest tests/e2e/ui_playwright -v
```

---

## 10. Vận Hành, Bảo Trì & Sao Lưu (Ops & Runbook)

### Lệnh Điều Khiển Dịch Vụ Trên VPS

Khi SSH vào máy chủ VPS (`ssh root@103.147.123.63`):

```bash
# Chuyển vào thư mục triển khai
cd /opt/mail-agent

# Xem trạng thái các container
docker compose ps

# Xem log thời gian thực của toàn bộ hệ thống
docker compose logs -f

# Xem riêng log của Worker (IMAP Ingestion / AI Draft)
docker compose logs -f mail-agent-worker

# Khởi động lại dịch vụ
docker compose restart
```

### Sao Lưu & Khôi Phục Dữ Liệu

- **Sao lưu tự động**: Script sao lưu cơ sở dữ liệu được tích hợp sẵn tại [deploy/scripts/backup.sh](file:///d:/D-Jobs/ae-B6/Shopify/tools/auto-reply-mail/deploy/scripts/backup.sh), tự động nén dump SQL và giữ bản lưu 14 ngày.
- **Khôi phục dữ liệu**:
  ```bash
  bash /opt/mail-agent/deploy/scripts/restore.sh /opt/mail-agent/backups/mailagent_backup_YYYYMMDD_HHMMSS.sql.gz
  ```

---

## 11. Tài Liệu Kèm Theo

Để tìm hiểu chi tiết hơn về các khía cạnh kỹ thuật, vui lòng tham khảo các tài liệu chuyên sâu trong thư mục `docs/` và `deploy/`:

- 📋 [**Báo Cáo Tổng Kết Triển Khai Toàn Diện**](docs/BAO_CAO_TRIEN_KHAI_MAIL_AGENT.md): Báo cáo chi tiết từng Phase từ 0 đến 7, các kết quả kiểm thử và số liệu nghiệm thu.
- 🚀 [**Quy Trình Triển Khai Tự Động VPS**](docs/QUY_TRINH_DEPLOY_VPS.md): Chi tiết kiến trúc phân tầng trên VPS, workflow 1-click deploy và các tiêu chuẩn an toàn mạng.
- 📖 [**Cẩm Nang Vận Hành Production (RUNBOOK.md)**](deploy/RUNBOOK.md): Hướng dẫn trực quan xử lý sự cố (troubleshooting), giám sát logs, cấu hình cron và xử lý cảnh báo.
- 📐 [**Kế Hoạch & Thiết Kế Kỹ Thuật Dự Án**](docs/plan-build-mail-agent.md): Bản tài liệu kiến trúc kỹ thuật chuẩn của dự án.

---

**Shopify Mail Agent Team** • Phiên bản: `1.0.0` • Trạng thái: `Production Ready` 🚀
