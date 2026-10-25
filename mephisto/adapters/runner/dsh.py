"""`dsh` 子进程运行器（每轮一个进程）。

为什么是子进程而不是 SDK：不需要 80 MB 的 Python SDK 包、取消就是杀进程、
进程崩了不留残留状态。详见 docs/25 §6。

**接口是按实测写的，不是按文档猜的**（本机 dsh 0.2.0-rc.2，2026-10-26 实测）：

    {"type":"session","sessionId":"session-...","cwd":"..."}
    {"type":"status","phase":"turn_start","turn":1}
    {"type":"status","phase":"step_start","turn":1,"step":1}
    {"type":"thinking", ...}
    {"type":"text","text":"..."}
    {"type":"status","phase":"step_end","step":1,"usage":{"inputTokens":..,"cacheReadTokens":..}}
    {"type":"status","phase":"turn_end","reason":{"kind":"completed"}}
    {"type":"final","text":"2"}

三条要点：
1. **任务文本走 stdin**——Windows 命令行上限约 32k，prompt 常有 30–60 KB。
2. **判定以退出码为准**：失败时事件流也可能有 `final`，只有退出码 0 才算 `turn/end` 完成。
3. 取消/超时都杀**进程树**，否则 dsh 拉起的工具进程会残留。
"""

from __future__ import annotations

import ctypes
import json
import os
import shutil
import subprocess
import sys
import threading
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Mapping, Sequence

from ...ports import EventHook, RunEvent, RunResult, Task

_TERMINAL_PHASES = ("turn_end",)
_ERROR_LIMIT = 4000


def _now() -> datetime:
    return datetime.now(tz=UTC)


# ---------- Windows Job Object ----------
#
# 为什么不用 `taskkill /T` 了事：它只杀"调用那一刻的子树"。dsh 会拉起
# `dsh-subprocess-local` 这类孙子进程，实测有一次打断之后它活了下来。
# Job Object 是内核级的归属关系：句柄一关（或 TerminateJobObject），组内所有进程一起死，
# 不依赖扫描时机，也不会因为被重挂而漏掉。


class _LARGE_INTEGER(ctypes.Structure):
    _fields_ = [("QuadPart", ctypes.c_longlong)]


class _IO_COUNTERS(ctypes.Structure):
    _fields_ = [
        ("ReadOperationCount", ctypes.c_ulonglong),
        ("WriteOperationCount", ctypes.c_ulonglong),
        ("OtherOperationCount", ctypes.c_ulonglong),
        ("ReadTransferCount", ctypes.c_ulonglong),
        ("WriteTransferCount", ctypes.c_ulonglong),
        ("OtherTransferCount", ctypes.c_ulonglong),
    ]


class _JOBOBJECT_BASIC_LIMIT_INFORMATION(ctypes.Structure):
    _fields_ = [
        ("PerProcessUserTimeLimit", _LARGE_INTEGER),
        ("PerJobUserTimeLimit", _LARGE_INTEGER),
        ("LimitFlags", ctypes.c_ulong),
        ("MinimumWorkingSetSize", ctypes.c_size_t),
        ("MaximumWorkingSetSize", ctypes.c_size_t),
        ("ActiveProcessLimit", ctypes.c_ulong),
        ("Affinity", ctypes.POINTER(ctypes.c_ulong)),
        ("PriorityClass", ctypes.c_ulong),
        ("SchedulingClass", ctypes.c_ulong),
    ]


class _JOBOBJECT_EXTENDED_LIMIT_INFORMATION(ctypes.Structure):
    _fields_ = [
        ("BasicLimitInformation", _JOBOBJECT_BASIC_LIMIT_INFORMATION),
        ("IoInfo", _IO_COUNTERS),
        ("ProcessMemoryLimit", ctypes.c_size_t),
        ("JobMemoryLimit", ctypes.c_size_t),
        ("PeakProcessMemoryUsed", ctypes.c_size_t),
        ("PeakJobMemoryUsed", ctypes.c_size_t),
    ]


_JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x00002000
_JOB_OBJECT_EXTENDED_LIMIT_INFORMATION = 9
_PROCESS_SET_QUOTA = 0x0100
_PROCESS_TERMINATE = 0x0001


class WindowsJob:
    """把进程放进 Job Object，保证"打断 = 整棵树消失"。"""

    def __init__(self) -> None:
        if os.name != "nt":
            raise OSError("Windows Job Object 只在 Windows 上可用")
        self._kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        handle = self._kernel32.CreateJobObjectW(None, None)
        if not handle:
            raise OSError(ctypes.get_last_error(), "CreateJobObjectW 失败")
        self.handle = handle
        info = _JOBOBJECT_EXTENDED_LIMIT_INFORMATION()
        info.BasicLimitInformation.LimitFlags = _JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        ok = self._kernel32.SetInformationJobObject(
            handle,
            _JOB_OBJECT_EXTENDED_LIMIT_INFORMATION,
            ctypes.byref(info),
            ctypes.sizeof(info),
        )
        if not ok:
            self._kernel32.CloseHandle(handle)
            raise OSError(ctypes.get_last_error(), "SetInformationJobObject 失败")
        self._closed = False

    def assign(self, pid: int) -> bool:
        handle = self._kernel32.OpenProcess(_PROCESS_SET_QUOTA | _PROCESS_TERMINATE, False, pid)
        if not handle:
            return False
        try:
            return bool(self._kernel32.AssignProcessToJobObject(self.handle, handle))
        finally:
            self._kernel32.CloseHandle(handle)

    def terminate(self) -> None:
        if not self._closed:
            self._kernel32.TerminateJobObject(self.handle, 1)

    def close(self) -> None:
        if not self._closed:
            self._closed = True
            self._kernel32.CloseHandle(self.handle)


def make_job() -> WindowsJob | None:
    """拿不到 Job Object 也不算致命：退回 taskkill 那条路。"""
    if os.name != "nt":
        return None
    try:
        return WindowsJob()
    except OSError:
        return None


def kill_tree(pid: int) -> None:
    """杀进程及其所有子进程。Windows 用 taskkill /T，其它平台按进程组。"""
    if os.name == "nt":
        subprocess.run(
            ["taskkill", "/F", "/T", "/PID", str(pid)],
            capture_output=True,
            check=False,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        return
    try:
        os.killpg(os.getpgid(pid), 9)
    except (ProcessLookupError, PermissionError):
        try:
            os.kill(pid, 9)
        except ProcessLookupError:
            pass


class DshRunner:
    """把 `dsh --profile <p> --json` 包成 Runner。"""

    def __init__(
        self,
        *,
        profile: str,
        dsh_home: Path,
        cwd: Path,
        timeout_seconds: int = 600,
        dsh_cmd: Sequence[str] | None = None,
        env_extra: Mapping[str, str] | None = None,
    ) -> None:
        self.profile = profile
        self.dsh_home = Path(dsh_home)
        self.cwd = Path(cwd)
        self.timeout_seconds = timeout_seconds
        self.dsh_cmd = list(dsh_cmd) if dsh_cmd else default_dsh_cmd()
        self.env_extra = dict(env_extra or {})
        self._proc: subprocess.Popen[bytes] | None = None
        self._job: WindowsJob | None = None
        self._lock = threading.Lock()
        self._cancelled = False

    # ---------- 供 CLI --dry-run 用 ----------
    def argv(self) -> list[str]:
        return [*self.dsh_cmd, "--profile", self.profile, "--json"]

    def env(self) -> dict[str, str]:
        env = dict(os.environ)
        env["DSH_HOME"] = str(self.dsh_home)
        env.update(self.env_extra)
        return env

    # ---------- 取消 ----------
    def cancel(self) -> None:
        """外部打断。返回后 `run()` 会以失败收尾，error 标注 cancelled。"""
        with self._lock:
            self._cancelled = True
            proc = self._proc
            job = self._job
        # 先掐 Job Object：孙子进程也一起死，不依赖 taskkill 的扫描时机
        if job is not None:
            job.terminate()
        if proc is not None and proc.poll() is None:
            kill_tree(proc.pid)

    # ---------- 主流程 ----------
    def run(self, task: Task, *, on_event: EventHook | None = None) -> RunResult:
        started = time.monotonic()
        with self._lock:
            self._cancelled = False
        self.cwd.mkdir(parents=True, exist_ok=True)
        self.dsh_home.mkdir(parents=True, exist_ok=True)

        events: list[RunEvent] = []
        stderr_chunks: list[bytes] = []
        timed_out = False

        proc = subprocess.Popen(
            self.argv(),
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            cwd=str(self.cwd),
            env=self.env(),
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0,
        )
        with self._lock:
            self._proc = proc
            job = make_job()
            self._job = job
        if job is not None:
            # 分配失败不致命：cancel() 里还有 taskkill 兜底
            job.assign(proc.pid)

        def _on_timeout() -> None:
            nonlocal timed_out
            if proc.poll() is None:
                timed_out = True
                kill_tree(proc.pid)

        timer = threading.Timer(self.timeout_seconds, _on_timeout)
        timer.daemon = True
        timer.start()

        stderr_thread = threading.Thread(target=_drain, args=(proc.stderr, stderr_chunks), daemon=True)
        stderr_thread.start()

        try:
            assert proc.stdin is not None
            try:
                proc.stdin.write(task.prompt.encode("utf-8"))
                proc.stdin.close()
            except OSError:
                # dsh 可能已经退出（例如 profile 不存在）；不要因此抛异常，
                # 让下面的读循环把退出码与 stderr 收集完，失败由 RunResult 表达。
                pass
            assert proc.stdout is not None
            for raw in proc.stdout:
                line = raw.decode("utf-8", errors="replace").strip()
                if not line:
                    continue
                event = _parse_line(line)
                if event is None:
                    continue
                events.append(event)
                if on_event is not None:
                    on_event(event)
            exit_code = proc.wait(timeout=10)
        finally:
            timer.cancel()
            stderr_thread.join(timeout=2)
            with self._lock:
                self._proc = None
                job = self._job
                self._job = None
            if job is not None:
                job.terminate()
            if proc.poll() is None:
                kill_tree(proc.pid)
            # 管道要显式关：被打断时读循环可能没读到 EOF，留着就是资源泄漏
            for stream in (proc.stdout, proc.stderr):
                if stream is not None:
                    try:
                        stream.close()
                    except OSError:
                        pass
            if job is not None:
                job.close()

        duration_ms = int((time.monotonic() - started) * 1000)
        stderr_text = b"".join(stderr_chunks).decode("utf-8", errors="replace").strip()
        return _build_result(
            events=events,
            exit_code=exit_code,
            stderr=stderr_text,
            timed_out=timed_out,
            cancelled=self._cancelled,
            duration_ms=duration_ms,
            timeout_seconds=self.timeout_seconds,
        )


def default_dsh_cmd() -> list[str]:
    exe = shutil.which("dsh")
    if exe is None:
        raise FileNotFoundError("PATH 里找不到 dsh；先装 DSH 或配置 runner.dsh_cmd")
    return [exe]


def _drain(stream: Any, sink: list[bytes]) -> None:
    if stream is None:
        return
    for chunk in stream:
        sink.append(chunk)


def _parse_line(line: str) -> RunEvent | None:
    try:
        payload = json.loads(line)
    except json.JSONDecodeError:
        return None
    if not isinstance(payload, dict) or "type" not in payload:
        return None
    kind = str(payload.pop("type"))
    return RunEvent(kind=kind, payload=payload, at=_now())


def _build_result(
    *,
    events: Sequence[RunEvent],
    exit_code: int,
    stderr: str,
    timed_out: bool,
    cancelled: bool,
    duration_ms: int,
    timeout_seconds: int,
) -> RunResult:
    final_text = ""
    fallback: list[str] = []
    session_id: str | None = None
    usage: dict[str, int] = {}
    error_event: str | None = None
    turn_end_kind: str | None = None

    for event in events:
        if event.kind == "session":
            value = event.payload.get("sessionId")
            session_id = value if isinstance(value, str) else session_id
        elif event.kind == "text":
            chunk = event.payload.get("text")
            if isinstance(chunk, str) and chunk:
                fallback.append(chunk)
        elif event.kind == "final":
            value = event.payload.get("text")
            if isinstance(value, str):
                final_text = value
        elif event.kind == "error":
            error_event = str(event.payload.get("message") or event.payload.get("text") or event.payload)
        elif event.kind == "status":
            if event.phase in _TERMINAL_PHASES:
                reason = event.payload.get("reason")
                if isinstance(reason, Mapping):
                    kind = reason.get("kind")
                    turn_end_kind = kind if isinstance(kind, str) else turn_end_kind
            step_usage = event.payload.get("usage")
            if isinstance(step_usage, Mapping):
                for key, value in step_usage.items():
                    if isinstance(value, int):
                        usage[key] = usage.get(key, 0) + value

    ok = exit_code == 0 and error_event is None and turn_end_kind in (None, "completed")
    text = final_text or "".join(fallback)

    error = None
    if timed_out:
        error = f"timeout after {timeout_seconds}s"
    elif cancelled:
        error = "cancelled"
    elif error_event is not None:
        error = error_event
    elif exit_code != 0:
        error = f"dsh exited with code {exit_code}"
    elif turn_end_kind not in (None, "completed"):
        error = f"turn ended with reason={turn_end_kind}"
    if error is not None and stderr:
        error = f"{error} | stderr: {stderr[:_ERROR_LIMIT]}"

    return RunResult(
        ok=ok,
        text=text,
        exit_code=exit_code,
        events=tuple(events),
        error=error,
        dsh_session_id=session_id,
        usage=usage,
        duration_ms=duration_ms,
    )


if __name__ == "__main__":  # pragma: no cover - 手工调试入口
    print(f"python {sys.version.split()[0]}；用 `mephisto run --dry-run` 看将要执行的命令")
