import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

spec = importlib.util.spec_from_file_location("release_scan", Path(__file__).parents[1] / "scripts/check_release.py")
scanner = importlib.util.module_from_spec(spec)
spec.loader.exec_module(scanner)


class ReleaseScanTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        for name in ("tiered-model-routing", "quota-aware-handoff", "portable-council"):
            target = self.root / "skills" / name
            target.mkdir(parents=True)
            (target / "SKILL.md").write_text("Synthetic fixture", encoding="utf-8")

    def test_clean_fixture_passes(self):
        self.assertEqual(scanner.scan(self.root)["status"], "PASS")

    def test_sensitive_values_not_echoed(self):
        secret = "ghp_" + "x" * 32
        (self.root / "oops.md").write_text(secret, encoding="utf-8")
        result = scanner.scan(self.root)
        self.assertEqual(result["findings"][0]["category"], "credential_material")
        self.assertNotIn(secret, json.dumps(result))

    def test_private_home_path_is_flagged(self):
        (self.root / "oops.md").write_text("C:" + "/" + "Users" + "/" + "synthetic-person/file", encoding="utf-8")
        result = scanner.scan(self.root)
        self.assertEqual(result["findings"][0]["category"], "private_home_path")

    def test_log_and_unknown_binary_are_flagged(self):
        (self.root / "private.log").write_text("synthetic", encoding="utf-8")
        (self.root / "unknown.bin").write_bytes(b"\x00\xff")
        categories = {item["category"] for item in scanner.scan(self.root)["findings"]}
        self.assertIn("private_or_generated_artifact", categories)
        self.assertIn("unreviewed_file_type", categories)

    def test_missing_skill_is_flagged(self):
        (self.root / "skills" / "portable-council" / "SKILL.md").unlink()
        self.assertIn("missing_skill", {item["category"] for item in scanner.scan(self.root)["findings"]})

    def test_git_history_is_excluded_from_source_archive_selection(self):
        git_dir = self.root / ".git"
        git_dir.mkdir()
        (git_dir / "private-history").write_text("not source", encoding="utf-8")
        self.assertFalse(any(".git" in path.relative_to(self.root).parts for path in scanner.files(self.root)))


if __name__ == "__main__":
    unittest.main()
