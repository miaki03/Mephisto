"""命令行入口：doctor / serve / run / db / version。

第 0 步只有 `doctor` 是真实现，其余先占位并明确告知"哪一步会实现"，
不留一个看起来能跑、其实什么都不做的命令。
"""

from __future__ import annotations

import argparse
import platform
import shutil
import subprocess
import sys
from pathlib import Path

from . import __version__
from .clock import SystemClock
from .config import Config, load

_OK = "OK  "
_NG = "FAIL"


def _human_bytes(n: int) -> str:
    size = float(n)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if size < 1024 or unit == "TB":
            return f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} TB"


def _check_dsh() -> tuple[bool, str]:
    exe = shutil.which("dsh")
    if exe is None:
        return False, "PATH 里没有 dsh（第 3 步会需要它）"
    try:
        done = subprocess.run(
            [exe, "--version"], capture_output=True, text=True, timeout=30, check=False
        )
    except subprocess.TimeoutExpired:
        return False, "dsh --version 超过 30 秒未返回"
    except OSError as exc:
        return False, f"无法执行 dsh：{exc}"
    text = (done.stdout or done.stderr or "").strip().splitlines()
    version = text[0] if text else "(无输出)"
    return done.returncode == 0, f"{version}  ← {exe}"


def cmd_doctor(cfg: Config) -> int:
    rows: list[tuple[bool, str, str]] = []

    py_ok = sys.version_info >= (3, 12)
    rows.append((py_ok, "Python", f"{platform.python_version()}  ({sys.executable})"))

    rows.append((True, "仓库根", str(cfg.root)))
    rows.append((True, "配置文件", str(cfg.config_path) if cfg.source else "未使用（全部默认值）"))
    rows.append((True, "数据目录", str(cfg.data_dir)))

    dsh_ok, dsh_note = _check_dsh()
    rows.append((dsh_ok, "DSH", dsh_note))

    try:
        usage = shutil.disk_usage(cfg.root)
        disk_ok = usage.free > 3 * 1024**3
        rows.append((disk_ok, "磁盘余量", f"{_human_bytes(usage.free)} 可用（需 >3 GB）"))
    except OSError as exc:
        rows.append((False, "磁盘余量", f"无法读取：{exc}"))

    now = SystemClock().now()
    rows.append((True, "系统时间", f"{now.isoformat()}  本地 {now.astimezone():%Y-%m-%d %H:%M:%S %Z}"))

    rows.append((True, "监听地址", f"{cfg.server.host}:{cfg.server.port}"))

    width = max(len(label) for _, label, _ in rows)
    print(f"mephisto {__version__} 体检")
    print("-" * (width + 60))
    for ok, label, note in rows:
        print(f"[{_OK if ok else _NG}] {label:<{width}}  {note}")
    print("-" * (width + 60))

    failed = [label for ok, label, _ in rows if not ok]
    if failed:
        print(f"结论：{len(failed)} 项不达标 → {'、'.join(failed)}")
        return 1
    print("结论：全部通过")
    return 0


def _not_yet(command: str, step: str) -> int:
    print(f"'{command}' 尚未实现：计划在第 {step} 步交付（见 docs/99-implementation-plan.md）", file=sys.stderr)
    return 2


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="mephisto", description="个人 Agent 服务内核")
    parser.add_argument("--config", type=Path, default=None, help="指定配置文件（默认 ./mephisto.toml）")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("doctor", help="体检：Python / 目录 / DSH / 磁盘")

    serve = sub.add_parser("serve", help="起 HTTP 服务（第 4 步）")
    serve.add_argument("--host", default=None)
    serve.add_argument("--port", type=int, default=None)

    run = sub.add_parser("run", help="跑一轮任务（DSH 子进程）")
    run.add_argument("text", nargs="?", default=None, help="任务文本；省略则从 stdin 读")
    run.add_argument("--dry-run", action="store_true", help="只打印将要执行的命令与 prompt，不执行")
    run.add_argument("--verbose", action="store_true", help="打印每条事件")

    db = sub.add_parser("db", help="数据库维护")
    db.add_argument("action", nargs="?", default="check", choices=("migrate", "check"), help="migrate 建表/升级；check 体检")

    sub.add_parser("version", help="打印版本")
    return parser


def cmd_db(cfg: Config, action: str) -> int:
    from .store import open_db

    db = open_db(cfg.data_dir)
    try:
        if action == "migrate":
            before, after = db.migrate()
            if before == after:
                print(f"数据库已是最新：v{after}（{db.path}）")
            else:
                print(f"迁移完成：v{before} → v{after}（{db.path}）")
            return 0

        info = db.check()
        print(f"数据库      {info['path']}")
        print(f"schema 版本 v{info['user_version']}   journal_mode={info['journal_mode']}")
        print(f"完整性      {info['integrity']}   外键问题 {info['foreign_key_problems']} 处")
        if info["user_version"] == 0 or not info["tables"]:
            print("结论：尚未初始化，先跑 `mephisto db migrate`")
            return 1
        print(f"表          {len(info['tables'])} 张")
        for name, rows in info["tables"].items():
            print(f"  {name:<18} {rows:>6} 行")
        ok = info["integrity"] == "ok" and info["foreign_key_problems"] == 0
        print("结论：通过" if ok else "结论：有问题")
        return 0 if ok else 1
    finally:
        db.close()


def cmd_run(cfg: Config, *, text: str | None, dry_run: bool, verbose: bool) -> int:
    from .adapters.runner import DshRunner
    from .ports import RunEvent, Task

    if text is None:
        if sys.stdin is None or sys.stdin.isatty():
            print("没有任务文本：给一个位置参数，或把内容管道进来", file=sys.stderr)
            return 2
        text = sys.stdin.read()
    prompt = text.strip()
    if not prompt:
        print("任务文本为空", file=sys.stderr)
        return 2

    try:
        runner = DshRunner(
            profile=cfg.runner.profile,
            dsh_home=cfg.dsh_home,
            cwd=cfg.root,
            timeout_seconds=cfg.runner.timeout_seconds,
        )
    except FileNotFoundError as exc:
        print(f"无法启动 dsh：{exc}", file=sys.stderr)
        return 1

    if dry_run:
        print("命令 : " + " ".join(runner.argv()))
        print("工作目录: " + str(runner.cwd))
        print("DSH_HOME: " + runner.dsh_home.as_posix())
        print(f"prompt  : {len(prompt)} 字符（走 stdin，不走命令行）")
        print("-" * 60)
        print(prompt)
        return 0

    def show(event: RunEvent) -> None:
        if event.kind == "status":
            phase = event.phase or "?"
            usage = event.payload.get("usage")
            suffix = ""
            if isinstance(usage, dict) and "totalTokens" in usage:
                suffix = f"  tokens={usage.get('totalTokens')} cacheRead={usage.get('cacheReadTokens')}"
            print(f"[status] {phase}{suffix}")
        elif event.kind in ("tool_call", "tool_result"):
            name = event.payload.get("name") or event.payload.get("tool") or "?"
            print(f"[{event.kind}] {name}")
        elif verbose:
            print(f"[{event.kind}] {str(event.payload)[:120]}")

    print(f"执行中（超时 {cfg.runner.timeout_seconds}s）…")
    result = runner.run(Task(prompt=prompt), on_event=show)
    print("-" * 60)
    if result.text:
        print(result.text)
    print("-" * 60)
    print(
        f"结果: {'成功' if result.ok else '失败'}  exit={result.exit_code}  "
        f"{result.duration_ms}ms  tokens={result.usage.get('totalTokens', 0)}  "
        f"cacheRead={result.cache_read_tokens}"
    )
    if result.error:
        print(f"错误: {result.error}", file=sys.stderr)
    return 0 if result.ok else 1


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "version":
        print(__version__)
        return 0

    cfg = load(args.config)
    if args.command == "doctor":
        return cmd_doctor(cfg)
    if args.command == "db":
        return cmd_db(cfg, args.action)
    if args.command == "run":
        return cmd_run(cfg, text=args.text, dry_run=args.dry_run, verbose=args.verbose)
    if args.command == "serve":
        return _not_yet("serve", "4")
    return _not_yet(str(args.command), "?")


if __name__ == "__main__":
    raise SystemExit(main())
