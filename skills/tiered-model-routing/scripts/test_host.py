"""Host control metadata is not a subscription or billing receipt."""
import unittest
from datetime import timedelta

from test_route import NOW, catalog, model, policy, run


def sample():
    p = policy()
    config = p["providers"]["example"]
    config.update(access_mode="host_native", host_control_authorized=True)
    for rule in config["tiers"].values():
        rule["modes"] = {}
    data = catalog([model(billing="unknown", subscription_eligible=None, quota_status="unknown")],
        authenticated=False, source_kind="native_control_catalog",
        host_control=dict(name="example.spawn_agent", source_ref="SYNTHETIC host tool schema",
                          observed_at=NOW.isoformat(), supported=True))
    return p, data


class HostToolTests(unittest.TestCase):
    def test_explicit_host_route_preserves_unknown_billing_and_quota(self):
        p, data = sample()
        result = run(data, p)
        self.assertEqual(result["status"], "HOST_TOOL_READY")
        self.assertEqual(result["requested"]["billing"], "unknown")
        self.assertEqual(result["resolved"]["billing"], "unknown")
        self.assertEqual(result["resolved"]["quota_status"], "unknown")
        self.assertTrue(all(v is None for v in result["effective"].values()))
        self.assertFalse(result["live_verified"])

    def test_missing_opt_in_or_existing_host_authorization_blocks(self):
        p, data = sample()
        del p["providers"]["example"]["access_mode"]
        self.assertEqual(run(data, p)["status"], "BLOCKED")
        p, data = sample()
        p["providers"]["example"]["host_control_authorized"] = False
        self.assertEqual(run(data, p)["status"], "BLOCKED")

    def test_absent_or_unsupported_control_blocks(self):
        p, data = sample()
        del data["host_control"]
        self.assertEqual(run(data, p)["status"], "BLOCKED")
        p, data = sample()
        data["host_control"]["supported"] = False
        self.assertEqual(run(data, p)["status"], "BLOCKED")

    def test_stale_control_or_wrong_source_blocks(self):
        p, data = sample()
        data["host_control"]["observed_at"] = (NOW - timedelta(hours=25)).isoformat()
        self.assertEqual(run(data, p)["status"], "BLOCKED")
        for source in ("fixture", "native_catalog", "official_docs"):
            p, data = sample()
            data["source_kind"] = source
            self.assertEqual(run(data, p)["status"], "BLOCKED")

    def test_api_or_fabricated_subscription_claim_and_exhausted_quota_block(self):
        for change in ({"billing": "api"}, {"billing": "subscription", "subscription_eligible": True},
                       {"quota_status": "exhausted"}):
            p, data = sample()
            data["models"][0].update(change)
            self.assertEqual(run(data, p)["status"], "BLOCKED")

    def test_host_opt_in_does_not_enable_paid_fast(self):
        p, data = sample()
        p["providers"]["example"]["tiers"]["D3"]["modes"]["fast"] = True
        self.assertEqual(run(data, p)["status"], "BLOCKED")
        data["models"][0]["mode_billing"]["fast"] = "unknown"
        self.assertEqual(run(data, p)["status"], "BLOCKED")

    def test_unknown_cli_billing_cannot_silently_become_host_mode(self):
        data = catalog([model(billing="unknown", subscription_eligible=None, quota_status="unknown")])
        result = run(data)
        self.assertEqual(result["status"], "BLOCKED")
        self.assertEqual(result["requested"]["billing"], "subscription")


if __name__ == "__main__":
    unittest.main()
