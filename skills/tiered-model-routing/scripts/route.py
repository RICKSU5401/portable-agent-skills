#!/usr/bin/env python3
"""Build an offline routing plan from declarations; never launch a model."""
import argparse
import copy
import json
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

VERSION = "2.0.0-rc.1"
RISK_FLOORS = dict.fromkeys(("security", "permissions", "release", "data-migration"), 3)
RISK_FLOORS.update(dict.fromkeys(("cross-provider", "unknown-mode", "mode-conflict"), 4))
FALLBACK_REASONS = {"subscription_not_entitled", "model_quota_exhausted",
                    "subscription_requires_paid_credits", "model_not_offered"}
NATIVE_SOURCES = {"native_catalog", "native_probe"}
SOURCES = NATIVE_SOURCES | {"native_config", "fixture", "official_docs", "native_control_catalog"}
MODE_KEYS = ("provider", "surface", "model_id", "family", "version", "effort",
             "modes", "context_tokens", "billing", "quota_status")


class Invalid(ValueError):
    pass


def require(condition, message):
    if not condition:
        raise Invalid(message)


def nonempty(value):
    return isinstance(value, str) and bool(value.strip())


def token(value):
    return nonempty(value) and not any(c.isspace() for c in value)


def integer(value, minimum=0):
    return type(value) is int and value >= minimum


def keys(value, required, optional=()):
    require(type(value) is dict and set(required) <= set(value)
            and set(value) <= set(required) | set(optional), "keys must match schema")


def version_key(version):
    """Numeric comparison, treating trailing zero components as equivalent."""
    normalized = list(version)
    while len(normalized) > 1 and normalized[-1] == 0:
        normalized.pop()
    return tuple(normalized)


def classify(complexity, uncertainty, impact, risks=(), failed_attempts=0):
    factors = dict(complexity=complexity, uncertainty=uncertainty, impact=impact)
    require(all(integer(v) and v <= 3 for v in factors.values()), "factors must be integers 0..3")
    require(integer(failed_attempts), "failed_attempts must be a nonnegative integer")
    require(isinstance(risks, (list, tuple)) and all(isinstance(r, str) and r in RISK_FLOORS
            for r in risks), "unknown risk")
    score = sum(factors.values())
    base = 1 if score <= 2 else 2 if score <= 4 else 3 if score <= 6 else 4
    floor = max((RISK_FLOORS[r] for r in risks), default=1)
    tier = min(4, max(base, floor) + int(failed_attempts >= 2))
    return dict(factors=factors, score=score, base=f"D{base}", risk_floor=f"D{floor}",
                failed_attempts=failed_attempts, risks=list(risks), tier=f"D{tier}")


def fresh(timestamp, now, max_age_hours):
    require(isinstance(timestamp, str) and re.fullmatch(
        r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|\+00:00)", timestamp),
        "observed_at must be an ISO UTC timestamp")
    try:
        observed = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
    except ValueError as error:
        raise Invalid("invalid observed_at") from error
    require(now - observed <= timedelta(hours=max_age_hours), "evidence is stale")
    require(observed - now <= timedelta(minutes=5), "evidence is more than 5 minutes in future")


def validate_policy(policy, provider):
    keys(policy, {"schema_version", "max_evidence_age_hours", "providers"})
    require(type(policy["schema_version"]) is int and policy["schema_version"] == 1,
            "policy schema_version must be integer 1")
    require(integer(policy["max_evidence_age_hours"], 1) and policy["max_evidence_age_hours"] <= 24,
            "max_evidence_age_hours must be an integer 1..24")
    require(type(policy["providers"]) is dict and bool(policy["providers"]), "providers must be nonempty")
    for name, config in policy["providers"].items():
        require(token(name), "provider must be a nonempty token")
        keys(config, {"surface", "tiers"}, {"access_mode", "host_control_authorized"})
        require(token(config["surface"]), "surface must be a nonempty token")
        access_mode = config.get("access_mode", "subscription_cli")
        require(access_mode in ("subscription_cli", "host_native"), "invalid access_mode")
        if access_mode == "host_native":
            require(config.get("host_control_authorized") is True,
                    "host_native requires explicit authorization to use existing host controls")
        else:
            require("host_control_authorized" not in config, "host authorization is not CLI billing evidence")
        keys(config["tiers"], {"D1", "D2", "D3", "D4"})
        for tier, rule in config["tiers"].items():
            keys(rule, {"family", "effort_preference", "modes", "min_context_tokens"}, {"fallback"})
            require(token(rule["family"]), "family must be a nonempty token")
            efforts = rule["effort_preference"]
            require(type(efforts) is list and bool(efforts) and all(token(e) for e in efforts),
                    "effort_preference must be a nonempty list of native effort tokens")
            require(len(efforts) == len(set(efforts)), "duplicate effort preference")
            require(type(rule["modes"]) is dict, "modes must be a mapping")
            require(access_mode == "host_native" or "fast" in rule["modes"], "CLI modes must explicitly set fast")
            require(all(token(k) and type(v) is bool for k, v in rule["modes"].items()),
                    "requested modes must be named booleans")
            require(rule["min_context_tokens"] is None or integer(rule["min_context_tokens"], 1),
                    "min_context_tokens must be positive integer or null")
            if "fallback" in rule:
                require(access_mode == "subscription_cli", "host-native plans do not infer subscription fallback")
                require(tier == "D4", "subscription fallback is limited to general-task D4")
                keys(rule["fallback"], {"family", "reasons"})
                require(token(rule["fallback"]["family"]) and rule["fallback"]["family"] != rule["family"],
                        "fallback must name a different family")
                reasons = rule["fallback"]["reasons"]
                require(type(reasons) is list and bool(reasons) and all(isinstance(r, str)
                        and r in FALLBACK_REASONS for r in reasons), "invalid fallback reasons")
    require(token(provider) and provider in policy["providers"], "provider missing from policy")
    return policy["providers"][provider]


def validate_catalog(catalog, provider, config, now, max_age_hours):
    keys(catalog, {"schema_version", "provider", "surface", "observed_at", "source_kind",
                   "source_ref", "account_alias", "authenticated", "models"}, {"fallback_evidence", "host_control"})
    require(type(catalog["schema_version"]) is int and catalog["schema_version"] == 2,
            "catalog schema_version must be integer 2")
    require(catalog["provider"] == provider, "catalog provider mismatch")
    require(catalog["surface"] == config["surface"], "catalog surface mismatch")
    require(isinstance(catalog["source_kind"], str) and catalog["source_kind"] in SOURCES, "unknown source_kind")
    for field in ("source_ref", "account_alias"):
        require(nonempty(catalog[field]), f"{field} must be nonempty")
    require(type(catalog["authenticated"]) is bool, "authenticated must be boolean")
    fresh(catalog["observed_at"], now, max_age_hours)
    require(type(catalog["models"]) is list, "models must be a list")
    ids = set()
    for model in catalog["models"]:
        keys(model, {"id", "family", "version", "available", "efforts", "modes", "mode_billing",
                     "context_tokens", "billing", "subscription_eligible", "quota_status"})
        require(token(model["id"]) and token(model["family"]), "id and family must be nonempty tokens")
        require(model["id"] not in ids, "duplicate model id")
        ids.add(model["id"])
        require(type(model["version"]) is list and bool(model["version"])
                and all(integer(v) for v in model["version"]), "version must contain nonnegative integers")
        for field in ("available", "subscription_eligible"):
            require(model[field] is None or type(model[field]) is bool, f"{field} must be boolean or null")
        require(type(model["efforts"]) is list and all(token(e) for e in model["efforts"]), "invalid efforts")
        require(len(model["efforts"]) == len(set(model["efforts"])), "duplicate effort")
        require(type(model["modes"]) is dict and type(model["mode_billing"]) is dict, "invalid modes")
        for name, states in model["modes"].items():
            require(token(name) and type(states) is list and all(type(v) is bool for v in states),
                    "mode capabilities must be lists of booleans")
            require(len(states) == len(set(states)), "duplicate mode state")
        for name, billing in model["mode_billing"].items():
            require(name in model["modes"] and isinstance(billing, str)
                    and billing in ("subscription", "paid", "unknown"), "invalid mode billing")
        require(model["context_tokens"] is None or integer(model["context_tokens"], 1),
                "context_tokens must be positive integer or null")
        require(isinstance(model["billing"], str) and model["billing"] in
                ("subscription", "api", "usage_credits", "unknown"), "invalid billing")
        require(isinstance(model["quota_status"], str) and model["quota_status"] in
                ("available", "exhausted", "unknown"), "invalid quota_status")


def latest_model(models, family):
    matching = [m for m in models if m["family"] == family]
    if not matching:
        return None
    newest = max(version_key(m["version"]) for m in matching)
    latest = [m for m in matching if version_key(m["version"]) == newest]
    require(len(latest) == 1, "ambiguous latest model; refine catalog using native identity evidence")
    return latest[0]


def eligible(model):
    return model is not None and model["available"] is True and model["billing"] == "subscription" \
        and model["subscription_eligible"] is True and model["quota_status"] == "available"


def validate_fallback(evidence, rule, catalog, now, max_age_hours):
    require("fallback" in rule, "fallback was not authorized in policy")
    keys(evidence, {"scope", "from_family", "to_family", "primary_model_id", "fallback_model_id", "reason", "observed_at", "source_kind",
                    "source_ref", "account_alias", "authenticated"})
    require(evidence["scope"] == "general_d4", "fallback must not change fixed council policy")
    require(evidence["from_family"] == rule["family"] and evidence["to_family"] == rule["fallback"]["family"],
            "fallback families mismatch")
    require(token(evidence["primary_model_id"]) and token(evidence["fallback_model_id"]), "fallback requires exact model IDs")
    require(evidence["reason"] in rule["fallback"]["reasons"], "fallback reason is not authorized")
    require(evidence["account_alias"] == catalog["account_alias"], "fallback account mismatch")
    require(evidence["authenticated"] is True and evidence["source_kind"] in NATIVE_SOURCES
            and catalog["source_kind"] in NATIVE_SOURCES and catalog["authenticated"] is True,
            "fallback needs authenticated native account evidence")
    require(nonempty(evidence["source_ref"]), "fallback needs an evidence reference")
    fresh(evidence["observed_at"], now, max_age_hours)


def plan(provider, complexity, uncertainty, impact, catalog, policy, risks=(), failed_attempts=0, now=None):
    output = dict(status="BLOCKED", requested=dict.fromkeys(MODE_KEYS),
                  resolved=dict.fromkeys(MODE_KEYS), effective=dict.fromkeys(MODE_KEYS),
                  difficulty=None, reasons=[], live_verified=False, execution="not_performed",
                  catalog_evidence=None, fallback=None)

    def block(code, message):
        output["reasons"].append(dict(code=code, message=message))

    try:
        config = validate_policy(policy, provider)
        difficulty = classify(complexity, uncertainty, impact, risks, failed_attempts)
        output["difficulty"] = difficulty
        rule = config["tiers"][difficulty["tier"]]
        requested = output["requested"]
        requested.update(provider=provider, surface=config["surface"], family=rule["family"],
                         effort=copy.deepcopy(rule["effort_preference"]), modes=copy.deepcopy(rule["modes"]),
                         context_tokens=rule["min_context_tokens"], billing="subscription", quota_status="available")
        now = now or datetime.now(timezone.utc)
        require(isinstance(now, datetime) and now.tzinfo is not None and now.utcoffset() == timedelta(0),
                "now must be UTC")
        max_age_hours = policy["max_evidence_age_hours"]
        validate_catalog(catalog, provider, config, now, max_age_hours)
        host_native = config.get("access_mode") == "host_native"
        if host_native:
            requested["billing"] = "unknown"
            requested["quota_status"] = None
            control = catalog.get("host_control")
            keys(control, {"name", "source_ref", "observed_at", "supported"})
            require(token(control["name"]) and nonempty(control["source_ref"]), "host control needs native evidence")
            require(control["supported"] is True and catalog["source_kind"] == "native_control_catalog",
                    "host-native mode needs a supported native control catalog")
            require("fallback_evidence" not in catalog, "host-native mode cannot infer subscription fallback")
            fresh(control["observed_at"], now, max_age_hours)
        output["catalog_evidence"] = {k: copy.deepcopy(v) for k, v in catalog.items()
                                      if k not in ("models", "fallback_evidence")}
        output["catalog_evidence"]["trust"] = "caller_declared_unverified"
        if not host_native and (catalog["source_kind"] not in NATIVE_SOURCES or catalog["authenticated"] is not True):
            block("INSUFFICIENT_EVIDENCE", "Authenticated account-specific native evidence is required.")
        family = rule["family"]
        model = latest_model(catalog["models"], family)
        if difficulty["tier"] == "D4" and "fallback_evidence" in catalog:
            evidence = catalog["fallback_evidence"]
            validate_fallback(evidence, rule, catalog, now, max_age_hours)
            require(not eligible(model), "conflicting primary availability and fallback evidence")
            if model is not None:
                require(model["id"] == evidence["primary_model_id"], "denial does not identify the latest primary model")
                reason = evidence["reason"]
                require(not (reason == "model_not_offered" and model["available"] is True),
                        "denial conflicts with primary availability")
                require(not (reason == "model_quota_exhausted" and model["quota_status"] == "available"),
                        "denial conflicts with primary quota")
                require(not (reason in ("subscription_not_entitled", "subscription_requires_paid_credits")
                             and model["billing"] == "subscription" and model["subscription_eligible"] is True),
                        "denial conflicts with primary subscription eligibility")
            family = rule["fallback"]["family"]
            model = latest_model(catalog["models"], family)
            require(model is not None and model["id"] == evidence["fallback_model_id"],
                    "fallback evidence does not identify the latest fallback model")
            output["fallback"] = dict(from_family=rule["family"], to_family=family,
                                      tier="D4", evidence=copy.deepcopy(evidence), state="planned_not_executed")
        require(model is not None, "requested family absent from catalog")
        resolved = output["resolved"]
        resolved.update(provider=provider, surface=config["surface"], model_id=model["id"],
                        family=family, version=copy.deepcopy(model["version"]), billing=model["billing"],
                        context_tokens=model["context_tokens"], quota_status=model["quota_status"], modes={})
        if model["available"] is not True:
            block("AVAILABILITY_UNCONFIRMED", "Latest model must be explicitly available.")
        if host_native:
            if model["billing"] != "unknown" or model["subscription_eligible"] is not None:
                block("BLOCKED_BILLING", "Host-tool mode preserves unexposed billing and eligibility as unknown.")
        elif model["billing"] != "subscription" or model["subscription_eligible"] is not True:
            block("BLOCKED_BILLING", "Only confirmed native subscription eligibility is permitted.")
        if model["quota_status"] == "exhausted" or (not host_native and model["quota_status"] != "available"):
            block("QUOTA_UNCONFIRMED", "Quota is unknown or exhausted; preserve a handoff budget and pause.")
        resolved["effort"] = next((e for e in rule["effort_preference"] if e in model["efforts"]), None)
        if resolved["effort"] is None:
            block("EFFORT_UNSUPPORTED", "Latest model lacks required effort; no downgrade is permitted.")
        minimum = rule["min_context_tokens"]
        if minimum is not None and (model["context_tokens"] is None or model["context_tokens"] < minimum):
            block("CONTEXT_UNCONFIRMED", "Required context capacity has not been evidenced.")
        for mode, state in rule["modes"].items():
            if state not in model["modes"].get(mode, []):
                resolved["modes"][mode] = None
                block("MODE_UNSUPPORTED", f"Latest model lacks explicit {mode}={state} capability.")
            else:
                resolved["modes"][mode] = state
            if state and model["mode_billing"].get(mode) != "subscription":
                block("BLOCKED_MODE_BILLING", f"Enabling {mode} needs confirmed subscription eligibility.")
        output["status"] = "BLOCKED" if output["reasons"] else ("HOST_TOOL_READY" if host_native else "PLAN_READY")
    except (Invalid, TypeError, KeyError) as error:
        block("INVALID_INPUT", str(error))
    return output


def unique_keys(pairs):
    result = {}
    for key, value in pairs:
        require(key not in result, f"duplicate JSON key: {key}")
        result[key] = value
    return result


def read_json(path):
    return json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=unique_keys,
                      parse_constant=lambda value: (_ for _ in ()).throw(Invalid(f"invalid JSON {value}")))


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--provider", required=True)
    for factor in ("complexity", "uncertainty", "impact"):
        parser.add_argument(f"--{factor}", required=True, type=int, choices=range(4))
    parser.add_argument("--catalog", required=True, type=Path)
    parser.add_argument("--policy", required=True, type=Path)
    parser.add_argument("--risk", action="append", choices=RISK_FLOORS, default=[])
    parser.add_argument("--failed-attempts", type=int, default=0)
    args = parser.parse_args(argv)
    try:
        policy, catalog = read_json(args.policy), read_json(args.catalog)
    except (OSError, ValueError) as error:
        result = plan(args.provider, args.complexity, args.uncertainty, args.impact, None, None)
        result["reasons"] = [dict(code="INVALID_JSON_INPUT", message=str(error))]
    else:
        result = plan(args.provider, args.complexity, args.uncertainty, args.impact,
                      catalog, policy, args.risk, args.failed_attempts)
    print(json.dumps(result, sort_keys=True, indent=2, allow_nan=False))
    return 0 if result["status"] in ("PLAN_READY", "HOST_TOOL_READY") else 2


if __name__ == "__main__":
    raise SystemExit(main())
