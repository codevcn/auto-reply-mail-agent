#!/usr/bin/env python3
"""Comprehensive E2E Test Runner for Mail Agent.
Supports execution across all 4 Tiers + Playwright UI Scenarios.
Works with both Python standard library unittest and pytest.
"""

from __future__ import annotations

import argparse
import os
import sys
import time
import unittest

# Ensure project root is in sys.path
PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)


def build_suite(tier: str | None = None) -> unittest.TestSuite:
    loader = unittest.TestLoader()
    suite = unittest.TestSuite()

    tier_modules = {
        "1": [
            "tests.e2e.tier1_features.test_auth_features",
            "tests.e2e.tier1_features.test_wizard_features",
            "tests.e2e.tier1_features.test_proxy_features",
            "tests.e2e.tier1_features.test_ingestion_features",
            "tests.e2e.tier1_features.test_classification_features",
            "tests.e2e.tier1_features.test_shopify_features",
            "tests.e2e.tier1_features.test_draft_approval_features",
        ],
        "2": [
            "tests.e2e.tier2_boundary.test_boundary_cases",
        ],
        "3": [
            "tests.e2e.tier3_pairwise.test_cross_feature_matrix",
        ],
        "4": [
            "tests.e2e.tier4_scenarios.test_real_world_scenarios",
        ],
        "ui": [
            "tests.e2e.ui_playwright.test_ui_scenarios",
        ],
    }

    selected_tiers = [tier] if tier else ["1", "2", "3", "4", "ui"]

    for t in selected_tiers:
        modules = tier_modules.get(t, [])
        for mod_name in modules:
            try:
                mod_suite = loader.loadTestsFromName(mod_name)
                suite.addTests(mod_suite)
            except Exception as e:
                print(f"[ERROR] Failed to load module {mod_name}: {e}", file=sys.stderr)

    return suite


def main():
    parser = argparse.ArgumentParser(description="Mail Agent E2E Test Runner")
    parser.add_argument(
        "--tier",
        choices=["1", "2", "3", "4", "ui"],
        help="Run only tests from a specific tier (1, 2, 3, 4, or ui)",
    )
    parser.add_argument("-v", "--verbose", action="store_true", help="Verbose output")
    args = parser.parse_args()

    print("=" * 80)
    print("MAIL AGENT E2E TEST SUITE — FOUR-TIER OPAQUE-BOX VERIFICATION")
    print("=" * 80)
    tier_label = f"Tier {args.tier}" if args.tier else "All Tiers (1-4 + UI Playwright)"
    print(f"Target Scope: {tier_label}")
    print(f"Project Root: {PROJECT_ROOT}")
    print("-" * 80)

    start_time = time.time()
    suite = build_suite(tier=args.tier)
    runner = unittest.TextTestRunner(verbosity=2 if args.verbose else 1)
    result = runner.run(suite)
    elapsed = time.time() - start_time

    print("-" * 80)
    print(f"Ran {result.testsRun} tests in {elapsed:.3f}s")
    if result.wasSuccessful():
        print("[SUCCESS] All E2E test cases PASSED perfectly with 100% integrity!")
        print("=" * 80)
        return 0
    else:
        print(f"[FAILURE] {len(result.failures)} failed, {len(result.errors)} errors.")
        print("=" * 80)
        return 1


if __name__ == "__main__":
    sys.exit(main())
