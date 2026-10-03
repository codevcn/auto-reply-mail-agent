"""Unit and Integration Tests for Attachment Processing & Safe MIME Extraction.

Requirements: Invariants R-04, R-20, R-21; Sections 12 & 13.
"""

from __future__ import annotations

import datetime
import io
import uuid
import zipfile

import pytest
from app.attachment.constants import (
    ERR_ATTACHMENT_CORRUPT,
    ERR_ATTACHMENT_COUNT_EXCEEDED,
    ERR_ATTACHMENT_TOO_LARGE,
    ERR_ENCRYPTED_OR_PASSWORD_PROTECTED,
    ERR_TOTAL_ATTACHMENT_SIZE_EXCEEDED,
    ERR_UNSUPPORTED_ATTACHMENT_TYPE,
)
from app.attachment.schemas import ParsedEmailPart
from app.attachment.security import (
    extract_safe_text,
    is_encrypted_or_password_protected,
    sanitize_filename,
)
from app.attachment.service import AttachmentService
from app.attachment.sniffing import sniff_mime_type
from app.db.models.email import EmailAttachmentMetadata, IncomingEmail
from app.db.models.store import Mailbox, StoreProfile
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession


def test_zero_raw_bytes_in_attachment_metadata_table():
    """INVARIANT R-04: email_attachment_metadata MUST NOT have raw binary content columns."""
    columns = [c.name for c in EmailAttachmentMetadata.__table__.columns]
    forbidden_columns = [
        "raw_bytes",
        "data",
        "blob",
        "content_bytes",
        "binary_payload",
        "file_data",
    ]
    for col in forbidden_columns:
        assert col not in columns, f"Column '{col}' violates Invariant R-04!"


def test_filename_sanitization_path_traversal():
    """Sanitizes dangerous path traversal payloads in attachment filenames."""
    assert sanitize_filename("../../etc/passwd") == "passwd"
    assert sanitize_filename("C:\\Windows\\System32\\cmd.exe") == "cmd.exe"
    assert sanitize_filename(None, default_index=2) == "attachment_2.bin"
    assert sanitize_filename("   ") == "attachment_1.bin"


def test_valid_attachments_magic_bytes_all_types():
    """TC-AI-01: Validates correct magic bytes identification for supported formats."""
    # 1. JPEG
    mime, err = sniff_mime_type(b"\xff\xd8\xff\xe0\x00\x10JFIF", "receipt.jpg")
    assert mime == "image/jpeg"
    assert err is None

    # 2. PNG
    mime, err = sniff_mime_type(b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR", "image.png")
    assert mime == "image/png"
    assert err is None

    # 3. WebP
    mime, err = sniff_mime_type(b"RIFF\x20\x00\x00\x00WEBPVP8 ", "graphic.webp")
    assert mime == "image/webp"
    assert err is None

    # 4. PDF
    mime, err = sniff_mime_type(b"%PDF-1.4\n1 0 obj\n<<>>\nendobj", "invoice.pdf")
    assert mime == "application/pdf"
    assert err is None

    # 5. TXT
    mime, err = sniff_mime_type(b"Hello world, this is a plain text note.", "notes.txt")
    assert mime == "text/plain"
    assert err is None

    # 6. DOCX (Valid zip with word/document.xml)
    docx_buf = io.BytesIO()
    with zipfile.ZipFile(docx_buf, "w") as zf:
        zf.writestr("[Content_Types].xml", b"<Types></Types>")
        zf.writestr("word/document.xml", b"<w:document><w:body><w:p><w:r><w:t>Hello DOCX</w:t></w:r></w:p></w:body></w:document>")
    mime, err = sniff_mime_type(docx_buf.getvalue(), "document.docx")
    assert mime == "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    assert err is None

    # 7. XLSX (Valid zip with xl/workbook.xml)
    xlsx_buf = io.BytesIO()
    with zipfile.ZipFile(xlsx_buf, "w") as zf:
        zf.writestr("[Content_Types].xml", b"<Types></Types>")
        zf.writestr("xl/workbook.xml", b"<workbook></workbook>")
    mime, err = sniff_mime_type(xlsx_buf.getvalue(), "sheet.xlsx")
    assert mime == "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    assert err is None


def test_spoofed_executable_as_pdf_rejected():
    """TC-AI-03: Windows PE executable masquerading as PDF is rejected via magic bytes."""
    malware_bytes = b"MZ\x90\x00\x03\x00\x00\x00\x04\x00\x00\x00"
    mime, err = sniff_mime_type(malware_bytes, "invoice.pdf", declared_mime="application/pdf")
    assert mime is None
    assert err == ERR_UNSUPPORTED_ATTACHMENT_TYPE


def test_encrypted_password_protected_pdf_rejected():
    """TC-AI-04: Encrypted or password-protected PDF is detected and rejected."""
    encrypted_pdf = b"%PDF-1.4\n<< /Encrypt 2 0 R /Root 1 0 R >>\ntrailer\n<<>>\n%%EOF"
    assert is_encrypted_or_password_protected(encrypted_pdf) is True

    att = ParsedEmailPart(
        filename="confidential.pdf",
        content_type="application/pdf",
        size_bytes=len(encrypted_pdf),
        raw_bytes=encrypted_pdf,
    )
    res = AttachmentService.validate_attachments([att])
    assert res.is_valid is False
    assert res.error_code == ERR_ENCRYPTED_OR_PASSWORD_PROTECTED


def test_encrypted_office_document_rejected():
    """Encrypted Microsoft Office document in OLE CFBF container is detected and rejected."""
    ole_encrypted = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1\x00\x00\x00EncryptedPackage\x00\x00"
    assert is_encrypted_or_password_protected(ole_encrypted) is True


def test_macro_enabled_office_document_rejected():
    """Office document containing malicious VBA macro is rejected as unsupported."""
    docm_buf = io.BytesIO()
    with zipfile.ZipFile(docm_buf, "w") as zf:
        zf.writestr("[Content_Types].xml", b"<Types></Types>")
        zf.writestr("word/vbaProject.bin", b"Dangerous macro bytecode")
        zf.writestr("word/document.xml", b"<w:document></w:document>")

    mime, err = sniff_mime_type(docm_buf.getvalue(), "invoice.docx")
    assert mime is None
    assert err == ERR_UNSUPPORTED_ATTACHMENT_TYPE


def test_single_attachment_too_large_rejected():
    """TC-AI-02: Single attachment exceeding limit is rejected with ATTACHMENT_TOO_LARGE."""
    oversized_att = ParsedEmailPart(
        filename="video.pdf",
        content_type="application/pdf",
        size_bytes=10 * 1024 * 1024 + 1,
        raw_bytes=b"%PDF-1.4 mock content",
    )
    res = AttachmentService.validate_attachments([oversized_att], max_single_bytes=10 * 1024 * 1024)
    assert res.is_valid is False
    assert res.error_code == ERR_ATTACHMENT_TOO_LARGE


def test_total_attachment_size_exceeded_rejected():
    """Multiple attachments exceeding combined size limit are rejected."""
    att1 = ParsedEmailPart(
        filename="part1.pdf",
        content_type="application/pdf",
        size_bytes=6 * 1024 * 1024,
        raw_bytes=b"%PDF-1.4 part 1",
    )
    att2 = ParsedEmailPart(
        filename="part2.pdf",
        content_type="application/pdf",
        size_bytes=5 * 1024 * 1024,
        raw_bytes=b"%PDF-1.4 part 2",
    )
    # Total = 11MB, max total = 10MB
    res = AttachmentService.validate_attachments([att1, att2], max_total_bytes=10 * 1024 * 1024)
    assert res.is_valid is False
    assert res.error_code == ERR_TOTAL_ATTACHMENT_SIZE_EXCEEDED


def test_attachment_count_exceeded_rejected():
    """Exceeding max attachment count (e.g. >10) triggers ATTACHMENT_COUNT_EXCEEDED."""
    parts = [
        ParsedEmailPart(
            filename=f"file_{i}.txt",
            content_type="text/plain",
            size_bytes=10,
            raw_bytes=b"sample text",
        )
        for i in range(12)
    ]
    res = AttachmentService.validate_attachments(parts, max_count=10)
    assert res.is_valid is False
    assert res.error_code == ERR_ATTACHMENT_COUNT_EXCEEDED


def test_corrupted_empty_file_rejected():
    """Empty 0-byte file is marked ATTACHMENT_CORRUPT."""
    att = ParsedEmailPart(
        filename="empty.txt",
        content_type="text/plain",
        size_bytes=0,
        raw_bytes=b"",
    )
    res = AttachmentService.validate_attachments([att])
    assert res.is_valid is False
    assert res.error_code == ERR_ATTACHMENT_CORRUPT


def test_safe_text_extraction():
    """Safely extracts text from valid DOCX without running executable scripts."""
    docx_buf = io.BytesIO()
    with zipfile.ZipFile(docx_buf, "w") as zf:
        zf.writestr("[Content_Types].xml", b"<Types></Types>")
        zf.writestr(
            "word/document.xml",
            b"<w:document><w:body><w:p><w:t>Customer question about size 42 shoe</w:t></w:p></w:body></w:document>",
        )
    text = extract_safe_text(
        docx_buf.getvalue(),
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    )
    assert text == "Customer question about size 42 shoe"


@pytest.mark.asyncio
async def test_persist_attachment_metadata_stores_zero_blobs(db_session: AsyncSession):
    """Verifies that persist_attachment_metadata writes only metadata to PostgreSQL."""
    store = StoreProfile(
        id=uuid.uuid4(),
        name="Test Store",
        brand_name="TestBrand",
        public_domain="testbrand.com",
    )
    mailbox = Mailbox(
        id=uuid.uuid4(),
        store_profile_id=store.id,
        address="support@testbrand.com",
        imap_host="mail.testbrand.com",
        imap_port=993,
        imap_tls_mode="SSL_TLS",
        smtp_host="mail.testbrand.com",
        smtp_port=587,
        smtp_tls_mode="STARTTLS",
        encrypted_password="enc_password",
    )
    email_rec = IncomingEmail(
        id=uuid.uuid4(),
        store_profile_id=store.id,
        mailbox_id=mailbox.id,
        folder="INBOX",
        imap_uid=200,
        uidvalidity=1,
        sender_email="buyer@gmail.com",
        recipient_email="support@testbrand.com",
        subject="Invoice inquiry",
        received_at=datetime.datetime.now(datetime.UTC),
    )
    db_session.add(store)
    db_session.add(mailbox)
    db_session.add(email_rec)
    await db_session.commit()

    valid_part = ParsedEmailPart(
        filename="order_receipt.pdf",
        content_type="application/pdf",
        size_bytes=512,
        raw_bytes=b"%PDF-1.4\nsample pdf",
    )
    val_res = AttachmentService.validate_attachments([valid_part])
    records = await AttachmentService.persist_attachment_metadata(db_session, email_rec.id, val_res)

    assert len(records) == 1
    assert records[0].filename == "order_receipt.pdf"
    assert records[0].size_bytes == 512
    assert len(records[0].sha256_hash) == 64
    assert records[0].is_valid is True

    # Query DB directly to verify
    stmt = select(EmailAttachmentMetadata).where(EmailAttachmentMetadata.incoming_email_id == email_rec.id)
    persisted = (await db_session.execute(stmt)).scalars().all()
    assert len(persisted) == 1
    assert persisted[0].filename == "order_receipt.pdf"
