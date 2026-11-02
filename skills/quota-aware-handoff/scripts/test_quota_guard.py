"""Admission tests exercise uncertainty, shared pools, timestamps, and parser gaps."""
import copy
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

import quota_guard as q

NOW = "2026-10-29T04:00:00Z"
RESET = "2026-10-29T05:00:00Z"


def fixtures():
    snap = {"provider": "codex", "quota_pool_id": "codex-personal", "account_ref": "personal",
            "model": "test-model", "observed_at": NOW, "freshness_seconds": 120,
            "coverage_complete": True, "hard_stop": False,
            "limits": [{"id": "weekly", "unit": "percent", "remaining": 35,
                        "observed_at": NOW, "source": "official:test", "reset_at": RESET,
                        "confidence": "measured"}],
            "context": {"effective_window": 200000, "remaining_tokens": 150000, "used_percent": 25}}
    unit = {"id": "unit-1", "quota_pool_id": "codex-personal", "model": "test-model",
            "estimate_basis": "Upper bound of 8 percentage points from 12 comparable completed units.",
            "estimates": {"weekly": {"unit": "percent", "next_cost_upper_bound": 8,
                                     "handoff_reserve": 5, "uncertainty_margin": 3}},
            "safety_factor": 1.5, "context_needed_tokens": 40000}
    return snap, unit


class GateTests(unittest.TestCase):
    def setUp(self):
        self.snap, self.unit = fixtures()

    def evaluate(self):
        return q.evaluate(self.snap, self.unit, NOW)

    def stop(self, reason):
        result = self.evaluate()
        self.assertEqual(result["decision"], q.STOP, result)
        self.assertIn(reason, result["reasons"])

    def test_enough(self):
        result = self.evaluate()
        self.assertEqual(result["decision"], "CONTINUE")
        self.assertEqual(result["checks"][0]["required"], 20)

    def test_exact_boundary_then_below(self):
        self.snap["limits"][0]["remaining"] = 20
        self.assertEqual(self.evaluate()["decision"], "CONTINUE")
        self.snap["limits"][0]["remaining"] = 19.999
        self.stop("weekly:insufficient_budget")

    def test_unknown_coverage(self):
        self.snap["coverage_complete"] = False
        self.stop("quota_coverage_unknown")

    def test_unknown_balance(self):
        self.snap["limits"][0]["confidence"] = "unknown"
        self.stop("weekly:balance_unknown")

    def test_missing_weekly_even_when_other_limit_sufficient(self):
        self.snap["limits"][0]["id"] = "five_hour"
        self.unit["estimates"]["five_hour"] = copy.deepcopy(self.unit["estimates"]["weekly"])
        self.stop("weekly:expected_limit_missing")

    def test_stale_and_future(self):
        self.snap["limits"][0]["observed_at"] = "2026-10-29T03:57:59Z"
        self.stop("weekly:stale")
        self.snap["limits"][0]["observed_at"] = "2026-10-29T04:00:06Z"
        self.stop("weekly:future_timestamp")

    def test_timezone_required(self):
        self.snap["observed_at"] = "2026-10-29T04:00:00"
        self.stop("snapshot:invalid_timestamp")

    def test_mismatched_pool_model_units(self):
        self.unit["quota_pool_id"] = "other-account"
        self.stop("quota_pool_id_mismatch")
        self.unit["model"] = "other-model"
        self.stop("model_mismatch")
        self.unit["estimates"]["weekly"]["unit"] = "tokens"
        self.stop("weekly:unit_mismatch")

    def test_reset_passed_never_assume_refill(self):
        self.snap["limits"][0]["reset_at"] = NOW
        self.stop("weekly:reset_passed_refresh_required")

    def test_negative_nan_infinity_and_bool_balance(self):
        for value in (-1, float("nan"), float("inf"), True, 101):
            with self.subTest(value=value):
                self.snap["limits"][0]["remaining"] = value
                self.stop("weekly:invalid_balance_or_unit")

    def test_no_handoff_reserve(self):
        self.unit["estimates"]["weekly"]["handoff_reserve"] = 0
        self.stop("weekly:invalid_estimate_or_no_handoff_reserve")

    def test_missing_estimate_basis(self):
        self.unit["estimate_basis"] = " "
        self.stop("missing_unit_estimate_basis")

    def test_nonfinite_costs(self):
        self.unit["estimates"]["weekly"]["next_cost_upper_bound"] = float("nan")
        self.stop("weekly:invalid_estimate_or_no_handoff_reserve")

    def test_duplicate_limit(self):
        self.snap["limits"].append(copy.deepcopy(self.snap["limits"][0]))
        self.stop("weekly:duplicate_limit")

    def test_hard_stop(self):
        self.snap["hard_stop"] = True
        self.stop("provider_hard_stop_or_unknown")

    def test_context_unknown(self):
        del self.snap["context"]
        self.stop("context_unknown")

    def test_context_50_70_90(self):
        for used, warning in ((50, "context_50_percent_warning"),
                              (70, "context_70_percent_save_checkpoint_and_compact")):
            self.snap["context"]["used_percent"] = used
            self.snap["context"]["remaining_tokens"] = 200000 * (1 - used / 100)
            self.assertIn(warning, self.evaluate()["warnings"])
        self.snap["context"]["used_percent"] = 90
        self.snap["context"]["remaining_tokens"] = 20000
        self.stop("context_90_percent_checkpoint_handoff")

    def test_context_threshold_scope_and_capacity(self):
        self.snap["context"] = {"effective_window": 1000000, "remaining_tokens": 100000, "used_percent": 90}
        self.assertEqual(self.evaluate()["decision"], "CONTINUE")
        self.snap["context"]["remaining_tokens"] = 39999
        self.stop("context_insufficient")

    def test_estimated_balance_requires_margin(self):
        self.snap["limits"][0]["confidence"] = "estimated"
        self.unit["estimates"]["weekly"]["uncertainty_margin"] = 0
        self.stop("weekly:estimated_balance_needs_margin")

    def test_inconsistent_context_cannot_claim_capacity(self):
        self.snap["context"]["used_percent"] = 80
        self.stop("context_inconsistent")


class ParserTests(unittest.TestCase):
    def normalize(self, provider, raw, **kwargs):
        return q.normalize(provider, raw, pool="personal", model="test-model", account_ref="local-alias",
                           observed_at=NOW, **kwargs)

    def test_codex_all_buckets_and_null_secondary(self):
        raw = {"result": {"rateLimitsByLimitId": {
            "codex": {"primary": {"usedPercent": 20}, "secondary": None},
            "other": {"primary": {"usedPercent": 40}, "secondary": {"usedPercent": 60}}}}}
        value = self.normalize("codex", raw)
        self.assertFalse(value["coverage_complete"])
        self.assertEqual([v["id"] for v in value["limits"]], ["codex/primary", "other/primary", "other/secondary"])
        self.assertEqual([v["remaining"] for v in value["limits"]], [80, 60, 40])
        self.assertEqual(value["normalization_issues"], [])
        self.assertIsNone(value["context"])

    def test_codex_empty_named_bucket_blocks_even_with_complete_coverage(self):
        for missing in ({}, {"primary": None}, {"primary": None, "secondary": None}):
            with self.subTest(bucket=missing):
                raw = {"result": {"rateLimitsByLimitId": {
                    "regular": {"primary": {"usedPercent": 20}},
                    "required_weekly": missing}}}
                value = self.normalize("codex", raw, coverage_complete=True)
                snapshot, unit = fixtures()
                value["context"] = snapshot["context"]
                unit["quota_pool_id"] = "personal"
                unit["estimates"] = {"regular/primary": unit["estimates"]["weekly"]}
                self.assertEqual([v["id"] for v in value["limits"]], ["regular/primary"])
                self.assertIn("required_weekly:missing_window_balance", value["normalization_issues"])
                result = q.evaluate(value, unit, NOW)
                self.assertEqual(result["decision"], q.STOP, result)
                self.assertEqual(result["reasons"], ["normalization_incomplete"])

    def test_codex_valid_primary_with_optional_null_secondary_can_continue(self):
        raw = {"result": {"rateLimitsByLimitId": {
            "regular": {"primary": {"usedPercent": 20}, "secondary": None}}}}
        value = self.normalize("codex", raw, coverage_complete=True)
        snapshot, unit = fixtures()
        value["context"] = snapshot["context"]
        unit["quota_pool_id"] = "personal"
        unit["estimates"] = {"regular/primary": unit["estimates"]["weekly"]}
        self.assertEqual(value["normalization_issues"], [])
        result = q.evaluate(value, unit, NOW)
        self.assertEqual(result["decision"], "CONTINUE", result)

    def test_codex_hard_stop(self):
        raw = {"params": {"rateLimits": {"limitId": "codex", "primary": {"usedPercent": 100},
                                         "rateLimitReachedType": "usage_limit"}}}
        self.assertTrue(self.normalize("codex", raw)["hard_stop"])

    def test_claude_partial_and_null_context(self):
        raw = {"rate_limits": {"five_hour": {"used_percentage": 30, "resets_at": 1793250000}},
               "context_window": {"context_window_size": 200000, "used_percentage": None}}
        value = self.normalize("claude", raw)
        self.assertEqual(len(value["limits"]), 1)
        self.assertEqual(value["limits"][0]["remaining"], 70)
        self.assertFalse(value["coverage_complete"])
        self.assertIsNone(value["context"]["remaining_tokens"])

    def test_claude_overdrawn_spend(self):
        value = self.normalize("claude", {"rate_limits": {"spend_limit": {"used_percentage": 105}}})
        self.assertTrue(value["hard_stop"])
        self.assertIsNone(value["limits"][0]["remaining"])

    def test_antigravity_fraction_amount_disabled(self):
        raw = {"quota": {"weekly": {"remaining_fraction": .25, "reset_time": RESET},
                         "credits": {"remaining_amount": 12, "disabled": True}}}
        value = self.normalize("antigravity", raw, amount_units={"credits": "credits"})
        self.assertEqual([v["unit"] for v in value["limits"]], ["percent", "credits"])
        self.assertEqual([v["remaining"] for v in value["limits"]], [25, 12])
        self.assertTrue(value["hard_stop"])

    def test_antigravity_both_quantities_retained(self):
        value = self.normalize("antigravity", {"quota": {"b": {"remaining_fraction": .5, "remaining_amount": 100}}},
                               amount_units={"b": "requests"})
        self.assertEqual([v["id"] for v in value["limits"]], ["b/percent", "b/amount"])
        self.assertEqual(value["limits"][1]["unit"], "requests")

    def test_antigravity_amount_unit_not_assumed(self):
        value = self.normalize("antigravity", {"quota": {"b": {"remaining_amount": 100}}})
        self.assertEqual(value["limits"][0]["unit"], "unknown")
        self.assertIsNone(value["limits"][0]["remaining"])
        self.assertEqual(value["limits"][0]["confidence"], "unknown")

    def test_empty_payload_is_unknown(self):
        for provider in ("codex", "claude", "antigravity"):
            value = self.normalize(provider, {})
            self.assertEqual(value["limits"], [])
            self.assertIn("quota_missing", value["normalization_issues"])

    def test_invalid_data_does_not_create_balance(self):
        value = self.normalize("antigravity", {"quota": {"b": {"remaining_fraction": float("nan")}}})
        self.assertIsNone(value["limits"][0]["remaining"])
        self.assertEqual(value["limits"][0]["confidence"], "unknown")
        json.dumps(value, allow_nan=False)

    def test_original_observation_time_retained(self):
        value = self.normalize("claude", {"rate_limits": {"five_hour": {"used_percentage": 20}}})
        self.assertEqual(value["observed_at"], NOW)
        self.assertEqual(value["limits"][0]["observed_at"], NOW)

    def test_cli_exit_and_atomic_output(self):
        with tempfile.TemporaryDirectory() as folder:
            base = Path(folder)
            snapshot, unit = fixtures()
            (base / "snapshot.json").write_text(json.dumps(snapshot), encoding="utf-8")
            (base / "unit.json").write_text(json.dumps(unit), encoding="utf-8")
            command = [sys.executable, str(Path(q.__file__)), "evaluate", "--snapshot", str(base / "snapshot.json"),
                       "--unit", str(base / "unit.json"), "--now", NOW]
            completed = subprocess.run(command, capture_output=True, text=True, encoding="utf-8", check=False)
            self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)
            self.assertEqual(json.loads(completed.stdout)["decision"], "CONTINUE")
            snapshot["coverage_complete"] = False
            q.write_json(base / "snapshot.json", snapshot)
            completed = subprocess.run(command, capture_output=True, text=True, encoding="utf-8", check=False)
            self.assertEqual(completed.returncode, 2)
            self.assertEqual(json.loads(completed.stdout)["decision"], q.STOP)
            self.assertFalse(any(p.name.startswith(".snapshot") for p in base.iterdir()))

    def test_cli_cp950_console_preserves_unicode_ids_and_utf8_files(self):
        with tempfile.TemporaryDirectory() as folder:
            base = Path(folder) / "選股研究🧪"
            snapshot, unit = fixtures()
            unit["id"] = "核對選股🧪"
            bucket_id = "每週額度🧪"
            snapshot["limits"][0]["id"] = bucket_id
            unit["estimates"] = {bucket_id: unit["estimates"]["weekly"]}
            q.write_json(base / "額度.json", snapshot)
            q.write_json(base / "工作.json", unit)
            self.assertIn(unit["id"], (base / "工作.json").read_text(encoding="utf-8"))
            result = subprocess.run(
                [sys.executable, str(Path(q.__file__)), "evaluate", "--snapshot", str(base / "額度.json"),
                 "--unit", str(base / "工作.json"), "--now", NOW],
                capture_output=True, env=dict(os.environ, PYTHONIOENCODING="cp950"), check=False)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            document = json.loads(result.stdout.decode("utf-8"))
            self.assertEqual(document["decision"], "CONTINUE")
            self.assertEqual(document["unit_id"], unit["id"])
            self.assertEqual(document["checks"][0]["id"], bucket_id)


if __name__ == "__main__":
    unittest.main()
