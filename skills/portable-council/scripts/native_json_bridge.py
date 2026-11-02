"""Opt-in bridge for the inspected Claude Code terminal JSON contract, not a CLI installer."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import shutil
import sys
import threading

from council import BASE_ENV, MAX_INPUT_BYTES, checked_number, require, strict_json
from process_runner import ProcessRunError, run_process

PLACEHOLDERS = {"{model}": ("--model", "-m"), "{effort}": ("--effort",),
                "{settings_json}": ("--settings",)}
BLOCKED_OPTIONS = {"--fast", "--fast-mode", "--api-key", "--api-base", "--base-url",
                   "--dangerously-skip-permissions", "--allow-dangerously-skip-permissions",
                   "--bare", "--fallback-model", "--cloud", "--environment", "--bg", "--exec",
                   "--continue", "-c", "--resume", "-r", "--fork-session"}


def read_json(path):
    data = Path(path).read_bytes()
    require(len(data) <= MAX_INPUT_BYTES, "bridge configuration exceeds 1 MB")
    return strict_json(data.decode("utf-8"))


def prepare(config, request, base):
    require(os.environ.get("COUNCIL_ACTIVE") == "1", "bridge must run inside council ownership")
    require(isinstance(config, dict) and set(config) == {
        "schema_version", "enabled", "provider", "parser", "reviewed_native_command", "settings", "command"},
        "unknown or missing bridge configuration fields")
    require(type(config["schema_version"]) is int and config["schema_version"] == 1, "invalid bridge schema")
    require(config["enabled"] is True and config["reviewed_native_command"] is True,
            "bridge is disabled or command has not been reviewed")
    require(config["parser"] == "claude_result_json_v1", "unsupported native parser")
    require(isinstance(request, dict) and type(request.get("protocol_version")) is int
            and request["protocol_version"] == 1, "invalid request protocol")
    require(request.get("provider") == config["provider"] and isinstance(config["provider"], str)
            and config["provider"].strip(), "provider mismatch")
    require(isinstance(request.get("job_id"), str) and request["job_id"].strip(), "missing job identity")
    require(request.get("billing") == "subscription", "subscription route required")
    requested = request.get("requested")
    require(isinstance(requested, dict) and requested == config["settings"], "settings differ from reviewed binding")
    require(set(requested) == {"model", "effort", "fast", "modes"}, "unsupported requested fields")
    for name in ("model", "effort"):
        require(isinstance(requested[name], str) and requested[name].strip()
                and not requested[name].startswith("-") and "\x00" not in requested[name], "invalid setting token")
    require(requested["fast"] is False, "Fast must be off")
    require(requested["effort"] != "ultracode", "configure effort and ultracode separately")
    modes = requested["modes"]
    require(isinstance(modes, dict) and set(modes).issubset({"ultracode"})
            and all(type(value) is bool for value in modes.values()), "bridge supports only the optional ultracode mode")
    native_settings = {"fastMode": False, **modes}
    command = config["command"]
    require(isinstance(command, list) and command and all(isinstance(x, str) and "\x00" not in x for x in command),
            "command must be an argv array")
    require(command[0].strip(), "missing native executable")
    require("--" not in command, "option terminator is unsupported")
    for arg in command[1:]:
        require(arg.split("=", 1)[0] not in BLOCKED_OPTIONS, "forbidden native option")
    require(not any(arg == "bypassPermissions" or arg == "--permission-mode=bypassPermissions" for arg in command),
            "permission bypass is unsupported")
    for token, flags in PLACEHOLDERS.items():
        require(command.count(token) == 1, f"one complete {token} argument required")
        index = command.index(token)
        require(index > 0 and command[index - 1] in flags, f"{token} must follow its native option")
        require(sum(arg.split("=", 1)[0] in flags for arg in command[1:]) == 1,
                "duplicate or inline native setting option")
    require(sum(arg in ("--print", "-p") for arg in command[1:]) == 1,
            "one native print-mode flag required; prompt arrives on stdin")
    require(command.count("--output-format") == 1, "one output-format option required")
    output_index = command.index("--output-format")
    require(output_index + 1 < len(command) and command[output_index + 1] in ("json", "stream-json"),
            "native JSON output required")
    require(not any(arg.startswith(("--output-format=", "--print=", "-p=")) for arg in command),
            "inline output/prompt arguments are unsupported")
    values = {"{model}": requested["model"], "{effort}": requested["effort"],
              "{settings_json}": json.dumps(native_settings, separators=(",", ":"))}
    argv = [values.get(arg, arg) for arg in command]
    executable = argv[0]
    if Path(executable).is_absolute() or "/" in executable or "\\" in executable:
        executable = str((base / executable).resolve())
        require(Path(executable).is_file(), "native executable does not exist")
    else:
        executable = shutil.which(executable)
        require(executable is not None, "native executable is not on PATH")
    require(Path(executable).suffix.lower() not in {".bat", ".cmd", ".ps1"}, "implicit shell executables are unsupported")
    argv[0] = executable
    question = request.get("question")
    require(isinstance(question, str) and question.strip() and len(question.encode("utf-8")) <= MAX_INPUT_BYTES,
            "invalid question")
    guard = request.get("guard")
    require(isinstance(guard, str) and guard.strip(), "missing consultation guard")
    timeout = checked_number(request.get("timeout_seconds"), 0.001, 600, "timeout_seconds")
    return argv, guard + "\n\n" + question, timeout


def parse_native(output, code, requested):
    require(code == 0, "native process did not succeed")
    try:
        events = [strict_json(output)]
    except json.JSONDecodeError:
        events = [strict_json(line) for line in output.splitlines() if line.strip()]
    require(events and all(isinstance(event, dict) for event in events), "invalid native JSON shape")
    models = set()
    billing = None
    for event in events:
        # Inspect structured metadata only, never parse model prose as settings.
        pending = [event]
        while pending:
            value = pending.pop()
            if isinstance(value, dict):
                require(value.get("isUsingOverage") is None or value.get("isUsingOverage") is False,
                        "native overage use reported or ambiguous")
                if value.get("billing") is not None:
                    require(value["billing"] == "subscription", "native billing contradiction")
                    billing = "subscription"
                pending.extend(value.values())
            elif isinstance(value, list):
                pending.extend(value)
        if event.get("type") in ("system", "result"):
            if event.get("model") is not None:
                require(isinstance(event["model"], str), "invalid native model metadata")
                models.add(event["model"])
            if event.get("type") == "result" and event.get("modelUsage") is not None:
                require(isinstance(event["modelUsage"], dict), "invalid native modelUsage")
                models.update(event["modelUsage"])
    require(not models or models == {requested["model"]}, "native model differs from request")
    results = [event for event in events if event.get("type") == "result"]
    require(results, "native terminal result missing")
    result = results[-1]
    require(result.get("subtype") == "success" and result.get("is_error") is False,
            "native result not successful")
    require(result.get("terminal_reason") in (None, "completed")
            and result.get("stop_reason") in (None, "end_turn", "stop_sequence"), "native result incomplete")
    answer = result.get("result")
    require(isinstance(answer, str) and answer.strip(), "native answer empty")
    effective = {"model": next(iter(models)) if models else None, "effort": None,
                 "fast": None, "modes": None, "billing": billing}
    return answer, effective


def bridge(config, request, base):
    argv, prompt, timeout = prepare(config, request, base)
    env = {key: value for key, value in os.environ.items() if key.upper() in BASE_ENV}
    env.update(COUNCIL_ACTIVE="1", PYTHONUTF8="1", PYTHONDONTWRITEBYTECODE="1")
    proc = run_process(argv, cwd=base, env=env, input_text=prompt, timeout=timeout,
                       cancelled=threading.Event(), label="native CLI", inherit_process_group=True)
    answer, effective = parse_native(proc.stdout, proc.returncode, request["requested"])
    return {"protocol_version": 1, "job_id": request["job_id"], "provider": request["provider"],
            "terminal": True, "status": "completed", "answer": answer, "effective": effective}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", required=True)
    args = parser.parse_args()
    request = None
    try:
        raw = sys.stdin.buffer.read(MAX_INPUT_BYTES + 1)
        require(len(raw) <= MAX_INPUT_BYTES, "adapter request exceeds 1 MB")
        request = strict_json(raw.decode("utf-8"))
        path = Path(args.config).resolve()
        result = bridge(read_json(path), request, path.parent)
        print(json.dumps(result, ensure_ascii=True))
        return 0
    except (ValueError, OSError, TypeError, ProcessRunError) as exc:
        # Raw native output/errors can contain private data. Fail without replay.
        request = request if isinstance(request, dict) else {}
        print(json.dumps({"protocol_version": 1, "job_id": request.get("job_id"),
                          "provider": request.get("provider"), "terminal": True,
                          "status": "failed", "error_kind": "unknown", "inference_started": None,
                          "cleanup_incomplete": bool(getattr(exc, "cleanup_incomplete", False))}))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
