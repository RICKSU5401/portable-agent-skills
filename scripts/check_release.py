#!/usr/bin/env python3
"""Heuristic privacy/package scan. Does not certify absence of all sensitive data."""
import argparse
import json
from pathlib import Path
import re
import stat

PATTERNS = {
    "private_home_path": re.compile(r"(?:[A-Z]:[/\\]+Users[/\\]+(?!Public\b|Example\b)[^\s/\\]+|/(?:Users|home)/[^\s/]+)", re.I),
    "personal_workspace": re.compile(r"[A-Z]:[/\\]+VS CODE[/\\]", re.I),
    "credential_material": re.compile(r"(?:-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----|gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,}|sk-[A-Za-z0-9_-]{24,})"),
    "hosted_personal_skill": re.compile(r"https://chatgpt\.com/skills\?skill_id=[A-Za-z0-9]+"),
    "email": re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}"),
}
EXCLUDED_DIRS = {".git", "__pycache__", ".venv", "dist", "sandbox-skills"}
TEXT_SUFFIXES = {".py", ".md", ".txt", ".json", ".yaml", ".yml", ".svg", ".toml", ".gitignore", ".bats", ".sh", ".ps1", ".cmd"}


def files(root):
    for path in sorted(root.rglob("*")):
        rel = path.relative_to(root)
        if any(part in EXCLUDED_DIRS for part in rel.parts):
            continue
        info = path.lstat()
        if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
            raise ValueError(f"link/reparse entry: {rel.as_posix()}")
        if path.is_file():
            yield path


def scan(root):
    findings = []
    checked = 0
    for path in files(root):
        checked += 1
        rel = path.relative_to(root).as_posix()
        if path.name.startswith(".env") or path.suffix in {".log", ".zip", ".b64", ".pem", ".key"}:
            findings.append({"file": rel, "category": "private_or_generated_artifact"})
        if path.suffix not in TEXT_SUFFIXES and path.name not in {"LICENSE", "VERSION", ".gitignore", "NOTICE"}:
            findings.append({"file": rel, "category": "unreviewed_file_type"})
            continue
        try:
            content = path.read_text(encoding="utf-8-sig")
        except UnicodeError:
            findings.append({"file": rel, "category": "non_utf8_content"})
            continue
        for line_no, line in enumerate(content.splitlines(), 1):
            for category, pattern in PATTERNS.items():
                for match in pattern.finditer(line):
                    if category == "email" and match.group().endswith(("@example.com", "@example.invalid")):
                        continue
                    findings.append({"file": rel, "line": line_no, "category": category})
    for name in ("tiered-model-routing", "quota-aware-handoff", "portable-council"):
        if not (root / "skills" / name / "SKILL.md").is_file():
            findings.append({"file": f"skills/{name}/SKILL.md", "category": "missing_skill"})
    return {"status": "PASS" if not findings else "REVIEW_REQUIRED", "files_checked": checked,
            "findings": findings, "scope": "source tree heuristic; not a comprehensive secret detector"}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    args = parser.parse_args()
    try:
        result = scan(args.root.resolve())
    except (ValueError, OSError) as exc:
        result = {"status": "BLOCKED", "reason": str(exc)}
    print(json.dumps(result, indent=2, ensure_ascii=True))
    return 0 if result["status"] == "PASS" else 2


if __name__ == "__main__":
    raise SystemExit(main())
