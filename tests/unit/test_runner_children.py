"""打断必须让**整棵进程树**消失。

这条用例是用"真孙子进程"验的：父脚本起一个 `sleep 120` 的孙子，把孙子的 PID 写进文件，
然后测试打断父进程，最后检查孙子还在不在。

为什么值得这么测：`taskkill /T` 只杀"调用那一刻的子树"。实测有一次打断 dsh 之后，
`dsh-subprocess-local` 活了下来（它在我们扫描之后才被创建，或者已经被重挂）。
所以现在父进程被塞进 Windows Job Object——内核级的归属关系，不靠扫描。
"""

from __future__ import annotations

import shutil
import subprocess
import sys
import textwrap
import threading
import time
import unittest
import uuid
from pathlib import Path

from mephisto.adapters.runner.dsh import DshRunner, make_job

TMP_ROOT = Path(__file__).resolve().parent / "_tmp"


def process_alive(pid: int) -> bool:
    if sys.platform != "win32":
        try:
            import os

            os.kill(pid, 0)
            return True
        except OSError:
            return False
    result = subprocess.run(
        ["tasklist", "/FI", f"PID eq {pid}", "/NH"],
        capture_output=True,
        text=True,
        check=False,
    )
    return str(pid) in result.stdout


class JobObjectTest(unittest.TestCase):
    def setUp(self) -> None:
        TMP_ROOT.mkdir(parents=True, exist_ok=True)
        self.dir = TMP_ROOT / f"job_{uuid.uuid4().hex[:12]}"
        self.dir.mkdir()

    def tearDown(self) -> None:
        shutil.rmtree(self.dir, ignore_errors=True)

    @unittest.skipUnless(sys.platform == "win32", "Job Object 只在 Windows 上有意义")
    def test_job_object_is_available(self) -> None:
        job = make_job()
        self.assertIsNotNone(job, "这台机器上应该能创建 Job Object")
        assert job is not None
        job.terminate()
        job.close()

    @unittest.skipUnless(sys.platform == "win32", "Job Object 只在 Windows 上有意义")
    def test_cancel_kills_grandchildren(self) -> None:
        pid_file = self.dir / "grandchild.pid"
        script = textwrap.dedent(
            f"""
            import subprocess, sys, time
            child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(120)"])
            open(r"{pid_file}", "w").write(str(child.pid))
            print("started", flush=True)
            time.sleep(120)
            """
        )
        runner = DshRunner(
            profile="test",
            dsh_home=self.dir / "dsh-home",
            cwd=self.dir,
            timeout_seconds=60,
            dsh_cmd=[sys.executable, "-c", script],
        )

        outcome: dict[str, object] = {}

        def work() -> None:
            outcome["result"] = runner.run(__import__("mephisto.ports", fromlist=["Task"]).Task(prompt="hi"))

        thread = threading.Thread(target=work, daemon=True)
        thread.start()

        deadline = time.time() + 15
        grandchild_pid = 0
        while time.time() < deadline:
            if pid_file.exists() and pid_file.read_text().strip():
                grandchild_pid = int(pid_file.read_text().strip())
                break
            time.sleep(0.05)
        self.assertGreater(grandchild_pid, 0, "孙子进程应该已经起来了")
        self.assertTrue(process_alive(grandchild_pid))

        runner.cancel()
        thread.join(timeout=20)
        self.assertFalse(thread.is_alive(), "run() 应该已经收尾")

        for _ in range(40):
            if not process_alive(grandchild_pid):
                break
            time.sleep(0.1)
        self.assertFalse(process_alive(grandchild_pid), f"孙子进程 {grandchild_pid} 被打断后不该还活着")

        result = outcome.get("result")
        self.assertIsNotNone(result)
        self.assertEqual(getattr(result, "error", None), "cancelled")


if __name__ == "__main__":
    unittest.main()
