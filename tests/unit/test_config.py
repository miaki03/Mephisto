from __future__ import annotations

import os
import shutil
import uuid
import unittest
from pathlib import Path
from unittest import mock

from mephisto.config import Config, load

TMP_ROOT = Path(__file__).resolve().parent / '_tmp'

TOML = """
[server]
host = "0.0.0.0"
port = 9999
trust_env_proxy = true

[runner]
kind = "fake"
profile = "test-profile"
dsh_home = "state/other-home"
timeout_seconds = 42

[storage]
data_dir = "var/data"
"""


class ConfigTest(unittest.TestCase):
    def setUp(self) -> None:
        # 不用 tempfile.TemporaryDirectory：本机沙箱里 %TEMP% 的子目录无法被删除，
        # 清理时会抛 PermissionError，把全绿的用例变成 9 个 error。
        # 所以临时目录建在仓库内（tests/_tmp），删除是尽力而为。
        TMP_ROOT.mkdir(parents=True, exist_ok=True)
        self.root = TMP_ROOT / ('case_' + uuid.uuid4().hex[:12])
        self.root.mkdir()

    def tearDown(self) -> None:
        shutil.rmtree(self.root, ignore_errors=True)

    def test_defaults_without_config_file(self) -> None:
        cfg = load(root=self.root)
        self.assertIsInstance(cfg, Config)
        self.assertEqual(cfg.server.host, "127.0.0.1")
        self.assertEqual(cfg.server.port, 8787)
        self.assertFalse(cfg.server.trust_env_proxy)
        self.assertEqual(cfg.runner.kind, "cli")
        self.assertIsNone(cfg.source)
        self.assertEqual(cfg.data_dir, (self.root / "state").resolve())

    def test_loads_toml(self) -> None:
        (self.root / "mephisto.toml").write_text(TOML, encoding="utf-8")
        cfg = load(root=self.root)
        self.assertEqual(cfg.source, self.root / "mephisto.toml")
        self.assertEqual(cfg.server.host, "0.0.0.0")
        self.assertEqual(cfg.server.port, 9999)
        self.assertTrue(cfg.server.trust_env_proxy)
        self.assertEqual(cfg.runner.kind, "fake")
        self.assertEqual(cfg.runner.timeout_seconds, 42)
        self.assertEqual(cfg.dsh_home, (self.root / "state/other-home").resolve())
        self.assertEqual(cfg.storage.data_dir, "var/data")

    def test_env_overrides_host_and_port_only(self) -> None:
        (self.root / "mephisto.toml").write_text(TOML, encoding="utf-8")
        with mock.patch.dict(
            os.environ, {"MEPHISTO_HOST": "127.0.0.1", "MEPHISTO_PORT": "8001"}
        ):
            cfg = load(root=self.root)
        self.assertEqual(cfg.server.host, "127.0.0.1")
        self.assertEqual(cfg.server.port, 8001)
        self.assertEqual(cfg.runner.kind, "fake", "环境变量不该覆盖 runner 段")

    def test_explicit_path_missing_raises(self) -> None:
        with self.assertRaises(FileNotFoundError):
            load(self.root / "nope.toml", root=self.root)

    def test_as_dict_is_json_safe(self) -> None:
        data = load(root=self.root).as_dict()
        self.assertIsInstance(data["root"], str)
        self.assertEqual(data["server"]["port"], 8787)
        self.assertTrue(str(data["runner"]["dsh_home"]).endswith("dsh-home"))

    def test_broken_section_type_raises(self) -> None:
        (self.root / "mephisto.toml").write_text('server = "not a table"\n', encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "必须是表"):
            load(root=self.root)


if __name__ == "__main__":
    unittest.main()
