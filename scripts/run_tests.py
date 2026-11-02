#!/usr/bin/env python3
"""Run offline suites in separate processes so helper module names cannot collide."""
import os
from pathlib import Path
import subprocess
import sys


def main():
    root = Path(__file__).resolve().parents[1]
    suites = [root / "tests"]
    for skill in sorted((root / "skills").iterdir()):
        for folder in (skill / "scripts", skill / "tests"):
            if folder.is_dir() and list(folder.glob("test_*.py")):
                suites.append(folder)
    env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1", PYTHONUTF8="1")
    failed = False
    for suite in suites:
        print(f"\nSUITE: {suite.relative_to(root).as_posix()}", flush=True)
        result = subprocess.run([sys.executable, "-B", "-m", "unittest", "discover",
                                 "-s", str(suite), "-p", "test_*.py", "-v"],
                                cwd=root, env=env, check=False)
        failed = failed or result.returncode != 0
    return int(failed)


if __name__ == "__main__":
    raise SystemExit(main())
