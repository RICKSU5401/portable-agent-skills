"""Ambiguous native exports must not hide a depleted balance at the CLI boundary."""
import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

import quota_guard as q
from test_quota_guard import fixtures, NOW


class QuotaJsonTests(unittest.TestCase):
    def test_duplicate_nested_balance_rejected_from_file_and_stdin(self):
        snapshot, _ = fixtures()
        encoded = json.dumps(snapshot, separators=(",", ":")).replace(
            '"remaining":35', '"remaining":0,"remaining":35')
        with tempfile.TemporaryDirectory() as temp:
            source = Path(temp) / "snapshot.json"
            source.write_text(encoded, encoding="utf-8-sig")
            with self.assertRaisesRegex(ValueError, "duplicate_json_key"):
                q.read_json(source)
        with mock.patch.object(q.sys, "stdin", io.StringIO(encoded)):
            with self.assertRaisesRegex(ValueError, "duplicate_json_key"):
                q.read_json("-")

    def test_nonfinite_and_exponent_overflow_rejected(self):
        for value in ("NaN", "Infinity", "-Infinity", "1e9999", "-1e9999"):
            with self.subTest(value=value):
                with self.assertRaisesRegex(ValueError, "nonfinite_json_number"):
                    q.strict_json('{"evidence":{"value":' + value + '}}')

    def test_valid_evidence_keeps_admission_behavior(self):
        snapshot, unit = fixtures()
        parsed = q.strict_json(json.dumps(snapshot))
        self.assertEqual(q.evaluate(parsed, unit, NOW)["decision"], "CONTINUE")

    def test_evaluate_cli_rejects_duplicate_balance(self):
        snapshot, unit = fixtures()
        encoded = json.dumps(snapshot, separators=(",", ":")).replace(
            '"remaining":35', '"remaining":0,"remaining":35')
        with tempfile.TemporaryDirectory() as temp:
            unit_path = Path(temp) / "unit.json"
            unit_path.write_text(json.dumps(unit), encoding="utf-8")
            result = subprocess.run([sys.executable, "-B", q.__file__, "evaluate",
                                     "--snapshot", "-", "--unit", str(unit_path), "--now", NOW],
                                    input=encoded, text=True, capture_output=True, timeout=10)
        self.assertEqual(result.returncode, 2, result.stderr)
        self.assertEqual(json.loads(result.stdout)["decision"], q.STOP)

    def test_normalize_cli_rejects_duplicate_usage_without_writing_output(self):
        encoded = '{"rate_limits":{"window":{"used_percentage":100,"used_percentage":0}}}'
        with tempfile.TemporaryDirectory() as temp:
            source, output = Path(temp) / "usage.json", Path(temp) / "normalized.json"
            source.write_text(encoded, encoding="utf-8")
            result = subprocess.run([sys.executable, "-B", q.__file__, "normalize", "--provider", "claude",
                                     "--input", str(source), "--output", str(output), "--pool", "synthetic-pool",
                                     "--model", "synthetic-model", "--account-ref", "synthetic", "--observed-at", NOW],
                                    text=True, capture_output=True, timeout=10)
            self.assertFalse(output.exists())
        self.assertEqual(result.returncode, 2, result.stderr)
        self.assertEqual(json.loads(result.stdout)["decision"], q.STOP)


if __name__ == "__main__":
    unittest.main()
