from __future__ import annotations

import contextlib
import io
import unittest

from mephisto import __version__
from mephisto.cli import main


def run_capture(argv: list[str]) -> tuple[int, str, str]:
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = main(argv)
    return code, out.getvalue(), err.getvalue()


class CliTest(unittest.TestCase):
    def test_version(self) -> None:
        code, out, _ = run_capture(["version"])
        self.assertEqual(code, 0)
        self.assertEqual(out.strip(), __version__)

    def test_doctor_reports_and_exits_cleanly(self) -> None:
        code, out, _ = run_capture(["doctor"])
        self.assertIn(code, (0, 1), "doctor 只允许 0（全通过）或 1（有不达标项）")
        for expected in ("体检", "Python", "DSH", "磁盘余量", "结论"):
            with self.subTest(expected=expected):
                self.assertIn(expected, out)

    def test_unfinished_commands_say_so(self) -> None:
        for command, step in (("serve", "4"), ("run", "3")):
            with self.subTest(command=command):
                code, _, err = run_capture([command])
                self.assertEqual(code, 2)
                self.assertIn("尚未实现", err)
                self.assertIn(step, err)


if __name__ == "__main__":
    unittest.main()
