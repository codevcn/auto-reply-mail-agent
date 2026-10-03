"""Async SMTP Client with STARTTLS (port 587) / SSL (port 465) verification."""

from __future__ import annotations

import asyncio
import contextlib
import smtplib
import ssl
import time
from email.message import EmailMessage

from app.core.redaction import register_secret
from app.mail.exceptions import (
    PiezaprintExclusionError,
)
from app.mail.schemas import SMTPDeliveryResult, SMTPTestResult

EXCLUDED_DOMAINS = ["piezaprint.com", "piezaprint"]


class SMTPClient:
    """Production SMTP Client supporting STARTTLS on 587 and direct SSL on 465."""

    def __init__(
        self,
        host: str = "mail.wrydeco.com",
        port: int = 587,
        username: str = "",
        password: str = "",
        tls_mode: str = "STARTTLS",
        timeout_seconds: float = 15.0,
    ) -> None:
        self.host = host.strip()
        self.port = port
        self.username = username.strip().lower()
        self.password = password
        self.tls_mode = tls_mode.upper()
        self.timeout_seconds = timeout_seconds

        # Enforce R-03 immediately upon instantiation
        if any(exc in self.username or exc in self.host.lower() for exc in EXCLUDED_DOMAINS):
            raise PiezaprintExclusionError()

        # Register plaintext password into Redaction Engine
        if self.password:
            register_secret(self.password)

    def _create_ssl_context(self) -> ssl.SSLContext:
        ctx = ssl.create_default_context()
        ctx.minimum_version = ssl.TLSVersion.TLSv1_2
        return ctx

    def _sync_test_connection(self) -> SMTPTestResult:
        start_time = time.perf_counter()
        server: smtplib.SMTP | None = None
        try:
            ssl_ctx = self._create_ssl_context()
            if self.tls_mode == "SSL" or self.port == 465:
                server = smtplib.SMTP_SSL(
                    host=self.host,
                    port=self.port,
                    timeout=self.timeout_seconds,
                    context=ssl_ctx,
                )
                server.ehlo()
            else:
                server = smtplib.SMTP(
                    host=self.host,
                    port=self.port,
                    timeout=self.timeout_seconds,
                )
                server.ehlo()
                if self.tls_mode == "STARTTLS":
                    if not server.has_extn("STARTTLS"):
                        latency = int((time.perf_counter() - start_time) * 1000)
                        return SMTPTestResult(
                            success=False,
                            latency_ms=latency,
                            error="SMTP_STARTTLS_NOT_SUPPORTED",
                            detail=f"Server {self.host}:{self.port} does not advertise STARTTLS extension.",
                        )
                    server.starttls(context=ssl_ctx)
                    server.ehlo()

            # Read ESMTP capabilities
            capabilities = list(server.esmtp_features.keys()) if server.esmtp_features else []

            # Authenticate (without sending any email - safe test)
            try:
                server.login(self.username, self.password)
            except smtplib.SMTPAuthenticationError as auth_exc:
                latency = int((time.perf_counter() - start_time) * 1000)
                err_detail = (
                    auth_exc.smtp_error.decode("utf-8", errors="replace")
                    if isinstance(auth_exc.smtp_error, bytes)
                    else str(auth_exc)
                )
                return SMTPTestResult(
                    success=False,
                    latency_ms=latency,
                    error="MAIL_AUTHENTICATION_FAILED",
                    detail=f"SMTP authentication rejected: {err_detail}",
                )

            latency = int((time.perf_counter() - start_time) * 1000)
            return SMTPTestResult(
                success=True,
                auth_success=True,
                starttls_supported=(self.tls_mode == "STARTTLS"),
                capabilities=capabilities,
                latency_ms=latency,
            )

        except TimeoutError:
            latency = int((time.perf_counter() - start_time) * 1000)
            return SMTPTestResult(
                success=False,
                latency_ms=latency,
                error="SMTP_TIMEOUT",
                detail=f"SMTP connection timed out after {self.timeout_seconds}s.",
            )
        except ssl.SSLError as exc:
            latency = int((time.perf_counter() - start_time) * 1000)
            return SMTPTestResult(
                success=False,
                latency_ms=latency,
                error="SMTP_SSL_ERROR",
                detail=f"SMTP TLS/SSL negotiation failed: {exc}",
            )
        except Exception as exc:
            latency = int((time.perf_counter() - start_time) * 1000)
            return SMTPTestResult(
                success=False,
                latency_ms=latency,
                error="SMTP_CONNECTION_FAILED",
                detail=str(exc),
            )
        finally:
            if server is not None:
                with contextlib.suppress(Exception):
                    server.quit()

    async def test_connection(self) -> SMTPTestResult:
        """Asynchronously test SMTP STARTTLS/SSL connection and authentication."""
        return await asyncio.to_thread(self._sync_test_connection)

    def _sync_send_message(self, message: EmailMessage, recipients: list[str]) -> tuple[bool, str]:
        """Send message via authenticated SMTP (preparation for Phase 6 Human Approval delivery)."""
        server: smtplib.SMTP | None = None
        try:
            ssl_ctx = self._create_ssl_context()
            if self.tls_mode == "SSL" or self.port == 465:
                server = smtplib.SMTP_SSL(
                    host=self.host, port=self.port, timeout=self.timeout_seconds, context=ssl_ctx
                )
                server.ehlo()
            else:
                server = smtplib.SMTP(host=self.host, port=self.port, timeout=self.timeout_seconds)
                server.ehlo()
                if self.tls_mode == "STARTTLS":
                    server.starttls(context=ssl_ctx)
                    server.ehlo()

            server.login(self.username, self.password)
            refused = server.send_message(message, to_addrs=recipients)
            if refused:
                return False, f"Recipients refused: {refused}"
            return True, "SENT"
        except Exception as exc:
            return False, str(exc)
        finally:
            if server:
                with contextlib.suppress(Exception):
                    server.quit()

    async def send_message(self, message: EmailMessage, recipients: list[str]) -> tuple[bool, str]:
        return await asyncio.to_thread(self._sync_send_message, message, recipients)

    def _sync_send_message_robust(
        self, message: EmailMessage, recipients: list[str]
    ) -> SMTPDeliveryResult:
        """Robustly sends an email via SMTP, distinguishing between deterministic and ambiguous (delivery_unknown) errors.

        INVARIANT R-25: Drops/timeouts occurring during or after the DATA command are ambiguous.
        Automated retries are forbidden to avoid duplicate deliveries.
        """
        server: smtplib.SMTP | None = None
        data_phase_started = False
        try:
            ssl_ctx = self._create_ssl_context()
            if self.tls_mode == "SSL" or self.port == 465:
                server = smtplib.SMTP_SSL(
                    host=self.host, port=self.port, timeout=self.timeout_seconds, context=ssl_ctx
                )
                server.ehlo()
            else:
                server = smtplib.SMTP(host=self.host, port=self.port, timeout=self.timeout_seconds)
                server.ehlo()
                if self.tls_mode == "STARTTLS":
                    server.starttls(context=ssl_ctx)
                    server.ehlo()

            server.login(self.username, self.password)

            # Mark transition into DATA phase
            data_phase_started = True
            refused = server.send_message(message, to_addrs=recipients)
            if refused:
                return SMTPDeliveryResult(
                    status="failed",
                    error_code="SMTP_RECIPIENTS_REFUSED",
                    error_detail=f"Recipients refused: {refused}",
                )

            return SMTPDeliveryResult(
                status="sent",
                response_code=250,
                response_text="250 2.0.0 Ok: Message accepted for delivery",
            )

        except (
            smtplib.SMTPServerDisconnected,
            TimeoutError,
            ConnectionResetError,
            BrokenPipeError,
        ) as net_exc:
            if data_phase_started:
                # Connection dropped after or during DATA transmission -> AMBIGUOUS
                return SMTPDeliveryResult(
                    status="delivery_unknown",
                    error_code="SMTP_DELIVERY_AMBIGUOUS",
                    error_detail=f"Connection dropped/timed out during or after DATA phase: {net_exc}",
                )
            # Dropped before DATA -> deterministic failure
            return SMTPDeliveryResult(
                status="failed",
                error_code="SMTP_CONNECTION_FAILED",
                error_detail=f"Connection failed before DATA phase: {net_exc}",
            )

        except smtplib.SMTPAuthenticationError as auth_exc:
            return SMTPDeliveryResult(
                status="failed",
                error_code="SMTP_AUTH_FAILED",
                error_detail=str(auth_exc),
            )

        except smtplib.SMTPResponseException as resp_exc:
            # Check if error occurred in DATA phase
            if data_phase_started and resp_exc.smtp_code in (421, 451, 452):
                return SMTPDeliveryResult(
                    status="delivery_unknown",
                    response_code=resp_exc.smtp_code,
                    error_code="SMTP_DATA_AMBIGUOUS",
                    error_detail=str(resp_exc),
                )
            return SMTPDeliveryResult(
                status="failed",
                response_code=resp_exc.smtp_code,
                error_code="SMTP_RESPONSE_ERROR",
                error_detail=str(resp_exc),
            )

        except Exception as exc:
            if data_phase_started:
                return SMTPDeliveryResult(
                    status="delivery_unknown",
                    error_code="SMTP_UNEXPECTED_DATA_ERROR",
                    error_detail=f"Unexpected error during DATA phase: {exc}",
                )
            return SMTPDeliveryResult(
                status="failed",
                error_code="SMTP_SEND_FAILED",
                error_detail=str(exc),
            )

        finally:
            if server:
                with contextlib.suppress(Exception):
                    server.quit()

    async def send_message_robust(
        self, message: EmailMessage, recipients: list[str]
    ) -> SMTPDeliveryResult:
        """Asynchronously send email with ambiguous failure detection."""
        return await asyncio.to_thread(self._sync_send_message_robust, message, recipients)

