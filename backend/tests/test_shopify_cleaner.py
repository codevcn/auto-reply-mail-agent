"""Tests for Policy HTML Sanitization, Text Normalization, and SHA-256 Hashing.

INVARIANT R-17: 6 Store Policies sync, malicious HTML sanitization, clean text normalization,
and standard 64-hex character SHA-256 content hashing.
"""

from __future__ import annotations

from app.shopify.cleaner import compute_content_hash, normalize_policy_text, sanitize_policy_html


def test_sanitize_policy_html_strips_scripts_and_iframes() -> None:
    raw_html = """
    <div>
        <h2>Refund Policy</h2>
        <script>alert("malicious script");</script>
        <p>Items can be returned within 30 days.</p>
        <iframe src="http://evil.com"></iframe>
        <style>body { display: none; }</style>
    </div>
    """
    sanitized = sanitize_policy_html(raw_html)
    assert "<script>" not in sanitized
    assert "alert(" not in sanitized
    assert "<iframe" not in sanitized
    assert "<style" not in sanitized
    assert "Refund Policy" in sanitized
    assert "Items can be returned within 30 days." in sanitized


def test_sanitize_policy_html_strips_inline_handlers() -> None:
    raw_html = '<a href="https://example.com" onclick="stealCookies()" onmouseover="run()">Terms</a>'
    sanitized = sanitize_policy_html(raw_html)
    assert "onclick" not in sanitized
    assert "onmouseover" not in sanitized
    assert "stealCookies" not in sanitized
    assert "Terms" in sanitized


def test_normalize_policy_text_converts_html_to_clean_markdown() -> None:
    raw_html = """
    <h1>Shipping Information</h1>
    <p>Orders are dispatched within <strong>2-3 business days</strong>.</p>
    <ul>
        <li>Standard Shipping: $5.99</li>
        <li>Express Shipping: $14.99</li>
    </ul>
    <p>For inquiries, visit <a href="https://wrydeco.com/help">Help Center</a>.</p>
    """
    text = normalize_policy_text(raw_html)
    assert "### Shipping Information" in text
    assert "Orders are dispatched within 2-3 business days." in text
    assert "- Standard Shipping: $5.99" in text
    assert "- Express Shipping: $14.99" in text
    assert "Help Center (https://wrydeco.com/help)" in text
    assert "<" not in text and ">" not in text


def test_normalize_policy_text_unescapes_entities_and_collapses_whitespace() -> None:
    raw_html = "<p>Terms &amp; Conditions &nbsp; &nbsp; &copy; 2026</p>"
    text = normalize_policy_text(raw_html)
    assert text == "Terms & Conditions © 2026"


def test_compute_content_hash_returns_64_hex_chars() -> None:
    text = "Items can be returned within 30 days of delivery."
    content_hash = compute_content_hash(text)
    assert len(content_hash) == 64
    assert all(c in "0123456789abcdef" for c in content_hash)


def test_compute_content_hash_empty_string_standard_sha256() -> None:
    empty_hash = compute_content_hash("")
    # Standard SHA-256 hash of empty byte sequence
    assert empty_hash == "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"
