"""Own a Windows child process tree using a kill-on-close Job Object."""
from __future__ import annotations

import ctypes
from ctypes import wintypes
import time


class WindowsJob:
    def __init__(self):
        self.attached_pid = None
        self.resumed = False
        # Windows uses 32-bit DWORD even on 64-bit Python.
        class BasicLimits(ctypes.Structure):
            _fields_ = [("process_time", ctypes.c_int64), ("job_time", ctypes.c_int64),
                        ("flags", wintypes.DWORD), ("min_working_set", ctypes.c_size_t),
                        ("max_working_set", ctypes.c_size_t), ("active_process_limit", wintypes.DWORD),
                        ("affinity", ctypes.c_size_t), ("priority", wintypes.DWORD), ("scheduling", wintypes.DWORD)]
        class IoCounters(ctypes.Structure):
            _fields_ = [(name, ctypes.c_uint64) for name in ("reads", "writes", "other", "read_bytes", "write_bytes", "other_bytes")]
        class ExtendedLimits(ctypes.Structure):
            _fields_ = [("basic", BasicLimits), ("io", IoCounters),
                        ("process_memory", ctypes.c_size_t), ("job_memory", ctypes.c_size_t),
                        ("peak_process_memory", ctypes.c_size_t), ("peak_job_memory", ctypes.c_size_t)]
        self.kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        self.kernel.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
        self.kernel.CreateJobObjectW.restype = wintypes.HANDLE
        self.kernel.SetInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD]
        self.kernel.SetInformationJobObject.restype = wintypes.BOOL
        self.kernel.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
        self.kernel.AssignProcessToJobObject.restype = wintypes.BOOL
        self.kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        self.kernel.CloseHandle.restype = wintypes.BOOL
        self.handle = self.kernel.CreateJobObjectW(None, None)
        if not self.handle:
            raise ctypes.WinError(ctypes.get_last_error())
        limits = ExtendedLimits()
        limits.basic.flags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        if not self.kernel.SetInformationJobObject(self.handle, 9, ctypes.byref(limits), ctypes.sizeof(limits)):
            error = ctypes.WinError(ctypes.get_last_error())
            self.close()
            raise error

    def attach(self, proc):
        if not self.kernel.AssignProcessToJobObject(self.handle, wintypes.HANDLE(int(proc._handle))):
            raise ctypes.WinError(ctypes.get_last_error())
        self.attached_pid = proc.pid

    def resume(self, proc):
        """Resume only our newly created, job-owned, suspended primary thread.

        Popen closes CreateProcess's primary-thread handle. Resolve the one
        initial thread by its owner PID, then recheck ownership on its handle.
        Any ambiguity fails closed and run_process cleans up the suspended job.
        """
        if self.attached_pid != proc.pid or self.resumed:
            raise OSError("process must be newly attached before resume")

        class ThreadEntry(ctypes.Structure):
            _fields_ = [("size", wintypes.DWORD), ("usage", wintypes.DWORD),
                        ("thread_id", wintypes.DWORD), ("owner_pid", wintypes.DWORD),
                        ("base_priority", wintypes.LONG), ("delta_priority", wintypes.LONG),
                        ("flags", wintypes.DWORD)]

        self.kernel.CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
        self.kernel.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
        self.kernel.Thread32First.argtypes = [wintypes.HANDLE, ctypes.POINTER(ThreadEntry)]
        self.kernel.Thread32First.restype = wintypes.BOOL
        self.kernel.Thread32Next.argtypes = [wintypes.HANDLE, ctypes.POINTER(ThreadEntry)]
        self.kernel.Thread32Next.restype = wintypes.BOOL
        self.kernel.OpenThread.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        self.kernel.OpenThread.restype = wintypes.HANDLE
        self.kernel.GetProcessIdOfThread.argtypes = [wintypes.HANDLE]
        self.kernel.GetProcessIdOfThread.restype = wintypes.DWORD
        self.kernel.ResumeThread.argtypes = [wintypes.HANDLE]
        self.kernel.ResumeThread.restype = wintypes.DWORD
        snapshot = self.kernel.CreateToolhelp32Snapshot(0x00000004, 0)  # TH32CS_SNAPTHREAD
        if snapshot == wintypes.HANDLE(-1).value:
            raise ctypes.WinError(ctypes.get_last_error())
        owned = []
        try:
            entry = ThreadEntry()
            entry.size = ctypes.sizeof(entry)
            if not self.kernel.Thread32First(snapshot, ctypes.byref(entry)):
                raise ctypes.WinError(ctypes.get_last_error())
            while True:
                if entry.owner_pid == proc.pid:
                    owned.append(entry.thread_id)
                entry.size = ctypes.sizeof(entry)
                if not self.kernel.Thread32Next(snapshot, ctypes.byref(entry)):
                    error = ctypes.get_last_error()
                    if error != 18:  # ERROR_NO_MORE_FILES
                        raise ctypes.WinError(error)
                    break
        finally:
            self.kernel.CloseHandle(snapshot)
        if len(owned) != 1:
            raise OSError("cannot identify exactly one suspended primary thread")
        thread = self.kernel.OpenThread(0x0002 | 0x0800, False, owned[0])
        if not thread:
            raise ctypes.WinError(ctypes.get_last_error())
        try:
            if self.kernel.GetProcessIdOfThread(thread) != proc.pid:
                raise OSError("primary thread ownership changed")
            previous = self.kernel.ResumeThread(thread)
            if previous == 0xFFFFFFFF:
                raise ctypes.WinError(ctypes.get_last_error())
            if previous != 1:
                raise OSError("unexpected primary thread suspension count")
            self.resumed = True
        finally:
            self.kernel.CloseHandle(thread)

    def close(self):
        if self.handle:
            class Accounting(ctypes.Structure):
                _fields_ = [("user_time", ctypes.c_int64), ("kernel_time", ctypes.c_int64),
                            ("period_user", ctypes.c_int64), ("period_kernel", ctypes.c_int64),
                            ("faults", wintypes.DWORD), ("total", wintypes.DWORD),
                            ("active", wintypes.DWORD), ("terminated", wintypes.DWORD)]
            self.kernel.TerminateJobObject.argtypes = [wintypes.HANDLE, wintypes.UINT]
            self.kernel.TerminateJobObject.restype = wintypes.BOOL
            self.kernel.QueryInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int,
                                                             ctypes.c_void_p, wintypes.DWORD, ctypes.c_void_p]
            self.kernel.QueryInformationJobObject.restype = wintypes.BOOL
            self.kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
            self.kernel.OpenProcess.restype = wintypes.HANDLE
            self.kernel.IsProcessInJob.argtypes = [wintypes.HANDLE, wintypes.HANDLE, ctypes.POINTER(wintypes.BOOL)]
            self.kernel.IsProcessInJob.restype = wintypes.BOOL
            self.kernel.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
            self.kernel.WaitForSingleObject.restype = wintypes.DWORD
            handles = []
            errors = []
            deadline = time.monotonic() + 0.5
            try:
                # Accounting.ActiveProcesses can reach zero before a terminated
                # descendant releases its handles (including its cwd). Retain
                # process handles before termination and wait for actual exit.
                # Enumerate this job only, then recheck membership against PID
                # reuse. Do not open or terminate unrelated process trees.
                try:
                    capacity = 16
                    while True:
                        class ProcessIds(ctypes.Structure):
                            _fields_ = [("assigned", wintypes.DWORD), ("count", wintypes.DWORD),
                                        ("ids", ctypes.c_size_t * capacity)]
                        ids = ProcessIds()
                        ok = self.kernel.QueryInformationJobObject(self.handle, 3, ctypes.byref(ids),
                                                                  ctypes.sizeof(ids), None)
                        if ok and ids.count == ids.assigned:
                            break
                        error = ctypes.get_last_error()
                        if not ok and error != 234:  # ERROR_MORE_DATA
                            raise ctypes.WinError(error)
                        capacity = max(capacity * 2, ids.assigned)
                        if capacity > 65536 or time.monotonic() >= deadline:
                            raise OSError("owned job process list exceeded cleanup budget")
                    for pid in ids.ids[:ids.count]:
                        if time.monotonic() >= deadline:
                            raise OSError("owned job handle collection exceeded cleanup budget")
                        handle = self.kernel.OpenProcess(0x00100000 | 0x1000, False, pid)
                        if not handle:
                            error = ctypes.get_last_error()
                            if error == 87:  # Process already disappeared.
                                continue
                            raise ctypes.WinError(error)
                        owned = wintypes.BOOL()
                        if not self.kernel.IsProcessInJob(handle, self.handle, ctypes.byref(owned)):
                            error = ctypes.WinError(ctypes.get_last_error())
                            self.kernel.CloseHandle(handle)
                            raise error
                        if owned.value:
                            handles.append(handle)
                        else:
                            self.kernel.CloseHandle(handle)
                except OSError as exc:
                    errors.append(str(exc))
                if not self.kernel.TerminateJobObject(self.handle, 1):
                    raise ctypes.WinError(ctypes.get_last_error())
                for handle in handles:
                    remaining_ms = max(0, int((deadline - time.monotonic()) * 1000))
                    outcome = self.kernel.WaitForSingleObject(handle, remaining_ms)
                    if outcome == 0xFFFFFFFF:
                        raise ctypes.WinError(ctypes.get_last_error())
                    if outcome != 0:
                        raise OSError("owned process did not finish cleanup")
                while True:
                    accounting = Accounting()
                    if not self.kernel.QueryInformationJobObject(self.handle, 1, ctypes.byref(accounting),
                                                                  ctypes.sizeof(accounting), None):
                        raise ctypes.WinError(ctypes.get_last_error())
                    if accounting.active == 0:
                        break
                    if time.monotonic() >= deadline:
                        raise OSError("owned job processes did not finish cleanup")
                    time.sleep(0.005)
                if errors:
                    raise OSError("; ".join(errors))
            finally:
                for handle in handles:
                    self.kernel.CloseHandle(handle)
                self.kernel.CloseHandle(self.handle)
                self.handle = None
