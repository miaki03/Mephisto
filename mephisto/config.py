"""配置：dataclass + TOML（标准库 tomllib），少量环境变量可覆盖。

优先级：默认值 < `mephisto.toml` < 环境变量（只有 host/port 允许被覆盖，
其余项走配置文件，避免"环境变量满天飞"这种更难排查的配置源）。
"""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

DEFAULT_PORT = 8787
CONFIG_FILENAME = "mephisto.toml"


@dataclass(frozen=True)
class ServerConfig:
    host: str = "127.0.0.1"          # 只听回环；公网入口在 Cloudflare
    port: int = DEFAULT_PORT
    trust_env_proxy: bool = False     # 内部调用不读 HTTP_PROXY


@dataclass(frozen=True)
class RunnerConfig:
    kind: str = "cli"                 # cli（每轮一个 dsh 子进程）| fake（测试）
    profile: str = "mephisto"
    dsh_home: str = "state/dsh-home"
    timeout_seconds: int = 600


@dataclass(frozen=True)
class StorageConfig:
    data_dir: str = "state"


@dataclass(frozen=True)
class Config:
    root: Path
    server: ServerConfig = field(default_factory=ServerConfig)
    runner: RunnerConfig = field(default_factory=RunnerConfig)
    storage: StorageConfig = field(default_factory=StorageConfig)
    source: Path | None = None

    @property
    def data_dir(self) -> Path:
        """相对**仓库根**解析，不是相对配置文件——一个部署只有一个 state 目录。"""
        return (self.root / self.storage.data_dir).resolve()

    @property
    def dsh_home(self) -> Path:
        return (self.root / self.runner.dsh_home).resolve()

    @property
    def config_path(self) -> Path:
        return self.source or (self.root / CONFIG_FILENAME)

    def as_dict(self) -> dict[str, object]:
        return {
            "root": str(self.root),
            "config": str(self.config_path),
            "server": {
                "host": self.server.host,
                "port": self.server.port,
                "trust_env_proxy": self.server.trust_env_proxy,
            },
            "runner": {
                "kind": self.runner.kind,
                "profile": self.runner.profile,
                "dsh_home": str(self.dsh_home),
                "timeout_seconds": self.runner.timeout_seconds,
            },
            "storage": {"data_dir": str(self.data_dir)},
        }


def find_root(start: Path | None = None) -> Path:
    """从 start 向上找含 pyproject.toml 的目录；找不到就用 start 本身。"""
    here = (start or Path.cwd()).resolve()
    for candidate in (here, *here.parents):
        if (candidate / "pyproject.toml").exists():
            return candidate
    return here


def _section(raw: dict, name: str) -> dict:
    value = raw.get(name, {})
    if not isinstance(value, dict):
        raise ValueError(f"配置段 [{name}] 必须是表，实际是 {type(value).__name__}")
    return value


def load(path: Path | None = None, *, root: Path | None = None) -> Config:
    """读配置。文件不存在时用默认值，并在 `source` 里标记为 None。"""
    base = root or find_root()
    candidate = path or (
        Path(os.environ["MEPHISTO_CONFIG"]) if os.environ.get("MEPHISTO_CONFIG") else None
    )
    config_file: Path | None = None
    raw: dict = {}

    if candidate is not None:
        config_file = candidate.expanduser().resolve()
        if not config_file.exists():
            raise FileNotFoundError(f"配置文件不存在：{config_file}")
        raw = tomllib.loads(config_file.read_text(encoding="utf-8"))
    else:
        default_file = base / CONFIG_FILENAME
        if default_file.exists():
            config_file = default_file
            raw = tomllib.loads(default_file.read_text(encoding="utf-8"))

    server_raw = _section(raw, "server")
    runner_raw = _section(raw, "runner")
    storage_raw = _section(raw, "storage")

    server = ServerConfig(
        host=os.environ.get("MEPHISTO_HOST", server_raw.get("host", ServerConfig.host)),
        port=int(os.environ.get("MEPHISTO_PORT", server_raw.get("port", DEFAULT_PORT))),
        trust_env_proxy=bool(server_raw.get("trust_env_proxy", False)),
    )
    runner = RunnerConfig(
        kind=str(runner_raw.get("kind", RunnerConfig.kind)),
        profile=str(runner_raw.get("profile", RunnerConfig.profile)),
        dsh_home=str(runner_raw.get("dsh_home", RunnerConfig.dsh_home)),
        timeout_seconds=int(runner_raw.get("timeout_seconds", RunnerConfig.timeout_seconds)),
    )
    storage = StorageConfig(data_dir=str(storage_raw.get("data_dir", StorageConfig.data_dir)))

    return Config(root=base, server=server, runner=runner, storage=storage, source=config_file)
