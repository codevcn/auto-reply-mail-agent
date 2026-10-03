# Original User Request

## Initial Request — 2026-10-02T20:10:20Z

# Teamwork Project Prompt — Launched

Triển khai hoàn chỉnh hệ thống Mail Agent (Auto-reply Mail cho các cửa hàng Shopify) theo đúng tài liệu đặc tả chuẩn duy nhất `plan-build-mail-agent.md`, thực thi tuần tự từ Phase 0 đến Phase 7, kiểm thử chặt chẽ acceptance criteria từng phase (kết hợp Playwright MCP cho E2E UI testing) và lập báo cáo tổng kết chi tiết dạng Markdown.

Working directory: `d:\D-Jobs\ae-B6\Shopify\tools\auto-reply-mail`
Integrity mode: development

## YÊU CẦU CỐT LÕI: BÁM SÁT 100% PLAN TRIỂN KHAI
- **Nguồn chuẩn duy nhất**: File `plan-build-mail-agent.md` là nguồn yêu cầu và thiết kế chuẩn DUY NHẤT của dự án. Tuyệt đối KHÔNG sử dụng file `archive/plan-build-mail-agent copy.md`.
- **Tuyệt đối không tự ý thay đổi quyết định**: Bám sát mọi quyết định kiến trúc, công nghệ, data model, quy tắc luồng và bảo mật đã được chốt trong plan. Nếu gặp mâu thuẫn hoặc phát hiện thiếu thông tin, phải DỪNG LẠI VÀ HỎI NGƯỜI DÙNG, không được tự ý suy diễn hoặc thay đổi.
- **Tuân thủ quy trình tuần tự theo Phase**: Triển khai chính xác theo từng phase trong Section 25, bắt đầu từ Phase 0. Sau mỗi phase, phải chạy toàn bộ acceptance criteria và test tương ứng đạt 100% trước khi chuyển sang phase tiếp theo.

## Requirements

### R1. Triển khai kiến trúc hệ thống chuẩn theo Section 5, 6, 7
- **Backend**: Python 3.12, FastAPI, SQLAlchemy 2, Alembic, PostgreSQL 16 (chạy Docker độc lập, không expose công khai và không xung đột port 5432 trên VPS host), Argon2id, Opaque session server-side trong DB, Transactional row-locked queue (`FOR UPDATE SKIP LOCKED`).
- **Frontend**: React 18, TypeScript, Vite, Tailwind CSS v4, sử dụng hệ thống ADS (Atlassian Design System) semantic tokens, light mode V1 (sẵn sàng cho dark theme), Server-Sent Events (SSE).
- **Cấu trúc thư mục**: Tuân thủ layout chuẩn tại Section 6 (`backend/`, `frontend/`, `deploy/`, `compose.yaml`).

### R2. Thực thi tuần tự từng Phase (Section 25) từ Phase 0 đến Phase 7
- **Phase 0**: Security & Repo hygiene (rotate secret cũ, xóa credentials plaintext, cấu hình `.env.example`, secret redaction logging, setup linter/test).
- **Phase 1**: Foundation, DB & Auth (FastAPI + Postgres, Alembic, User/Session/Admin role V1, user management, lock out safeguards, UI shell ADS tokens).
- **Phase 2**: Store, Proxy & Shopify (Setup Wizard, Envelope encryption, Proxy profile & test, Proxy-enforced fail-closed Shopify transport, Client credentials token manager).
- **Phase 3**: Mail Ingestion (IMAP/SMTP profile test, baseline UID, IMAP IDLE listener, 5-minute reconciliation, Deduplication).
- **Phase 4**: Attachment & AI Classification (Safe MIME parsing, Vertex AI Gemini provider behind `AIProvider`, multi-dimensional classification, Spam/Manual review routing).
- **Phase 5**: Shopify Enrichment & Policies (60-day order lookup, product search, 6 Shopify policy types sync via proxy, stale draft warning).
- **Phase 6**: Draft Review & Delivery (Draft editor, immutable versioning, Human Approval flow, SMTP STARTTLS delivery, Sent-folder append, Idempotency key).
- **Phase 7**: Operations & Deployment (Dashboard, 120-day retention cron, Docker compose trên VPS, Host Nginx reverse proxy `mail-agent.wrydeco.com:8090`, Certbot SSL).

### R3. Ranh giới an toàn và quy tắc bất biến (Critical Invariants)
- **Zero autonomous sending**: 100% email phản hồi phải được người dùng đăng nhập bấm `Approve & Send` thủ công.
- **Fail-closed Shopify Proxy**: 100% request gọi sang Shopify Admin API (bao gồm lấy token) bắt buộc phải qua SOCKS5 Proxy đã cấu hình (sử dụng thông tin trong `proxy-credentials.md`). Tuyệt đối không fallback ra kết nối trực tiếp.
- **Không nhân bản nội dung email vào PostgreSQL**: DB chỉ lưu metadata, reference UID, draft, snapshots và audit log. Email body và file đính kèm đọc trực tiếp từ mailserver khi cần.
- **Bảo vệ tuyệt đối hạ tầng VPS hiện tại**: Không làm gián đoạn, restart hay sửa đổi `docker-mailserver`, SnappyMail (`webmail.wrydeco.com`), Roundcube hay các app khác trên VPS `103.147.123.63`.
- **Hạn lưu trữ (Retention)**: Tối đa 120 ngày cho workflow metadata, draft và audit log.

### R4. Kiểm thử đa tầng & Tích hợp Playwright MCP
- Chạy unit tests và integration tests tương ứng sau mỗi phase.
- Sử dụng **Playwright MCP** để kiểm thử E2E giao diện người dùng (Login, Setup Wizard, Duyệt draft, Approve & Send, Quản lý User) và chụp ảnh màn hình nghiệm thu.

### R5. Báo cáo tổng kết triển khai
- Sau khi hoàn thành toàn bộ hệ thống và nghiệm thu, biên soạn một tài liệu Markdown chi tiết (`BAO_CAO_TRIEN_KHAI_MAIL_AGENT.md`) tóm tắt quá trình thực hiện, kết quả kiểm thử từng phase, cấu hình môi trường và hướng dẫn vận hành.

## Acceptance Criteria

### Phase 0
- [ ] Bám sát 100% checklist Phase 0 của plan; không còn secret plaintext nào trong repository.
- [ ] Log hệ thống đảm bảo redact toàn bộ token, mật khẩu và secret.
- [ ] Khung mã nguồn, cấu hình linter và test framework sẵn sàng.

### Phase 1
- [ ] Khởi tạo thành công database PostgreSQL qua Alembic migrations.
- [ ] Tạo được tài khoản admin đầu tiên qua CLI bootstrap command.
- [ ] Đăng nhập/đăng xuất với session server-side an toàn (HttpOnly, Secure cookie).
- [ ] Quản lý user: chặn self-disable và chặn disable user cuối cùng.
- [ ] UI shell React + Tailwind v4 hiển thị đúng ADS tokens.

### Phase 2
- [ ] Setup Wizard cho phép nhập cấu hình Store Profile từng bước.
- [ ] Test kết nối Proxy SOCKS5 thành công (thông tin từ `proxy-credentials.md`).
- [ ] Kiểm thử chứng minh request Shopify bị chặn (fail-closed) nếu tắt proxy.
- [ ] Lấy và tự động gia hạn Shopify Access Token qua Client Credentials thành công qua Proxy.

### Phase 3
- [ ] Test kết nối IMAP và SMTP thành công với hòm thư trên VPS (ví dụ `support@wrydeco.com`).
- [ ] Thiết lập baseline UID chuẩn xác khi kích hoạt store; không import mail cũ.
- [ ] IMAP IDLE và tiến trình Reconcile 5 phút phát hiện mail mới near-real-time và không nhân bản mail.

### Phase 4
- [ ] Trích xuất và kiểm tra an toàn các file đính kèm hợp lệ (JPEG, PNG, WebP, PDF, TXT, DOCX, XLSX).
- [ ] File đính kèm lỗi/quá dung lượng tự động chuyển sang `Needs manual review`, không tạo draft.
- [ ] Vertex AI Gemini phân loại chuẩn xác 3 chiều: Spam status, Customer/Order status, Intent.
- [ ] Thư spam được dán nhãn và ẩn khỏi hàng duyệt (không xóa trên mailserver).

### Phase 5
- [ ] Tra cứu đơn hàng Shopify 60 ngày gần nhất qua proxy và gắn cờ trạng thái chính xác.
- [ ] Tra cứu thông tin sản phẩm trực tiếp từ Shopify cho các yêu cầu `product_inquiry`.
- [ ] Đồng bộ thành công 6 loại policy từ Shopify qua proxy; kích hoạt cảnh báo nếu draft dùng policy cũ.

### Phase 6
- [ ] Draft được sinh tự động cho các intent hợp lệ (`product_inquiry`, `order_support`, `complaint`, `return_or_refund`).
- [ ] Draft editor cho phép chỉnh sửa nội dung, lưu phiên bản mới (immutable versioning) hoặc bấm Regenerate.
- [ ] Bấm `Approve & Send` thực hiện gửi mail qua SMTP STARTTLS với đúng header thread (`In-Reply-To`, `References`).
- [ ] Copy mail gửi thành công vào thư mục `Sent` trên IMAP.
- [ ] Idempotency key ngăn chặn triệt để gửi lặp do click đúp hoặc worker retry.

### Phase 7 & Deployment
- [ ] Dashboard hiển thị trạng thái sức khỏe kết nối (Mailbox, Shopify, Proxy, AI) và thống kê queue.
- [ ] Cron dọn dẹp dữ liệu quá 120 ngày hoạt động đúng.
- [ ] Ứng dụng chạy qua Docker Compose trên VPS, truy cập HTTPS an toàn tại `https://mail-agent.wrydeco.com` qua Host Nginx.
- [ ] Dịch vụ mailserver và webmail SnappyMail/Roundcube hiện có trên VPS không bị gián đoạn hay khởi động lại.
- [ ] Playwright MCP hoàn thành toàn bộ kịch bản E2E trên trình duyệt thật và chụp ảnh màn hình lưu trữ.
- [ ] File báo cáo tổng kết Markdown được tạo đầy đủ.
