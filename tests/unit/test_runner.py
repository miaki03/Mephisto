"""运行器测试：用假 dsh（`sys.executable -c`）保证 hermetic。

真 dsh 的接口形状是在本机实测后写进 `dsh.py` 文档串的；
这些用例只验证**我们的解析与失败判定**，不联网、不依赖 profile。
"""

from __future__ import annotations

import shutil
import sys
import textwrap
import threading
import time
import unittest
import uuid
from pathlib import Path

from mephisto.adapters.runner import DshRunner
from mephisto.ports import Task

TMP_ROOT = Path(__file__).resolve().parent / "_tmp"

FAKE_OK = textwrap.dedent(
    """
    import json, sys
    prompt = sys.stdin.read()
    def emit(**kw): print(json.dumps(kw, ensure_ascii=False), flush=True)
    emit(type="session", sessionId="session-fake", cwd=".")
    emit(type="status", phase="turn_start", turn=1)
    emit(type="status", phase="step_start", turn=1, step=1)
    emit(type="thinking", text="想一想")
    emit(type="text", text="中间")
    emit(type="status", phase="step_end", turn=1, step=1,
         usage={"inputTokens": 100, "outputTokens": 2, "cacheReadTokens": 64, "totalTokens": 102})
    emit(type="status", phase="step_end", turn=2, step=1,
         usage={"inputTokens": 10, "outputTokens": 1, "cacheReadTokens": 6, "totalTokens": 11})
    emit(type="status", phase="turn_end", turn=2, reason={"kind": "completed"})
    emit(type="final", text="收到的提示词长度=" + str(len(prompt)))
    sys.exit(0)
    """
).strip()

FAKE_FAIL = textwrap.dedent(
    """
    import json, sys
    sys.stdin.read()
    print(json.dumps({"type": "status", "phase": "turn_end", "reason": {"kind": "error"}}), flush=True)
    print(json.dumps({"type": "final", "text": ""}), flush=True)
    sys.exit(1)
    """
).strip()

FAKE_ERROR_EVENT = textwrap.dedent(
    """
    import json, sys
    sys.stdin.read()
    print(json.dumps({"type": "error", "message": "模型不可用"}), flush=True)
    print("dsh: boom", file=sys.stderr, flush=True)
    sys.exit(1)
    """
).strip()

FAKE_SLOW = textwrap.dedent(
    """
    import json, sys, time
    sys.stdin.read()
    print(json.dumps({"type": "status", "phase": "turn_start", "turn": 1}), flush=True)
    time.sleep(30)
    """
).strip()

FAKE_NOISE = textwrap.dedent(
    """
    import json, sys
    sys.stdin.read()
    print("这不是 JSON", flush=True)
    print(json.dumps({"no_type": 1}), flush=True)
    print(json.dumps({"type": "status", "phase": "turn_end", "reason": {"kind": "completed"}}), flush=True)
    print(json.dumps({"type": "final", "text": "ok"}), flush=True)
    sys.exit(0)
    """
).strip()


class RunnerTestBase(unittest.TestCase):
    def setUp(self) -> None:
        TMP_ROOT.mkdir(parents=True, exist_ok=True)
        self.dir = TMP_ROOT / f"runner_{uuid.uuid4().hex[:12]}"
        self.dir.mkdir()

    def tearDown(self) -> None:
        shutil.rmtree(self.dir, ignore_errors=True)

    def runner(self, script: str, *, timeout: int = 20) -> DshRunner:
        return DshRunner(
            profile="test",
            dsh_home=self.dir / "dsh-home",
            cwd=self.dir,
            timeout_seconds=timeout,
            dsh_cmd=[sys.executable, "-c", script],
        )


class HappyPathTest(RunnerTestBase):
    def test_parses_events_and_final_text(self) -> None:
        seen: list[str] = []
        result = self.runner(FAKE_OK).run(Task(prompt="你好"), on_event=lambda e: seen.append(e.kind))
        self.assertTrue(result.ok, result.error)
        self.assertEqual(result.exit_code, 0)
        self.assertEqual(result.dsh_session_id, "session-fake")
        self.assertIn("收到的提示词长度=", result.text)
        self.assertEqual(seen, [e.kind for e in result.events])
        self.assertIn("status", seen)
        self.assertIn("text", seen)

    def test_prompt_goes_through_stdin(self) -> None:
        long_prompt = "长" * 40_000
        result = self.runner(FAKE_OK).run(Task(prompt=long_prompt))
        self.assertTrue(result.ok, result.error)
        self.assertIn(str(len(long_prompt)), result.text, "40k 字符必须能通过 stdin 送达")

    def test_usage_is_summed_across_steps(self) -> None:
        result = self.runner(FAKE_OK).run(Task(prompt="x"))
        self.assertEqual(result.usage["totalTokens"], 113, "两步的 usage 要累加")
        self.assertEqual(result.cache_read_tokens, 70)

    def test_noise_lines_are_ignored(self) -> None:
        result = self.runner(FAKE_NOISE).run(Task(prompt="x"))
        self.assertTrue(result.ok, result.error)
        self.assertEqual(result.text, "ok")
        self.assertEqual([e.kind for e in result.events], ["status", "final"])


class FailureTest(RunnerTestBase):
    def test_nonzero_exit_is_failure_even_with_final_event(self) -> None:
        """真实的坑：失败时事件流里也有 final，只有退出码能说明问题。"""
        result = self.runner(FAKE_FAIL).run(Task(prompt="x"))
        self.assertFalse(result.ok)
        self.assertEqual(result.exit_code, 1)
        self.assertIn("exited with code 1", result.error or "")

    def test_error_event_is_surfaced(self) -> None:
        result = self.runner(FAKE_ERROR_EVENT).run(Task(prompt="x"))
        self.assertFalse(result.ok)
        self.assertIn("模型不可用", result.error or "")
        self.assertIn("boom", result.error or "", "stderr 要带上，否则排查没线索")

    def test_timeout_kills_process_and_returns_fast(self) -> None:
        runner = self.runner(FAKE_SLOW, timeout=1)
        started = time.monotonic()
        result = runner.run(Task(prompt="x"))
        elapsed = time.monotonic() - started
        self.assertFalse(result.ok)
        self.assertIn("timeout", result.error or "")
        self.assertLess(elapsed, 15, "超时必须真的杀掉进程，而不是等它睡完")

    def test_cancel_from_another_thread(self) -> None:
        runner = self.runner(FAKE_SLOW, timeout=60)
        box: dict[str, object] = {}

        def work() -> None:
            box["result"] = runner.run(Task(prompt="x"))

        thread = threading.Thread(target=work)
        thread.start()
        time.sleep(0.8)
        runner.cancel()
        thread.join(timeout=20)
        self.assertFalse(thread.is_alive(), "cancel 后线程必须退出")
        result = box.get("result")
        self.assertIsNotNone(result)
        assert result is not None
        self.assertFalse(result.ok)
        self.assertIn("cancel", (result.error or "").lower())


class CommandShapeTest(RunnerTestBase):
    def test_argv_and_env(self) -> None:
        runner = self.runner(FAKE_OK)
        self.assertEqual(runner.argv()[-3:], ["--profile", "test", "--json"])
        self.assertEqual(runner.env()["DSH_HOME"], str(self.dir / "dsh-home"))


if __name__ == "__main__":
    unittest.main()
