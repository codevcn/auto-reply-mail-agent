"""Phase 6 Challenger Adversarial Test Entrypoint for backend/tests.

Integrates the empirical challenger harness into backend pytest test runner.
"""

from __future__ import annotations

import pytest

from tests.adversarial.test_challenger_phase6_empirical import EmpiricalPhase6Challenger


@pytest.mark.asyncio
async def test_backend_adversarial_zero_autonomous_sending():
    c = EmpiricalPhase6Challenger()
    await c.run_zero_autonomous_sending_suite()
    assert c.failed == 0, f"Adversarial failures: {c.findings}"


@pytest.mark.asyncio
async def test_backend_adversarial_send_idempotency_concurrency():
    c = EmpiricalPhase6Challenger()
    await c.run_send_idempotency_concurrency_suite()
    assert c.failed == 0, f"Adversarial failures: {c.findings}"


@pytest.mark.asyncio
async def test_backend_adversarial_threading_and_imap_isolation():
    c = EmpiricalPhase6Challenger()
    await c.run_threading_and_imap_isolation_suite()
    assert c.failed == 0, f"Adversarial failures: {c.findings}"


@pytest.mark.asyncio
async def test_backend_adversarial_ambiguous_delivery():
    c = EmpiricalPhase6Challenger()
    await c.run_ambiguous_delivery_suite()
    assert c.failed == 0, f"Adversarial failures: {c.findings}"


@pytest.mark.asyncio
async def test_backend_adversarial_piezaprint_exclusion():
    c = EmpiricalPhase6Challenger()
    await c.run_piezaprint_exclusion_suite()
    assert c.failed == 0, f"Adversarial failures: {c.findings}"


@pytest.mark.asyncio
async def test_backend_adversarial_draft_rules_and_prompts():
    c = EmpiricalPhase6Challenger()
    await c.run_draft_rules_and_prompts_suite()
    assert c.failed == 0, f"Adversarial failures: {c.findings}"


@pytest.mark.asyncio
async def test_backend_adversarial_immutable_versioning_and_stale_policy():
    c = EmpiricalPhase6Challenger()
    await c.run_immutable_versioning_and_stale_policy_suite()
    assert c.failed == 0, f"Adversarial failures: {c.findings}"
