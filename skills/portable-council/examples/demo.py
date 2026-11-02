"""Run two local synthetic seats. No real model, account, quota, or network access."""
import argparse
import copy
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import council

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--run-dir", required=True, help="New directory for synthetic receipts")
args = parser.parse_args()
requested = {"model": "synthetic-model", "effort": "synthetic-effort", "fast": False, "modes": {}}
seats = []
for name, scenario in (("demo-success", "success"), ("demo-failure", "auth")):
    seats.append({
        "provider": name, "enabled": True,
        "command": [sys.executable, "-B", str(Path(__file__).with_name("demo_adapter.py")), scenario],
        "timeout_seconds": 5, "max_attempts": 1, "requested": requested,
        "evidence": {"authenticated": True, "source_kind": "native_probe",
                     "source_ref": "SYNTHETIC DEMO FIXTURE: no authenticated account evidence",
                     "account_alias": "offline-fixture", "observed_at": council.stamp(),
                     "billing": "subscription", "subscription_eligible": True,
                     "settings": copy.deepcopy(requested), "quota": {"state": "available", "remaining": None}}
    })
result = council.ask({"schema_version": 1, "host": "demo-host", "seats": seats},
                     Path(__file__).parent, "A synthetic offline example.", args.run_dir)
print(json.dumps(result, ensure_ascii=False, indent=2))
sys.exit(0 if result["status"] == "partial" else 1)
