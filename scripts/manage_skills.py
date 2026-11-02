#!/usr/bin/env python3
"""Install into an explicit existing directory; never edit host configuration."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import stat
import tempfile

SKILLS = ("tiered-model-routing", "quota-aware-handoff", "portable-council")
MARKER = ".workflow-skill-install.json"


def safe_path(path):
    path = Path(os.path.abspath(path))
    for part in (path, *path.parents):
        if part.exists() or part.is_symlink():
            info = part.lstat()
            if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
                raise ValueError("Symlink/reparse paths are not supported")
    return path


def digest_tree(root):
    root = safe_path(root)
    result = {}
    for path in sorted(root.rglob("*")):
        safe_path(path)
        rel = path.relative_to(root)
        if path.is_dir():
            continue
        if not path.is_file():
            raise ValueError("Only ordinary files/directories are supported")
        if rel.as_posix() == MARKER:
            continue
        if "__pycache__" in rel.parts or path.suffix == ".pyc":
            raise ValueError("Remove bytecode before installing")
        result[rel.as_posix()] = hashlib.sha256(path.read_bytes()).hexdigest()
    if "SKILL.md" not in result:
        raise ValueError("Missing SKILL.md")
    return result


def installed_manifest(target, name):
    marker = safe_path(target / MARKER)
    if not marker.is_file():
        raise ValueError("Existing installation has no ownership manifest; preserve it")
    saved = json.loads(marker.read_text(encoding="utf-8"))
    if saved.get("schema_version") != 1 or saved.get("skill") != name:
        raise ValueError("Invalid ownership manifest")
    if saved.get("files") != digest_tree(target):
        raise ValueError("Installation has local changes; preserve and review them first")
    return saved


def manage(action, name, root, source, apply=False):
    if name not in SKILLS or action not in ("install", "update", "remove"):
        raise ValueError("Invalid skill or action")
    root = safe_path(root)
    if not root.is_dir():
        raise ValueError("Create the explicit destination directory first")
    target = safe_path(root / name)
    if target.parent != root:
        raise ValueError("Invalid destination")
    if action == "install":
        if target.exists():
            raise ValueError("Destination exists; use update for a managed installation")
    else:
        if not target.is_dir():
            raise ValueError("Managed installation not found")
        installed_manifest(target, name)
    source_hashes = None
    if action != "remove":
        source = safe_path(source)
        if source == target or source in target.parents or target in source.parents:
            raise ValueError("Source/destination overlap")
        source_hashes = digest_tree(source)
    result = {"status": "DRY_RUN", "action": action, "skill": name,
              "target": str(target), "backup": None}
    if not apply:
        return result
    # Always preserve removed/replaced installations. No recursive deletion of user data.
    staged = None
    if action != "remove":
        staged = Path(tempfile.mkdtemp(prefix=".skill-stage-", dir=root))
        shutil.copytree(source, staged, dirs_exist_ok=True)
        if digest_tree(staged) != source_hashes:
            raise ValueError("Source changed during staging; staging retained for review")
        (staged / MARKER).write_text(json.dumps({"schema_version": 1, "skill": name,
                                               "files": source_hashes}, indent=2) + "\n", encoding="utf-8")
    if action != "install":
        # Revalidate immediately before moving to a unique sibling backup.
        installed_manifest(target, name)
        backup_holder = Path(tempfile.mkdtemp(prefix=f".skill-backup-{name}-", dir=root))
        backup = backup_holder / name
        target.rename(backup)
        result["backup"] = str(backup)
    try:
        if staged is not None:
            staged.rename(target)
    except OSError:
        if result["backup"] and not target.exists():
            Path(result["backup"]).rename(target)
        raise
    result["status"] = "APPLIED"
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("install", "update", "remove"))
    parser.add_argument("skill", choices=SKILLS)
    parser.add_argument("--root", required=True, type=Path,
                        help="Existing skills directory explicitly chosen by you")
    parser.add_argument("--source", type=Path, help="Override candidate skill source")
    parser.add_argument("--apply", action="store_true", help="Otherwise only validate/preview")
    args = parser.parse_args()
    source = args.source or Path(__file__).resolve().parents[1] / "skills" / args.skill
    try:
        value = manage(args.action, args.skill, args.root, source, args.apply)
    except (ValueError, OSError) as exc:
        print(json.dumps({"status": "BLOCKED", "reason": str(exc)}, ensure_ascii=True))
        return 2
    print(json.dumps(value, ensure_ascii=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
