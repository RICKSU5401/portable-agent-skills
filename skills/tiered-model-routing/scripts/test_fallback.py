"""Synthetic subscription fallback tests, never native account evidence."""
import unittest
from datetime import timedelta

from test_route import NOW, catalog, model, policy, run


def sample():
    p = policy()
    p["providers"]["example"]["tiers"]["D4"]["fallback"] = {
        "family": "strong", "reasons": ["model_quota_exhausted", "model_not_offered"]}
    data = catalog([model("expert", quota_status="exhausted"), model("strong", [1, 9]), model("strong")])
    data["fallback_evidence"] = dict(scope="general_d4", from_family="expert", to_family="strong",
        primary_model_id=data["models"][0]["id"], fallback_model_id=data["models"][-1]["id"],
        reason="model_quota_exhausted", observed_at=NOW.isoformat(), source_kind="native_probe",
        source_ref="SYNTHETIC native denial fixture", account_alias="test-account", authenticated=True)
    return p, data


def d4(p, data):
    return run(data, p, factors=(3, 3, 3))


class FallbackTests(unittest.TestCase):
    def test_explicit_fallback_keeps_requested_family_and_d4(self):
        p, data = sample()
        result = d4(p, data)
        self.assertEqual(result["status"], "PLAN_READY")
        self.assertEqual(result["difficulty"]["tier"], "D4")
        self.assertEqual(result["requested"]["family"], "expert")
        self.assertEqual(result["resolved"]["family"], "strong")
        self.assertEqual(result["resolved"]["version"], [1, 10])
        self.assertEqual(result["fallback"]["state"], "planned_not_executed")
        self.assertTrue(all(v is None for v in result["effective"].values()))

    def test_missing_policy_authorization_or_evidence_blocks(self):
        p, data = sample()
        del p["providers"]["example"]["tiers"]["D4"]["fallback"]
        self.assertEqual(d4(p, data)["status"], "BLOCKED")
        p, data = sample()
        del data["fallback_evidence"]
        result = d4(p, data)
        self.assertEqual(result["status"], "BLOCKED")
        self.assertIsNone(result["fallback"])

    def test_missing_primary_requires_explicit_native_denial(self):
        p, data = sample()
        data["models"].pop(0)
        data["fallback_evidence"]["reason"] = "model_not_offered"
        self.assertEqual(d4(p, data)["status"], "PLAN_READY")

    def test_conflicting_primary_eligibility_blocks(self):
        p, data = sample()
        data["models"][0]["quota_status"] = "available"
        result = d4(p, data)
        self.assertEqual(result["status"], "BLOCKED")
        self.assertIsNone(result["fallback"])

    def test_timeout_refusal_and_unknown_billing_do_not_authorize_fallback(self):
        for reason in ("timeout", "network_error", "safety_refusal", "unknown_billing", "organization_policy"):
            p, data = sample()
            data["fallback_evidence"]["reason"] = reason
            self.assertEqual(d4(p, data)["status"], "BLOCKED")

    def test_fallback_evidence_matches_scope_account_families_and_authentication(self):
        for field, value in (("scope", "council"), ("account_alias", "another-account"),
                             ("from_family", "small"), ("to_family", "balanced"),
                             ("primary_model_id", "other-primary"), ("fallback_model_id", "older-fallback"),
                             ("authenticated", False), ("source_ref", ""), ("source_kind", "native_config")):
            p, data = sample()
            data["fallback_evidence"][field] = value
            self.assertEqual(d4(p, data)["status"], "BLOCKED")

    def test_quota_unknown_does_not_hide_conflicting_entitlement_evidence(self):
        p, data = sample()
        p["providers"]["example"]["tiers"]["D4"]["fallback"]["reasons"].append("subscription_not_entitled")
        data["models"][0]["quota_status"] = "unknown"
        data["fallback_evidence"]["reason"] = "subscription_not_entitled"
        self.assertEqual(d4(p, data)["status"], "BLOCKED")

    def test_fallback_evidence_expiry(self):
        for seconds in (-86401, 301):
            p, data = sample()
            data["fallback_evidence"]["observed_at"] = (NOW + timedelta(seconds=seconds)).isoformat()
            self.assertEqual(d4(p, data)["status"], "BLOCKED")

    def test_latest_fallback_paid_or_unknown_does_not_downgrade(self):
        for changes in ({"billing": "usage_credits"}, {"quota_status": "unknown"},
                        {"quota_status": "exhausted"}, {"efforts": ["minimal"]},
                        {"subscription_eligible": False}):
            p, data = sample()
            data["models"][-1].update(changes)
            result = d4(p, data)
            self.assertEqual(result["status"], "BLOCKED")
            self.assertEqual(result["resolved"]["version"], [1, 10])

    def test_ambiguous_primary_does_not_infer_unavailability(self):
        p, data = sample()
        data["models"].append(model("expert", [1, 10, 0], available=False))
        self.assertEqual(d4(p, data)["status"], "BLOCKED")

    def test_d3_cannot_inherit_d4_fallback(self):
        p, data = sample()
        result = run(data, p)
        self.assertEqual(result["status"], "PLAN_READY")
        self.assertIsNone(result["fallback"])
        p["providers"]["example"]["tiers"]["D3"]["fallback"] = {"family": "small", "reasons": ["model_not_offered"]}
        self.assertEqual(run(data, p)["status"], "BLOCKED")


if __name__ == "__main__":
    unittest.main()
