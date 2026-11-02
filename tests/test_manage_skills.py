import importlib.util
from pathlib import Path
import tempfile
import unittest

spec = importlib.util.spec_from_file_location("manage_skills", Path(__file__).parents[1] / "scripts/manage_skills.py")
manager = importlib.util.module_from_spec(spec)
spec.loader.exec_module(manager)


class InstallTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.source = self.base / "source"
        self.source.mkdir()
        (self.source / "SKILL.md").write_text("version one", encoding="utf-8")
        self.dest = self.base / "skills"
        self.dest.mkdir()
        self.name = "tiered-model-routing"

    def call(self, action, apply=True):
        return manager.manage(action, self.name, self.dest, self.source, apply)

    def test_dry_run_does_not_write(self):
        self.assertEqual(self.call("install", False)["status"], "DRY_RUN")
        self.assertEqual(list(self.dest.iterdir()), [])

    def test_install_update_remove_preserve_backups(self):
        self.call("install")
        (self.source / "SKILL.md").write_text("version two", encoding="utf-8")
        result = self.call("update")
        self.assertEqual((Path(result["backup"]) / "SKILL.md").read_text(), "version one")
        result = self.call("remove")
        self.assertFalse((self.dest / self.name).exists())
        self.assertEqual((Path(result["backup"]) / "SKILL.md").read_text(), "version two")

    def test_modified_install_is_not_overwritten_or_removed(self):
        self.call("install")
        (self.dest / self.name / "SKILL.md").write_text("my changes", encoding="utf-8")
        for action in ("update", "remove"):
            with self.assertRaisesRegex(ValueError, "local changes"):
                self.call(action)
        self.assertEqual((self.dest / self.name / "SKILL.md").read_text(), "my changes")

    def test_unmanaged_install_is_preserved(self):
        target = self.dest / self.name
        target.mkdir()
        (target / "SKILL.md").write_text("personal installation")
        for action in ("install", "update", "remove"):
            with self.assertRaises(ValueError):
                self.call(action)

    def test_unknown_skill_and_missing_root_rejected(self):
        with self.assertRaises(ValueError):
            manager.manage("install", "../escape", self.dest, self.source, True)
        with self.assertRaises(ValueError):
            manager.manage("install", self.name, self.base / "missing", self.source, True)

    def test_source_overlap_rejected(self):
        with self.assertRaisesRegex(ValueError, "overlap"):
            manager.manage("install", self.name, self.source, self.source, True)

    def test_source_link_rejected_if_supported(self):
        link = self.source / "external"
        try:
            link.symlink_to(self.base / "outside")
        except OSError:
            self.skipTest("Symlink creation unavailable")
        with self.assertRaisesRegex(ValueError, "Symlink"):
            self.call("install")


if __name__ == "__main__":
    unittest.main()
