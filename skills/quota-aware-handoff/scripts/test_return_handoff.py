"""Offline round-trip integrity tests; no provider calls or actual quota data."""
import copy
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

import handoff as h


class ReturnTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        (self.root / "result.txt").write_text("source state", encoding="utf-8")
        (self.root / "note.md").write_text("Synthetic source", encoding="utf-8")
        self.source = h.checkpoint(self.root, self.root / "note.md", ["result.txt"],
                                   handoff_id="source-1", task_id="task-1", next_unit_id="unit-1",
                                   owner="origin", quiescent=True)
        self.receipt = {
            "schema_version": 1, "kind": "return", "origin_ref": "origin-1",
            "source_handoff_id": "source-1", "source_manifest_sha256": self.source["manifest_sha256"],
            "task_id": "task-1", "delegated_unit_id": "unit-1", "return_handoff_id": "return-1",
            "outcome": "completed", "summary": "Synthetic unit result", "remaining_work": []}

    def freeze(self, receipt=None, text=None, task_id="task-1", include_receipt=True):
        (self.root / "result.txt").write_text("changed result", encoding="utf-8")
        (self.root / "note.md").write_text("Synthetic return", encoding="utf-8")
        encoded = text if text is not None else json.dumps(receipt if receipt is not None else self.receipt)
        (self.root / "return-receipt.json").write_text(encoded, encoding="utf-8")
        files = ["result.txt"] + (["return-receipt.json"] if include_receipt else [])
        self.returned = h.checkpoint(self.root, self.root / "note.md", files, handoff_id="return-1",
                                     task_id=task_id, next_unit_id="origin-reconcile", owner="successor",
                                     quiescent=True)

    def inspect(self, **changes):
        args = dict(project=self.root, handoff_id="return-1", manifest_sha256=self.returned["manifest_sha256"],
                    origin_ref="origin-1", source_handoff_id="source-1",
                    source_manifest_sha256=self.source["manifest_sha256"])
        return h.inspect_return(**{**args, **changes})

    def test_round_trip_reads_frozen_source_and_does_not_merge(self):
        self.freeze()
        (self.root / "result.txt").write_text("independent origin edit", encoding="utf-8")
        lock = self.root / ".ai-relay" / "dispatch.lock"
        lock.write_text("retained owner", encoding="utf-8")
        before = {p.relative_to(self.root): p.read_bytes() for p in self.root.rglob("*") if p.is_file()}
        report = self.inspect()
        self.assertTrue(report["ready_for_reconciliation"], report)
        self.assertFalse(report["task_complete"])
        self.assertFalse(report["files_merged"])
        self.assertEqual(report["outcome"], "completed")
        self.assertEqual(before, {p.relative_to(self.root): p.read_bytes() for p in self.root.rglob("*") if p.is_file()})

    def test_all_unit_outcomes_preserved(self):
        for outcome in ("completed", "partial", "blocked", "failed", "cancelled"):
            with self.subTest(outcome=outcome), tempfile.TemporaryDirectory() as scratch:
                # Independent test fixtures keep immutable checkpoint IDs unique.
                test = ReturnTests("test_round_trip_reads_frozen_source_and_does_not_merge")
                test.setUp()
                try:
                    test.receipt["outcome"] = outcome
                    test.freeze()
                    self.assertEqual(test.inspect()["outcome"], outcome)
                finally:
                    test.doCleanups()

    def test_independent_expectations_cannot_be_replaced_by_receipt(self):
        self.freeze()
        for kwargs in ({"origin_ref": "another-origin"}, {"source_handoff_id": "absent"},
                       {"source_manifest_sha256": "0" * 64}, {"manifest_sha256": "0" * 64}):
            with self.subTest(kwargs=kwargs):
                self.assertFalse(self.inspect(**kwargs)["ready_for_reconciliation"])

    def test_wrong_task_unit_or_return_identity_rejected(self):
        self.receipt["delegated_unit_id"] = "wrong-unit"
        self.freeze()
        self.assertFalse(self.inspect()["ready_for_reconciliation"])

    def test_wrong_return_task_rejected(self):
        self.freeze(task_id="another-task")
        self.assertFalse(self.inspect()["ready_for_reconciliation"])

    def test_missing_receipt_rejected(self):
        self.freeze(include_receipt=False)
        self.assertFalse(self.inspect()["ready_for_reconciliation"])

    def test_tampered_return_artifact_rejected(self):
        self.freeze()
        artifact = Path(self.returned["checkpoint_path"]) / "artifacts" / "result.txt"
        artifact.write_text("tampered", encoding="utf-8")
        self.assertFalse(self.inspect()["ready_for_reconciliation"])

    def test_tampered_source_rejected(self):
        self.freeze()
        artifact = Path(self.source["checkpoint_path"]) / "artifacts" / "result.txt"
        artifact.write_text("tampered baseline", encoding="utf-8")
        self.assertFalse(self.inspect()["ready_for_reconciliation"])

    def test_duplicate_receipt_keys_rejected(self):
        text = json.dumps(self.receipt)[:-1] + ', "origin_ref": "origin-1"}'
        self.freeze(text=text)
        self.assertFalse(self.inspect()["ready_for_reconciliation"])

    def test_nonfinite_receipt_rejected_even_extra_field(self):
        self.freeze(text=json.dumps({**self.receipt, "untrusted_extra": float("nan")}))
        self.assertFalse(self.inspect()["ready_for_reconciliation"])

    def test_bool_schema_rejected(self):
        self.freeze(receipt={**self.receipt, "schema_version": True})
        self.assertFalse(self.inspect()["ready_for_reconciliation"])

    def test_summary_and_remaining_bounds(self):
        self.freeze(receipt={**self.receipt, "summary": " ", "remaining_work": [""]})
        self.assertFalse(self.inspect()["ready_for_reconciliation"])

    def test_claimed_completion_extra_field_does_not_grant_acceptance(self):
        self.freeze(receipt={**self.receipt, "task_complete": True, "delivered": True})
        report = self.inspect()
        self.assertTrue(report["ready_for_reconciliation"])
        self.assertFalse(report["task_complete"])

    def test_cli_round_trip(self):
        self.freeze()
        proc = subprocess.run([sys.executable, "-B", str(Path(h.__file__)), "inspect-return",
            "--project", str(self.root), "--handoff-id", "return-1",
            "--manifest-sha256", self.returned["manifest_sha256"], "--origin-ref", "origin-1",
            "--source-handoff-id", "source-1", "--source-manifest-sha256", self.source["manifest_sha256"]],
            capture_output=True, text=True, encoding="utf-8", timeout=10)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertTrue(json.loads(proc.stdout)["ready_for_reconciliation"])


if __name__ == "__main__":
    unittest.main()
