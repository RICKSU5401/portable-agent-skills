"""Local subprocess regressions; no provider inference or network access."""
from __future__ import annotations

import ctypes
import os
from pathlib import Path
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
from unittest.mock import Mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import process_runner
from process_runner import ProcessRunError, run_process


class ProcessRunnerTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory(prefix="runner-test-")
        self.addCleanup(self.folder.cleanup)
        self.cancelled = threading.Event()

    def run_child(self, source, *, payload=None, timeout=3):
        return run_process([sys.executable, "-B", "-u", "-c", source],
                           cwd=self.folder.name, env=os.environ.copy(), input_text=payload,
                           timeout=timeout, cancelled=self.cancelled, label="test child")

    def assert_no_io_threads(self):
        self.assertFalse([t.name for t in threading.enumerate()
                          if t.name.startswith("council-io-")])

    def test_large_input_never_read_obeys_timeout(self):
        started = time.monotonic()
        with self.assertRaises(ProcessRunError) as got:
            self.run_child("import time; print('started',flush=True); time.sleep(20)",
                           payload="x" * 1_000_000, timeout=0.4)
        self.assertEqual(got.exception.kind, "timeout")
        self.assertLess(time.monotonic() - started, 1.8)
        self.assertFalse(got.exception.cleanup_incomplete)
        self.assertIn("started", got.exception.stdout)
        self.assert_no_io_threads()

    def test_cancel_while_stdin_blocked(self):
        timer = threading.Timer(0.3, self.cancelled.set)
        timer.start()
        self.addCleanup(timer.join)
        started = time.monotonic()
        with self.assertRaises(ProcessRunError) as got:
            self.run_child("import time; time.sleep(20)", payload="x" * 1_000_000, timeout=10)
        self.assertEqual(got.exception.kind, "cancelled")
        self.assertLess(time.monotonic() - started, 1.8)
        self.assert_no_io_threads()

    def test_drains_both_streams_while_writing_input(self):
        result = self.run_child(
            "import sys; sys.stdout.write('o'*200000); sys.stdout.flush(); "
            "sys.stderr.write('e'*200000); sys.stderr.flush(); "
            "data=sys.stdin.buffer.read(); print(len(data))",
            payload="x" * 500_000)
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stdout, "o" * 200000 + "500000\n")
        self.assertEqual(result.stderr, "e" * 200000)
        self.assert_no_io_threads()

    def test_nonzero_exit_preserves_diagnostic(self):
        result = self.run_child("import sys; print('failure',file=sys.stderr); sys.exit(7)")
        self.assertEqual(result.returncode, 7)
        self.assertEqual(result.stderr, "failure\n")
        self.assert_no_io_threads()

    def test_successful_leader_exit_drains_inherited_pipes(self):
        started = time.monotonic()
        result = self.run_child(
            "import subprocess,sys; "
            "subprocess.Popen([sys.executable,'-B','-c','import time;time.sleep(20)']); "
            "sys.stdout.write('o'*200000); sys.stdout.flush(); "
            "sys.stderr.write('e'*200000); sys.stderr.flush()",
            timeout=3)
        self.assertLess(time.monotonic() - started, 1.8)
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stdout, "o" * 200000)
        self.assertEqual(result.stderr, "e" * 200000)
        self.assert_no_io_threads()

    def test_nonzero_leader_exit_does_not_wait_for_inherited_pipes(self):
        started = time.monotonic()
        result = self.run_child(
            "import subprocess,sys; "
            "subprocess.Popen([sys.executable,'-B','-c','import time;time.sleep(20)']); "
            "print('failure',file=sys.stderr,flush=True); sys.exit(7)",
            timeout=3)
        self.assertLess(time.monotonic() - started, 1.8)
        self.assertEqual(result.returncode, 7)
        self.assertEqual(result.stderr, "failure\n")
        self.assert_no_io_threads()

    def test_empty_input_closes_stdin(self):
        result = self.run_child("import sys; print(repr(sys.stdin.buffer.read()))")
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stdout, "b''\n")
        self.assert_no_io_threads()

    def test_early_exit_can_stop_reading_large_input(self):
        result = self.run_child("print('finished')", payload="x" * 1_000_000)
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stdout, "finished\n")
        self.assert_no_io_threads()

    def test_utf8_round_trip(self):
        text = "跨模型回應測試 🧪\n" * 12000
        result = self.run_child(
            "import sys; data=sys.stdin.buffer.read(); sys.stdout.buffer.write(data)",
            payload=text)
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stdout, text)
        self.assert_no_io_threads()

    def test_spawn_failure_is_explicit_io_error(self):
        with self.assertRaises(ProcessRunError) as got:
            run_process([str(Path(self.folder.name) / "missing-program")],
                        cwd=self.folder.name, env=os.environ.copy(), input_text=None,
                        timeout=1, cancelled=self.cancelled, label="test child")
        self.assertEqual(got.exception.kind, "io")
        self.assertIn("could not run", str(got.exception))
        self.assert_no_io_threads()

    def test_timeout_preserves_stderr(self):
        with self.assertRaises(ProcessRunError) as got:
            self.run_child("import sys,time; print('waiting for login',file=sys.stderr,flush=True); time.sleep(20)",
                           timeout=0.4)
        self.assertEqual(got.exception.kind, "timeout")
        self.assertIn("waiting for login", got.exception.stderr)
        self.assert_no_io_threads()

    def test_overflow_is_explicit(self):
        with patch.object(process_runner, "MAX_OUTPUT_BYTES", 1024):
            with self.assertRaises(ProcessRunError) as got:
                self.run_child("print('x'*20000)")
        self.assertEqual(got.exception.kind, "io")
        self.assertIn("exceeded 1024 bytes", str(got.exception))
        self.assertEqual(len(got.exception.stdout.encode("utf-8")), 1024)
        self.assert_no_io_threads()

    def test_pre_cancel_does_not_spawn(self):
        self.cancelled.set()
        with patch.object(process_runner.subprocess, "Popen") as popen:
            with self.assertRaises(ProcessRunError) as got:
                self.run_child("raise RuntimeError('must not run')")
        popen.assert_not_called()
        self.assertEqual(got.exception.kind, "cancelled")

    def test_inherited_group_cleanup_never_kills_caller_group(self):
        child = Mock()
        child.poll.return_value = None
        with patch.object(process_runner.os, "name", "posix"), \
                patch.object(process_runner.os, "killpg", create=True) as kill_group:
            process_runner._stop_tree(child, None, kill_group=False)
        kill_group.assert_not_called()
        child.kill.assert_called_once()

    @unittest.skipIf(os.name == "nt", "POSIX nested bridge process group")
    def test_nested_child_keeps_outer_process_group(self):
        result = run_process([sys.executable, "-B", "-c", "import os;print(os.getpgrp())"],
                             cwd=self.folder.name, env=os.environ.copy(), input_text=None,
                             timeout=3, cancelled=self.cancelled, label="nested child",
                             inherit_process_group=True)
        self.assertEqual(int(result.stdout.strip()), os.getpgrp())
        self.assert_no_io_threads()

    def test_timeout_keeps_cleanup_failure_diagnostic(self):
        original = process_runner._stop_tree

        def stop_then_report(*args, **kwargs):
            original(*args, **kwargs)
            raise OSError("synthetic cleanup diagnostic")

        with patch.object(process_runner, "_stop_tree", stop_then_report):
            with self.assertRaises(ProcessRunError) as got:
                self.run_child("import time;time.sleep(3)", timeout=0.2)
        self.assertEqual(got.exception.kind, "timeout")
        self.assertIn("process cleanup failed", str(got.exception))
        self.assertIn("synthetic cleanup diagnostic", str(got.exception))
        self.assertTrue(got.exception.cleanup_incomplete)
        self.assert_no_io_threads()

    def test_process_wait_cleanup_failure_is_structured(self):
        original = process_runner.subprocess.Popen.wait

        def wait_then_report(child, timeout=None):
            original(child, timeout=timeout)
            raise process_runner.subprocess.TimeoutExpired(child.args, timeout)

        with patch.object(process_runner.subprocess.Popen, "wait", wait_then_report):
            with self.assertRaises(ProcessRunError) as got:
                self.run_child("print('complete')")
        self.assertEqual(got.exception.kind, "io")
        self.assertTrue(got.exception.cleanup_incomplete)
        self.assertIn("did not stop during cleanup", str(got.exception))
        self.assert_no_io_threads()

    def test_pipe_cleanup_failure_is_structured(self):
        with patch.object(threading.Thread, "is_alive", return_value=True):
            with self.assertRaises(ProcessRunError) as got:
                self.run_child("print('complete')")
        self.assertEqual(got.exception.kind, "io")
        self.assertTrue(got.exception.cleanup_incomplete)
        self.assertIn("pipes did not close", str(got.exception))
        self.assert_no_io_threads()

    @unittest.skipUnless(os.name == "nt", "Windows suspended launch regression")
    def test_delayed_job_attach_cannot_run_early_descendant(self):
        from process_tree import WindowsJob
        marker = Path(self.folder.name) / "early-child.pid"
        original_attach = WindowsJob.attach

        def delayed_attach(job, proc):
            time.sleep(0.25)  # Simulate scheduling delay in the former race window.
            self.assertFalse(marker.exists(), "child executed before job ownership")
            original_attach(job, proc)

        source = (
            "import subprocess,sys,time,pathlib; "
            "p=subprocess.Popen([sys.executable,'-B','-c','import time;time.sleep(3)'],"
            "stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL); "
            f"pathlib.Path({str(marker)!r}).write_text(str(p.pid)); time.sleep(3)"
        )
        with patch.object(WindowsJob, "attach", delayed_attach):
            with self.assertRaises(ProcessRunError) as got:
                self.run_child(source, timeout=1.0)
        self.assertEqual(got.exception.kind, "timeout")
        pid = int(marker.read_text())
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.OpenProcess.argtypes = [ctypes.c_ulong, ctypes.c_int, ctypes.c_ulong]
        kernel.OpenProcess.restype = ctypes.c_void_p
        kernel.GetExitCodeProcess.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_ulong)]
        kernel.CloseHandle.argtypes = [ctypes.c_void_p]
        handle = kernel.OpenProcess(0x1000, False, pid)
        if handle:
            try:
                code = ctypes.c_ulong()
                self.assertTrue(kernel.GetExitCodeProcess(handle, ctypes.byref(code)))
                self.assertNotEqual(code.value, 259, "descendant survived job cleanup")
            finally:
                kernel.CloseHandle(handle)
        self.assert_no_io_threads()
        # A non-STILL_ACTIVE exit code alone does not establish that Windows
        # released the descendant's cwd handle. No grace sleep/retry here.
        folder = Path(self.folder.name)
        moved = folder.with_name(folder.name + "-released")
        folder.rename(moved)
        moved.rename(folder)

    @unittest.skipUnless(os.name == "nt", "Windows suspended launch regression")
    def test_attach_failure_stops_suspended_process_without_execution(self):
        from process_tree import WindowsJob
        marker = Path(self.folder.name) / "must-not-execute"
        children = []

        def fail_attach(job, proc):
            children.append(proc)
            raise OSError("synthetic assignment failure")

        with patch.object(WindowsJob, "attach", fail_attach):
            with self.assertRaises(ProcessRunError) as got:
                self.run_child(f"import pathlib; pathlib.Path({str(marker)!r}).touch()")
        self.assertEqual(got.exception.kind, "io")
        self.assertFalse(marker.exists())
        self.assertIsNotNone(children[0].poll())
        self.assert_no_io_threads()

    @unittest.skipUnless(os.name == "nt", "Windows suspended launch regression")
    def test_cancel_before_resume_stops_suspended_job(self):
        from process_tree import WindowsJob
        original_attach = WindowsJob.attach
        children = []
        marker = Path(self.folder.name) / "must-not-resume"

        def attach_and_cancel(job, proc):
            children.append(proc)
            original_attach(job, proc)
            self.cancelled.set()

        with patch.object(WindowsJob, "attach", attach_and_cancel), patch.object(WindowsJob, "resume") as resume:
            with self.assertRaises(ProcessRunError) as got:
                self.run_child(f"import pathlib; pathlib.Path({str(marker)!r}).touch()")
        self.assertEqual(got.exception.kind, "cancelled")
        resume.assert_not_called()
        self.assertFalse(marker.exists())
        self.assertIsNotNone(children[0].poll())
        self.assert_no_io_threads()

    @unittest.skipUnless(os.name == "nt", "Windows suspended launch regression")
    def test_deadline_before_resume_stops_suspended_job(self):
        from process_tree import WindowsJob
        original_attach = WindowsJob.attach
        children = []
        marker = Path(self.folder.name) / "deadline-must-not-resume"

        def delayed_attach(job, proc):
            children.append(proc)
            original_attach(job, proc)
            time.sleep(0.2)

        with patch.object(WindowsJob, "attach", delayed_attach), patch.object(WindowsJob, "resume") as resume:
            with self.assertRaises(ProcessRunError) as got:
                self.run_child(f"import pathlib; pathlib.Path({str(marker)!r}).touch()", timeout=0.1)
        self.assertEqual(got.exception.kind, "timeout")
        resume.assert_not_called()
        self.assertFalse(marker.exists())
        self.assertIsNotNone(children[0].poll())
        self.assert_no_io_threads()

    def test_timeout_terminates_descendant(self):
        pid_path = Path(self.folder.name) / "child.pid"
        source = (
            "import subprocess,sys,time,pathlib; "
            "p=subprocess.Popen([sys.executable,'-B','-c','import time;time.sleep(20)']); "
            f"pathlib.Path({str(pid_path)!r}).write_text(str(p.pid)); "
            "time.sleep(20)"
        )
        with self.assertRaises(ProcessRunError):
            self.run_child(source, timeout=0.6)
        pid = int(pid_path.read_text())
        if os.name == "nt":
            kernel = ctypes.WinDLL("kernel32", use_last_error=True)
            kernel.OpenProcess.argtypes = [ctypes.c_ulong, ctypes.c_int, ctypes.c_ulong]
            kernel.OpenProcess.restype = ctypes.c_void_p
            kernel.GetExitCodeProcess.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_ulong)]
            kernel.CloseHandle.argtypes = [ctypes.c_void_p]
            handle = kernel.OpenProcess(0x1000, False, pid)
            if handle:
                code = ctypes.c_ulong()
                try:
                    self.assertTrue(kernel.GetExitCodeProcess(handle, ctypes.byref(code)))
                    self.assertNotEqual(code.value, 259, "descendant remains active")
                finally:
                    kernel.CloseHandle(handle)
        else:
            try:
                os.kill(pid, 0)
            except ProcessLookupError:
                pass
            else:
                # A killed orphan can await reaping in a minimal Linux container.
                stat = Path(f"/proc/{pid}/stat")
                self.assertTrue(stat.exists() and stat.read_text().split(")", 1)[1].split()[0] == "Z",
                                "descendant remains active")
        self.assert_no_io_threads()


if __name__ == "__main__":
    unittest.main(verbosity=2)
