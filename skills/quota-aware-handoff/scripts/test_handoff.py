#!/usr/bin/env python3
"""Meaningful integrity, path, and cooperative-lock tests; no model calls."""

import hashlib
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch


SPEC = importlib.util.spec_from_file_location("handoff", Path(__file__).with_name("handoff.py"))
handoff = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(handoff)


class HandoffTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name).resolve()
        (self.root / "src").mkdir()
        (self.root / "src" / "app.bin").write_bytes(b"\x00\xffdeliverable\x01")
        (self.root / "check.log").write_text("passed\n", encoding="utf-8")
        self.note = self.root / "handoff-draft.md"
        self.note.write_text("# HANDOFF\nTask task-1; next unit-2.\n成果已保存。\n", encoding="utf-8")
        self.records = self.root / "verification-input.json"
        self.records.write_text(json.dumps([{
            "argv": ["python", "-c", "raise RuntimeError('never execute this record')"],
            "cwd": ".", "exit_code": 0, "status": "passed",
            "timestamp": "2026-01-01T00:00:00Z", "evidence_files": ["check.log"]
        }]), encoding="utf-8")

    def tearDown(self):
        self.temp.cleanup()

    def create(self, identifier="cp-1", files=None, **overrides):
        arguments = {"project": self.root, "note": self.note,
                     "files": files or ["src/app.bin", "check.log"],
                     "handoff_id": identifier, "task_id": "task-1",
                     "next_unit_id": "unit-2", "owner": "codex",
                     "quiescent": True, "verification_file": self.records}
        arguments.update(overrides)
        return handoff.checkpoint(**arguments)

    def snapshot(self):
        return self.root / ".ai-relay" / "checkpoints" / "cp-1"

    def assert_bad(self, **kwargs):
        result = handoff.verify(self.root, **kwargs)
        self.assertFalse(result["verified"], result)
        self.assertTrue(result["reasons"], result)
        return result

    def test_roundtrip_binary_and_records_are_not_executed(self):
        created = self.create()
        report = handoff.verify(self.root, live=True)
        self.assertTrue(report["verified"], report)
        self.assertEqual(report["manifest_sha256"], created["manifest_sha256"])
        self.assertEqual(report["task_id"], "task-1")
        self.assertEqual(report["next_unit_id"], "unit-2")
        self.assertTrue(report["convenience_note_matches"])
        self.assertTrue(report["quiescent_asserted_at_checkpoint"])
        self.assertTrue(report["quiescent"])
        self.assertFalse(report["runtime_writers_verified"])
        self.assertFalse(report["verification_records_reexecuted"])
        self.assertIn("age_seconds", report["freshness"])
        self.assertEqual((self.snapshot() / "artifacts/src/app.bin").read_bytes(),
                         (self.root / "src/app.bin").read_bytes())

    def test_cli_unicode_paths_roundtrip_with_legacy_console_encoding(self):
        project = self.root / "研究-🚦"
        (project / "src").mkdir(parents=True)
        (project / "src/app.bin").write_bytes((self.root / "src/app.bin").read_bytes())
        (project / "check.log").write_bytes((self.root / "check.log").read_bytes())
        script = str(Path(__file__).with_name("handoff.py"))
        env = {**os.environ, "PYTHONIOENCODING": "cp950"}
        commands = [
            [sys.executable, script, "checkpoint", "--project", str(project),
             "--note", str(self.note), "--files", "src/app.bin", "check.log",
             "--handoff-id", "unicode-1", "--task-id", "task-1",
             "--next-unit-id", "unit-2", "--owner", "codex", "--quiescent",
             "--verification-file", str(self.records)],
            [sys.executable, script, "verify", "--project", str(project), "--live"]
        ]
        for argv in commands:
            with self.subTest(command=argv[2]):
                result = subprocess.run(argv, capture_output=True, env=env, timeout=10)
                self.assertEqual(result.returncode, 0, result.stderr.decode("cp950", errors="replace"))
                report = json.loads(result.stdout.decode("utf-8"))
                self.assertTrue(result.stdout.isascii())
                self.assertEqual(report["checkpoint_path"], str(project / ".ai-relay/checkpoints/unicode-1"))

    def test_live_corruption_does_not_change_snapshot(self):
        self.create()
        (self.root / "src/app.bin").write_bytes(b"changed")
        self.assertTrue(handoff.verify(self.root)["verified"])
        self.assertIn("live artifact", self.assert_bad(live=True)["reasons"][0])

    def test_snapshot_artifact_corruption(self):
        self.create()
        (self.snapshot() / "artifacts/src/app.bin").write_bytes(b"bad")
        self.assertIn("mismatch", self.assert_bad()["reasons"][0])

    def test_canonical_note_corruption(self):
        self.create()
        (self.snapshot() / "HANDOFF.md").write_text("bad", encoding="utf-8")
        self.assertIn("HANDOFF.md", self.assert_bad()["reasons"][0])

    def test_convenience_note_is_not_canonical(self):
        self.create()
        (self.root / "HANDOFF.md").write_text("old convenience note", encoding="utf-8")
        report = handoff.verify(self.root, live=True)
        self.assertTrue(report["verified"], report)
        self.assertFalse(report["convenience_note_matches"])
        self.assertEqual(report["handoff_path"], str(self.snapshot() / "HANDOFF.md"))
        self.assertTrue(any("project HANDOFF.md differs from the canonical note" in warning
                            for warning in report["warnings"]))
        self.assertIsNone(handoff.verify(self.root)["convenience_note_matches"])

    def test_missing_convenience_note_does_not_invalidate_snapshot(self):
        self.create()
        (self.root / "HANDOFF.md").unlink()
        report = handoff.verify(self.root, live=True)
        self.assertTrue(report["verified"], report)
        self.assertIsNone(report["convenience_note_matches"])

    def test_unreadable_convenience_note_warns_without_invalidating_snapshot(self):
        self.create()
        original_digest = handoff._digest

        def deny_convenience_note(path):
            if path == self.root / "HANDOFF.md":
                raise PermissionError("convenience copy is unreadable")
            return original_digest(path)

        with patch.object(handoff, "_digest", side_effect=deny_convenience_note):
            report = handoff.verify(self.root, live=True)
        self.assertTrue(report["verified"], report)
        self.assertIsNone(report["convenience_note_matches"])
        self.assertTrue(any("project HANDOFF.md convenience copy could not be checked" in warning
                            for warning in report["warnings"]))

    def test_manifest_corruption(self):
        self.create()
        manifest = self.snapshot() / "manifest.json"
        manifest.write_bytes(manifest.read_bytes() + b" ")
        self.assertIn("manifest SHA-256 mismatch", self.assert_bad()["reasons"][0])

    def test_tampered_pointer_hash_and_traversal(self):
        self.create()
        pointer = self.root / ".ai-relay/current.json"
        data = json.loads(pointer.read_text())
        data["manifest_sha256"] = "0" * 64
        pointer.write_text(json.dumps(data))
        self.assertIn("pointer", self.assert_bad()["reasons"][0])
        data["handoff_id"] = "../../escape"
        pointer.write_text(json.dumps(data))
        self.assert_bad()

    def test_duplicate_checkpoint_id_is_rejected_and_old_snapshot_preserved(self):
        self.create()
        original = (self.snapshot() / "manifest.json").read_bytes()
        with self.assertRaises(handoff.HandoffError):
            self.create()
        (self.root / "src/app.bin").write_bytes(b"new revision")
        self.create("cp-2")
        self.assertEqual((self.snapshot() / "manifest.json").read_bytes(), original)
        self.assertTrue(handoff.verify(self.root, "cp-1")["verified"])
        self.assertTrue(handoff.verify(self.root, live=True)["verified"])

    def test_expanded_manifest_limit_preserves_last_valid_checkpoint(self):
        self.create()
        pointer = self.root / ".ai-relay/current.json"
        old_pointer = pointer.read_bytes()
        old_note = (self.root / "HANDOFF.md").read_bytes()
        # Every record is within the public field/count bounds and the compact
        # source JSON is below 8 MiB; the pretty-printed manifest exceeds it.
        record = {
            "argv": ["x" * 53] * 128, "cwd": ".", "exit_code": 0,
            "status": "passed", "timestamp": "2026-01-01T00:00:00Z",
            "evidence_files": ["check.log"]
        }
        self.records.write_text(json.dumps([record] * 1000, separators=(",", ":")),
                                encoding="utf-8")
        self.assertLess(self.records.stat().st_size, handoff.MAX_JSON_BYTES)
        with self.assertRaisesRegex(handoff.HandoffError, "serialized manifest exceeds"):
            self.create("oversize")
        self.assertEqual(pointer.read_bytes(), old_pointer)
        self.assertEqual((self.root / "HANDOFF.md").read_bytes(), old_note)
        self.assertFalse((self.root / ".ai-relay/checkpoints/oversize").exists())
        self.assertFalse(list((self.root / ".ai-relay/checkpoints").glob(".staging-*")))
        self.assertTrue(handoff.verify(self.root, live=True)["verified"])

    def test_paths_absolute_traversal_and_sensitive_paths_rejected(self):
        forbidden = ["../outside", "/tmp/outside", "C:\\temp\\x", "src/../../x",
                     ".env", ".env.local", ".ssh/id_rsa", "auth.json", "token.json",
                     "keys/api-token.txt", ".ai-relay/current.json", "relay-config.json",
                     "HANDOFF.md", "src/file:stream", "src/file."]
        for path in forbidden:
            with self.subTest(path=path), self.assertRaises(handoff.HandoffError):
                self.create(files=[path])

    def test_quiescent_assertion_required(self):
        with self.assertRaises(handoff.HandoffError):
            self.create(quiescent=False)

    def test_symlink_file_parent_and_relay_directory_rejected(self):
        link = self.root / "linked"
        try:
            link.symlink_to(self.root / "src", target_is_directory=True)
        except (OSError, NotImplementedError):
            self.skipTest("symlink privilege unavailable")
        with self.assertRaises(handoff.HandoffError):
            self.create(files=["linked/app.bin"], verification_file=None)
        link.unlink()
        (self.root / "linked.bin").symlink_to(self.root / "src/app.bin")
        with self.assertRaises(handoff.HandoffError):
            self.create(files=["linked.bin"], verification_file=None)
        # No checkpoint was published; replace the empty relay directory safely.
        relay = self.root / ".ai-relay"
        if relay.exists():
            import shutil
            shutil.rmtree(relay)
        relay.symlink_to(self.root / "src", target_is_directory=True)
        with self.assertRaises(handoff.HandoffError):
            self.create()

    def test_existing_lock_is_never_removed_or_overwritten(self):
        (self.root / ".ai-relay").mkdir()
        lock = self.root / ".ai-relay/checkpoint.lock"
        original = b'{"token":"someone-else","created_at":"2000-01-01T00:00:00Z"}'
        lock.write_bytes(original)
        with self.assertRaises(handoff.HandoffError):
            self.create()
        self.assertEqual(lock.read_bytes(), original)

    def test_replaced_lock_is_not_released(self):
        lock = self.root / ".ai-relay/checkpoint.lock"
        with handoff._lock(self.root):
            lock.write_text('{"token":"replacement"}', encoding="utf-8")
        self.assertTrue(lock.exists())
        self.assertEqual(json.loads(lock.read_text())["token"], "replacement")

    def test_live_process_lock_contention(self):
        with handoff._lock(self.root):
            result = subprocess.run([
                sys.executable, str(Path(__file__).with_name("handoff.py")),
                "checkpoint", "--project", str(self.root), "--note", str(self.note),
                "--files", "src/app.bin", "check.log", "--handoff-id", "other-writer",
                "--task-id", "task-1", "--next-unit-id", "unit-2", "--owner", "claude",
                "--quiescent", "--verification-file", str(self.records)
            ], capture_output=True, text=True, timeout=10)
            self.assertEqual(result.returncode, 1, result.stderr)
            self.assertIn("lock already exists", json.loads(result.stdout)["reasons"][0])
        self.assertFalse((self.root / ".ai-relay/checkpoint.lock").exists())

    def test_snapshot_symlink_rejected_even_when_bytes_match(self):
        self.create()
        snapshot_file = self.snapshot() / "artifacts/src/app.bin"
        original = snapshot_file.read_bytes()
        snapshot_file.unlink()
        try:
            snapshot_file.symlink_to(self.root / "src/app.bin")
        except (OSError, NotImplementedError):
            snapshot_file.write_bytes(original)
            self.skipTest("symlink privilege unavailable")
        self.assertIn("symlink", self.assert_bad()["reasons"][0])

    def test_missing_checkpoint_and_missing_live_file(self):
        self.assert_bad()
        self.create()
        (self.root / "check.log").unlink()
        self.assert_bad(live=True)

    def test_freshness_limit(self):
        self.create()
        result = self.assert_bad(max_age_seconds=0)
        self.assertTrue(result["freshness"]["stale"])

    def test_missing_evidence_refused_and_no_executable_side_effect(self):
        self.records.write_text(json.dumps([{
            "argv": ["touch", str(self.root / "would-be-created")], "cwd": ".",
            "exit_code": 0, "status": "passed", "timestamp": "2026-01-01T00:00:00Z",
            "evidence_files": ["missing.log"]
        }]))
        with self.assertRaises(handoff.HandoffError):
            self.create()
        self.assertFalse((self.root / "would-be-created").exists())

    def test_unexecuted_records_allow_absent_command_and_require_null_exit(self):
        base = {"timestamp": "2026-01-01T00:00:00Z", "evidence_files": ["check.log"]}
        good = [dict(base, status=status, argv=argv, cwd=cwd, exit_code=None)
                for status in ("not_run", "skipped")
                for argv, cwd in ((None, None), ([], None), (["python", "-m", "unittest"], "src"))]
        self.records.write_text(json.dumps(good), encoding="utf-8")
        self.create("cp-unexecuted")
        report = handoff.verify(self.root, live=True)
        self.assertTrue(report["verified"], report)
        self.assertEqual(report["verification_count"], 6)
        self.assertIn("Some recorded checks did not pass; the receiver must assess them before continuing.",
                      report["warnings"])
        manifest = json.loads((self.root / ".ai-relay/checkpoints/cp-unexecuted/manifest.json").read_text())
        self.assertEqual([(r["argv"], r["cwd"]) for r in manifest["verifications"][:3]],
                         [(None, None), ([], None), (["python", "-m", "unittest"], "src")])
        for status in ("not_run", "skipped"):
            for exit_code in (0, 1, -1):
                with self.subTest(status=status, exit_code=exit_code):
                    self.records.write_text(json.dumps([dict(
                        base, status=status, argv=None, cwd=None, exit_code=exit_code)]))
                    with self.assertRaisesRegex(handoff.HandoffError, "exit_code null"):
                        self.create("bad-exit")
            for argv, cwd in (([""], None), ("python", None), (None, "../outside"), (None, "C:/abs")):
                with self.subTest(status=status, argv=argv, cwd=cwd):
                    self.records.write_text(json.dumps([dict(
                        base, status=status, argv=argv, cwd=cwd, exit_code=None)]))
                    with self.assertRaises(handoff.HandoffError):
                        self.create("bad-shape")

    def test_executed_records_keep_strict_command_and_cwd(self):
        base = {"timestamp": "2026-01-01T00:00:00Z", "evidence_files": ["check.log"]}
        for status, exit_code in (("passed", 0), ("failed", 1)):
            for argv, cwd in ((None, "."), ([], "."), (["python"], None),
                              (["python"], "../outside"), (["python"], "C:/abs")):
                with self.subTest(status=status, argv=argv, cwd=cwd):
                    self.records.write_text(json.dumps([dict(
                        base, status=status, argv=argv, cwd=cwd, exit_code=exit_code)]))
                    with self.assertRaises(handoff.HandoffError):
                        self.create("bad-executed")
        for status, exit_code in (("passed", None), ("passed", 1), ("failed", None), ("failed", 0)):
            with self.subTest(status=status, exit_code=exit_code):
                self.records.write_text(json.dumps([dict(
                    base, status=status, argv=["python"], cwd=".", exit_code=exit_code)]))
                with self.assertRaises(handoff.HandoffError):
                    self.create("bad-exit-executed")
        self.assertFalse((self.root / ".ai-relay/checkpoints/bad-executed").exists())

    def test_manifest_traversal_is_refused_even_if_hashes_recomputed(self):
        self.create()
        manifest_path = self.snapshot() / "manifest.json"
        manifest = json.loads(manifest_path.read_text())
        manifest["artifacts"][0]["path"] = "../../outside"
        data = json.dumps(manifest).encode()
        digest = hashlib.sha256(data).hexdigest()
        manifest_path.write_bytes(data)
        (self.snapshot() / "manifest.sha256").write_text(digest + "\n")
        pointer = self.root / ".ai-relay/current.json"
        current = json.loads(pointer.read_text())
        current["manifest_sha256"] = digest
        pointer.write_text(json.dumps(current))
        self.assertIn("traversing", self.assert_bad()["reasons"][0])


if __name__ == "__main__":
    unittest.main()
