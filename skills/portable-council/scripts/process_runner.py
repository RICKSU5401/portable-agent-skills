"""Bounded CLI process execution, including Windows pipe writes.

Popen.communicate(input=..., timeout=...) writes stdin synchronously on Windows.
Keep all pipe I/O off the thread which owns the deadline and cancellation check.
"""
from __future__ import annotations

import math
import os
import signal
import subprocess
import threading
import time


MAX_OUTPUT_BYTES = 8 * 1024 * 1024  # Per stream; overflow is an explicit failure.
POLL_SECONDS = 0.02
CLEANUP_SECONDS = 1.0
CREATE_SUSPENDED = 0x00000004  # Win32; not exported by subprocess on all versions.


class ProcessRunError(RuntimeError):
    def __init__(self, message: str, *, kind: str, stdout: str = "", stderr: str = "",
                 cleanup_incomplete: bool = False):
        super().__init__(message)
        self.kind = kind
        self.stdout = stdout
        self.stderr = stderr
        self.cleanup_incomplete = cleanup_incomplete


def _stop_tree(proc: subprocess.Popen | None, job, *, kill_group: bool = True) -> None:
    errors = []
    if job is not None:
        try:
            job.close()
        except OSError as exc:
            errors.append(str(exc))
    if proc is None:
        if errors:
            raise OSError("; ".join(errors))
        return
    if os.name != "nt" and kill_group:
        # The group can outlive its leader, including after a normal leader exit.
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        except OSError as exc:
            errors.append(str(exc))
    if proc.poll() is None:
        try:
            proc.kill()
        except ProcessLookupError:
            pass
        except OSError as exc:
            errors.append(str(exc))
    if errors:
        raise OSError("; ".join(errors))


def run_process(command: list[str], *, cwd, env, input_text: str | None,
                timeout: float, cancelled: threading.Event,
                label: str, inherit_process_group: bool = False) -> subprocess.CompletedProcess[str]:
    """Return captured output, or raise with bounded timeout/cancellation cleanup.

    stderr/stdout remain available on ProcessRunError. No data is logged here.
    The caller owns interpretation of exit codes and provider response formats.
    A bridge nested inside this runner may retain its parent's POSIX process
    group. It then kills only its direct child; the outer runner owns descendant
    cleanup. Never use this option without an outer lifetime supervisor.
    """
    if not isinstance(timeout, (int, float)) or not math.isfinite(timeout) or timeout <= 0:
        raise ValueError("timeout must be a finite positive number")
    if cancelled.is_set():
        raise ProcessRunError(f"{label} cancelled", kind="cancelled")
    payload = (input_text or "").encode("utf-8")
    deadline = time.monotonic() + timeout
    proc = None
    job = None
    threads: list[threading.Thread] = []
    chunks: dict[str, list[bytes]] = {"stdout": [], "stderr": []}
    sizes = {"stdout": 0, "stderr": 0}
    io_lock = threading.Lock()
    io_errors: list[str] = []
    io_failed = threading.Event()
    failure: tuple[str, str] | None = None
    cleanup_incomplete = False

    def fail_io(message: str) -> None:
        with io_lock:
            io_errors.append(message)
        io_failed.set()

    def read_pipe(name: str, pipe) -> None:
        try:
            while True:
                data = os.read(pipe.fileno(), 65536)
                if not data:
                    break
                with io_lock:
                    room = MAX_OUTPUT_BYTES - sizes[name]
                    if room > 0:
                        chunks[name].append(data[:room])
                        sizes[name] += min(room, len(data))
                if len(data) > room:
                    fail_io(f"{name} exceeded {MAX_OUTPUT_BYTES} bytes")
                    break
        except OSError as exc:
            fail_io(f"reading {name}: {exc}")
        finally:
            pipe.close()

    def write_pipe(pipe) -> None:
        try:
            view = memoryview(payload)
            while view:
                written = os.write(pipe.fileno(), view[:65536])
                if written <= 0:
                    raise OSError("stdin write made no progress")
                view = view[written:]
        except BrokenPipeError:
            # A child may intentionally stop reading early; its exit/output wins.
            pass
        except OSError as exc:
            fail_io(f"writing stdin: {exc}")
        finally:
            pipe.close()

    try:
        if os.name == "nt":
            from process_tree import WindowsJob
            job = WindowsJob()
        proc = subprocess.Popen(command, cwd=cwd, env=env, stdin=subprocess.PIPE,
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                bufsize=0, start_new_session=(os.name != "nt" and not inherit_process_group),
                                creationflags=(subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_NO_WINDOW | CREATE_SUSPENDED) if os.name == "nt" else 0)
        if job is not None:
            job.attach(proc)
            if cancelled.is_set():
                raise ProcessRunError(f"{label} cancelled before resume", kind="cancelled")
            if time.monotonic() >= deadline:
                raise ProcessRunError(f"{label} timed out before resume", kind="timeout")
            job.resume(proc)
        for name, target, args in (
            ("stdout", read_pipe, ("stdout", proc.stdout)),
            ("stderr", read_pipe, ("stderr", proc.stderr)),
            ("stdin", write_pipe, (proc.stdin,)),
        ):
            thread = threading.Thread(target=target, args=args,
                                      name=f"council-io-{proc.pid}-{name}", daemon=True)
            thread.start()
            threads.append(thread)
        while True:
            if cancelled.is_set():
                failure = ("cancelled", f"{label} cancelled")
                break
            if io_failed.is_set():
                with io_lock:
                    detail = "; ".join(io_errors)
                failure = ("io", f"{label} I/O failed: {detail}")
                break
            # A descendant can inherit pipes after the CLI leader has exited.
            # Cleanup closes the owned tree, then drains the buffered output.
            if proc.poll() is not None:
                break
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                failure = ("timeout", f"{label} timed out after {timeout:g}s")
                break
            cancelled.wait(min(POLL_SECONDS, remaining))
    except ProcessRunError as exc:
        failure = (exc.kind, str(exc))
    except OSError as exc:
        failure = ("io", f"{label} could not run: {exc}")
    finally:
        # Never call communicate or close a pipe owned by a blocked I/O thread.
        # Killing the owned tree releases its pipe endpoints and wakes the threads.
        cleanup_deadline = time.monotonic() + CLEANUP_SECONDS
        try:
            _stop_tree(proc, job, kill_group=not inherit_process_group)
        except OSError as exc:
            cleanup_incomplete = True
            if failure is None:
                failure = ("io", f"{label} process cleanup failed: {exc}")
            else:
                failure = (failure[0], f"{failure[1]}; process cleanup failed: {exc}")
        if proc is not None:
            try:
                proc.wait(timeout=max(0.001, cleanup_deadline - time.monotonic()))
            except subprocess.TimeoutExpired:
                cleanup_incomplete = True
                if failure is None:
                    failure = ("io", f"{label} process did not stop during cleanup")
                else:
                    failure = (failure[0], f"{failure[1]}; process did not stop during cleanup")
            for thread in threads:
                thread.join(timeout=max(0, cleanup_deadline - time.monotonic()))
            if any(thread.is_alive() for thread in threads):
                cleanup_incomplete = True
                if failure is None:
                    failure = ("io", f"{label} subprocess pipes did not close during cleanup")
                else:
                    failure = (failure[0], f"{failure[1]}; subprocess pipes did not close during cleanup")
            # An attach/start failure may occur before threads own the pipes.
            if not threads:
                for pipe in (proc.stdin, proc.stdout, proc.stderr):
                    pipe.close()
    with io_lock:
        if failure is None and io_errors:
            failure = ("io", f"{label} I/O failed: {'; '.join(io_errors)}")
        # Match the universal-newline behavior of the previous text=True runner.
        stdout = b"".join(chunks["stdout"]).decode("utf-8", errors="replace").replace("\r\n", "\n").replace("\r", "\n")
        stderr = b"".join(chunks["stderr"]).decode("utf-8", errors="replace").replace("\r\n", "\n").replace("\r", "\n")
    if failure is not None:
        kind, message = failure
        raise ProcessRunError(message, kind=kind, stdout=stdout, stderr=stderr,
                              cleanup_incomplete=cleanup_incomplete)
    return subprocess.CompletedProcess(command, proc.returncode, stdout, stderr)
