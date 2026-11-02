import copy
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import sys
import subprocess
import tempfile
import threading
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import council

ADAPTER = Path(__file__).resolve().parents[1] / "examples" / "demo_adapter.py"


def fixture(name="demo", scenario="success"):
    requested = {"model": "synthetic-model", "effort": "synthetic-effort", "fast": False, "modes": {}}
    return {"provider": name, "enabled": True, "command": [sys.executable, "-B", str(ADAPTER), scenario],
            "timeout_seconds": 4, "max_attempts": 1, "requested": requested,
            "evidence": {"authenticated": True, "source_kind": "native_probe",
                         "source_ref": "SYNTHETIC TEST FIXTURE - no account capability claim",
                         "account_alias": "offline-fixture", "observed_at": council.stamp(),
                         "billing": "subscription", "subscription_eligible": True,
                         "settings": copy.deepcopy(requested), "quota": {"state": "available", "remaining": None}}}


class CouncilTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.config = {"schema_version": 1, "host": "coordinator", "seats": [fixture()]}

    def run_council(self, config=None, **kwargs):
        return council.ask(config or self.config, self.base, "測試 question; $() remains text", self.base / "run", **kwargs)

    def test_completed_unknown_effective_stays_null(self):
        result = self.run_council()
        self.assertEqual(result["status"], "completed")
        self.assertIsNone(result["seats"][0]["effective"])
        self.assertIn("$()", result["seats"][0]["answer"])
        self.assertEqual(json.loads((self.base / "run" / "receipt.json").read_text(encoding="utf-8")), result)

    def test_mixed_success_failure_keeps_answer(self):
        self.config["seats"].append(fixture("other", "auth"))
        result = self.run_council()
        self.assertEqual(result["status"], "partial")
        self.assertEqual(result["seats"][0]["status"], "completed")
        self.assertEqual(result["seats"][1]["error"], "auth")

    def test_timeout_is_not_retried(self):
        seat = fixture(scenario="wait")
        seat.update(timeout_seconds=0.2, max_attempts=2)
        self.config["seats"] = [seat]
        result = self.run_council()
        self.assertEqual(result["seats"][0]["status"], "timeout")
        self.assertEqual(len(result["seats"][0]["attempts"]), 1)

    def test_cancellation_event(self):
        self.config["seats"] = [fixture(scenario="wait")]
        cancelled = threading.Event()
        timer = threading.Timer(0.2, cancelled.set)
        timer.start()
        self.addCleanup(timer.join)
        result = self.run_council(cancelled=cancelled)
        self.assertEqual(result["seats"][0]["status"], "cancelled")

    def test_existing_cancel_file_never_spawns(self):
        marker = self.base / "cancel"
        marker.touch()
        with patch("process_runner.subprocess.Popen") as popen:
            result = self.run_council(cancel_file=marker)
        popen.assert_not_called()
        self.assertEqual(result["seats"][0]["status"], "cancelled")

    def test_transient_explicit_retry(self):
        self.config["seats"] = [fixture(scenario="transient")]
        self.config["seats"][0]["max_attempts"] = 2
        result = self.run_council()
        self.assertEqual(result["status"], "completed")
        self.assertEqual(len(result["seats"][0]["attempts"]), 2)
        self.assertNotIn("error", result["seats"][0])

    def test_default_no_retry(self):
        self.config["seats"] = [fixture(scenario="transient")]
        result = self.run_council()
        self.assertEqual(result["status"], "failed")
        self.assertEqual(len(result["seats"][0]["attempts"]), 1)

    def test_incomplete_or_model_mismatch_is_failure(self):
        for scenario in ("incomplete", "mismatch"):
            with self.subTest(scenario=scenario):
                result = council.run_seat(fixture(scenario=scenario), self.base, "question", "job", self.base, threading.Event())
                self.assertEqual(result["status"], "failed")

    def test_auth_quota_unknown_never_retry(self):
        for scenario in ("auth", "quota", "unknown"):
            seat = fixture(scenario=scenario)
            seat["max_attempts"] = 2
            with self.subTest(scenario=scenario):
                result = council.run_seat(seat, self.base, "question", "job", self.base, threading.Event())
                self.assertEqual(len(result["attempts"]), 1)

    def test_credentials_not_inherited_and_recursion_guard_set(self):
        self.config["seats"] = [fixture(scenario="environment")]
        with patch.dict(os.environ, {"EXAMPLE_API_KEY": "synthetic-test-value"}):
            result = self.run_council()
        answer = json.loads(result["seats"][0]["answer"])
        self.assertEqual(answer, {"guard": "1", "secret_present": False})

    def test_native_evidence_fail_closed(self):
        cases = [{"billing": "api"}, {"billing": "unknown"}, {"quota": {"state": "unknown"}},
                 {"quota": {"state": "available", "remaining": 0}},
                 {"quota": {"state": "available", "remaining": -1}},
                 {"quota": {"state": "exhausted"}}, {"authenticated": False}, {"settings": None},
                 {"subscription_eligible": False}, {"source_kind": "public_docs"}, {"account_alias": None},
                 {"observed_at": (datetime.now(timezone.utc) - timedelta(days=2)).isoformat()},
                 {"observed_at": (datetime.now(timezone.utc) + timedelta(days=2)).isoformat()}]
        for changes in cases:
            seat = fixture()
            seat["evidence"].update(changes)
            with self.subTest(changes=changes):
                self.assertEqual(council.resolve_seat(seat, self.base)["status"], "blocked")

    def test_null_invalid_settings_fail_closed(self):
        for field, value in (("evidence", None), ("requested", None), ("command", None), ("enabled", False)):
            seat = fixture()
            seat[field] = value
            self.assertEqual(council.resolve_seat(seat, self.base)["status"], "blocked")

    def test_paid_fast_and_missing_model_blocked(self):
        for field, value in (("fast", True), ("model", ""), ("effort", None), ("modes", [])):
            seat = fixture()
            seat["requested"][field] = value
            self.assertEqual(council.resolve_seat(seat, self.base)["status"], "blocked")

    def test_recursion_blocks_before_output_creation(self):
        with patch.dict(os.environ, {"COUNCIL_ACTIVE": "1"}):
            with self.assertRaises(ValueError):
                self.run_council()
        self.assertFalse((self.base / "run").exists())

    def test_existing_run_directory_not_overwritten(self):
        (self.base / "run").mkdir()
        with self.assertRaises(FileExistsError):
            self.run_council()

    def test_schema_roster_validation(self):
        path = self.base / "config.json"
        variants = [dict(self.config, seats=[]), dict(self.config, seats=[fixture("coordinator")]),
                    dict(self.config, seats=[fixture(), fixture()]), dict(self.config, schema_version=2)]
        for config in variants:
            path.write_text(json.dumps(config), encoding="utf-8")
            with self.assertRaises(ValueError):
                council.load_config(path)

    def test_bound_timeout_and_attempts(self):
        path = self.base / "config.json"
        for field, value in (("timeout_seconds", 0), ("timeout_seconds", float("nan")), ("max_attempts", 3), ("max_attempts", True)):
            config = copy.deepcopy(self.config)
            config["seats"][0][field] = value
            path.write_text(json.dumps(config), encoding="utf-8")
            with self.assertRaises(ValueError):
                council.load_config(path)

    def test_protocol_identity_and_nonzero_exit(self):
        response = {"protocol_version": 1, "job_id": "job", "provider": "demo", "terminal": True,
                    "status": "completed", "answer": "answer", "effective": None}
        for code, identity in ((1, "job"), (0, "wrong")):
            result = council.parse_response(json.dumps(response), code, {}, identity, "demo")
            self.assertEqual(result["status"], "failed")

    def test_effective_paid_billing_rejected(self):
        response = {"protocol_version": 1, "job_id": "job", "provider": "demo", "terminal": True,
                    "status": "completed", "answer": "answer", "effective": {"billing": "api"}}
        self.assertEqual(council.parse_response(json.dumps(response), 0, {}, "job", "demo")["status"], "failed")

    def test_duplicate_response_fields_are_rejected_at_all_levels(self):
        prefix = ('{"protocol_version":1,"job_id":"job","provider":"demo",'
                  '"terminal":true,"status":"completed","answer":"answer",')
        suffixes = ['"effective":{"billing":"api","billing":null}}',
                    '"effective":{"modes":{"fast":true,"fast":false}}}',
                    '"effective":null,"effective":null}']
        for suffix in suffixes:
            with self.subTest(suffix=suffix):
                result = council.parse_response(prefix + suffix, 0, {}, "job", "demo")
                self.assertEqual(result["status"], "failed")
                self.assertIn("duplicate JSON key", result["error"])
                self.assertFalse(result["retryable"])

    def test_nonfinite_response_numbers_are_rejected_at_all_levels(self):
        for number in ("NaN", "Infinity", "-Infinity", "1e999", "-1e999"):
            response = ('{"protocol_version":1,"job_id":"job","provider":"demo",'
                        '"terminal":true,"status":"completed","answer":"answer",'
                        '"effective":{"extra":[{"number":' + number + '}]}}')
            with self.subTest(number=number):
                result = council.parse_response(response, 0, {}, "job", "demo")
                self.assertEqual(result["status"], "failed")
                self.assertIn("non-finite JSON number", result["error"])

    def test_config_rejects_ambiguous_json(self):
        path = self.base / "invalid.json"
        valid = json.dumps(self.config)
        cases = [valid.replace('"schema_version": 1', '"schema_version": 2, "schema_version": 1'),
                 valid.replace('"billing": "subscription"', '"billing": "api", "billing": "subscription"'),
                 valid.replace('"remaining": null', '"remaining": NaN'),
                 valid.replace('"remaining": null', '"remaining": 1e999')]
        for raw in cases:
            with self.subTest(raw=raw):
                path.write_text(raw, encoding="utf-8")
                with self.assertRaises(ValueError):
                    council.load_config(path)

    def test_retry_revalidates_expired_evidence_before_dispatch(self):
        seat = fixture(scenario="transient")
        seat["max_attempts"] = 2
        observed = datetime.fromisoformat(seat["evidence"]["observed_at"])

        class Clock(datetime):
            current = observed + timedelta(hours=24) - timedelta(milliseconds=1)

            @classmethod
            def now(cls, tz=None):
                return cls.current

        def transient(command, **kwargs):
            Clock.current = observed + timedelta(hours=24, milliseconds=1)
            response = {"protocol_version": 1, "job_id": "job", "provider": "demo", "terminal": True,
                        "status": "failed", "error_kind": "transient", "inference_started": False}
            return subprocess.CompletedProcess(command, 1, json.dumps(response), "")

        with patch.object(council, "datetime", Clock), patch.object(council, "run_process", side_effect=transient) as run:
            result = council.run_seat(seat, self.base, "question", "job", self.base, threading.Event())
        self.assertEqual(run.call_count, 1)
        self.assertEqual(result["status"], "blocked")
        self.assertIn("stale", result["error"])
        self.assertEqual(result["attempts"], [{"attempt": 1, "status": "failed", "error": "transient"}])

    def test_checkpoint_recovery_preserves_final(self):
        folder = self.base / "receipt-test"
        folder.mkdir()
        receipt = {"status": "completed", "job_id": "job"}
        with patch.object(Path, "replace", side_effect=PermissionError("synthetic lock")):
            council.checkpoint(folder, receipt)
        self.assertEqual(json.loads((folder / "recovery.json").read_text()), receipt)

    def test_plan_does_not_execute(self):
        with patch.object(council, "run_process") as run:
            result = council.plan(self.config, self.base)
        run.assert_not_called()
        self.assertEqual(result["status"], "ready")


if __name__ == "__main__":
    unittest.main(verbosity=2)
