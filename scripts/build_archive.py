#!/usr/bin/env python3
"""Create a source-only ZIP and SHA256 manifest outside the source directory."""
import argparse
import hashlib
import json
from pathlib import Path
import zipfile
import check_release


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    output = args.output.resolve()
    if output == root or root in output.parents or output.exists():
        parser.error("Output must be a new file outside the source tree")
    result = check_release.scan(root)
    if result["status"] != "PASS":
        print(json.dumps(result, indent=2))
        return 2
    contents = {path.relative_to(root).as_posix(): path.read_bytes() for path in check_release.files(root)}
    manifest = {name: hashlib.sha256(data).hexdigest() for name, data in contents.items()}
    output.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(output, "x", zipfile.ZIP_DEFLATED) as archive:
        for name, data in contents.items():
            archive.writestr("portable-agent-skills/" + name, data)
        archive.writestr("portable-agent-skills/SHA256SUMS.json", json.dumps(manifest, indent=2) + "\n")
    print(json.dumps({"archive": str(output), "files": len(contents),
                      "sha256": hashlib.sha256(output.read_bytes()).hexdigest()}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
