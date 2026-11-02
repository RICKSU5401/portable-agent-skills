"""Synthetic offline tests. No authentication, model calls, or real quota."""
import copy
import io
import json
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from datetime import datetime, timedelta, timezone
from pathlib import Path

import route

NOW = datetime(2030, 1, 1, 12, tzinfo=timezone.utc)


def policy():
    tiers = {f"D{i}": dict(family=family, effort_preference=["intensive", "normal"],
                          modes={"fast": False}, min_context_tokens=None)
             for i, family in enumerate(("small", "balanced", "strong", "expert"), 1)}
    return dict(schema_version=1, max_evidence_age_hours=24,
                providers={"example": dict(surface="example-cli", tiers=tiers)})


def model(family="strong", version=None, **changes):
    version = [1, 10] if version is None else version
    row = dict(id="example-" + family + "-" + ".".join(map(str, version)), family=family,
               version=version, available=True, efforts=["normal", "intensive"],
               modes={"fast": [False, True], "workflow": [False, True]},
               mode_billing={"fast": "paid", "workflow": "subscription"}, context_tokens=100000,
               billing="subscription", subscription_eligible=True, quota_status="available")
    row.update(changes)
    return row


def catalog(models=None, **changes):
    data = dict(schema_version=2, provider="example", surface="example-cli",
                observed_at=NOW.isoformat(), source_kind="native_catalog", authenticated=True,
                source_ref="SYNTHETIC test-only native declaration", account_alias="test-account",
                models=models if models is not None else [model()])
    data.update(changes)
    return data


def run(data=None, rules=None, factors=(2, 2, 1), **changes):
    return route.plan("example", *factors, catalog() if data is None else data,
                      policy() if rules is None else rules, now=NOW, **changes)


class RoutingTests(unittest.TestCase):
    def assertBlocked(self, result, code=None):
        self.assertEqual(result["status"], "BLOCKED")
        if code:
            self.assertIn(code, [r["code"] for r in result["reasons"]])

    def test_all_score_boundaries(self):
        for score, tier in enumerate((1, 1, 1, 2, 2, 3, 3, 4, 4, 4)):
            factors = (min(score, 3), min(max(score - 3, 0), 3), max(score - 6, 0))
            self.assertEqual(route.classify(*factors)["tier"], f"D{tier}")

    def test_risk_floors_and_repeated_failure(self):
        for risk, floor in route.RISK_FLOORS.items():
            self.assertEqual(route.classify(0, 0, 0, [risk])["tier"], f"D{floor}")
        self.assertEqual(route.classify(0, 0, 0, failed_attempts=2)["tier"], "D2")
        self.assertEqual(route.classify(3, 3, 3, failed_attempts=10)["tier"], "D4")

    def test_invalid_classification(self):
        for changes in ({"complexity": True}, {"complexity": -1}, {"impact": 4},
                        {"risks": ["unknown"]}, {"failed_attempts": -1}):
            args = dict(complexity=0, uncertainty=0, impact=0)
            args.update(changes)
            with self.subTest(changes=changes), self.assertRaises(route.Invalid):
                route.classify(**args)

    def test_user_defined_provider_family_and_effort_order(self):
        p = policy()
        p["providers"]["example"]["tiers"]["D3"]["family"] = "custom-family"
        result = run(catalog([model("custom-family")]), p)
        self.assertEqual(result["status"], "PLAN_READY")
        self.assertEqual(result["resolved"]["family"], "custom-family")
        self.assertEqual(result["resolved"]["effort"], "intensive")

    def test_each_tier_uses_configured_family(self):
        for family, factors in (("small", (0, 0, 0)), ("balanced", (1, 1, 1)),
                                ("strong", (2, 2, 1)), ("expert", (3, 3, 3))):
            result = run(catalog([model(family)]), factors=factors)
            self.assertEqual(result["status"], "PLAN_READY")
            self.assertEqual(result["resolved"]["family"], family)

    def test_numeric_latest_not_lexical(self):
        data = catalog([model(version=[1, 9]), model(version=[1, 10])])
        self.assertEqual(run(data)["resolved"]["version"], [1, 10])

    def test_latest_unavailable_does_not_downgrade(self):
        data = catalog([model(version=[1, 9]), model(available=False)])
        result = run(data)
        self.assertBlocked(result, "AVAILABILITY_UNCONFIRMED")
        self.assertEqual(result["resolved"]["version"], [1, 10])

    def test_latest_missing_mode_does_not_downgrade(self):
        data = catalog([model(version=[1, 9]), model(modes={"fast": []})])
        data["models"][-1]["mode_billing"] = {}
        result = run(data)
        self.assertBlocked(result, "MODE_UNSUPPORTED")
        self.assertEqual(result["resolved"]["version"], [1, 10])

    def test_latest_missing_effort_does_not_downgrade(self):
        data = catalog([model(version=[1, 9]), model(efforts=["minimal"])])
        self.assertBlocked(run(data), "EFFORT_UNSUPPORTED")

    def test_duplicate_and_ambiguous_numeric_versions_block(self):
        for rows in ([model(), model()], [model(), model(version=[1, 10, 0])]):
            self.assertBlocked(run(catalog(rows)), "INVALID_INPUT")

    def test_provider_and_surface_mismatch(self):
        for change in ({"provider": "other"}, {"surface": "example-api"}):
            self.assertBlocked(run(catalog(**change)), "INVALID_INPUT")

    def test_only_authenticated_native_catalog_or_probe(self):
        for source in ("fixture", "official_docs", "native_config"):
            self.assertBlocked(run(catalog(source_kind=source)), "INSUFFICIENT_EVIDENCE")
        self.assertBlocked(run(catalog(authenticated=False)), "INSUFFICIENT_EVIDENCE")
        self.assertEqual(run(catalog(source_kind="native_probe"))["status"], "PLAN_READY")

    def test_freshness_boundaries(self):
        for seconds in (-86401, 301):
            self.assertBlocked(run(catalog(observed_at=(NOW + timedelta(seconds=seconds)).isoformat())))
        for seconds in (-86400, 300):
            self.assertEqual(run(catalog(observed_at=(NOW + timedelta(seconds=seconds)).isoformat()))["status"], "PLAN_READY")

    def test_stricter_user_expiry_and_invalid_date(self):
        p = policy()
        p["max_evidence_age_hours"] = 1
        self.assertBlocked(run(catalog(observed_at=(NOW - timedelta(hours=2)).isoformat()), p))
        for timestamp in ("2030-02-30T00:00:00Z", "2030-01-01T13:00:00+01:00", "tomorrow"):
            self.assertBlocked(run(catalog(observed_at=timestamp)))

    def test_api_unknown_and_usage_credits_block(self):
        for billing in ("api", "unknown", "usage_credits"):
            self.assertBlocked(run(catalog([model(billing=billing)])), "BLOCKED_BILLING")

    def test_unknown_eligibility_and_availability_block(self):
        for change, code in (({"subscription_eligible": None}, "BLOCKED_BILLING"),
                             ({"subscription_eligible": False}, "BLOCKED_BILLING"),
                             ({"available": None}, "AVAILABILITY_UNCONFIRMED")):
            self.assertBlocked(run(catalog([model(**change)])), code)

    def test_unknown_quota_is_preserved_not_guessed(self):
        for status in ("unknown", "exhausted"):
            result = run(catalog([model(quota_status=status)]))
            self.assertBlocked(result, "QUOTA_UNCONFIRMED")
            self.assertEqual(result["resolved"]["quota_status"], status)

    def test_fast_defaults_off_even_if_paid_supported(self):
        result = run()
        self.assertEqual(result["status"], "PLAN_READY")
        self.assertIs(result["requested"]["modes"]["fast"], False)
        self.assertIs(result["resolved"]["modes"]["fast"], False)

    def test_fast_on_needs_explicit_policy_and_subscription_mode_billing(self):
        p = policy()
        p["providers"]["example"]["tiers"]["D3"]["modes"]["fast"] = True
        for billing in ("paid", "unknown"):
            self.assertBlocked(run(catalog([model(mode_billing={"fast": billing})]), p), "BLOCKED_MODE_BILLING")
        self.assertBlocked(run(catalog([model(mode_billing={})]), p), "BLOCKED_MODE_BILLING")
        result = run(catalog([model(mode_billing={"fast": "subscription"})]), p)
        self.assertEqual(result["status"], "PLAN_READY")

    def test_custom_modes_require_support_and_subscription_billing(self):
        p = policy()
        p["providers"]["example"]["tiers"]["D3"]["modes"]["workflow"] = True
        self.assertEqual(run(rules=p)["status"], "PLAN_READY")
        self.assertBlocked(run(catalog([model(mode_billing={})]), p), "BLOCKED_MODE_BILLING")

    def test_required_context_unknown_or_too_small_blocks(self):
        p = policy()
        p["providers"]["example"]["tiers"]["D3"]["min_context_tokens"] = 100000
        for context in (None, 99999):
            self.assertBlocked(run(catalog([model(context_tokens=context)]), p), "CONTEXT_UNCONFIRMED")
        self.assertEqual(run(rules=p)["status"], "PLAN_READY")

    def test_no_live_claim_no_effective_values_and_no_mutation(self):
        p, data = policy(), catalog()
        original = copy.deepcopy((p, data))
        result = run(data, p)
        self.assertEqual((p, data), original)
        self.assertFalse(result["live_verified"])
        self.assertEqual(result["execution"], "not_performed")
        self.assertTrue(all(v is None for v in result["effective"].values()))
        self.assertEqual(result["catalog_evidence"]["trust"], "caller_declared_unverified")

    def test_strict_catalog_schema(self):
        for field, value in (("available", 1), ("version", [1, True]), ("version", []),
                             ("context_tokens", True), ("efforts", ["normal", "normal"]),
                             ("modes", {"fast": [0]}), ("modes", {"fast": [False, False]})):
            self.assertBlocked(run(catalog([model(**{field: value})])), "INVALID_INPUT")
        for change in ({"schema_version": True}, {"extra": "ignored?"}, {"source_ref": ""}):
            self.assertBlocked(run(catalog(**change)), "INVALID_INPUT")

    def test_strict_policy_schema(self):
        for change in ({"schema_version": True}, {"max_evidence_age_hours": 25}, {"providers": {}}):
            p = policy()
            p.update(change)
            self.assertBlocked(run(rules=p), "INVALID_INPUT")
        p = policy()
        del p["providers"]["example"]["tiers"]["D3"]["modes"]["fast"]
        self.assertBlocked(run(rules=p), "INVALID_INPUT")

    def test_cli_duplicate_key_nonfinite_and_missing_file_fail_closed(self):
        with tempfile.TemporaryDirectory() as root:
            p = Path(root) / "policy.json"
            c = Path(root) / "catalog.json"
            c.write_text("{}", encoding="utf-8")
            for content in ('{"x":1,"x":2}', '{"x":NaN}', '{"x":Infinity}', None):
                if content is None:
                    p.unlink()
                else:
                    p.write_text(content, encoding="utf-8")
                with redirect_stdout(io.StringIO()) as stream:
                    code = route.main(["--provider", "example", "--complexity", "2", "--uncertainty", "2",
                                       "--impact", "1", "--policy", str(p), "--catalog", str(c)])
                self.assertEqual(code, 2)
                self.assertEqual(json.loads(stream.getvalue())["reasons"][0]["code"], "INVALID_JSON_INPUT")

    def test_shipped_example_is_blocked_in_a_real_child_process(self):
        root = Path(__file__).resolve().parents[1]
        result = subprocess.run([sys.executable, "-B", str(root / "scripts" / "route.py"),
            "--provider", "example-provider", "--complexity", "2", "--uncertainty", "2", "--impact", "1",
            "--policy", str(root / "examples" / "policy.json"),
            "--catalog", str(root / "examples" / "catalog.fixture.json")], capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 2, result.stderr)
        response = json.loads(result.stdout)
        self.assertEqual(response["status"], "BLOCKED")
        self.assertEqual(response["execution"], "not_performed")


if __name__ == "__main__":
    unittest.main()
