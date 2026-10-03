"""Safe MIME Multipart Email Parser.

INVARIANT R-04: Parsing operates purely in-memory via RFC 822 streaming.
No raw email bodies or binary payloads are persisted to PostgreSQL.
"""

from __future__ import annotations

import email
import html
import re
from email.policy import default

from app.attachment.schemas import ParsedEmailContent, ParsedEmailPart
from app.attachment.security import sanitize_filename


def sanitize_html(raw_html: str) -> str:
    """Sanitizes HTML content by stripping scripts, iframes, and active event handlers."""
    if not raw_html:
        return ""
    cleaned = re.sub(r"(?i)<script[^>]*>.*?</script>", "", raw_html, flags=re.DOTALL)
    cleaned = re.sub(r"(?i)<iframe[^>]*>.*?</iframe>", "", cleaned, flags=re.DOTALL)
    cleaned = re.sub(r"(?i)<object[^>]*>.*?</object>", "", cleaned, flags=re.DOTALL)
    cleaned = re.sub(r"(?i)<embed[^>]*>.*?</embed>", "", cleaned, flags=re.DOTALL)
    cleaned = re.sub(r"(?i)onload|onerror|onclick|onmouseover|javascript:", "", cleaned)
    return cleaned.strip()


def decode_payload_str(payload: object, charset: str | None) -> str:
    """Safely decodes textual payload into a Unicode string."""
    if isinstance(payload, bytes):
        for enc in (charset, "utf-8", "cp1252", "latin-1"):
            if enc:
                try:
                    return payload.decode(enc)
                except Exception:  # noqa: S112
                    continue
        return payload.decode("utf-8", errors="replace")
    return str(payload) if payload else ""


class MimeEmailParser:
    """Parses raw email bytes into structured in-memory representation."""

    @staticmethod
    def parse_raw_message(raw_bytes: bytes) -> ParsedEmailContent:
        """Parses an RFC 822 / MIME raw email message in memory."""
        msg = email.message_from_bytes(raw_bytes, policy=default)

        subject = str(msg.get("Subject", "") or "(No Subject)")
        sender_header = str(msg.get("From", "") or "")
        recipient_header = str(msg.get("To", "") or "")

        # Extract sender name and email
        sender_name: str | None = None
        sender_email = sender_header
        if "<" in sender_header and ">" in sender_header:
            parts = sender_header.split("<", 1)
            sender_name = parts[0].strip().strip('"')
            sender_email = parts[1].split(">", 1)[0].strip()

        body_text = ""
        body_html = ""
        attachments: list[ParsedEmailPart] = []
        att_counter = 1

        if msg.is_multipart():
            for part in msg.walk():
                if part.is_multipart():
                    continue

                content_type = part.get_content_type().lower()
                content_disposition = str(part.get("Content-Disposition", "")).lower()
                filename = part.get_filename()
                content_id = part.get("Content-ID")
                if content_id:
                    content_id = str(content_id).strip("<>")

                # Determine if this part is an attachment
                is_attachment = False
                is_inline = False

                if "attachment" in content_disposition or filename:
                    is_attachment = True
                elif "inline" in content_disposition and (
                    content_type.startswith("image/") or content_type.startswith("application/")
                ):
                    is_attachment = True
                    is_inline = True

                if is_attachment:
                    try:
                        raw_payload = part.get_payload(decode=True)
                    except Exception:
                        raw_payload = b""

                    payload_bytes = raw_payload if isinstance(raw_payload, bytes) else b""
                    safe_name = sanitize_filename(filename, default_index=att_counter)
                    att_counter += 1

                    attachments.append(
                        ParsedEmailPart(
                            filename=safe_name,
                            content_type=content_type,
                            size_bytes=len(payload_bytes),
                            raw_bytes=payload_bytes,
                            is_inline=is_inline,
                            content_id=content_id,
                        )
                    )

                else:
                    # Body part
                    if content_type == "text/plain" and not body_text:
                        raw_payload = part.get_payload(decode=True)
                        body_text = decode_payload_str(raw_payload, part.get_content_charset())
                    elif content_type == "text/html" and not body_html:
                        raw_payload = part.get_payload(decode=True)
                        body_html = decode_payload_str(raw_payload, part.get_content_charset())
        else:
            # Single-part message
            content_type = msg.get_content_type().lower()
            raw_payload = msg.get_payload(decode=True)
            text_content = decode_payload_str(raw_payload, msg.get_content_charset())
            if content_type == "text/html":
                body_html = text_content
            else:
                body_text = text_content

        sanitized_html = sanitize_html(body_html) if body_html else html.escape(body_text)

        return ParsedEmailContent(
            subject=subject,
            sender_email=sender_email,
            sender_name=sender_name,
            recipient_email=recipient_header,
            body_text=body_text.strip(),
            body_html_sanitized=sanitized_html,
            attachments=attachments,
            has_attachments=len(attachments) > 0,
            attachment_count=len(attachments),
        )
