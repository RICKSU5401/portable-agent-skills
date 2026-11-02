#!/usr/bin/env python3
"""Behavioral launch tests with a local fake receiver; no AI calls or network."""
import copy
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
from datetime import datetime, timezone, timedelta
from types import SimpleNamespace
from unittest.mock import patch


SCRIPT = Path(__file__).with_name("dispatch.py")
SPEC = importlib.util.spec_from_file_location("relay_dispatch", SCRIPT)
dispatch = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(dispatch)
HSPEC = importlib.util.spec_from_file_location("relay_handoff", SCRIPT.with_name("handoff.py"))
handoff = importlib.util.module_from_spec(HSPEC)
HSPEC.loader.exec_module(handoff)


FAKE_RECEIVER = r'''
import hashlib, json, os, sys, time
from pathlib import Path
from datetime import datetime, timezone, timedelta
root = Path.cwd()
mode = sys.argv[1]
(root / "receiver-started").write_text("started", encoding="utf-8")
print("receiver output is local and may be sensitive", flush=True)
try:
    pointer = json.loads((root / ".ai-relay/current.json").read_text(encoding="utf-8"))
    ident = pointer["handoff_id"]
    snap = root / ".ai-relay/checkpoints" / ident
    manifest = json.loads((snap / "manifest.json").read_text(encoding="utf-8"))
    for item in manifest["artifacts"]:
        assert hashlib.sha256((root / item["path"]).read_bytes()).hexdigest() == item["sha256"]
    if mode == "exit":
        sys.exit(4)
    if mode == "no_ack":
        time.sleep(0.6)
        sys.exit(0)
    ack = {"handoff_id": ident, "task_id": manifest["task_id"],
           "next_unit_id": manifest["next_unit_id"], "target": "claude",
           "manifest_sha256": pointer["manifest_sha256"], "status": "verified_ready",
           "checks": {"artifacts": True, "validation_evidence": True, "authorization_scope": True},
           "timestamp": datetime.now(timezone.utc).isoformat()}
    if mode == "wrong_hash": ack["manifest_sha256"] = "0" * 64
    if mode == "wrong_target": ack["target"] = "grok"
    if mode == "false_check": ack["checks"]["artifacts"] = False
    if mode == "stale": ack["timestamp"] = (datetime.now(timezone.utc)-timedelta(minutes=5)).isoformat()
    if mode == "mutate": (root / "result.txt").write_text("changed without permit", encoding="utf-8")
    if mode in ("refresh_enough", "refresh_insufficient", "refresh_older",
                "refresh_growth", "refresh_ttl", "refresh_stale"):
        snapshot_path = Path(os.environ["FAKE_QUOTA_SNAPSHOT"])
        snapshot = json.loads(snapshot_path.read_text(encoding="utf-8"))
        observed = datetime.now(timezone.utc)
        if mode == "refresh_older":
            observed = datetime.fromisoformat(snapshot["observed_at"]) - timedelta(seconds=1)
        if mode == "refresh_stale":
            attempt = json.loads((root / ".ai-relay/dispatch-attempts" / (ident + ".json")).read_text(encoding="utf-8"))
            observed = datetime.fromisoformat(attempt["started_at"]) - timedelta(
                seconds=snapshot["freshness_seconds"] - 0.01)
            time.sleep(0.05)  # Fresh at launch time, stale at the real post-ACK check.
        snapshot["observed_at"] = observed.isoformat()
        snapshot["limits"][0]["observed_at"] = observed.isoformat()
        snapshot["limits"][0]["remaining"] = 1 if mode == "refresh_insufficient" else 79
        if mode == "refresh_growth": snapshot["limits"][0]["remaining"] = 81
        if mode == "refresh_ttl": snapshot["freshness_seconds"] *= 2
        snapshot_temporary = snapshot_path.with_suffix(".tmp")
        snapshot_temporary.write_text(json.dumps(snapshot), encoding="utf-8")
        os.replace(snapshot_temporary, snapshot_path)
    ackpath = root / ".ai-relay/acks" / (ident + ".json")
    if mode in ("split_ack", "split_late"):
        encoded = json.dumps(ack)
        middle = len(encoded) // 2
        with ackpath.open("w", encoding="utf-8") as stream:
            stream.write(encoded[:middle])
            stream.flush()
            os.fsync(stream.fileno())
            time.sleep(0.5 if mode == "split_late" else 0.15)
            stream.write(encoded[middle:])
            stream.flush()
    else:
        temporary = ackpath.with_suffix(".tmp")
        temporary.write_text(json.dumps(ack), encoding="utf-8")
        os.replace(temporary, ackpath)
    if mode == "exit_after_ack":
        time.sleep(0.2)
        sys.exit(9)
    permit = root / ".ai-relay/permits" / (ident + ".json")
    until = time.monotonic() + 2
    while time.monotonic() < until:
        if permit.exists():
            grant = json.loads(permit.read_text(encoding="utf-8"))
            assert grant["status"] == "proceed" and grant["handoff_id"] == ident
            (root / "receiver-ran").write_text("one unit after permit", encoding="utf-8")
            break
        time.sleep(0.02)
finally:
    (root / "receiver-finished").write_text("finished", encoding="utf-8")
'''


class DispatchTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.base = Path(self.tmp.name).resolve()
        self.project = self.base / "project"
        self.project.mkdir()
        (self.project / "result.txt").write_text("saved artifact", encoding="utf-8")
        (self.project / "check.log").write_text("validation passed", encoding="utf-8")
        note = self.project / "draft.md"
        note.write_text("# HANDOFF\nContinue unit-2 only after verification.\n", encoding="utf-8")
        verifications = self.project / "verification-input.json"
        verifications.write_text(json.dumps([{
            "argv": ["python", "check.py"], "cwd": ".", "exit_code": 0,
            "status": "passed", "timestamp": self.now(), "evidence_files": ["check.log"]
        }]), encoding="utf-8")
        handoff.checkpoint(project=self.project, note=note, files=["result.txt", "check.log"],
                           handoff_id="handoff-1", task_id="task-1", next_unit_id="unit-2",
                           owner="codex", quiescent=True, verification_file=verifications)
        self.fake = self.base / "receiver.py"
        self.fake.write_text(FAKE_RECEIVER, encoding="utf-8")
        self.config = self.base / "authorized.json"
        self.snapshot = self.base / "snapshot.json"
        self.unit = self.base / "unit.json"
        self.config_data = {
            "schema_version": 1, "approval_ref": "test-user-authorization",
            "max_attempts_per_handoff": 1,
            "profiles": {"claude": {
                "enabled": True, "authorized": True, "authorization_ref": "test-user-authorization",
                "allowed_projects": [str(self.project)], "allowed_task_ids": ["task-1"],
                "executable": sys.executable, "argv": [str(self.fake), "valid", "--model", "test-model", "{prompt}"],
                "quota_pool_id": "test-claude-personal", "model": "test-model", "account_ref": "personal",
                "billing_mode": "subscription", "auth_verified": True, "fast_mode": False,
                "billing_verified_at": self.now()
            }}
        }
        self.snapshot_data = {
            "provider": "claude", "quota_pool_id": "test-claude-personal", "model": "test-model",
            "account_ref": "personal", "observed_at": self.now(), "freshness_seconds": 120,
            "coverage_complete": True, "hard_stop": False,
            "limits": [{"id": "weekly", "unit": "percent", "remaining": 80,
                        "observed_at": self.now(), "source": "test-fixture", "confidence": "measured", "reset_at": None}],
            "context": {"effective_window": 200000, "remaining_tokens": 150000, "used_percent": 25}
        }
        self.unit_data = {
            "id": "unit-2", "quota_pool_id": "test-claude-personal", "model": "test-model",
            "estimate_basis": "Synthetic upper bound for local unit test, not a real account estimate.",
            "safety_factor": 1.5, "context_needed_tokens": 20000,
            "estimates": {"weekly": {"unit": "percent", "next_cost_upper_bound": 5,
                                     "handoff_reserve": 5, "uncertainty_margin": 2}}
        }
        self.env = {k: v for k, v in os.environ.items() if k not in dispatch.API_ENV}
        self.save()

    @staticmethod
    def now():
        return datetime.now(timezone.utc).isoformat()

    def save(self):
        for path, value in ((self.config, self.config_data), (self.snapshot, self.snapshot_data), (self.unit, self.unit_data)):
            path.write_text(json.dumps(value), encoding="utf-8")

    def tearDown(self):
        # Fake children have a bounded lifetime. Observe their completion; never kill.
        if (self.project / "receiver-started").exists():
            deadline = time.monotonic() + 4
            while not (self.project / "receiver-finished").exists() and time.monotonic() < deadline:
                time.sleep(0.02)
        # The marker precedes interpreter shutdown; Windows can still hold the
        # inherited log handle briefly. Retry cleanup, but surface lasting errors.
        cleanup_deadline = time.monotonic() + 4
        while True:
            try:
                self.tmp.cleanup()
                break
            except PermissionError:
                if time.monotonic() >= cleanup_deadline:
                    raise
                time.sleep(0.02)

    def run_dispatch(self, execute=False, action="launch", timeout=3, env=None):
        argv = [sys.executable, str(SCRIPT), action, "--project", str(self.project),
                "--config", str(self.config), "--target", "claude", "--snapshot", str(self.snapshot),
                "--unit", str(self.unit), "--ack-timeout", str(timeout)]
        if execute:
            argv.append("--execute")
        result = subprocess.run(argv, capture_output=True, text=True, encoding="utf-8", timeout=12,
                                env=self.env if env is None else env)
        return result, json.loads(result.stdout)

    def blocked(self, reason):
        process, report = self.run_dispatch(execute=True)
        self.assertEqual(process.returncode, 2, process.stderr)
        self.assertEqual(report["status"], "BLOCKED", report)
        self.assertEqual(report["reason"], reason)
        self.assertFalse((self.project / "receiver-started").exists())
        return process, report

    def test_fast_mode_unknown_or_on_is_blocked(self):
        for value in (None, True, "false"):
            self.config_data["profiles"]["claude"]["fast_mode"] = value
            self.save()
            self.blocked("fast_mode_must_be_verified_off")

    def test_generic_provider_profile_plan(self):
        self.config_data["profiles"]["native-provider"] = self.config_data["profiles"].pop("claude")
        self.snapshot_data["provider"] = "native-provider"
        self.save()
        args = self.dispatch_arguments(action="plan", execute=False, target="native-provider")
        with patch.dict(os.environ, self.env, clear=True):
            report, code = dispatch.dispatch(args)
        self.assertEqual(code, 0)
        self.assertEqual(report["status"], "PLAN_READY")
        self.assertFalse((self.project / "receiver-started").exists())

    def test_explicit_fast_flag_rejected(self):
        self.config_data["profiles"]["claude"]["argv"].append("--fast")
        self.save()
        self.blocked("unsafe_or_billing_override_argument")

    def test_option_terminator_cannot_hide_model_flag(self):
        with self.assertRaisesRegex(dispatch.RelayError, "option_terminator"):
            dispatch.check_argv(["--", "--model", "test-model", "{prompt}"], "test-model")

    def test_known_fast_configuration_overrides_rejected(self):
        for setting in ('service_tier="fast"', 'service_tier=priority', 'fastMode=true'):
            with self.subTest(setting=setting), self.assertRaisesRegex(dispatch.RelayError, "unsafe_or_billing"):
                dispatch.check_argv(["--model", "test-model", "--config", setting, "{prompt}"], "test-model")

    def test_absolute_paths_with_literal_braces_are_supported(self):
        path = str(self.base / "native-{installation}" / "entry.py")
        dispatch.check_argv([path, "--model", "test-model", "{prompt}"], "test-model")

    def test_duplicate_and_nonfinite_json_rejected(self):
        for encoded in ('{"authorized":false,"authorized":true}', '{"extra":NaN}', '{"extra":1e999}'):
            with self.subTest(encoded=encoded), self.assertRaises(dispatch.RelayError):
                dispatch.strict_json(encoded)

    def dispatch_arguments(self, **changes):
        arguments = dict(project=str(self.project), config=str(self.config), target="claude",
                         snapshot=str(self.snapshot), unit=str(self.unit), action="launch",
                         execute=True, ack_timeout=3)
        return SimpleNamespace(**{**arguments, **changes})

    def assert_no_launch_attempt(self):
        self.assertFalse((self.project / "receiver-started").exists())
        self.assertFalse((self.project / ".ai-relay/dispatch.lock").exists())
        self.assertFalse((self.project / ".ai-relay/dispatch-attempts/handoff-1.json").exists())
        self.assertFalse((self.project / ".ai-relay/permits/handoff-1.json").exists())

    def test_default_launch_and_plan_never_start_or_mutate(self):
        for action in ("launch", "plan"):
            process, report = self.run_dispatch(action=action)
            self.assertEqual(process.returncode, 0, report)
            self.assertEqual(report["status"], "PLAN_READY")
            self.assertFalse(report["executed"])
            self.assertTrue(report["quiescent_asserted_at_checkpoint"])
            self.assertFalse(report["runtime_writers_verified"])
            self.assertFalse((self.project / "receiver-started").exists())
            self.assertFalse((self.project / ".ai-relay/dispatch.lock").exists())

    def test_plan_accepts_utf8_bom_inputs(self):
        for path in (self.config, self.unit, self.snapshot):
            path.write_bytes(b"\xef\xbb\xbf" + path.read_bytes())
        process, report = self.run_dispatch(action="plan")
        self.assertEqual(process.returncode, 0, process.stderr)
        self.assertEqual(report["status"], "PLAN_READY")
        self.assert_no_launch_attempt()

    def test_cli_json_preserves_unicode_paths_with_cp950_stdout(self):
        renamed = self.base / "專案-🚀"
        self.project.rename(renamed)
        self.project = renamed
        self.config_data["profiles"]["claude"]["allowed_projects"] = [str(self.project)]
        self.save()
        handoff.checkpoint(project=self.project, note=self.project / "draft.md",
                           files=["result.txt", "check.log"], handoff_id="handoff-unicode",
                           task_id="task-1", next_unit_id="unit-2", owner="codex",
                           quiescent=True, verification_file=self.project / "verification-input.json")
        process, report = self.run_dispatch(
            execute=True, env={**self.env, "PYTHONIOENCODING": "cp950"})
        self.assertEqual(process.returncode, 0, process.stdout + process.stderr)
        self.assertTrue(process.stdout.isascii())
        self.assertEqual(report["status"], "ACKNOWLEDGED")
        self.assertEqual(report["log_path"], str(self.project / ".ai-relay/logs/handoff-unicode.log"))

    def test_receiver_prompt_contains_exact_inputs_skill_and_identity(self):
        args = SimpleNamespace(project=str(self.project), config=str(self.config), target="claude",
                               snapshot=str(self.snapshot), unit=str(self.unit))
        with patch.dict(os.environ, self.env, clear=True):
            state = dispatch.preflight(args)
        prompt = dispatch.prompt_for(state, "claude", time.time() + 60)
        expected_paths = {"config": str(self.config), "snapshot": str(self.snapshot), "unit": str(self.unit)}
        self.assertEqual(state["input_paths"], expected_paths)
        self.assertEqual(state["skill_path"], str(SCRIPT.resolve().parent.parent / "SKILL.md"))
        for value in [*expected_paths.values(), state["skill_path"],
                      state["handoff"]["handoff_path"], state["handoff"]["manifest_path"],
                      "personal", "test-claude-personal", "test-model"]:
            self.assertIn(json.dumps(value, ensure_ascii=False), prompt)
        self.assertIn("read-only", prompt)
        self.assertIn("quota_guard.py", prompt)
        self.assertIn("handoff.py", prompt)
        self.assertIn("canonical_handoff_path", prompt)
        self.assertNotIn("Read HANDOFF.md", prompt)

    def test_convenience_note_warning_does_not_block_canonical_plan(self):
        (self.project / "HANDOFF.md").write_text("Old convenience copy", encoding="utf-8")
        process, report = self.run_dispatch(action="plan")
        self.assertEqual(process.returncode, 0, process.stderr)
        self.assertEqual(report["status"], "PLAN_READY")
        self.assertTrue(report["handoff_warnings"])
        self.assert_no_launch_attempt()

    def test_checkpoint_lock_blocks_plan_and_launch(self):
        lock = self.project / ".ai-relay/checkpoint.lock"
        lock.write_text("{}", encoding="utf-8")
        for action, execute in (("plan", False), ("launch", True)):
            with self.subTest(action=action):
                process, report = self.run_dispatch(action=action, execute=execute)
                self.assertEqual(process.returncode, 2, process.stderr)
                self.assertEqual(report["reason"], "checkpoint_lock_exists")
                self.assertEqual(lock.read_text(encoding="utf-8"), "{}")
                self.assert_no_launch_attempt()

    def test_missing_hardlink_support_blocks_before_attempt_but_plan_is_read_only(self):
        args = self.dispatch_arguments(action="plan", execute=False)
        before = {str(path.relative_to(self.project)) for path in self.project.rglob("*")}
        with patch.dict(os.environ, self.env, clear=True):
            with patch.object(dispatch.os, "link", side_effect=OSError("synthetic unsupported link")) as link:
                report, code = dispatch.dispatch(args)
                self.assertEqual(code, 0, report)
                link.assert_not_called()
                self.assertEqual(before, {str(path.relative_to(self.project)) for path in self.project.rglob("*")})
                args.action, args.execute = "launch", True
                with self.assertRaisesRegex(dispatch.RelayError, "atomic_permit_publication_unavailable"):
                    dispatch.dispatch(args)
                self.assertEqual(link.call_count, 1)
        self.assert_no_launch_attempt()
        self.assertFalse(list((self.project / ".ai-relay/permits").glob(".permit-probe-*")))

    def test_all_launch_observations_must_cover_ack_timeout(self):
        original_snapshot, original_config = copy.deepcopy(self.snapshot_data), copy.deepcopy(self.config_data)
        for constraint in ("snapshot", "weekly", "context", "billing", "weekly:reset_at"):
            with self.subTest(constraint=constraint):
                self.snapshot_data = copy.deepcopy(original_snapshot)
                self.config_data = copy.deepcopy(original_config)
                now = datetime.now(timezone.utc)
                near_expiry = (now - timedelta(seconds=90)).isoformat()
                if constraint == "snapshot": self.snapshot_data["observed_at"] = near_expiry
                elif constraint == "weekly": self.snapshot_data["limits"][0]["observed_at"] = near_expiry
                elif constraint == "context": self.snapshot_data["context"]["observed_at"] = near_expiry
                elif constraint == "billing":
                    self.config_data["profiles"]["claude"]["billing_verified_at"] = (
                        now - timedelta(seconds=86400 - 30)).isoformat()
                else: self.snapshot_data["limits"][0]["reset_at"] = (now + timedelta(seconds=30)).isoformat()
                self.save()
                process, report = self.run_dispatch(execute=True, timeout=60)
                self.assertEqual(process.returncode, 2, process.stdout + process.stderr)
                self.assertEqual(report["reason"], "insufficient_ack_headroom")
                self.assertIn(constraint, report["headroom_reasons"])
                self.assert_no_launch_attempt()

    def test_headroom_uses_snapshot_refreshed_under_lock(self):
        args = self.dispatch_arguments(ack_timeout=60)
        original_preflight = dispatch.preflight
        calls = 0

        def shorten_observation_ttl(arguments):
            nonlocal calls
            state = original_preflight(arguments)
            calls += 1
            if calls == 1:
                self.snapshot_data["freshness_seconds"] = 30
                self.save()
            return state

        with patch.dict(os.environ, self.env, clear=True):
            with patch.object(dispatch, "preflight", side_effect=shorten_observation_ttl):
                with self.assertRaisesRegex(dispatch.RelayError, "insufficient_ack_headroom"):
                    dispatch.dispatch(args)
        self.assertEqual(calls, 2)
        self.assert_no_launch_attempt()

    def test_authorization_is_required(self):
        self.config_data["profiles"]["claude"]["authorized"] = False
        self.save()
        self.blocked("target_not_authorized")

    def test_project_cannot_supply_its_own_authorization(self):
        self.config = self.project / "authorized.json"
        self.save()
        self.blocked("authorization_config_must_be_outside_project")

    def test_project_scope_is_exact_and_not_a_parent_wildcard(self):
        self.config_data["profiles"]["claude"]["allowed_projects"] = [str(self.base)]
        self.save()
        self.blocked("project_outside_authorization_scope")

    def test_task_scope_requires_exact_task_id(self):
        profile = self.config_data["profiles"]["claude"]
        profile["allowed_task_ids"] = ["another-task"]
        self.save()
        self.blocked("task_outside_authorization_scope")
        profile["allowed_task_ids"] = ["*"]
        self.save()
        self.blocked("explicit_task_scope_required")

    def test_snapshot_pool_account_model_provider_must_match(self):
        for key, value, reason in (
            ("quota_pool_id", "other-pool", "snapshot_identity_mismatch"),
            ("account_ref", "other-user", "snapshot_identity_mismatch"),
            ("model", "other-model", "snapshot_identity_mismatch"),
            ("provider", "grok", "snapshot_provider_mismatch"),
        ):
            with self.subTest(key=key):
                old = self.snapshot_data[key]
                self.snapshot_data[key] = value
                self.save()
                self.blocked(reason)
                self.snapshot_data[key] = old

    def test_unknown_or_insufficient_quota_blocks(self):
        self.snapshot_data["coverage_complete"] = False
        self.save()
        _, report = self.blocked("quota_gate_did_not_continue")
        self.assertIn("quota_coverage_unknown", report["quota_reasons"])
        self.snapshot_data["coverage_complete"] = True
        self.snapshot_data["limits"][0]["remaining"] = 1
        self.save()
        _, report = self.blocked("quota_gate_did_not_continue")
        self.assertIn("weekly:insufficient_budget", report["quota_reasons"])

    def test_next_work_unit_must_match_checkpoint(self):
        self.unit_data["id"] = "unrelated-unit"
        self.save()
        self.blocked("next_unit_identity_mismatch")

    def test_bad_live_hash_blocks_launch(self):
        (self.project / "result.txt").write_text("different bytes", encoding="utf-8")
        self.blocked("handoff_not_verified_and_quiescent")

    def test_billing_attestation_expires(self):
        self.config_data["profiles"]["claude"]["billing_verified_at"] = (
            datetime.now(timezone.utc) - timedelta(days=2)).isoformat()
        self.save()
        self.blocked("billing_attestation_stale_or_future")

    def test_no_unsafe_flags_or_implicit_model(self):
        profile = self.config_data["profiles"]["claude"]
        original = profile["argv"][:]
        profile["argv"].append("--dangerously-skip-permissions")
        self.save()
        self.blocked("unsafe_or_billing_override_argument")
        profile["argv"] = [str(self.fake), "valid", "{prompt}"]
        self.save()
        self.blocked("exactly_one_matching_explicit_model_required")
        profile["argv"] = original

    def test_api_env_is_rejected_without_value_disclosure(self):
        sentinel = "PRIVATE-KEY-DO-NOT-PRINT-437817"
        process, report = self.run_dispatch(execute=True, env={**self.env, "XAI_API_KEY": sentinel})
        self.assertEqual(process.returncode, 2)
        self.assertEqual(report["reason"], "subscription_environment_ambiguous")
        self.assertNotIn(sentinel, process.stdout + process.stderr)
        self.assertFalse((self.project / "receiver-started").exists())

    def assert_preflight_input_update_blocks_launch(self, path, replacement):
        args = SimpleNamespace(project=str(self.project), config=str(self.config), target="claude",
                               snapshot=str(self.snapshot), unit=str(self.unit), action="launch",
                               execute=True, ack_timeout=3)
        original_helper = dispatch.helper
        calls = 0

        def replace_after_validation(name, arguments):
            nonlocal calls
            result = original_helper(name, arguments)
            calls += 1
            if calls == 1:
                # Inputs have been parsed and validated. A concurrent writer
                # replaces one before preflight returns its pinned launch state.
                temporary = path.with_suffix(".tmp")
                temporary.write_text(json.dumps(replacement), encoding="utf-8")
                os.replace(temporary, path)
            return result

        with patch.dict(os.environ, self.env, clear=True):
            with patch.object(dispatch, "helper", side_effect=replace_after_validation):
                with self.assertRaisesRegex(dispatch.RelayError, "inputs_changed_before_launch"):
                    dispatch.dispatch(args)
        self.assertEqual(calls, 2)
        self.assertFalse((self.project / "receiver-started").exists())
        self.assertFalse((self.project / ".ai-relay/dispatch.lock").exists())
        self.assertFalse((self.project / ".ai-relay/dispatch-attempts/handoff-1.json").exists())
        self.assertFalse((self.project / ".ai-relay/permits/handoff-1.json").exists())

    def test_config_replaced_after_read_cannot_launch_old_state(self):
        self.config_data["profiles"]["claude"]["argv"][1] = "no_ack"
        self.assert_preflight_input_update_blocks_launch(self.config, self.config_data)

    def test_unit_replaced_after_read_cannot_launch_old_state(self):
        self.unit_data["estimates"]["weekly"]["next_cost_upper_bound"] = 10
        self.assert_preflight_input_update_blocks_launch(self.unit, self.unit_data)

    def test_valid_ack_grants_one_unit_and_retains_lock(self):
        process, report = self.run_dispatch(execute=True)
        self.assertEqual(process.returncode, 0, process.stdout + process.stderr)
        self.assertEqual(report["status"], "ACKNOWLEDGED")
        self.assertFalse(report["task_complete"])
        self.assertTrue(report["lock_retained"])
        self.assertTrue(report["log_sensitive_local_only"])
        permit = self.project / ".ai-relay/permits/handoff-1.json"
        self.assertEqual(json.loads(permit.read_text(encoding="utf-8"))["scope"], "one_work_unit")
        again, second = self.run_dispatch(execute=True)
        self.assertEqual(again.returncode, 2)
        self.assertEqual(second["reason"], "dispatch_lock_exists_recovery_required")

    def test_failed_ack_keeps_attempt_even_after_operator_clears_lock(self):
        self.config_data["profiles"]["claude"]["argv"][1] = "wrong_hash"
        self.save()
        process, report = self.run_dispatch(execute=True)
        self.assertEqual(process.returncode, 3, report)
        self.assertEqual(report["reason"], "ack_identity_mismatch")
        self.assertTrue((self.project / ".ai-relay/dispatch-attempts/handoff-1.json").exists())
        self.assertFalse((self.project / ".ai-relay/permits/handoff-1.json").exists())
        # Simulated operator clearing only a lock does not authorize a retry.
        (self.project / ".ai-relay/dispatch.lock").unlink()
        again, second = self.run_dispatch(execute=True)
        self.assertEqual(again.returncode, 2)
        self.assertEqual(second["reason"], "handoff_already_attempted")

    def test_stale_ack_is_rejected(self):
        self.config_data["profiles"]["claude"]["argv"][1] = "stale"
        self.save()
        process, report = self.run_dispatch(execute=True)
        self.assertEqual(process.returncode, 3, report)
        self.assertEqual(report["reason"], "ack_timestamp_outside_launch")

    def test_split_write_ack_is_waited_for_until_complete(self):
        self.config_data["profiles"]["claude"]["argv"][1] = "split_ack"
        self.save()
        process, report = self.run_dispatch(execute=True)
        self.assertEqual(process.returncode, 0, process.stdout + process.stderr)
        self.assertEqual(report["status"], "ACKNOWLEDGED")
        self.assertTrue((self.project / ".ai-relay/permits/handoff-1.json").exists())

    def test_split_write_finishing_after_deadline_gets_no_permit(self):
        self.config_data["profiles"]["claude"]["argv"][1] = "split_late"
        self.save()
        process, report = self.run_dispatch(execute=True, timeout=0.2)
        self.assertEqual(process.returncode, 3, report)
        self.assertEqual(report["status"], "WAITING_FOR_ACK")
        self.assertEqual(report["reason"], "ack_timeout")
        self.assertFalse((self.project / ".ai-relay/permits/handoff-1.json").exists())

    def test_ack_read_retry_does_not_tolerate_parseable_invalid_ack(self):
        directory = self.project / ".ai-relay/acks"
        directory.mkdir()
        path = directory / "handoff-1.json"
        expected = {"handoff_id": "handoff-1", "task_id": "task-1", "next_unit_id": "unit-2",
                    "target": "claude", "manifest_sha256": "a" * 64}
        valid = {**expected, "status": "verified_ready", "timestamp": self.now(),
                 "checks": {"artifacts": True, "validation_evidence": True, "authorization_scope": True}}
        started, deadline = time.time() - 1, time.time() + 10
        path.write_text(json.dumps(valid), encoding="utf-8")
        with patch.object(Path, "open", side_effect=PermissionError("synthetic temporary file lock")):
            self.assertFalse(dispatch.validate_ack(path, expected, started, deadline))
        self.assertTrue(dispatch.validate_ack(path, expected, started, deadline))
        invalid_cases = [
            ({"target": "grok"}, "ack_identity_mismatch"),
            ({"status": "not_ready"}, "ack_verification_missing"),
            ({"checks": {"artifacts": False}}, "ack_verification_missing"),
            ({"timestamp": (datetime.now(timezone.utc) - timedelta(minutes=5)).isoformat()},
             "ack_timestamp_outside_launch"),
        ]
        for changes, reason in invalid_cases:
            with self.subTest(reason=reason, changes=changes):
                path.write_text(json.dumps({**valid, **changes}), encoding="utf-8")
                with self.assertRaisesRegex(dispatch.RelayError, reason):
                    dispatch.validate_ack(path, expected, started, deadline)

    def test_mutation_before_permit_is_detected(self):
        self.config_data["profiles"]["claude"]["argv"][1] = "mutate"
        self.save()
        process, report = self.run_dispatch(execute=True)
        self.assertEqual(process.returncode, 3, report)
        self.assertEqual(report["reason"], "handoff_not_verified_and_quiescent")
        self.assertFalse((self.project / ".ai-relay/permits/handoff-1.json").exists())

    def test_no_ack_timeout_does_not_retry_or_release(self):
        self.config_data["profiles"]["claude"]["argv"][1] = "no_ack"
        self.save()
        process, report = self.run_dispatch(execute=True, timeout=0.15)
        self.assertEqual(process.returncode, 3, report)
        self.assertEqual(report["status"], "WAITING_FOR_ACK")
        self.assertTrue(report["pid"] > 0)
        self.assertTrue((self.project / ".ai-relay/dispatch.lock").exists())
        self.assertFalse((self.project / ".ai-relay/permits/handoff-1.json").exists())

    def test_live_quota_refresh_with_enough_budget_gets_permit(self):
        self.config_data["profiles"]["claude"]["argv"][1] = "refresh_enough"
        self.save()
        process, report = self.run_dispatch(execute=True, env={**self.env, "FAKE_QUOTA_SNAPSHOT": str(self.snapshot)})
        self.assertEqual(process.returncode, 0, report)
        self.assertEqual(report["status"], "ACKNOWLEDGED")
        self.assertEqual(json.loads(self.snapshot.read_text(encoding="utf-8"))["limits"][0]["remaining"], 79)
        self.assertTrue((self.project / ".ai-relay/permits/handoff-1.json").exists())

    def test_live_quota_refresh_with_insufficient_budget_cannot_get_permit(self):
        self.config_data["profiles"]["claude"]["argv"][1] = "refresh_insufficient"
        self.save()
        process, report = self.run_dispatch(execute=True, env={**self.env, "FAKE_QUOTA_SNAPSHOT": str(self.snapshot)})
        self.assertEqual(process.returncode, 3, report)
        self.assertEqual(report["status"], "RECOVERY_REQUIRED")
        self.assertEqual(report["reason"], "quota_gate_did_not_continue")
        self.assertIn("weekly:insufficient_budget", report["quota_reasons"])
        self.assertFalse((self.project / ".ai-relay/permits/handoff-1.json").exists())

    def test_live_quota_growth_without_reset_cannot_get_permit(self):
        self.config_data["profiles"]["claude"]["argv"][1] = "refresh_growth"
        self.save()
        process, report = self.run_dispatch(execute=True, env={**self.env, "FAKE_QUOTA_SNAPSHOT": str(self.snapshot)})
        self.assertEqual(process.returncode, 3, report)
        self.assertEqual(report["reason"], "unobserved_budget_growth")
        self.assertFalse((self.project / ".ai-relay/permits/handoff-1.json").exists())

    def test_live_quota_ttl_extension_cannot_get_permit(self):
        self.config_data["profiles"]["claude"]["argv"][1] = "refresh_ttl"
        self.save()
        process, report = self.run_dispatch(execute=True, env={**self.env, "FAKE_QUOTA_SNAPSHOT": str(self.snapshot)})
        self.assertEqual(process.returncode, 3, report)
        self.assertEqual(report["reason"], "unobserved_budget_growth")
        self.assertFalse((self.project / ".ai-relay/permits/handoff-1.json").exists())

    def test_post_ack_quota_check_uses_current_time_and_reports_staleness(self):
        self.config_data["profiles"]["claude"]["argv"][1] = "refresh_stale"
        self.save()
        process, report = self.run_dispatch(execute=True, env={**self.env, "FAKE_QUOTA_SNAPSHOT": str(self.snapshot)})
        self.assertEqual(process.returncode, 3, report)
        self.assertEqual(report["reason"], "quota_gate_did_not_continue")
        self.assertIn("snapshot:stale", report["quota_reasons"])
        self.assertFalse((self.project / ".ai-relay/permits/handoff-1.json").exists())

    def test_snapshot_refill_requires_passed_reset_and_new_window_evidence(self):
        now = time.time()
        def iso(offset):
            return datetime.fromtimestamp(now + offset, timezone.utc).isoformat()
        previous = copy.deepcopy(self.snapshot_data)
        previous["observed_at"] = previous["limits"][0]["observed_at"] = iso(-20)
        previous["limits"][0].update(remaining=50, reset_at=iso(-10))
        current = copy.deepcopy(previous)
        current["observed_at"] = current["limits"][0]["observed_at"] = iso(0)
        current["limits"][0].update(remaining=80, reset_at=iso(60))
        dispatch.validate_snapshot_transition(previous, current, now=now)
        for missing_proof in ("old_reset", "reset_not_passed", "new_observation", "new_reset"):
            with self.subTest(missing_proof=missing_proof):
                old, new = copy.deepcopy(previous), copy.deepcopy(current)
                if missing_proof == "old_reset": old["limits"][0]["reset_at"] = None
                elif missing_proof == "reset_not_passed": old["limits"][0]["reset_at"] = iso(10)
                elif missing_proof == "new_observation": new["limits"][0]["observed_at"] = iso(-15)
                else: new["limits"][0]["reset_at"] = None
                with self.assertRaisesRegex(dispatch.RelayError, "unobserved_budget_growth"):
                    dispatch.validate_snapshot_transition(old, new, now=now)

    def test_snapshot_transition_preserves_limit_identity_and_units(self):
        for field, replacement, reason in (("id", "other-window", "quota_snapshot_limits_changed"),
                                           ("unit", "tokens", "quota_snapshot_limit_unit_changed")):
            with self.subTest(field=field):
                changed = copy.deepcopy(self.snapshot_data)
                changed["limits"][0][field] = replacement
                with self.assertRaisesRegex(dispatch.RelayError, reason):
                    dispatch.validate_snapshot_transition(self.snapshot_data, changed)

    def test_live_quota_timestamp_regression_cannot_get_permit(self):
        self.config_data["profiles"]["claude"]["argv"][1] = "refresh_older"
        self.save()
        process, report = self.run_dispatch(execute=True, env={**self.env, "FAKE_QUOTA_SNAPSHOT": str(self.snapshot)})
        self.assertEqual(process.returncode, 3, report)
        self.assertEqual(report["reason"], "quota_snapshot_timestamp_regressed")
        self.assertFalse((self.project / ".ai-relay/permits/handoff-1.json").exists())

    def test_receiver_crash_does_not_retry(self):
        self.config_data["profiles"]["claude"]["argv"][1] = "exit"
        self.save()
        process, report = self.run_dispatch(execute=True)
        self.assertEqual(process.returncode, 3, report)
        self.assertEqual(report["reason"], "receiver_exited_without_ack")
        self.assertTrue((self.project / ".ai-relay/dispatch.lock").exists())

    def test_receiver_exit_during_final_preflight_cannot_get_permit(self):
        self.config_data["profiles"]["claude"]["argv"][1] = "exit_after_ack"
        self.save()
        args = SimpleNamespace(project=str(self.project), config=str(self.config), target="claude",
                               snapshot=str(self.snapshot), unit=str(self.unit), action="launch",
                               execute=True, ack_timeout=3)
        original_preflight = dispatch.preflight
        calls = 0

        def delayed_preflight(arguments):
            nonlocal calls
            calls += 1
            if calls == 3:
                time.sleep(0.5)
            return original_preflight(arguments)

        with patch.dict(os.environ, self.env, clear=True):
            with patch.object(dispatch, "preflight", side_effect=delayed_preflight):
                report, code = dispatch.dispatch(args)
        self.assertEqual(calls, 3)
        self.assertEqual(code, 3, report)
        self.assertEqual(report["status"], "RECOVERY_REQUIRED")
        self.assertEqual(report["reason"], "receiver_exited_before_permit")
        self.assertFalse((self.project / ".ai-relay/permits/handoff-1.json").exists())

    def test_preexisting_ack_is_not_trusted(self):
        ackdir = self.project / ".ai-relay/acks"
        ackdir.mkdir()
        (ackdir / "handoff-1.json").write_text("{}", encoding="utf-8")
        self.blocked("preexisting_ack_or_permit")


if __name__ == "__main__":
    unittest.main()
