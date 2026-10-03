"""Policy HTML sanitizer, text normalizer, and SHA-256 hash generator.

INVARIANTS:
- R-17: 6 Store Policies sync, malicious HTML sanitization, clean text normalization,
        and standard 64-hex character SHA-256 content hashing.
"""

from __future__ import annotations

import hashlib
import html
import re


def sanitize_policy_html(raw_html: str | None) -> str:
    """Sanitizes raw HTML policy body by stripping script, iframe, styles, and dangerous event handlers.

    Preserves safe formatting structure for web display while removing security risks.
    """
    if not raw_html:
        return ""

    content = raw_html

    # Strip dangerous executable container tags and their content
    dangerous_tags = ["script", "iframe", "style", "object", "embed", "noscript", "form", "svg"]
    for tag in dangerous_tags:
        pattern = re.compile(rf"<{tag}[^>]*>.*?</{tag}>", re.IGNORECASE | re.DOTALL)
        content = pattern.sub("", content)
        # Also strip self-closing or unclosed single tags
        single_pattern = re.compile(rf"<{tag}[^>]*\/?>", re.IGNORECASE)
        content = single_pattern.sub("", content)

    # Strip inline event handlers (e.g. onload, onerror, onclick, onmouseover)
    handler_pattern = re.compile(r"\s+on\w+\s*=\s*(?:\"[^\"]*\"|'[^']*'|[^\s>]+)", re.IGNORECASE)
    content = handler_pattern.sub("", content)

    # Strip javascript: pseudo-protocols in href or src
    js_protocol_pattern = re.compile(r"""(?:href|src)\s*=\s*["']?\s*javascript:[^"'>\s]*["']?""", re.IGNORECASE)
    content = js_protocol_pattern.sub("", content)

    return content.strip()


def normalize_policy_text(text_or_html: str | None) -> str:
    """Converts sanitized HTML or rich text to clean, normalized plain text/markdown.

    Used as the legal ground truth for AI drafting prompts and content hash calculation.
    """
    if not text_or_html:
        return ""

    text = text_or_html

    # First sanitize out scripts/styles
    text = sanitize_policy_html(text)

    # Structural conversions
    # Headings -> Markdown headings
    text = re.sub(r"<h[1-6][^>]*>(.*?)</h[1-6]>", r"\n\n### \1\n\n", text, flags=re.IGNORECASE | re.DOTALL)

    # List items -> bullet points
    text = re.sub(r"<li[^>]*>", r"\n- ", text, flags=re.IGNORECASE)

    # Links -> Text (URL)
    text = re.sub(r"""<a\s+[^>]*href=["']([^"']*)["'][^>]*>(.*?)</a>""", r"\2 (\1)", text, flags=re.IGNORECASE | re.DOTALL)

    # Line breaks and block terminators -> newlines
    text = re.sub(r"<br\s*/?>", r"\n", text, flags=re.IGNORECASE)
    text = re.sub(r"</(?:p|div|tr|li|ul|ol|table|blockquote)>", r"\n", text, flags=re.IGNORECASE)

    # Strip all remaining HTML tags
    text = re.sub(r"<[^>]+>", "", text)

    # Unescape HTML entities (&amp;, &nbsp;, &lt;, etc.)
    text = html.unescape(text)

    # Normalize non-breaking spaces and whitespace
    text = text.replace("\xa0", " ")

    # Collapse horizontal whitespace
    text = re.sub(r"[ \t]+", " ", text)

    # Normalize newlines and strip leading/trailing spaces on each line
    lines = [line.strip() for line in text.splitlines()]
    text = "\n".join(lines)

    # Collapse multiple consecutive newlines (max 2 consecutive)
    text = re.sub(r"\n{3,}", "\n\n", text)

    return text.strip()


def compute_content_hash(text: str | None) -> str:
    """Computes a strict 64-hex character SHA-256 hash over normalized policy text.

    For empty content, returns the standard empty SHA-256 hash.
    """
    normalized = (text or "").strip()
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest().lower()
