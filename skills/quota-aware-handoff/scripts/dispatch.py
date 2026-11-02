#!/usr/bin/env python3
"""Cooperative, one-attempt handoff launcher. No models run without --execute.

This is not a daemon or a security boundary. A receiver must validate the saved
work, publish its ACK atomically, and wait for a matching permit before editing.
Locks deliberately survive failure, timeout, and acknowledgement. An operator
must verify every old writer and descendant has stopped before removing a lock;
never remove the attempt record to retry an uncertain launch.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import math
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import time
import uuid
from datetime import datetime, timezone


MAX_JSON_BYTES = 1_048_576
API_ENV = (
    "OPENAI_API_KEY", "ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN",
    "XAI_API_KEY", "GEMINI_API_KEY", "GOOGLE_API_KEY", "AZURE_OPENAI_API_KEY",
    "GOOGLE_APPLICATION_CREDENTIALS", "CLAUDE_CODE_USE_BEDROCK",
    "CLAUDE_CODE_USE_VERTEX", "CLAUDE_CODE_USE_FOUNDRY",
    "GOOGLE_GENAI_USE_VERTEXAI", "OPENAI_BASE_URL", "ANTHROPIC_BASE_URL",
    "XAI_BASE_URL",
)
FORBIDDEN = (
    "--bare", "--yolo", "--always-approve", "--skip-permissions",
    "--dangerously-skip-permissions", "--allow-dangerously-skip-permissions",
    "--dangerously-bypass-approvals-and-sandbox", "--no-sandbox",
    "--allow-all", "--system-prompt-override", "--trust",
)


class RelayError(Exception):
    def __init__(self, reason, **details):
        super().__init__(reason)
        self.details = details


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def stamp(value) -> float:
    if not isinstance(value, str):
        raise RelayError("invalid_timestamp")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            raise ValueError()
        return parsed.timestamp()
    except (ValueError, OverflowError):
        raise RelayError("invalid_timestamp") from None


def named(value) -> bool:
    return (isinstance(value, str) and bool(value.strip())
            and value.strip().lower() not in ("unknown", "null", "none", "auto", "default", "?", "tbd"))


def strict_json(encoded):
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise RelayError("duplicate_json_key")
            result[key] = value
        return result

    def finite(value):
        parsed = float(value)
        if not math.isfinite(parsed):
            raise RelayError("nonfinite_json_number")
        return parsed

    return json.loads(encoded, object_pairs_hook=pairs, parse_constant=finite, parse_float=finite)


def read_json_with_hash(path: Path) -> tuple[dict, str]:
    """Parse and pin the same bounded bytes, even if the file is replaced."""
    try:
        with path.open("rb") as stream:
            encoded = stream.read(MAX_JSON_BYTES + 1)
        if len(encoded) > MAX_JSON_BYTES:
            raise RelayError("json_input_too_large")
        result = strict_json(encoded.decode("utf-8-sig"))
        if not isinstance(result, dict):
            raise ValueError()
        return result, hashlib.sha256(encoded).hexdigest()
    except (OSError, UnicodeError, ValueError):
        raise RelayError("invalid_or_unreadable_json") from None


def read_json(path: Path) -> dict:
    return read_json_with_hash(path)[0]


def within(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
        return True
    except ValueError:
        return False


def relay_path(project: Path, *parts: str) -> Path:
    """Do not allow a project symlink to redirect operational records."""
    path = project
    for part in (".ai-relay", *parts):
        path = path / part
        if path.is_symlink():
            raise RelayError("relay_symlink_not_allowed")
    if not within(path.resolve(), project):
        raise RelayError("relay_path_outside_project")
    return path


def write_json(path: Path, data: dict, exclusive: bool = False) -> None:
    encoded = (json.dumps(data, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    if exclusive:
        fd = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "wb") as stream:
            stream.write(encoded)
            stream.flush()
            os.fsync(stream.fileno())
        return
    fd, temporary = tempfile.mkstemp(prefix=".relay-", dir=str(path.parent))
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(encoded)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def publish_permit(path: Path, data: dict) -> None:
    """Publish a complete file without replacing a preexisting permit."""
    fd, temporary = tempfile.mkstemp(prefix=".permit-", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(data, stream, ensure_ascii=False)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.link(temporary, path)  # Same-filesystem atomic creation; fails if present.
    finally:
        os.unlink(temporary)


def probe_permit_link(directory: Path) -> None:
    """Test the required atomic primitive on the permit filesystem before launch."""
    fd, temporary = tempfile.mkstemp(prefix=".permit-probe-", dir=str(directory))
    os.close(fd)
    linked = False
    destination = temporary + ".link"
    try:
        os.link(temporary, destination)
        linked = True
    except OSError:
        raise RelayError("atomic_permit_publication_unavailable") from None
    finally:
        if linked:
            os.unlink(destination)
        os.unlink(temporary)


def update_lock(path: Path, record: dict) -> None:
    if path.is_symlink() or read_json(path).get("dispatch_token") != record["dispatch_token"]:
        raise RelayError("dispatch_lock_ownership_lost")
    write_json(path, record)


def helper(name: str, args: list[str]) -> tuple[int, dict]:
    script = Path(__file__).resolve().with_name(name)
    if not script.is_file():
        raise RelayError("required_helper_unavailable")
    try:
        completed = subprocess.run(
            [sys.executable, str(script), *args], shell=False, capture_output=True,
            text=True, encoding="utf-8", timeout=20, check=False,
        )
        output = json.loads(completed.stdout)
        if not isinstance(output, dict):
            raise ValueError()
        return completed.returncode, output
    except (OSError, subprocess.TimeoutExpired, ValueError, UnicodeError):
        # Helper output can contain private project details. Never echo it here.
        raise RelayError("helper_failed_or_invalid_output") from None


def evaluate_snapshot(snapshot: dict, unit: dict) -> dict:
    """Evaluate the exact object whose identity was checked, despite live refreshes."""
    script = Path(__file__).resolve().with_name("quota_guard.py")
    if not script.is_file():
        raise RelayError("required_helper_unavailable")
    try:
        spec = importlib.util.spec_from_file_location("relay_quota_guard", script)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module.evaluate(snapshot, unit)
    except (OSError, ValueError, TypeError, AttributeError, ImportError):
        raise RelayError("quota_helper_failed") from None


def validate_snapshot_transition(previous: dict, current: dict, now=None) -> None:
    """Allow observed consumption or evidenced resets, never invented headroom."""
    now = time.time() if now is None else now
    observed = stamp(current["observed_at"])
    if observed < stamp(previous["observed_at"]):
        raise RelayError("quota_snapshot_timestamp_regressed")
    if current.get("freshness_seconds", 120) > previous.get("freshness_seconds", 120):
        raise RelayError("unobserved_budget_growth")
    old_limits = {item["id"]: item for item in previous["limits"]}
    new_limits = {item["id"]: item for item in current["limits"]}
    if old_limits.keys() != new_limits.keys():
        raise RelayError("quota_snapshot_limits_changed")
    for key, old in old_limits.items():
        new = new_limits[key]
        if old["unit"] != new["unit"]:
            raise RelayError("quota_snapshot_limit_unit_changed")
        old_observed, new_observed = stamp(old["observed_at"]), stamp(new["observed_at"])
        if new_observed < old_observed:
            raise RelayError("quota_snapshot_timestamp_regressed")
        old_reset = stamp(old["reset_at"]) if old.get("reset_at") is not None else None
        new_reset = stamp(new["reset_at"]) if new.get("reset_at") is not None else None
        rollover = (old_reset is not None and old_reset <= now
                    and new_observed >= old_reset and new_observed > old_observed
                    and observed >= old_reset and new_reset is not None
                    and new_reset > max(now, old_reset, new_observed))
        if (new["remaining"] > old["remaining"]
                or (old_reset is not None and new_reset != old_reset)) and not rollover:
            raise RelayError("unobserved_budget_growth")
    old_context = previous["context"].get("observed_at", previous["observed_at"])
    new_context = current["context"].get("observed_at", current["observed_at"])
    if stamp(new_context) < stamp(old_context):
        raise RelayError("quota_snapshot_timestamp_regressed")


def require_launch_headroom(state: dict, deadline: float) -> None:
    """Every attestation must remain usable for the full planned ACK window."""
    snapshot = state["snapshot"]
    ttl = snapshot.get("freshness_seconds", 120)
    expiries = [("snapshot", stamp(snapshot["observed_at"]) + ttl),
                ("context", stamp(snapshot["context"].get("observed_at", snapshot["observed_at"])) + ttl),
                ("billing", stamp(state["profile"]["billing_verified_at"]) + 86400)]
    for limit in snapshot["limits"]:
        expiries.append((limit["id"], stamp(limit["observed_at"]) + ttl))
        if limit.get("reset_at") is not None:
            # A reset at the deadline also requires a newer observation.
            if stamp(limit["reset_at"]) <= deadline:
                raise RelayError("insufficient_ack_headroom", headroom_reasons=[limit["id"] + ":reset_at"])
    insufficient = [label for label, expiry in expiries if expiry < deadline]
    if insufficient:
        raise RelayError("insufficient_ack_headroom", headroom_reasons=insufficient)


def check_argv(argv, model: str) -> None:
    if not isinstance(argv, list) or not argv or any(not isinstance(x, str) for x in argv):
        raise RelayError("invalid_argv")
    if argv.count("{prompt}") != 1:
        raise RelayError("exactly_one_prompt_argument_required")
    specified_models = []
    for index, arg in enumerate(argv):
        low = arg.lower()
        if "\x00" in arg or (arg != "{prompt}" and ("{" in arg or "}" in arg)
                             and not Path(arg).is_absolute()):
            raise RelayError("invalid_argument_or_placeholder")
        if low == "--":
            raise RelayError("option_terminator_not_supported")
        compact = re.sub(r"[\s\"']", "", low)
        if re.search(r"(?:service_tier|service-tier)[=:](?:fast|priority)|fastmode[=:]true", compact):
            raise RelayError("unsafe_or_billing_override_argument")
        if (low in ("-y", "-c", "-e", "--eval", "--command", "--encodedcommand")
                or low.startswith(("--eval=", "--command=", "--encodedcommand="))
                or (low.startswith(("-c", "-e")) and not low.startswith("--"))
                or any(low == flag or low.startswith(flag + "=") for flag in FORBIDDEN)
                or "danger-full-access" in low or "bypasspermissions" in low
                or "api-key" in low or low.startswith("--auth-token")
                or low == "--fast" or low.startswith("--fast=")):
            raise RelayError("unsafe_or_billing_override_argument")
        if low in ("off", "yolo", "never", "bypass", "bypass-permissions") and index:
            if argv[index - 1].lower() in ("--sandbox", "--approval-mode", "--permission-mode", "--ask-for-approval", "-a"):
                raise RelayError("unsafe_permission_argument")
        if low in ("--sandbox=off", "--approval-mode=yolo", "--ask-for-approval=never", "--permission-mode=bypass"):
            raise RelayError("unsafe_permission_argument")
        if low in ("--model", "-m"):
            specified_models.append(argv[index + 1] if index + 1 < len(argv) else None)
        elif low.startswith("--model="):
            specified_models.append(arg.split("=", 1)[1])
        elif low.startswith("-m") and not low.startswith("--"):
            specified_models.append(arg[2:].lstrip("="))
    if specified_models != [model]:
        raise RelayError("exactly_one_matching_explicit_model_required")


def preflight(args) -> dict:
    if not isinstance(args.target, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}", args.target):
        raise RelayError("invalid_target_identifier")
    skill_path = Path(__file__).resolve().parent.parent / "SKILL.md"
    try:
        with skill_path.open("rb") as skill_file:
            if not skill_file.read(1):
                raise RelayError("skill_file_unavailable_or_empty")
    except OSError:
        raise RelayError("skill_file_unavailable_or_empty") from None
    project = Path(args.project).expanduser().resolve(strict=True)
    if not project.is_dir():
        raise RelayError("project_not_directory")
    if relay_path(project, "checkpoint.lock").exists():
        raise RelayError("checkpoint_lock_exists")
    config_path = Path(args.config).expanduser().resolve(strict=True)
    if within(config_path, project):
        raise RelayError("authorization_config_must_be_outside_project")
    config, config_hash = read_json_with_hash(config_path)
    if type(config.get("schema_version")) is not int or config["schema_version"] != 1 or not named(config.get("approval_ref")):
        raise RelayError("missing_configuration_authorization")
    if type(config.get("max_attempts_per_handoff")) is not int or config["max_attempts_per_handoff"] != 1:
        raise RelayError("max_attempts_must_equal_one")
    profiles = config.get("profiles", {})
    profile = profiles.get(args.target) if isinstance(profiles, dict) else None
    if not isinstance(profile, dict):
        raise RelayError("target_profile_missing")
    if profile.get("enabled") is not True or profile.get("authorized") is not True or not named(profile.get("authorization_ref")):
        raise RelayError("target_not_authorized")
    allowed_projects = profile.get("allowed_projects")
    allowed_tasks = profile.get("allowed_task_ids")
    if (not isinstance(allowed_projects, list) or not allowed_projects
            or any(not isinstance(p, str) or not Path(p).is_absolute() or any(c in p for c in "*?[]") for p in allowed_projects)):
        raise RelayError("explicit_project_scope_required")
    if project not in [Path(p).expanduser().resolve(strict=True) for p in allowed_projects]:
        raise RelayError("project_outside_authorization_scope")
    if (not isinstance(allowed_tasks, list) or not allowed_tasks
            or any(not named(t) or any(c in t for c in "*?[]") for t in allowed_tasks)):
        raise RelayError("explicit_task_scope_required")
    if profile.get("billing_mode") != "subscription" or profile.get("auth_verified") is not True:
        raise RelayError("subscription_authentication_not_verified")
    if profile.get("fast_mode") is not False:
        raise RelayError("fast_mode_must_be_verified_off")
    for field in ("model", "quota_pool_id", "account_ref"):
        if not named(profile.get(field)):
            raise RelayError("unknown_profile_identity")
    age = time.time() - stamp(profile.get("billing_verified_at"))
    if age < -60 or age > 86400:
        raise RelayError("billing_attestation_stale_or_future")
    if any(os.environ.get(name, "").strip() for name in API_ENV):
        raise RelayError("subscription_environment_ambiguous")
    executable_text = profile.get("executable")
    if not isinstance(executable_text, str) or not Path(executable_text).is_absolute():
        raise RelayError("executable_must_be_absolute")
    executable = Path(executable_text).resolve(strict=True)
    if not executable.is_file() or (os.name != "nt" and not os.access(executable, os.X_OK)):
        raise RelayError("executable_unavailable")
    if executable.suffix.lower() in (".cmd", ".bat"):
        raise RelayError("shell_wrapper_not_supported_use_native_executable")
    if executable.stem.lower() in ("sh", "bash", "dash", "zsh", "fish", "cmd", "powershell", "pwsh"):
        raise RelayError("shell_executable_not_supported")
    check_argv(profile.get("argv"), profile["model"])
    snapshot_path = Path(args.snapshot).expanduser().resolve(strict=True)
    unit_path = Path(args.unit).expanduser().resolve(strict=True)
    snapshot = read_json(snapshot_path)
    unit, unit_hash = read_json_with_hash(unit_path)
    if snapshot.get("provider") != args.target:
        raise RelayError("snapshot_provider_mismatch")
    for field in ("model", "quota_pool_id", "account_ref"):
        if snapshot.get(field) != profile[field]:
            raise RelayError("snapshot_identity_mismatch")
    if any(unit.get(field) != profile[field] for field in ("model", "quota_pool_id")):
        raise RelayError("unit_identity_mismatch")
    quota = evaluate_snapshot(snapshot, unit)
    if quota.get("decision") != "CONTINUE":
        raise RelayError("quota_gate_did_not_continue", quota_reasons=quota.get("reasons", []))
    code, handoff = helper("handoff.py", ["verify", "--project", str(project), "--live"])
    if relay_path(project, "checkpoint.lock").exists():
        raise RelayError("checkpoint_lock_exists")
    if code != 0 or handoff.get("verified") is not True or handoff.get("quiescent") is not True:
        raise RelayError("handoff_not_verified_and_quiescent")
    ident = handoff.get("handoff_id")
    if not isinstance(ident, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}", ident):
        raise RelayError("invalid_handoff_id")
    if not named(handoff.get("task_id")) or not named(handoff.get("next_unit_id")):
        raise RelayError("handoff_identity_missing")
    if handoff["task_id"] not in allowed_tasks:
        raise RelayError("task_outside_authorization_scope")
    if unit.get("id") != handoff["next_unit_id"] or quota.get("unit_id") != unit.get("id"):
        raise RelayError("next_unit_identity_mismatch")
    if not isinstance(handoff.get("manifest_sha256"), str) or not re.fullmatch(r"[0-9a-f]{64}", handoff["manifest_sha256"]):
        raise RelayError("invalid_manifest_hash")
    relay_path(project)
    return {"project": project, "profile": profile, "executable": executable,
            "handoff": handoff, "quota": quota, "snapshot": snapshot,
            "skill_path": str(skill_path),
            "input_paths": {"config": str(config_path), "snapshot": str(snapshot_path), "unit": str(unit_path)},
            "pins": {str(config_path): config_hash, str(unit_path): unit_hash}}


def prompt_for(state: dict, target: str, ack_deadline: float) -> str:
    handoff = state["handoff"]
    ident = handoff["handoff_id"]
    scripts = Path(__file__).resolve().parent
    receiver_inputs = {"skill_path": state["skill_path"], "input_paths": state["input_paths"],
                       "canonical_handoff_path": handoff["handoff_path"],
                       "manifest_path": handoff["manifest_path"],
                       "expected_identity": {"provider": target, "model": state["profile"]["model"],
                                             "account_ref": state["profile"]["account_ref"],
                                             "quota_pool_id": state["profile"]["quota_pool_id"]},
                       "verification_argv": [
                           [sys.executable, str(scripts / "quota_guard.py"), "evaluate", "--snapshot",
                            state["input_paths"]["snapshot"], "--unit", state["input_paths"]["unit"]],
                           [sys.executable, str(scripts / "handoff.py"), "verify", "--project",
                            str(state["project"]), "--live"]]}
    ack = {"handoff_id": ident, "task_id": handoff["task_id"],
           "next_unit_id": handoff["next_unit_id"], "target": target,
           "manifest_sha256": handoff["manifest_sha256"], "status": "verified_ready",
           "checks": {"artifacts": True, "validation_evidence": True, "authorization_scope": True},
           "timestamp": "REPLACE_WITH_CURRENT_TIMEZONE_AWARE_ISO_TIMESTAMP"}
    return ("Apply quota-aware-handoff. You are the receiving agent for one work unit. "
            "Read the full Skill and receiver inputs at these exact paths; the authorization registry "
            "(input_paths.config) is read-only and must never be edited to authorize yourself. "
            "Run the adjacent helper verification commands as argv arrays without a shell, and "
            "confirm the registry permits this project, task and target. Receiver input descriptor: "
            + json.dumps(receiver_inputs, ensure_ascii=False) + ". "
            "Read the exact canonical_handoff_path and manifest_path in the descriptor first. "
            "The project-root HANDOFF.md is only a convenience copy and is not authoritative. "
            "Before editing project artifacts, independently verify artifact hashes, actual validation "
            "evidence, current user authorization and scope, and the quota gate. Confirm your actual "
            "session uses model '" + state["profile"]["model"] + "', the configured account and subscription "
            "quota pool. If a check fails or identity differs, stop without ACK. "
            "After these checks, atomically write .ai-relay/acks/" + ident + ".json using this exact identity "
            "and truthful checks (replace timestamp): " + json.dumps(ack, ensure_ascii=False) + ". "
            "Then WAIT for .ai-relay/permits/" + ident + ".json with matching handoff_id, task_id, "
            "next_unit_id, target and manifest_sha256 and status='proceed'. The existing dispatch.lock "
            "belongs to this launch; do not remove it or launch another writer. Only after the matching "
            "permit may you run the one next work unit, then checkpoint. If no permit arrives before "
            + datetime.fromtimestamp(ack_deadline, timezone.utc).isoformat() + ", stop and leave a "
            "recoverable record. A launch or ACK is not task completion. Do not enable paid fallback, "
            "change accounts, bypass permissions, or copy secrets into handoff files.")


def validate_ack(path: Path, expected: dict, started: float, deadline: float) -> bool:
    try:
        if path.is_symlink() or not path.is_file():
            if path.exists() or path.is_symlink():
                raise RelayError("ack_file_invalid_or_stale")
            return False
        if path.stat().st_mtime < started - 1:
            raise RelayError("ack_file_invalid_or_stale")
        with path.open("rb") as stream:
            encoded = stream.read(MAX_JSON_BYTES + 1)
    except OSError:
        return False  # A writer may still hold the file or be replacing it.
    if len(encoded) > MAX_JSON_BYTES:
        raise RelayError("json_input_too_large")
    try:
        ack = strict_json(encoded.decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError):
        return False  # Incomplete writes get the remaining deadline, never a permit.
    if not isinstance(ack, dict):
        raise RelayError("ack_verification_missing")
    for key in ("handoff_id", "task_id", "next_unit_id", "target", "manifest_sha256"):
        if ack.get(key) != expected[key]:
            raise RelayError("ack_identity_mismatch")
    checks = ack.get("checks")
    if (ack.get("status") != "verified_ready" or not isinstance(checks, dict)
            or any(checks.get(key) is not True for key in ("artifacts", "validation_evidence", "authorization_scope"))):
        raise RelayError("ack_verification_missing")
    when = stamp(ack.get("timestamp"))
    if when < started or when > time.time() + 1 or when > deadline:
        raise RelayError("ack_timestamp_outside_launch")
    if time.time() > deadline:
        raise RelayError("ack_deadline_passed_before_permit")
    return True


def dispatch(args) -> tuple[dict, int]:
    state = preflight(args)
    project, handoff, profile = state["project"], state["handoff"], state["profile"]
    ident = handoff["handoff_id"]
    lock = relay_path(project, "dispatch.lock")
    attempt = relay_path(project, "dispatch-attempts", ident + ".json")
    if lock.exists():
        raise RelayError("dispatch_lock_exists_recovery_required")
    if attempt.exists():
        raise RelayError("handoff_already_attempted")
    ack = relay_path(project, "acks", ident + ".json")
    permit = relay_path(project, "permits", ident + ".json")
    if ack.exists() or permit.exists():
        raise RelayError("preexisting_ack_or_permit")
    base = {"handoff_id": ident, "task_id": handoff["task_id"], "next_unit_id": handoff["next_unit_id"],
            "target": args.target, "manifest_sha256": handoff["manifest_sha256"],
            "model": profile["model"], "quota_pool_id": profile["quota_pool_id"],
            "account_ref": profile["account_ref"], "task_complete": False}
    if args.action != "launch" or not args.execute:
        return ({**base, "status": "PLAN_READY", "executed": False,
                 "executable": str(state["executable"]), "argument_count": len(profile["argv"]),
                 "quota_decision": "CONTINUE", "handoff_verified": True,
                 "quiescent_asserted_at_checkpoint": handoff.get("quiescent_asserted_at_checkpoint", handoff["quiescent"]),
                 "runtime_writers_verified": False, "handoff_warnings": handoff.get("warnings", []),
                 "note": "No model was launched. Quiescence is a checkpoint assertion, not runtime writer proof. Billing/auth fields are operator attestations, not a live provider query."}, 0)
    dispatch_token = uuid.uuid4().hex
    write_json(lock, {**base, "dispatch_token": dispatch_token, "status": "PREPARING", "dispatcher_pid": os.getpid(), "created_at": now_iso()}, exclusive=True)
    reserved = False
    process = None
    try:
        # Recheck live artifacts and quota under the cooperative lock.
        refreshed = preflight(args)
        if refreshed["pins"] != state["pins"] or refreshed["handoff"]["manifest_sha256"] != handoff["manifest_sha256"]:
            raise RelayError("inputs_changed_before_launch")
        validate_snapshot_transition(state["snapshot"], refreshed["snapshot"])
        state = refreshed
        for folder in ("dispatch-attempts", "acks", "permits", "logs"):
            relay_path(project, folder).mkdir(exist_ok=True)
        if ack.exists() or permit.exists():
            raise RelayError("preexisting_ack_or_permit")
        probe_permit_link(relay_path(project, "permits"))
        started = time.time()
        deadline = started + args.ack_timeout
        require_launch_headroom(state, deadline)
        record = {**base, "status": "PENDING", "started_at": now_iso(), "pid": None,
                  "dispatcher_pid": os.getpid(), "dispatch_token": dispatch_token, "lock_retained": True}
        write_json(attempt, record, exclusive=True)
        reserved = True
        log = relay_path(project, "logs", ident + ".log")
        command = [str(state["executable"]), *[
            prompt_for(state, args.target, deadline) if arg == "{prompt}" else arg for arg in profile["argv"]]]
        fd = os.open(str(log), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "wb") as output:
            process = subprocess.Popen(command, cwd=str(project), shell=False,
                                       stdin=subprocess.DEVNULL, stdout=output, stderr=subprocess.STDOUT)
        record.update(pid=process.pid, log_path=str(log), log_sensitive_local_only=True)
        write_json(attempt, record)
        update_lock(lock, record)
        monotonic_deadline = time.monotonic() + max(0, deadline - time.time())
        while time.monotonic() < monotonic_deadline:
            if validate_ack(ack, base, started, deadline):
                if process.poll() is not None:
                    raise RelayError("receiver_exited_before_permit")
                refreshed = preflight(args)
                if refreshed["pins"] != state["pins"] or refreshed["handoff"]["manifest_sha256"] != handoff["manifest_sha256"]:
                    raise RelayError("inputs_changed_before_permit")
                validate_snapshot_transition(state["snapshot"], refreshed["snapshot"])
                state = refreshed
                if time.time() > deadline:
                    raise RelayError("ack_deadline_passed_before_permit")
                if read_json(lock).get("dispatch_token") != dispatch_token:
                    raise RelayError("dispatch_lock_ownership_lost")
                if process.poll() is not None:
                    raise RelayError("receiver_exited_before_permit")
                publish_permit(permit, {**base, "status": "proceed", "scope": "one_work_unit",
                                       "receiver_pid": process.pid, "granted_at": now_iso()})
                record.update(status="ACKNOWLEDGED", acknowledged_at=now_iso())
                write_json(attempt, record)
                update_lock(lock, record)
                return ({**record, "executed": True, "note": "Receiver acknowledged verified work; this is not task completion. Lock retained."}, 0)
            if process.poll() is not None:
                raise RelayError("receiver_exited_without_ack")
            time.sleep(min(0.05, max(0, monotonic_deadline - time.monotonic())))
        record.update(status="WAITING_FOR_ACK", reason="ack_timeout", updated_at=now_iso())
        write_json(attempt, record)
        update_lock(lock, record)
        return ({**record, "executed": True, "note": "No retry or kill was attempted. Inspect the receiver and descendants before recovery."}, 3)
    except (RelayError, OSError) as error:
        reason = str(error) if isinstance(error, RelayError) else "local_launch_or_write_failure"
        if reserved:
            record.update(status="RECOVERY_REQUIRED", reason=reason,
                          pid=process.pid if process else None, updated_at=now_iso(),
                          lock_retained=True, executed=process is not None,
                          **(error.details if isinstance(error, RelayError) else {}))
            try:
                write_json(attempt, record)
                update_lock(lock, record)
            except (OSError, RelayError):
                pass  # The original O_EXCL lock/attempt must never be removed.
            return record, 3
        if read_json(lock).get("dispatch_token") == dispatch_token:
            lock.unlink()  # No attempt was reserved and no child was started.
        raise


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("plan", "launch"))
    parser.add_argument("--project", required=True)
    parser.add_argument("--config", required=True)
    parser.add_argument("--target", required=True, help="explicit registry profile identifier")
    parser.add_argument("--snapshot", required=True)
    parser.add_argument("--unit", required=True)
    parser.add_argument("--execute", action="store_true", help="Only launch --execute starts a model.")
    parser.add_argument("--ack-timeout", type=float, default=60.0, help="ACK deadline, >0 and <=60 seconds.")
    args = parser.parse_args(argv)
    try:
        if args.action == "plan" and args.execute:
            raise RelayError("plan_cannot_execute")
        if not math.isfinite(args.ack_timeout) or not 0 < args.ack_timeout <= 60:
            raise RelayError("invalid_ack_timeout")
        result, code = dispatch(args)
    except (RelayError, OSError) as error:
        result = {"status": "BLOCKED", "executed": False,
                  "reason": str(error) if isinstance(error, RelayError) else "unreadable_path_or_local_io_error",
                  **(error.details if isinstance(error, RelayError) else {})}
        code = 2
    print(json.dumps(result, ensure_ascii=True, sort_keys=True))
    return code


if __name__ == "__main__":
    raise SystemExit(main())
