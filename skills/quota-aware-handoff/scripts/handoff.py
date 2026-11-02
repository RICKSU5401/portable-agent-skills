#!/usr/bin/env python3
"""Create and verify explicit, immutable-by-convention handoff snapshots.

This tool never executes verification records or commits/stashes/resets Git.
--quiescent is the caller's assertion that every writer has stopped, not a
process detector. The exclusive lock coordinates cooperative processes on one
filesystem; it is not a distributed lock for Drive/Dropbox or separate hosts.
Hashes detect corruption against saved digests, not an attacker who can rewrite
both a snapshot and all its digests. Verification establishes file integrity,
not that recorded tests actually ran or that the task is correct.
"""

from __future__ import annotations

import argparse
from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path, PureWindowsPath
import re
import shutil
import stat
import sys
import tempfile
import uuid


SCHEMA_VERSION = 1
ID_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}\Z")
HASH_RE = re.compile(r"[0-9a-f]{64}\Z")
MAX_JSON_BYTES = 8 * 1024 * 1024
MAX_NOTE_BYTES = 2 * 1024 * 1024
SECRET_PARTS = {".git", ".ssh", ".aws", ".azure", ".gnupg", ".kube",
                ".ai-relay", "secrets", "credentials"}
SECRET_NAMES = {"auth.json", "credentials.json", "credentials", "id_rsa",
                "id_ed25519", "id_ecdsa", "id_dsa", "tokens.json",
                "token.json", "token", "relay-config.json", "relay.json",
                "relay.config.json", "authorized_providers.json"}


class HandoffError(ValueError):
    pass


def _now():
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _timestamp(value):
    if not isinstance(value, str):
        raise HandoffError("timestamp must be an ISO 8601 string")
    try:
        result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise HandoffError("invalid ISO 8601 timestamp") from exc
    if result.tzinfo is None:
        raise HandoffError("timestamp must include its timezone")
    return result


def _id(value, label):
    if not isinstance(value, str) or not ID_RE.fullmatch(value):
        raise HandoffError(label + " must be 1-128 safe identifier characters")
    if value.upper().split(".")[0] in {"CON", "PRN", "AUX", "NUL", *["COM" + str(i) for i in range(1, 10)], *["LPT" + str(i) for i in range(1, 10)]}:
        raise HandoffError(label + " is a reserved Windows name")
    return value


def _rel(value):
    if not isinstance(value, str) or not value:
        raise HandoffError("artifact path must be a nonempty relative string")
    value = value.replace("\\", "/")
    if value.startswith("/") or PureWindowsPath(value).drive:
        raise HandoffError("absolute artifact paths are forbidden: " + value)
    parts = value.split("/")
    if any(p in ("", ".", "..") or p.endswith((" ", ".")) for p in parts):
        raise HandoffError("noncanonical or traversing artifact path: " + value)
    if any(ord(c) < 32 or c in '<>:"|?*' for c in value):
        raise HandoffError("unsafe artifact path: " + value)
    return "/".join(parts)


def _artifact_rel(value):
    value = _rel(value)
    parts = [p.lower() for p in value.split("/")]
    for part in parts:
        if (part in SECRET_PARTS or part in SECRET_NAMES or
                part.startswith(".env") or part.endswith((".pem", ".key", ".p12", ".pfx")) or
                re.search(r"(^|[-_.])(secret|secrets|token|tokens|credential|credentials)([-_.]|$)", part) or
                ("relay" in part and "config" in part)):
            raise HandoffError("sensitive or relay-control path is forbidden: " + value)
    if value.lower() == "handoff.md":
        raise HandoffError("HANDOFF.md is reserved; supply it with --note")
    return value


def _project(value):
    path = Path(value).expanduser().absolute()
    if path.is_symlink():
        raise HandoffError("project root must not be a symlink")
    path = path.resolve(strict=True)
    if not path.is_dir():
        raise HandoffError("project root is not a directory")
    return path


def _inside(root, relative, *, must_exist=True, file=False):
    """Reject symlinks in every component beneath an already resolved root."""
    relative = _rel(relative)
    current = root
    for part in relative.split("/"):
        current = current / part
        try:
            info = current.lstat()
        except FileNotFoundError:
            if must_exist:
                raise HandoffError("missing path: " + relative)
            continue
        if stat.S_ISLNK(info.st_mode):
            raise HandoffError("symlink path is forbidden: " + relative)
    resolved = current.resolve(strict=must_exist)
    try:
        resolved.relative_to(root)
    except ValueError as exc:
        raise HandoffError("path escapes its root: " + relative) from exc
    if file and (not current.is_file() or not stat.S_ISREG(current.stat().st_mode)):
        raise HandoffError("not a regular file: " + relative)
    return current


def _source_file(value, limit):
    path = Path(value).expanduser().absolute()
    # Reject source-file symlinks too, including parent-directory links.
    current = path
    while current != current.parent:
        if current.is_symlink():
            raise HandoffError("source file contains a symlink: " + str(path))
        current = current.parent
    if not path.is_file() or not stat.S_ISREG(path.stat().st_mode):
        raise HandoffError("source is not a regular file: " + str(path))
    if path.stat().st_size > limit:
        raise HandoffError("source file exceeds size limit")
    return path


def _pairs(pairs):
    out = {}
    for key, value in pairs:
        if key in out:
            raise HandoffError("duplicate JSON key: " + key)
        out[key] = value
    return out


def _json_bytes(data):
    return (json.dumps(data, ensure_ascii=False, sort_keys=True, indent=2,
                       allow_nan=False) + "\n").encode("utf-8")


def _load_json(path):
    if path.stat().st_size > MAX_JSON_BYTES:
        raise HandoffError("JSON file exceeds size limit")
    def bad_number(value):
        raise HandoffError("nonfinite JSON number: " + value)
    return json.loads(path.read_text(encoding="utf-8-sig"),
                      object_pairs_hook=_pairs, parse_constant=bad_number)


def _digest(path):
    digest = hashlib.sha256()
    size = 0
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
            size += len(chunk)
    return {"size": size, "sha256": digest.hexdigest()}


def _write_new(path, data):
    with path.open("xb") as stream:
        stream.write(data)
        stream.flush()
        os.fsync(stream.fileno())


def _atomic_write(root, relative, data):
    path = _inside(root, relative, must_exist=False)
    if path.exists() and not path.is_file():
        raise HandoffError("cannot replace a non-file: " + relative)
    fd, temporary = tempfile.mkstemp(prefix=".handoff-", dir=str(path.parent))
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        # Recheck immediately before replacement; all writers must cooperate.
        _inside(root, relative, must_exist=False)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


@contextmanager
def _lock(root):
    relay = _inside(root, ".ai-relay", must_exist=False)
    relay.mkdir(exist_ok=True)
    path = _inside(root, ".ai-relay/checkpoint.lock", must_exist=False)
    token = uuid.uuid4().hex
    try:
        fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError as exc:
        raise HandoffError("checkpoint lock already exists; never steal or auto-expire it") from exc
    with os.fdopen(fd, "wb") as stream:
        stream.write(_json_bytes({"token": token, "pid": os.getpid(), "created_at": _now()}))
        stream.flush()
        os.fsync(stream.fileno())
        identity = os.fstat(stream.fileno())
    try:
        yield
    finally:
        try:
            info = path.lstat()
            if (stat.S_ISREG(info.st_mode) and info.st_dev == identity.st_dev and
                    info.st_ino == identity.st_ino and _load_json(path).get("token") == token):
                path.unlink()
        except (OSError, ValueError, AttributeError):
            # A replaced or unreadable lock may belong to somebody else.
            pass


def _validate_verifications(records, artifact_paths):
    if not isinstance(records, list) or len(records) > 1000:
        raise HandoffError("verifications must be a list of at most 1000 records")
    result = []
    for item in records:
        if not isinstance(item, dict):
            raise HandoffError("verification record must be an object")
        status_value = item.get("status")
        if status_value not in {"passed", "failed", "not_run", "skipped"}:
            raise HandoffError("verification status must be passed, failed, not_run, or skipped")
        # An unexecuted check may truthfully have no command or directory.
        unexecuted = status_value in {"not_run", "skipped"}
        argv = item.get("argv")
        if not (unexecuted and argv in (None, [])) and (
                not isinstance(argv, list) or not argv or len(argv) > 128 or
                any(not isinstance(a, str) or not a or len(a) > 4096 for a in argv)):
            raise HandoffError("verification argv must be a bounded list of strings")
        cwd = item.get("cwd", ".")
        if not (unexecuted and cwd is None) and cwd != ".":
            cwd = _rel(cwd)
        exit_code = item.get("exit_code")
        if exit_code is not None and (type(exit_code) is not int):
            raise HandoffError("verification exit_code must be an integer or null")
        if unexecuted and exit_code is not None:
            raise HandoffError("not_run or skipped verification requires exit_code null")
        if status_value == "passed" and exit_code != 0:
            raise HandoffError("passed verification requires exit_code 0")
        if status_value == "failed" and (exit_code is None or exit_code == 0):
            raise HandoffError("failed verification requires a nonzero exit_code")
        timestamp = item.get("timestamp")
        _timestamp(timestamp)
        evidence = item.get("evidence_files", [])
        if not isinstance(evidence, list):
            raise HandoffError("verification evidence_files must be a list")
        evidence = [_artifact_rel(p) for p in evidence]
        if any(p not in artifact_paths for p in evidence):
            raise HandoffError("every verification evidence file must be explicitly included in --files")
        result.append({"argv": argv, "cwd": cwd, "exit_code": exit_code,
                       "status": status_value, "timestamp": timestamp,
                       "evidence_files": evidence})
    return result


def checkpoint(project, note, files, *, handoff_id, task_id, next_unit_id,
               owner, quiescent=False, verification_file=None):
    """Freeze listed files. No external commands or recorded tests are executed."""
    if quiescent is not True:
        raise HandoffError("all writers must be stopped; --quiescent is required")
    root = _project(project)
    for value, label in ((handoff_id, "handoff_id"), (task_id, "task_id"),
                         (next_unit_id, "next_unit_id"), (owner, "owner")):
        _id(value, label)
    note_bytes = _source_file(note, MAX_NOTE_BYTES).read_bytes()
    if not note_bytes.decode("utf-8-sig").strip():
        raise HandoffError("handoff note must be nonempty UTF-8 text")
    paths = [_artifact_rel(p) for p in files]
    if not paths or len(paths) > 10000 or len(set(p.casefold() for p in paths)) != len(paths):
        raise HandoffError("supply 1-10000 distinct artifact paths, without case collisions")
    records = [] if verification_file is None else _load_json(_source_file(verification_file, MAX_JSON_BYTES))
    records = _validate_verifications(records, set(paths))
    created = _now()
    with _lock(root):
        snapshots = _inside(root, ".ai-relay/checkpoints", must_exist=False)
        snapshots.mkdir(exist_ok=True)
        target = _inside(root, ".ai-relay/checkpoints/" + handoff_id, must_exist=False)
        if target.exists():
            raise HandoffError("handoff_id already exists; snapshots are never overwritten")
        # Reject unsafe convenience/pointer destinations before publishing.
        for destination_name in ("HANDOFF.md", ".ai-relay/current.json"):
            destination_path = _inside(root, destination_name, must_exist=False)
            if destination_path.exists() and not destination_path.is_file():
                raise HandoffError("publication destination is not a regular file: " + destination_name)
        staging = Path(tempfile.mkdtemp(prefix=".staging-", dir=str(snapshots)))
        try:
            _write_new(staging / "HANDOFF.md", note_bytes)
            artifacts = []
            for relative in paths:
                source = _inside(root, relative, file=True)
                before = source.stat()
                destination = staging / "artifacts" / Path(*relative.split("/"))
                destination.parent.mkdir(parents=True, exist_ok=True)
                with source.open("rb") as incoming, destination.open("xb") as outgoing:
                    shutil.copyfileobj(incoming, outgoing, 1024 * 1024)
                    outgoing.flush()
                    os.fsync(outgoing.fileno())
                after = source.stat()
                if (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns) != (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns):
                    raise HandoffError("source changed while checkpointing: " + relative)
                artifacts.append({"path": relative, **_digest(destination)})
            manifest = {"schema_version": SCHEMA_VERSION, "handoff_id": handoff_id,
                        "task_id": task_id, "next_unit_id": next_unit_id, "owner": owner,
                        "created_at": created, "source_project": str(root),
                        "quiescent": True,
                        "handoff": {"path": "HANDOFF.md", "size": len(note_bytes),
                                    "sha256": hashlib.sha256(note_bytes).hexdigest()},
                        "artifacts": artifacts, "verifications": records}
            manifest_bytes = _json_bytes(manifest)
            # Compact verification input can expand beyond the reader's limit
            # after adding manifest fields and indentation. Never replace the
            # last usable pointer with a snapshot that our verifier cannot read.
            if len(manifest_bytes) > MAX_JSON_BYTES:
                raise HandoffError("serialized manifest exceeds JSON size limit")
            manifest_hash = hashlib.sha256(manifest_bytes).hexdigest()
            _write_new(staging / "manifest.json", manifest_bytes)
            _write_new(staging / "manifest.sha256", (manifest_hash + "\n").encode("ascii"))
            if target.exists():
                raise HandoffError("handoff_id appeared while checkpointing")
            os.rename(staging, target)
            staging = None
            pointer = {"schema_version": SCHEMA_VERSION, "handoff_id": handoff_id,
                       "manifest_sha256": manifest_hash, "updated_at": created}
            _atomic_write(root, ".ai-relay/current.json", _json_bytes(pointer))
            # This copy is for human convenience; the snapshot is canonical.
            # A failed convenience copy must not hide a published checkpoint.
            warnings = []
            try:
                _atomic_write(root, "HANDOFF.md", note_bytes)
            except (OSError, HandoffError) as exc:
                warnings.append("Canonical checkpoint published, but project HANDOFF.md copy could not be updated: " + str(exc))
            return {"created": True, "handoff_id": handoff_id,
                    "task_id": task_id, "next_unit_id": next_unit_id,
                    "quiescent": True, "manifest_sha256": manifest_hash,
                    "convenience_copy_updated": not warnings, "warnings": warnings,
                    "checkpoint_path": str(target), "handoff_path": str(target / "HANDOFF.md"),
                    "manifest_path": str(target / "manifest.json")}
        finally:
            if staging is not None:
                shutil.rmtree(staging)


def _check_digest(path, record, label):
    if (not isinstance(record, dict) or type(record.get("size")) is not int or
            record["size"] < 0 or not isinstance(record.get("sha256"), str) or
            not HASH_RE.fullmatch(record["sha256"])):
        raise HandoffError("invalid digest record: " + label)
    actual = _digest(path)
    if actual != {"size": record["size"], "sha256": record["sha256"]}:
        raise HandoffError("size or SHA-256 mismatch: " + label)


def verify(project, handoff_id=None, *, live=False, max_age_seconds=None):
    """Return a JSON-safe integrity report. Recorded verification argv is data."""
    report = {"verified": False, "reasons": [], "warnings": [],
              "checked_at": _now(), "live_checked": bool(live),
              "convenience_note_matches": None, "runtime_writers_verified": False,
              "verification_records_reexecuted": False}
    try:
        root = _project(project)
        pointer = None
        if handoff_id is None:
            pointer_path = _inside(root, ".ai-relay/current.json", file=True)
            pointer = _load_json(pointer_path)
            if (not isinstance(pointer, dict) or type(pointer.get("schema_version")) is not int or
                    pointer["schema_version"] != SCHEMA_VERSION):
                raise HandoffError("invalid current pointer schema")
            handoff_id = pointer.get("handoff_id")
            if not isinstance(pointer.get("manifest_sha256"), str) or not HASH_RE.fullmatch(pointer["manifest_sha256"]):
                raise HandoffError("invalid current pointer manifest hash")
            _timestamp(pointer.get("updated_at"))
        _id(handoff_id, "handoff_id")
        snapshot = _inside(root, ".ai-relay/checkpoints/" + handoff_id)
        if not snapshot.is_dir():
            raise HandoffError("checkpoint is not a directory")
        manifest_path = _inside(snapshot, "manifest.json", file=True)
        hash_path = _inside(snapshot, "manifest.sha256", file=True)
        if hash_path.stat().st_size > 66:
            raise HandoffError("invalid manifest digest file")
        expected_hash = hash_path.read_text(encoding="ascii").strip()
        if not HASH_RE.fullmatch(expected_hash):
            raise HandoffError("invalid manifest SHA-256")
        actual_hash = _digest(manifest_path)["sha256"]
        if actual_hash != expected_hash:
            raise HandoffError("manifest SHA-256 mismatch")
        if pointer and pointer["manifest_sha256"] != actual_hash:
            raise HandoffError("current pointer manifest SHA-256 mismatch")
        manifest = _load_json(manifest_path)
        if (not isinstance(manifest, dict) or type(manifest.get("schema_version")) is not int or
                manifest["schema_version"] != SCHEMA_VERSION):
            raise HandoffError("unsupported manifest schema")
        if manifest.get("handoff_id") != handoff_id or manifest.get("quiescent") is not True:
            raise HandoffError("manifest identity/quiescent assertion mismatch")
        for field in ("task_id", "next_unit_id", "owner"):
            _id(manifest.get(field), field)
        created = _timestamp(manifest.get("created_at"))
        age = (datetime.now(timezone.utc) - created).total_seconds()
        if age < -60:
            raise HandoffError("checkpoint creation time is in the future")
        if max_age_seconds is not None and (not math.isfinite(max_age_seconds) or max_age_seconds < 0):
            raise HandoffError("max_age_seconds must be finite and nonnegative")
        stale = max_age_seconds is not None and age > max_age_seconds
        report["freshness"] = {"age_seconds": max(0, round(age, 3)),
                               "max_age_seconds": max_age_seconds,
                               "stale": stale if max_age_seconds is not None else None}
        if stale:
            raise HandoffError("checkpoint exceeds the requested freshness limit")
        note_record = manifest.get("handoff")
        if not isinstance(note_record, dict) or note_record.get("path") != "HANDOFF.md":
            raise HandoffError("invalid canonical handoff note path")
        note_path = _inside(snapshot, "HANDOFF.md", file=True)
        _check_digest(note_path, note_record, "canonical HANDOFF.md")
        if note_path.stat().st_size > MAX_NOTE_BYTES or not note_path.read_text(encoding="utf-8-sig").strip():
            raise HandoffError("canonical handoff note must be nonempty bounded UTF-8")
        if live:
            # The root copy is advisory; neither drift nor unreadability can
            # invalidate an otherwise intact canonical snapshot.
            try:
                convenience_path = _inside(root, "HANDOFF.md", must_exist=False)
                if convenience_path.exists():
                    convenience_path = _inside(root, "HANDOFF.md", file=True)
                    report["convenience_note_matches"] = _digest(convenience_path) == {
                        "size": note_record["size"], "sha256": note_record["sha256"]}
                    if not report["convenience_note_matches"]:
                        report["warnings"].append(
                            "project HANDOFF.md differs from the canonical note; read handoff_path before resuming.")
            except (OSError, HandoffError) as exc:
                report["warnings"].append(
                    "project HANDOFF.md convenience copy could not be checked; read handoff_path: " + str(exc))
        artifacts = manifest.get("artifacts")
        if not isinstance(artifacts, list) or not 1 <= len(artifacts) <= 10000:
            raise HandoffError("manifest must list 1-10000 artifacts")
        seen = set()
        for item in artifacts:
            if not isinstance(item, dict):
                raise HandoffError("invalid artifact record")
            relative = _artifact_rel(item.get("path"))
            if relative.casefold() in seen:
                raise HandoffError("duplicate or case-colliding manifest artifact")
            seen.add(relative.casefold())
            frozen = _inside(snapshot, "artifacts/" + relative, file=True)
            _check_digest(frozen, item, "snapshot artifact " + relative)
            if live:
                source = _inside(root, relative, file=True)
                _check_digest(source, item, "live artifact " + relative)
        records = _validate_verifications(manifest.get("verifications"),
                                          {item["path"] for item in artifacts})
        if not records:
            report["warnings"].append("No verification results recorded; snapshot integrity is not task correctness.")
        elif any(item["status"] != "passed" for item in records):
            report["warnings"].append("Some recorded checks did not pass; the receiver must assess them before continuing.")
        report.update({"verified": True, "handoff_id": handoff_id,
                       "task_id": manifest["task_id"], "next_unit_id": manifest["next_unit_id"],
                       "owner": manifest["owner"],
                       "quiescent": True,  # Compatibility alias for the checkpoint assertion, not live process evidence.
                       "quiescent_asserted_at_checkpoint": True,
                       "created_at": manifest["created_at"], "manifest_sha256": actual_hash,
                       "checkpoint_path": str(snapshot), "handoff_path": str(note_path),
                       "manifest_path": str(manifest_path), "artifact_count": len(artifacts),
                       "verification_count": len(records)})
    except (OSError, ValueError, TypeError, KeyError, AttributeError) as exc:
        report["reasons"].append(str(exc))
    return report


def _strict_json(encoded):
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise HandoffError("duplicate JSON key")
            result[key] = value
        return result

    def finite_number(value):
        parsed = float(value)
        if not math.isfinite(parsed):
            raise HandoffError("non-finite JSON number")
        return parsed

    return json.loads(encoded.decode("utf-8-sig"), object_pairs_hook=pairs,
                      parse_constant=finite_number, parse_float=finite_number)


def _pinned_manifest(root, ident, expected):
    if not isinstance(expected, str) or not HASH_RE.fullmatch(expected):
        raise HandoffError("invalid independently retained manifest hash")
    report = verify(root, ident)
    if not report["verified"] or report["manifest_sha256"] != expected:
        raise HandoffError("snapshot verification or pinned manifest hash mismatch")
    path = _inside(root, ".ai-relay/checkpoints/" + ident + "/manifest.json", file=True)
    with path.open("rb") as stream:
        encoded = stream.read(MAX_JSON_BYTES + 1)
    if len(encoded) > MAX_JSON_BYTES or hashlib.sha256(encoded).hexdigest() != expected:
        raise HandoffError("parsed manifest bytes differ from pinned hash")
    return _strict_json(encoded)


def inspect_return(project, handoff_id, manifest_sha256, origin_ref,
                   source_handoff_id, source_manifest_sha256):
    """Read-only identity/integrity check; never merge, launch or release locks."""
    result = {"ready_for_reconciliation": False, "reasons": [],
              "task_complete": False, "files_merged": False,
              "runtime_writers_verified": False, "verification_records_reexecuted": False}
    try:
        root = _project(project)
        for value, label in ((handoff_id, "handoff_id"), (origin_ref, "origin_ref"),
                             (source_handoff_id, "source_handoff_id")):
            _id(value, label)
        if handoff_id == source_handoff_id:
            raise HandoffError("return must be a new checkpoint")
        source = _pinned_manifest(root, source_handoff_id, source_manifest_sha256)
        returned = _pinned_manifest(root, handoff_id, manifest_sha256)
        records = [item for item in returned["artifacts"] if item["path"] == "return-receipt.json"]
        if len(records) != 1:
            raise HandoffError("return manifest must include root return-receipt.json")
        path = _inside(root, ".ai-relay/checkpoints/" + handoff_id +
                       "/artifacts/return-receipt.json", file=True)
        with path.open("rb") as stream:
            encoded = stream.read(MAX_JSON_BYTES + 1)
        record = records[0]
        if (len(encoded) > MAX_JSON_BYTES or len(encoded) != record["size"] or
                hashlib.sha256(encoded).hexdigest() != record["sha256"]):
            raise HandoffError("parsed receipt bytes differ from pinned artifact hash")
        receipt = _strict_json(encoded)
        if (not isinstance(receipt, dict) or type(receipt.get("schema_version")) is not int or
                receipt["schema_version"] != 1 or receipt.get("kind") != "return"):
            raise HandoffError("invalid return receipt schema")
        expected = {"origin_ref": origin_ref, "source_handoff_id": source_handoff_id,
                    "source_manifest_sha256": source_manifest_sha256,
                    "task_id": source["task_id"], "delegated_unit_id": source["next_unit_id"],
                    "return_handoff_id": handoff_id}
        if any(receipt.get(key) != value for key, value in expected.items()):
            raise HandoffError("return receipt identity mismatch")
        if returned["task_id"] != source["task_id"]:
            raise HandoffError("return and source task mismatch")
        if receipt.get("outcome") not in {"completed", "partial", "blocked", "failed", "cancelled"}:
            raise HandoffError("invalid delegated unit outcome")
        summary = receipt.get("summary")
        remaining = receipt.get("remaining_work")
        if not isinstance(summary, str) or not summary.strip() or len(summary) > 16384:
            raise HandoffError("return summary must be nonempty bounded text")
        if (not isinstance(remaining, list) or len(remaining) > 1000 or
                any(not isinstance(item, str) or not item.strip() or len(item) > 4096
                    for item in remaining)):
            raise HandoffError("remaining_work must be bounded nonempty strings")
        result.update(ready_for_reconciliation=True, **expected,
                      manifest_sha256=manifest_sha256, outcome=receipt["outcome"],
                      summary=summary, remaining_work=remaining)
    except (OSError, ValueError, TypeError, KeyError, AttributeError) as exc:
        result["reasons"].append(str(exc))
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    create = sub.add_parser("checkpoint", help="freeze explicit files; never run recorded commands")
    create.add_argument("--project", required=True)
    create.add_argument("--note", required=True)
    create.add_argument("--files", nargs="+", required=True)
    create.add_argument("--handoff-id", required=True)
    create.add_argument("--task-id", required=True)
    create.add_argument("--next-unit-id", required=True)
    create.add_argument("--owner", required=True)
    create.add_argument("--quiescent", action="store_true", help="assert every writer has stopped (not automatically detected)")
    create.add_argument("--verification-file", required=True)
    check = sub.add_parser("verify", help="check saved hashes and optionally compare live files")
    check.add_argument("--project", required=True)
    check.add_argument("--handoff-id")
    check.add_argument("--live", action="store_true")
    check.add_argument("--max-age-seconds", type=float)
    returned = sub.add_parser("inspect-return", help="read-only source/return identity verification")
    for flag in ("project", "handoff-id", "manifest-sha256", "origin-ref",
                 "source-handoff-id", "source-manifest-sha256"):
        returned.add_argument("--" + flag, required=True)
    args = vars(parser.parse_args(argv))
    command = args.pop("command")
    try:
        operation = {"checkpoint": checkpoint, "verify": verify, "inspect-return": inspect_return}[command]
        result = operation(**args)
    except (OSError, ValueError, TypeError, KeyError, AttributeError) as exc:
        result = {"created": False, "reasons": [str(exc)]}
    # JSON consumers must not depend on the terminal's legacy code page.
    # ASCII escapes preserve Unicode paths after parsing on every platform.
    print(json.dumps(result, ensure_ascii=True, allow_nan=False))
    return 0 if result.get("created") or result.get("verified") or result.get("ready_for_reconciliation") else 1


if __name__ == "__main__":
    sys.exit(main())
