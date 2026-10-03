"""Mock AI Provider for deterministic offline testing and local development."""

from __future__ import annotations

import datetime
import re
import time

from app.ai.provider import AIProvider
from app.ai.schemas import (
    AITestConnectionResult,
    AIUsageMetadata,
    ClassificationResult,
    DraftGenerationInput,
    DraftResult,
    EmailClassificationInput,
    ExtractedEntities,
    IntentType,
    OrderFlags,
    OrderStatusType,
    StoreContext,
)

# Expanded Prompt Injection Indicators & Boundary Breakout Defenses (Phase 4 Hardening)
PROMPT_INJECTION_KEYWORDS: list[str] = [
    "SYSTEM OVERRIDE",
    "OVERRIDE SYSTEM",
    "OVERRIDE ALL RULES",
    "OVERRIDE RULES",
    "OVERRIDE ALL",
    "IGNORE PREVIOUS INSTRUCTIONS",
    "IGNORE ALL PREVIOUS INSTRUCTIONS",
    "IGNORE ALL PREVIOUS",
    "IGNORE ALL INSTRUCTIONS",
    "IGNORE PREVIOUS",
    "IGNORE ALL",
    "DISREGARD PREVIOUS INSTRUCTIONS",
    "DISREGARD ALL PREVIOUS INSTRUCTIONS",
    "DISREGARD ALL INSTRUCTIONS",
    "DISREGARD ALL PREVIOUS",
    "DISREGARD PREVIOUS",
    "DISREGARD ALL",
    "DISREGARD ANY",
    "FORGET PREVIOUS INSTRUCTIONS",
    "FORGET ALL PREVIOUS INSTRUCTIONS",
    "FORGET ALL INSTRUCTIONS",
    "FORGET PREVIOUS",
    "FORGET ALL",
    "NEW INSTRUCTIONS",
    "BYPASS RULES",
    "BYPASS ALL RULES",
    "BYPASS SYSTEM",
    "DEVELOPER MODE",
    "YOU ARE NOW IN",
    "YOU ARE NOW DAN",
    "JAILBREAK",
    "DAN MODE",
    "PROMPT INJECTION",
]

PROMPT_INJECTION_REGEXES: list[re.Pattern[str]] = [
    # Semantic action + scope + directive combinations (case-insensitive)
    re.compile(
        r"(?i)\b(ignore|disregard|bypass|forget|override)\b.*?\b(previous|all|above|system|prior|any|other)\b.*?\b(instruction|rule|prompt|directive|constraint)s?\b"
    ),
    # Direct ignore/disregard/forget previous instructions or rules
    re.compile(
        r"(?i)\b(ignore|disregard|forget)\b\s+(?:all\s+|any\s+)?previous\s+(?:instructions?|rules?|prompts?|directives?|system|context|commands?)\b"
    ),
    # Direct override/bypass rules or system
    re.compile(
        r"(?i)\b(override|bypass)\b\s+(?:all\s+)?(?:rules?|system|instructions?|policies|policy)\b"
    ),
    # New instructions or role hijacking
    re.compile(r"(?i)\bnew\s+instructions?\b"),
    re.compile(r"(?i)\byou\s+are\s+now\s+(?:in\s+)?(?:dan|developer(?:\s+mode)?|unrestricted)\b"),
    # Boundary delimiter breakout attempts (e.g. <<<END_CUSTOMER_EMAIL_BODY>>>, <<<...>>> or standalone <<<)
    re.compile(r"<<<[^>]+>>>"),
    re.compile(r"<<<"),
    # Fake system instruction or system tags
    re.compile(r"(?i)\[\s*SYSTEM(?:\s+INSTRUCTION)?\s*\]"),
    re.compile(r"(?i)<\s*system\b[^>]*>"),
    re.compile(r"(?i)<\/\s*system\s*>"),
    re.compile(r"(?i)<<\s*SYSTEM\b[^>]*>>"),
    re.compile(r"(?i)\[\s*\/?INST\s*\]"),
    re.compile(r"(?i)<\s*\|\s*im_(?:start|end)\s*\|\s*>"),
    re.compile(r"(?i)\[\s*(?:ADMIN|DEVELOPER)\s*\]"),
]


def detect_prompt_injection(text: str) -> bool:
    """Detects prompt injection attempts including keyword variants, boundary breakout, and fake tags."""
    text_upper = text.upper()
    if any(ind in text_upper for ind in PROMPT_INJECTION_KEYWORDS):
        return True
    return any(pattern.search(text) is not None for pattern in PROMPT_INJECTION_REGEXES)


class MockAIProvider(AIProvider):
    """Deterministic, genuine offline AI provider adhering to all Phase 4 rules."""

    def __init__(self) -> None:
        self.call_count: int = 0
        self.last_call_at: datetime.datetime | None = None

    async def classify_email(
        self,
        email_input: EmailClassificationInput,
        store_context: StoreContext,
    ) -> ClassificationResult:
        """Classifies incoming email on 3 independent dimensions."""
        self.call_count += 1
        self.last_call_at = datetime.datetime.now(datetime.UTC)
        start_time = time.time()
        combined_text = f"{email_input.subject}\n{email_input.body_text}"

        # Combine attachment texts if any
        attachment_texts = [
            att.extracted_text for att in email_input.attachments if att.extracted_text
        ]
        if attachment_texts:
            combined_text += "\n" + "\n".join(attachment_texts)
        combined_upper = combined_text.upper()

        # 1. Tầng phòng thủ Prompt Injection (R-07 & TC-AI-05, TC-INJ-02, TC-INJ-03)
        if detect_prompt_injection(combined_text):
            elapsed_ms = int((time.time() - start_time) * 1000)
            return ClassificationResult(
                is_spam=False,
                spam_status="not_spam",
                spam_score=0.0,
                order_status="not_checked",
                intent="uncertain",
                confidence=0.9,
                reason_codes=["PROMPT_INJECTION_DETECTED"],
                detected_language=store_context.default_language or "en",
                requires_manual_review=True,
                review_reason_code="PROMPT_INJECTION_DETECTED",
                is_prompt_injection=True,
                reasoning_summary="Prompt injection attempt quarantined in manual review.",
                provider_type="mock",
                model_identifier="mock-v1",
                prompt_version="v1.0",
                execution_time_ms=elapsed_ms,
            )

        # 2. Phát hiện Thư Rác / Spam (R-08 & TC-AI-06)
        spam_indicators = [
            "VIAGRA",
            "CLAIM YOUR LOTTERY",
            "LOTTERY PRIZE",
            "CLAIM YOUR PRIZE",
            "SPAM.XYZ",
            "CASINO BONUS",
            "CRYPTO GIVEAWAY",
            "HOT SINGLES IN YOUR AREA",
        ]
        if any(ind in combined_upper for ind in spam_indicators):
            elapsed_ms = int((time.time() - start_time) * 1000)
            return ClassificationResult(
                is_spam=True,
                spam_status="spam",
                spam_score=0.99,
                spam_reason="Promotional / Phishing spam keywords detected.",
                order_status="no_order",
                intent="other",
                confidence=0.99,
                reason_codes=["SPAM_PROMOTIONAL"],
                detected_language=store_context.default_language or "en",
                requires_manual_review=False,
                is_prompt_injection=False,
                reasoning_summary="Classified as spam with high confidence.",
                provider_type="mock",
                model_identifier="mock-v1",
                prompt_version="v1.0",
                execution_time_ms=elapsed_ms,
            )

        # 3. Trích xuất Thực thể (Order numbers, Tracking, Products)
        order_numbers = list(set(re.findall(r"#(?:[0-9]{4,6})|(?:WRY-[0-9]{4,6})", combined_text, re.IGNORECASE)))
        tracking_numbers = list(set(re.findall(r"\b(?:9[0-9]{15,22}|1Z[0-9A-Z]{16})\b", combined_text)))
        entities = ExtractedEntities(
            order_numbers=order_numbers,
            tracking_numbers=tracking_numbers,
            product_names=[],
        )

        # 4. Xác định Chiều 2: Order Status
        has_order = len(order_numbers) > 0 or "ORDER" in combined_upper or "INVOICE" in combined_upper
        order_status: OrderStatusType = "has_order_record" if has_order else "no_order"
        order_flags = OrderFlags(
            has_paid_order=has_order,
            has_active_order=has_order,
            has_fulfilled_order=False,
            has_cancelled_order=False,
            has_refunded_order=False,
        )

        # 5. Xác định Chiều 3: Intent & Confidence
        intent: IntentType = "product_inquiry"
        confidence = 0.95
        reason_codes = []
        requires_manual_review = False
        review_reason_code = None

        if any(w in combined_upper for w in ["RETURN", "REFUND", "EXCHANGE", "MONEY BACK"]):
            intent = "return_or_refund"
            reason_codes = ["CUSTOMER_REQUESTS_RETURN"]
        elif any(w in combined_upper for w in ["BROKEN", "DAMAGED", "DEFECTIVE", "TERRIBLE", "COMPLAINT", "ANGRY"]):
            intent = "complaint"
            reason_codes = ["QUALITY_ISSUE_REPORTED"]
        elif any(
            w in combined_upper
            for w in [
                "WHERE IS MY ORDER",
                "TRACK",
                "SHIPPING",
                "DELIVERY",
                "PACKAGE DELAY",
                "ĐƠN HÀNG",
                "GIAO HÀNG",
                "KHI NÀO NHẬN",
            ]
        ):
            intent = "order_support"
            reason_codes = ["DELIVERY_INQUIRY"]
        elif any(w in combined_upper for w in ["PARTNERSHIP", "COLLABORATE", "WHOLESALE", "INFLUENCER"]):
            intent = "partnership"
            reason_codes = ["BUSINESS_COLLABORATION"]
            requires_manual_review = True
            review_reason_code = "NON_DRAFTING_INTENT"
        elif any(w in combined_upper for w in ["UNSUBSCRIBE", "OPT OUT", "NEWSLETTER"]):
            intent = "other"
            reason_codes = ["SUBSCRIPTION_MANAGEMENT"]
            requires_manual_review = True
            review_reason_code = "NON_DRAFTING_INTENT"
        else:
            intent = "product_inquiry"
            reason_codes = ["PRODUCT_SPEC_QUESTION"]

        # 6. Nhận diện Ngôn ngữ
        vietnamese_keywords = ["xin chào", "đơn hàng", "sản phẩm", "giao hàng", "cảm ơn", "giá bao nhiêu"]
        detected_language = store_context.default_language or "en"
        if any(kw in combined_text.lower() for kw in vietnamese_keywords):
            detected_language = "vi"

        elapsed_ms = int((time.time() - start_time) * 1000)

        return ClassificationResult(
            is_spam=False,
            spam_status="not_spam",
            spam_score=0.01,
            order_status=order_status,
            order_flags=order_flags,
            intent=intent,
            confidence=confidence,
            reason_codes=reason_codes,
            detected_language=detected_language,
            entities=entities,
            requires_manual_review=requires_manual_review,
            review_reason_code=review_reason_code,
            is_prompt_injection=False,
            reasoning_summary=f"Processed inquiry for {store_context.brand_name} with intent {intent}.",
            provider_type="mock",
            model_identifier="mock-v1",
            prompt_version="v1.0",
            execution_time_ms=elapsed_ms,
        )

    async def generate_reply_draft(
        self,
        draft_input: DraftGenerationInput,
        store_context: StoreContext,
    ) -> DraftResult:
        """Generates a genuine context-aware reply draft adhering to Phase 6 anti-hallucination rules."""
        self.call_count += 1
        self.last_call_at = datetime.datetime.now(datetime.UTC)

        lang = draft_input.target_language or draft_input.classification.detected_language or "en"
        brand_name = store_context.brand_name
        intent = draft_input.classification.intent
        warning_codes: list[str] = []

        # Localized greetings and closings
        if lang == "vi":
            greeting = "Kính gửi quý khách,"
            closing = f"Trân trọng,\nĐội ngũ hỗ trợ {brand_name}"
        elif lang == "fr":
            greeting = "Bonjour,"
            closing = f"Cordialement,\nL'équipe {brand_name}"
        elif lang == "de":
            greeting = "Guten Tag,"
            closing = f"Mit freundlichen Grüßen,\nIhr {brand_name} Team"
        elif lang == "es":
            greeting = "Estimado cliente,"
            closing = f"Atentamente,\nEl equipo de {brand_name}"
        elif lang == "ja":
            greeting = "お客様へ、"
            closing = f"敬具\n{brand_name} サポートチーム"
        elif lang == "zh":
            greeting = "尊敬的客户："
            closing = f"顺祝商祺，\n{brand_name} 客服团队"
        else:
            greeting = "Dear Customer,"
            closing = f"Best regards,\n{brand_name} Support Team"

        body_parts = [greeting]

        # 1. Product Inquiry with Unresolved Product (Anti-Hallucination Guardrail)
        prod_snap = draft_input.product_snapshot
        if intent == "product_inquiry":
            if prod_snap and not prod_snap.get("product_resolved", True):
                warning_codes.append("PRODUCT_NOT_RESOLVED")
                if lang == "vi":
                    body_parts.append(
                        f"Cảm ơn bạn đã quan tâm đến các sản phẩm của {brand_name}. "
                        f"Để chúng tôi có thể tư vấn chính xác nhất về sản phẩm bạn đang tìm kiếm, "
                        f"bạn vui lòng gửi cho chúng tôi xin đường dẫn sản phẩm trên website https://{store_context.public_domain} "
                        f"hoặc hình ảnh/tên sản phẩm cụ thể nhé ạ."
                    )
                else:
                    body_parts.append(
                        f"Thank you for your interest in {brand_name}. "
                        f"To provide you with the exact specifications, pricing, and availability, "
                        f"could you please reply with the direct product link from our store (https://{store_context.public_domain}) "
                        f"or an image of the item you are inquiring about?"
                    )
            elif prod_snap and prod_snap.get("matched_products"):
                matched = prod_snap["matched_products"][0]
                p_title = matched.get("title", "Product")
                p_min = matched.get("min_price", "")
                p_in_stock = "in stock" if matched.get("has_in_stock_variant") else "currently out of stock"
                if lang == "vi":
                    body_parts.append(
                        f"Cảm ơn bạn đã quan tâm đến {p_title}. Sản phẩm hiện có giá từ ${p_min} và {p_in_stock}. "
                        f"Bạn có thể xem chi tiết tại https://{store_context.public_domain}."
                    )
                else:
                    body_parts.append(
                        f"Thank you for asking about the {p_title}. It is priced starting at ${p_min} and is {p_in_stock}. "
                        f"You can view complete details directly on our website."
                    )
            else:
                body_parts.append(
                    f"Thank you for contacting {brand_name} regarding our products. "
                    f"We are delighted to assist you with any questions."
                )

        # 2. Complaint Handling
        elif intent == "complaint":
            warning_codes.append("COMPLAINT_DETECTED")
            if lang == "vi":
                body_parts.append(
                    f"Chúng tôi thành thật xin lỗi vì trải nghiệm chưa được trọn vẹn của quý khách tại {brand_name}. "
                    f"Chúng tôi rất coi trọng phản hồi này và đang khẩn trương xử lý vấn đề của quý khách."
                )
            else:
                body_parts.append(
                    f"We sincerely apologize for any inconvenience you have experienced with {brand_name}. "
                    f"We deeply value your satisfaction and are prioritizing resolution of your concern."
                )

        # 3. Return or Refund Handling
        elif intent == "return_or_refund":
            warning_codes.append("RETURN_OR_REFUND_REQUESTED")
            refund_policy = draft_input.policies.get("refund", "")
            policy_excerpt = "According to our return policy, items may be returned within eligible guidelines."
            if refund_policy:
                policy_excerpt = f"In accordance with our official policy: {refund_policy[:120]}..."
            if lang == "vi":
                body_parts.append(
                    f"Chúng tôi đã nhận được yêu cầu đổi/trả hoặc hoàn tiền của quý khách. "
                    f"Theo chính sách của cửa hàng: {policy_excerpt} "
                    f"Vui lòng cung cấp thêm hình ảnh sản phẩm còn nguyên tem mác để chúng tôi hoàn tất thủ tục tiếp nhận."
                )
            else:
                body_parts.append(
                    f"We have received your request regarding returns and refunds. "
                    f"{policy_excerpt} "
                    f"Please reply with clear photos of the item in its original condition to initiate the return process."
                )

        # 4. Order Support Handling
        elif intent in ("order_support", "recent_order"):
            order_snap = draft_input.order_snapshot
            if order_snap and order_snap.get("has_paid_order") and order_snap.get("latest_order_name"):
                order_name = order_snap.get("latest_order_name")
                order_status = order_snap.get("latest_order_fulfillment_status") or "processing"
                if lang == "vi":
                    body_parts.append(
                        f"Cảm ơn bạn đã liên hệ. Đơn hàng {order_name} của bạn hiện đang ở trạng thái: {order_status}. "
                        f"Chúng tôi đang theo dõi sát sao để đơn hàng tới tay bạn sớm nhất."
                    )
                else:
                    body_parts.append(
                        f"Thank you for reaching out regarding your order {order_name}. "
                        f"Your package is currently {order_status}. We are actively tracking it for prompt delivery."
                    )
            else:
                warning_codes.append("NO_RECENT_ORDER")
                if lang == "vi":
                    body_parts.append(
                        "Cảm ơn bạn đã liên hệ. Chúng tôi chưa tìm thấy đơn hàng nào liên kết với email này trong 60 ngày qua. "
                        "Bạn vui lòng cung cấp mã đơn hàng (bắt đầu bằng #...) hoặc email đã sử dụng khi đặt hàng nhé."
                    )
                else:
                    body_parts.append(
                        "Thank you for contacting us. We could not find a matching order under this email address within the past 60 days. "
                        "Could you please share your order number (e.g. #1001) or the email address used during checkout?"
                    )

        else:
            body_parts.append(
                f"Thank you for contacting {brand_name}. We have received your inquiry regarding '{draft_input.subject}' "
                f"and are delighted to assist you."
            )

        body_parts.append(closing)
        body_text = "\n\n".join(body_parts)

        # Generate semantic HTML
        html_paragraphs = "".join(f"<p>{p.replace(chr(10), '<br>')}</p>" for p in body_parts)

        clean_subj = draft_input.subject.strip()
        final_subj = clean_subj if clean_subj.lower().startswith("re:") else f"Re: {clean_subj}"

        return DraftResult(
            subject=final_subj,
            body_text=body_text,
            body_html=html_paragraphs,
            language=lang,
            warning_codes=warning_codes,
            provider_type="mock",
            model_identifier="mock-v1",
        )

    async def test_connection(self) -> AITestConnectionResult:
        """Tests mock provider availability."""
        return AITestConnectionResult(
            success=True,
            provider_type="mock",
            model_tested="mock-v1",
            latency_ms=5,
            direct_https_verified=True,
            sample_response='{"status": "ok", "provider": "mock"}',
        )

    def get_usage_metadata(self) -> AIUsageMetadata:
        return AIUsageMetadata(
            total_calls=self.call_count,
            total_input_tokens=self.call_count * 150,
            total_output_tokens=self.call_count * 60,
            last_call_at=self.last_call_at,
        )
