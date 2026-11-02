import copy
import ctypes
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))
import council
import native_json_bridge as bridge


def settings():
    return {"model": "synthetic-model", "effort": "synthetic-effort", "fast": False, "modes": {}}


def request():
    return {"protocol_version": 1, "job_id": "synthetic-job", "provider": "synthetic-peer",
            "question": "A question; not shell code", "requested": settings(), "billing": "subscription",
            "guard": "Consultation only", "timeout_seconds": 5}


def config():
    return {"schema_version": 1, "enabled": True, "reviewed_native_command": True,
            "provider": "synthetic-peer", "parser": "claude_result_json_v1", "settings": settings(),
            "command": [sys.executable, "synthetic_cli.py", "--print", "--model", "{model}",
                        "--effort", "{effort}", "--settings", "{settings_json}", "--output-format", "json"]}


def result(**changes):
    value = {"type": "result", "subtype": "success", "is_error": False,
             "result": "Synthetic final answer", "model": "synthetic-model"}
    value.update(changes)
    return value


class NativeBridgeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.env = patch.dict(os.environ, {"COUNCIL_ACTIVE": "1"})
        self.env.start()
        self.addCleanup(self.env.stop)

    def test_exact_settings_mapping_and_question_on_stdin(self):
        cfg, req = config(), request()
        cfg["settings"]["modes"] = req["requested"]["modes"] = {"ultracode": True}
        argv, text, timeout = bridge.prepare(cfg, req, self.base)
        self.assertEqual(argv[argv.index("--model") + 1], "synthetic-model")
        self.assertEqual(argv[argv.index("--effort") + 1], "synthetic-effort")
        self.assertEqual(json.loads(argv[argv.index("--settings") + 1]), {"fastMode": False, "ultracode": True})
        self.assertEqual(text, "Consultation only\n\nA question; not shell code")
        self.assertNotIn(req["question"], argv)
        self.assertEqual(timeout, 5)

    def test_disabled_unreviewed_or_outside_council_cannot_launch(self):
        for key in ("enabled", "reviewed_native_command"):
            cfg = config(); cfg[key] = False
            with self.assertRaises(ValueError): bridge.prepare(cfg, request(), self.base)
        with patch.dict(os.environ, {"COUNCIL_ACTIVE": "0"}), self.assertRaises(ValueError):
            bridge.prepare(config(), request(), self.base)

    def test_binding_identity_billing_and_timeout_required(self):
        for key, value in (("provider", "wrong"), ("billing", "api"), ("timeout_seconds", None),
                           ("protocol_version", True), ("job_id", "")):
            req = request(); req[key] = value
            with self.subTest(key=key), self.assertRaises(ValueError): bridge.prepare(config(), req, self.base)
        req = request(); req["requested"]["model"] = "different"
        with self.assertRaises(ValueError): bridge.prepare(config(), req, self.base)

    def test_mode_or_fast_contradictions_rejected(self):
        for modes in ({"fastMode": True}, {"fast_mode": False}, {"ultracode": "yes"}, {"env": {}}):
            cfg, req = config(), request()
            cfg["settings"]["modes"] = req["requested"]["modes"] = modes
            with self.subTest(modes=modes), self.assertRaises(ValueError): bridge.prepare(cfg, req, self.base)
        cfg, req = config(), request()
        cfg["settings"]["fast"] = req["requested"]["fast"] = True
        with self.assertRaises(ValueError): bridge.prepare(cfg, req, self.base)

    def test_command_overrides_and_permission_bypass_rejected(self):
        extras = [["--model", "different"], ["--model=different"], ["-m", "different"],
                  ["--effort=other"], ["--settings", "{}"], ["--fast"], ["--fast-mode=true"],
                  ["--api-key", "synthetic"], ["--dangerously-skip-permissions"], ["--"],
                  ["--bare"], ["--fallback-model", "other"], ["--environment", "other"],
                  ["--cloud", "other"], ["--bg"], ["--resume", "other"],
                  ["--permission-mode=bypassPermissions"],
                  ["--output-format=text"], ["--print=injected"]]
        for extra in extras:
            cfg = config(); cfg["command"] += extra
            with self.subTest(extra=extra), self.assertRaises(ValueError): bridge.prepare(cfg, request(), self.base)

    def test_native_terminal_success_preserves_unknown_effective_fields(self):
        answer, effective = bridge.parse_native(json.dumps(result()), 0, settings())
        self.assertEqual(answer, "Synthetic final answer")
        self.assertEqual(effective, {"model": "synthetic-model", "effort": None, "fast": None, "modes": None, "billing": None})

    def test_last_terminal_result_after_exit_wins(self):
        stream = "\n".join(json.dumps(event) for event in [
            {"type": "system", "subtype": "init", "model": "synthetic-model"}, result(result="intermediate"), result()])
        self.assertEqual(bridge.parse_native(stream, 0, settings())[0], "Synthetic final answer")
        with self.assertRaises(ValueError): bridge.parse_native(stream + "\n" + json.dumps(result(is_error=True)), 0, settings())

    def test_incomplete_nonzero_empty_or_ambiguous_native_result_rejected(self):
        variants = [result(is_error=True), result(stop_reason="max_tokens"), result(result=""),
                    result(subtype="error"), {"type": "assistant", "text": "fragment"}]
        for value in variants:
            with self.subTest(value=value), self.assertRaises(ValueError): bridge.parse_native(json.dumps(value), 0, settings())
        with self.assertRaises(ValueError): bridge.parse_native(json.dumps(result()), 1, settings())
        for raw in ('{"type":"result","type":"result"}', '{"value":NaN}', '{"value":1e999}'):
            with self.assertRaises(ValueError): bridge.parse_native(raw, 0, settings())

    def test_native_model_billing_and_overage_contradictions_rejected(self):
        variants = [result(model="different"), result(modelUsage={"different": {}}),
                    result(billing="api"), result(rate_limit_info={"isUsingOverage": True}),
                    result(rate_limit_info={"isUsingOverage": 0})]
        for value in variants:
            with self.subTest(value=value), self.assertRaises(ValueError): bridge.parse_native(json.dumps(value), 0, settings())

    def test_native_process_keeps_outer_ownership_and_no_api_env(self):
        completed = subprocess.CompletedProcess([], 0, json.dumps(result()), "")
        with patch.dict(os.environ, {"ANTHROPIC_API_KEY": "synthetic"}), patch.object(bridge, "run_process", return_value=completed) as run:
            response = bridge.bridge(config(), request(), self.base)
        self.assertTrue(run.call_args.kwargs["inherit_process_group"])
        self.assertNotIn("ANTHROPIC_API_KEY", run.call_args.kwargs["env"])
        self.assertEqual(response["status"], "completed")

    def test_full_bridge_subprocess_with_synthetic_native_cli(self):
        fake = self.base / "synthetic_cli.py"
        fake.write_text('import json,sys\ntext=sys.stdin.read()\nprint(json.dumps(' + repr(result()) + '))\n', encoding="utf-8")
        cfg = config(); cfg["command"][1] = str(fake)
        path = self.base / "bridge.json"; path.write_text(json.dumps(cfg), encoding="utf-8")
        proc = subprocess.run([sys.executable, "-B", str(SCRIPTS / "native_json_bridge.py"), "--config", str(path)],
                              input=json.dumps(request()), text=True, capture_output=True, timeout=10, cwd=self.base)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(json.loads(proc.stdout)["answer"], "Synthetic final answer")

    def test_failed_response_effective_contradictions_never_retry(self):
        for effective in ({"billing": "api"}, {"model": "different"}, {"fast": True}, {"modes": {"ultracode": True}}):
            response = {"protocol_version": 1, "job_id": "job", "provider": "demo", "terminal": True,
                        "status": "failed", "error_kind": "transient", "inference_started": False, "effective": effective}
            outcome = council.parse_response(json.dumps(response), 1, settings(), "job", "demo")
            self.assertEqual(outcome["status"], "failed")
            self.assertFalse(outcome["retryable"])

    def test_cleanup_incomplete_blocks_adapter_retry_and_success(self):
        response = {"protocol_version": 1, "job_id": "job", "provider": "demo", "terminal": True,
                    "status": "failed", "error_kind": "transient", "inference_started": False,
                    "cleanup_incomplete": True}
        outcome = council.parse_response(json.dumps(response), 1, settings(), "job", "demo")
        self.assertFalse(outcome["retryable"])
        self.assertTrue(outcome["cleanup_incomplete"])
        response.update(status="completed", answer="untrusted completion")
        self.assertEqual(council.parse_response(json.dumps(response), 0, settings(), "job", "demo")["status"], "failed")

    def test_outer_cancellation_terminates_real_bridge_native_descendant(self):
        marker = self.base / "owned-descendant.pid"
        descendant = self.base / "owned_descendant.py"
        descendant.write_text("import os,time,pathlib\n" +
                              f"pathlib.Path({str(marker)!r}).write_text(str(os.getpid()))\n" +
                              "time.sleep(20)\n", encoding="utf-8")
        native = self.base / "synthetic_cli.py"
        native.write_text("import subprocess,sys,time\nsys.stdin.read()\n" +
                          f"subprocess.Popen([sys.executable,'-B',{str(descendant)!r}])\n" +
                          "time.sleep(20)\n", encoding="utf-8")
        cfg = config(); cfg["command"][1] = str(native)
        path = self.base / "bridge.json"; path.write_text(json.dumps(cfg), encoding="utf-8")
        seat = {"provider": "synthetic-peer", "enabled": True, "requested": settings(),
                "command": [sys.executable, "-B", str(SCRIPTS / "native_json_bridge.py"), "--config", str(path)],
                "timeout_seconds": 15, "max_attempts": 1,
                "evidence": {"source_kind": "native_probe", "source_ref": "SYNTHETIC_OFFLINE_TEST",
                             "account_alias": "synthetic-account", "authenticated": True,
                             "observed_at": datetime.now(timezone.utc).isoformat(),
                             "billing": "subscription", "subscription_eligible": True,
                             "settings": settings(), "quota": {"state": "available", "remaining": None}}}
        cancelled = threading.Event()
        observed = []

        def cancel_after_ready():
            deadline = time.monotonic() + 10
            try:
                while time.monotonic() < deadline:
                    try:
                        observed.append(int(marker.read_text()))
                        break
                    except (OSError, ValueError):
                        time.sleep(0.02)
            finally:
                cancelled.set()

        watcher = threading.Thread(target=cancel_after_ready)
        watcher.start()
        try:
            with patch.dict(os.environ, {"COUNCIL_ACTIVE": "0"}):
                receipt = council.ask({"host": "coordinator", "seats": [seat]}, self.base,
                                      "Synthetic local cancellation test", self.base / "run", cancelled=cancelled)
        finally:
            cancelled.set()
            watcher.join(timeout=11)
        self.assertTrue(observed, "descendant never reached readiness; no cancellation claim")
        self.assertFalse(watcher.is_alive())
        self.assertEqual(receipt["seats"][0]["status"], "cancelled")
        self.assertFalse(receipt["seats"][0].get("cleanup_incomplete", False))
        pid = observed[0]
        if os.name == "nt":
            kernel = ctypes.WinDLL("kernel32", use_last_error=True)
            kernel.OpenProcess.argtypes = [ctypes.c_ulong, ctypes.c_int, ctypes.c_ulong]
            kernel.OpenProcess.restype = ctypes.c_void_p
            kernel.WaitForSingleObject.argtypes = [ctypes.c_void_p, ctypes.c_ulong]
            kernel.WaitForSingleObject.restype = ctypes.c_ulong
            kernel.CloseHandle.argtypes = [ctypes.c_void_p]
            handle = kernel.OpenProcess(0x00100000, False, pid)
            if handle:
                try:
                    self.assertEqual(kernel.WaitForSingleObject(handle, 1000), 0, "owned descendant still running")
                finally:
                    kernel.CloseHandle(handle)
        else:
            # A killed orphan can remain a zombie until the host reaps it.
            probe = subprocess.run(["ps", "-p", str(pid), "-o", "stat="],
                                   text=True, capture_output=True, timeout=2, check=False)
            state = probe.stdout.strip()
            self.assertTrue(not state or state.startswith("Z"), "owned descendant still running: " + state)
        self.assertGreater(os.getpid(), 0, "outer test caller survived group cleanup")


if __name__ == "__main__":
    unittest.main()
