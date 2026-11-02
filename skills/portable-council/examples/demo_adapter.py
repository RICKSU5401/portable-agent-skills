"""Offline protocol example. Never calls an AI service or reads credentials."""
import json
import os
import sys
import time

request = json.load(sys.stdin)
scenario = sys.argv[1] if len(sys.argv) > 1 else "success"
response = {"protocol_version": 1, "job_id": request["job_id"],
            "provider": request["provider"], "terminal": True}
if scenario == "wait":
    time.sleep(20)
if scenario == "transient" and request["attempt"] == 1:
    response.update(status="failed", error_kind="transient", inference_started=False)
elif scenario in ("auth", "quota", "unknown"):
    response.update(status="failed", error_kind=scenario, inference_started=False)
elif scenario == "incomplete":
    response.update(status="completed", terminal=False, answer="fragment")
else:
    effective = None
    if scenario == "mismatch":
        effective = {"model": "different-synthetic-model"}
    response.update(status="completed", answer="OFFLINE DEMO: " + request["question"], effective=effective)
    if scenario == "environment":
        response["answer"] = json.dumps({"guard": os.getenv("COUNCIL_ACTIVE"),
                                         "secret_present": bool(os.getenv("EXAMPLE_API_KEY"))})
print(json.dumps(response, ensure_ascii=False))
