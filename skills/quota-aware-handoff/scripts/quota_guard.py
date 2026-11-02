#!/usr/bin/env python3
"""Conservative quota normalization and work-unit admission; Python 3.9+ stdlib.

No credentials, network requests, inference calls, or provider launches. Percent
means percentage points of ONE named quota bucket, never context-window tokens.
"""
import argparse
import json
import math
import os
from pathlib import Path
import sys
import tempfile
from datetime import datetime, timedelta, timezone

STOP = "CHECKPOINT_AND_HANDOFF"
UNITS = {"percent", "credits", "tokens", "requests"}


def number(value):
    try:
        return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)
    except OverflowError:
        return False


def timestamp(value):
    if not isinstance(value, str):
        raise ValueError("timestamp must be an ISO string with a time zone")
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("timestamp requires a time zone")
    return parsed.astimezone(timezone.utc)


def iso(value):
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def text_value(value):
    return isinstance(value, str) and bool(value.strip())


def evaluate(snapshot, unit, now=None):
    """Return admission evidence; never infer quota coverage or missing balances."""
    reasons, warnings, checks = [], [], []
    current = timestamp(now) if isinstance(now, str) else (now or datetime.now(timezone.utc))
    if current.tzinfo is None:
        raise ValueError("now requires a time zone")
    if not isinstance(snapshot, dict) or not isinstance(unit, dict):
        return {"decision": STOP, "reasons": ["invalid_document"], "warnings": [], "checks": []}
    for key in ("provider", "quota_pool_id", "account_ref", "model"):
        if not text_value(snapshot.get(key)):
            reasons.append("missing_snapshot_" + key)
    for key in ("id", "quota_pool_id", "model", "estimate_basis"):
        if not text_value(unit.get(key)):
            reasons.append("missing_unit_" + key)
    for key in ("quota_pool_id", "model"):
        if snapshot.get(key) != unit.get(key):
            reasons.append(key + "_mismatch")
    if snapshot.get("coverage_complete") is not True:
        reasons.append("quota_coverage_unknown")
    if snapshot.get("hard_stop") is not False:
        reasons.append("provider_hard_stop_or_unknown")
    if snapshot.get("normalization_issues"):
        reasons.append("normalization_incomplete")
    ttl = snapshot.get("freshness_seconds", 120)
    if not number(ttl) or ttl <= 0:
        reasons.append("invalid_freshness_seconds")
        ttl = 120

    def fresh(value, label):
        try:
            age = (current - timestamp(value)).total_seconds()
            if age < -5:
                reasons.append(label + ":future_timestamp")
            elif age > ttl:
                reasons.append(label + ":stale")
        except (TypeError, ValueError, OverflowError):
            reasons.append(label + ":invalid_timestamp")

    fresh(snapshot.get("observed_at"), "snapshot")
    safety = unit.get("safety_factor", 1.5)
    if not number(safety) or safety < 1:
        reasons.append("invalid_safety_factor")
        safety = 1.5
    estimates = unit.get("estimates")
    if not isinstance(estimates, dict) or not estimates:
        reasons.append("missing_estimates")
        estimates = {}
    limits = snapshot.get("limits")
    if not isinstance(limits, list) or not limits:
        reasons.append("missing_quota_limits")
        limits = []
    seen = set()
    for limit in limits:
        if not isinstance(limit, dict) or not text_value(limit.get("id")):
            reasons.append("invalid_limit")
            continue
        key = limit["id"]
        if key in seen:
            reasons.append(key + ":duplicate_limit")
        seen.add(key)
        fresh(limit.get("observed_at"), key)
        if not text_value(limit.get("source")):
            reasons.append(key + ":missing_source")
        confidence = limit.get("confidence")
        if confidence not in ("measured", "estimated"):
            reasons.append(key + ":balance_unknown")
        remaining = limit.get("remaining")
        valid_balance = number(remaining) and remaining >= 0
        if limit.get("unit") == "percent" and valid_balance and remaining > 100:
            valid_balance = False
        if not valid_balance or limit.get("unit") not in UNITS:
            reasons.append(key + ":invalid_balance_or_unit")
        reset = limit.get("reset_at")
        if reset is not None:
            try:
                if timestamp(reset) <= current:
                    reasons.append(key + ":reset_passed_refresh_required")
            except (TypeError, ValueError, OverflowError):
                reasons.append(key + ":invalid_reset_at")
        estimate = estimates.get(key)
        if not isinstance(estimate, dict):
            reasons.append(key + ":missing_estimate")
            continue
        if estimate.get("unit") != limit.get("unit"):
            reasons.append(key + ":unit_mismatch")
        values = [estimate.get(field) for field in
                  ("next_cost_upper_bound", "handoff_reserve", "uncertainty_margin")]
        if not all(number(v) and v >= 0 for v in values) or values[1] <= 0:
            reasons.append(key + ":invalid_estimate_or_no_handoff_reserve")
            continue
        if confidence == "estimated" and values[2] <= 0:
            reasons.append(key + ":estimated_balance_needs_margin")
        required = safety * values[0] + values[1] + values[2]
        if not math.isfinite(required):
            reasons.append(key + ":nonfinite_required_budget")
            continue
        checks.append({"id": key, "unit": limit.get("unit"),
                       "remaining": remaining if valid_balance else None, "required": required})
        if valid_balance and remaining < required:
            reasons.append(key + ":insufficient_budget")
    for missing in sorted(set(estimates) - seen):
        reasons.append(str(missing) + ":expected_limit_missing")

    needed = unit.get("context_needed_tokens")
    context = snapshot.get("context")
    if not number(needed) or needed <= 0:
        reasons.append("invalid_context_needed_tokens")
    if not isinstance(context, dict):
        reasons.append("context_unknown")
    else:
        window, remaining, used = (context.get(k) for k in
                                   ("effective_window", "remaining_tokens", "used_percent"))
        valid = all(number(v) for v in (window, remaining, used))
        if not valid or window <= 0 or remaining < 0 or remaining > window or not 0 <= used <= 100:
            reasons.append("invalid_context")
        else:
            fresh(context.get("observed_at", snapshot.get("observed_at")), "context")
            if remaining > window * (1 - used / 100) + window * 0.01:
                reasons.append("context_inconsistent")
            if number(needed) and remaining < needed:
                reasons.append("context_insufficient")
            if window <= 500000:
                if used >= 90:
                    reasons.append("context_90_percent_checkpoint_handoff")
                elif used >= 70:
                    warnings.append("context_70_percent_save_checkpoint_and_compact")
                elif used >= 50:
                    warnings.append("context_50_percent_warning")
    return {"decision": STOP if reasons else "CONTINUE", "unit_id": unit.get("id"),
            "checked_at": iso(current), "reasons": list(dict.fromkeys(reasons)),
            "warnings": warnings, "checks": checks}


def normalize(provider, raw, *, pool, model, account_ref, observed_at,
              coverage_complete=False, freshness_seconds=120, amount_units=None):
    """Normalize official exported/statusline JSON, preserving missing data.

    The caller must retain the actual observation time of exported/pasted data.
    --coverage-complete is an attestation, not automatic provider discovery.
    """
    observed = timestamp(observed_at)
    result = {"schema_version": 1, "provider": provider, "quota_pool_id": pool,
              "account_ref": account_ref, "model": model, "observed_at": iso(observed),
              "freshness_seconds": freshness_seconds, "coverage_complete": bool(coverage_complete),
              "hard_stop": False, "limits": [], "context": None, "normalization_issues": []}
    issues = result["normalization_issues"]
    if not isinstance(raw, dict):
        issues.append("invalid_payload")
        return result

    def reset_time(value, label):
        if value is None:
            return None
        try:
            return iso(datetime.fromtimestamp(value, timezone.utc) if number(value) else timestamp(value))
        except (TypeError, ValueError, OverflowError, OSError):
            issues.append(label + ":invalid_reset_at")
            return None

    def add(key, unit, amount, reset, source):
        valid = unit in UNITS and number(amount) and amount >= 0 and (unit != "percent" or amount <= 100)
        if not valid:
            issues.append(key + ":missing_or_invalid_balance")
        result["limits"].append({"id": key, "unit": unit, "remaining": amount if valid else None,
                                 "observed_at": iso(observed), "source": source,
                                 "reset_at": reset_time(reset, key),
                                 "confidence": "measured" if valid else "unknown"})

    if provider == "codex":
        body = raw.get("result", raw.get("params", raw))
        body = body if isinstance(body, dict) else {}
        buckets = body.get("rateLimitsByLimitId")
        if not isinstance(buckets, dict) or not buckets:
            bucket = body.get("rateLimits")
            buckets = {bucket.get("limitId") or "codex": bucket} if isinstance(bucket, dict) else {}
        for bucket_id, bucket in buckets.items():
            if not isinstance(bucket, dict):
                issues.append(str(bucket_id) + ":invalid_bucket")
                continue
            if bucket.get("rateLimitReachedType") is not None:
                result["hard_stop"] = True
            window_count = 0
            for name in ("primary", "secondary"):
                window = bucket.get(name)
                if window is None:
                    continue  # Null secondary is not evidence of a remaining balance.
                if not isinstance(window, dict):
                    issues.append(str(bucket_id) + ":invalid_window")
                    continue
                window_count += 1
                used = window.get("usedPercent")
                amount = 100 - used if number(used) else None
                add(str(bucket_id) + "/" + name, "percent", amount, window.get("resetsAt"),
                    "codex:account/rateLimits/read")
            if window_count == 0:
                issues.append(str(bucket_id) + ":missing_window_balance")
    elif provider == "claude":
        windows = raw.get("rate_limits")
        if isinstance(windows, dict):
            for name, window in windows.items():
                if not isinstance(window, dict):
                    issues.append(str(name) + ":missing_window")
                    continue
                used = window.get("used_percentage")
                if number(used) and used >= 100:
                    result["hard_stop"] = True
                add(str(name), "percent", 100 - used if number(used) else None,
                    window.get("resets_at"), "claude:statusline.rate_limits")
    elif provider == "antigravity":
        buckets = raw.get("quota")
        if isinstance(buckets, dict):
            for key, bucket in buckets.items():
                if not isinstance(bucket, dict):
                    issues.append(str(key) + ":invalid_bucket")
                    continue
                if bucket.get("disabled") is True:
                    result["hard_stop"] = True
                elif bucket.get("disabled") is not None and bucket.get("disabled") is not False:
                    issues.append(str(key) + ":invalid_disabled_flag")
                reset = bucket.get("reset_time")
                if reset is None and number(bucket.get("reset_in_seconds")):
                    reset = iso(observed + timedelta(seconds=bucket["reset_in_seconds"]))
                fraction, amount = bucket.get("remaining_fraction"), bucket.get("remaining_amount")
                both = fraction is not None and amount is not None
                if fraction is not None or amount is None:
                    add(str(key) + ("/percent" if both else ""), "percent",
                        fraction * 100 if number(fraction) else None, reset, "antigravity:statusline.quota")
                if amount is not None:
                    amount_unit = (amount_units or {}).get(str(key), "unknown")
                    add(str(key) + ("/amount" if both else ""), amount_unit, amount,
                        reset, "antigravity:statusline.quota")
    else:
        raise ValueError("unsupported provider")
    if not result["limits"]:
        issues.append("quota_missing")
    raw_model = raw.get("model")
    if isinstance(raw_model, dict) and text_value(raw_model.get("id")) and raw_model["id"] != model:
        issues.append("model_mismatch")
    context = raw.get("context_window")
    if isinstance(context, dict):
        window, remaining, used = (context.get(k) for k in
                                   ("context_window_size", "remaining_percentage", "used_percentage"))
        context_remaining = None
        if number(window) and number(remaining) and window > 0 and 0 <= remaining <= 100:
            context_remaining = math.floor(window * remaining / 100)
        result["context"] = {"effective_window": window if number(window) else None,
                             "remaining_tokens": context_remaining,
                             "used_percent": used if number(used) else None, "observed_at": iso(observed),
                             "source": provider + ":statusline.context_window",
                             "confidence": "estimated"}  # Percentage-derived, not an exact token count.
    return result


def strict_json(encoded):
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError("duplicate_json_key")
            result[key] = value
        return result

    def finite(value):
        parsed = float(value)
        if not math.isfinite(parsed):
            raise ValueError("nonfinite_json_number")
        return parsed

    return json.loads(encoded, object_pairs_hook=pairs, parse_constant=finite, parse_float=finite)


def read_json(path):
    if path == "-":
        return strict_json(sys.stdin.read())
    with open(path, encoding="utf-8-sig") as stream:
        return strict_json(stream.read())


def write_json(path, value):
    if path is None or path == "-":
        print(json.dumps(value, ensure_ascii=True, indent=2, allow_nan=False))
        return
    text = json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n"
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix="." + target.name, dir=target.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            stream.write(text)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, target)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    admission = commands.add_parser("evaluate", help="evaluate normalized snapshot against one work unit")
    admission.add_argument("--snapshot", required=True)
    admission.add_argument("--unit", required=True)
    admission.add_argument("--now", help="ISO timestamp, for deterministic verification only")
    norm = commands.add_parser("normalize", help="normalize exported official payload; does not query providers")
    norm.add_argument("--provider", choices=("codex", "claude", "antigravity"), required=True)
    norm.add_argument("--input", required=True, help="JSON file or - for stdin")
    norm.add_argument("--output", default="-", help="atomic JSON output, or - for stdout")
    norm.add_argument("--pool", required=True)
    norm.add_argument("--model", required=True)
    norm.add_argument("--account-ref", required=True, help="nonsecret local alias; never a token or API key")
    norm.add_argument("--observed-at", required=True, help="actual original observation time, with timezone")
    norm.add_argument("--freshness-seconds", type=float, default=120)
    norm.add_argument("--coverage-complete", action="store_true", help="attest all applicable quota buckets are represented")
    norm.add_argument("--amount-unit", action="append", default=[], metavar="BUCKET=UNIT",
                      help="verified unit of an Antigravity remaining_amount bucket; repeat per bucket")
    args = parser.parse_args()
    try:
        if args.command == "evaluate":
            result = evaluate(read_json(args.snapshot), read_json(args.unit), args.now)
            write_json(None, result)
            return 0 if result["decision"] == "CONTINUE" else 2
        amount_units = {}
        for pair in args.amount_unit:
            bucket, separator, unit_name = pair.partition("=")
            if not separator or not bucket or unit_name not in UNITS or bucket in amount_units:
                raise ValueError("invalid amount-unit mapping")
            amount_units[bucket] = unit_name
        result = normalize(args.provider, read_json(args.input), pool=args.pool, model=args.model,
                           account_ref=args.account_ref, observed_at=args.observed_at,
                           coverage_complete=args.coverage_complete, freshness_seconds=args.freshness_seconds,
                           amount_units=amount_units)
        write_json(args.output, result)
        return 0
    except (OSError, ValueError, TypeError, OverflowError) as exc:
        # Avoid copying input JSON (which can contain private provider data) into logs.
        write_json(None, {"decision": STOP, "reasons": ["input_error:" + type(exc).__name__]})
        return 2


if __name__ == "__main__":
    sys.exit(main())
