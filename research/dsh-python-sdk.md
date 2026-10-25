# DSH 的 Python 分发现状（实测于 PyPI，2026-10-25）

> 目的：回答"分发要不要带 dsh / 要不要带 Node"。
> 方法：直接读 PyPI JSON API 与官方文档站，记录版本、体积、依赖、原文措辞。
> 注意：**以下全部是"读到的"而不是"跑通的"**。能不能真的在干净 venv 里跑起来，见 `docs/65-distribution.md` §7 的清单。

---

## 1. 四个包名并存，版本号互不对应

| PyPI 包 | 最新版本 | 说明 |
|---|---|---|
| `deepseek-harness-sdk` | **0.2.1rc2**（2026-10-17） | Python SDK。依赖 `deepseek-harness-runtime-bin==0.2.1rc2` + `pydantic>=2.12`。wheel 仅 **18 KB** |
| `deepseek-harness-runtime-bin` | **0.2.1rc2**（2026-10-17） | 平台运行时：`dsh` 可执行体 + 闭源 Node 闭包。**只发 wheel** |
| `deepseek-harness-cli` | 0.5.0（搜索结果，未逐字段核对） | 与上面两个版本号不在同一序列 |
| `deepseek-harness` | 0.3.0（搜索结果，未逐字段核对） | 同上 |

**结论**：不要假设四个包版本同步。装之前先确认哪个是当前推荐入口，并把 `sdk` 与 `runtime-bin` 的 pin **成对**写死（SDK 已经用 `==` 锁了 runtime，别手动解耦）。

## 2. runtime-bin 的体积（实测 wheel size）

| 平台 | wheel | 字节 |
|---|---|---|
| Windows x64 (`win_amd64`) | `deepseek_harness_runtime_bin-0.2.1rc2-py3-none-win_amd64.whl` | **83,860,784（≈80 MB）** |
| Linux x64 (`manylinux_2_28_x86_64`) | 同上 | 94,615,763（≈90 MB） |
| Linux arm64 | 同上 | 96,833,408 |
| macOS arm64 | 同上 | 90,016,291 |
| macOS x64 | 同上 | 93,636,346 |

**没有 Windows arm64 轮子，也没有 Android 轮子。**

## 3. 原文措辞（关键三句）

- "It packages the normal `dsh` CLI and its closed Node dependency tree into a native executable, **so SDK use requires no system Node.js**."
- "The wheel installs a `dsh` console command and the `deepseek_harness_runtime` Python module. `dsh` forwards its arguments to the bundled executable and **requires a non-empty `DSH_HOME`; it never falls back to `~/.dsh`**."
- "Both carriers execute the same `dsh` grammar and shipped profiles, including the standalone `sdk-minimal` tree and the full `web` profile with its frontend assets."

## 4. 对 Mephisto 有直接影响的五条

1. **目标机不需要 Node**，也不需要在 PATH 上有 `dsh`。`pip install` 之后 `dsh` 就是那个 wheel 带的。
2. **`DSH_HOME` 必须显式**，且不回落 `~/.dsh` → 默认隔离，正好用来给 Mephisto 一个自己的 home。
3. **SDK 的 profile 必须含 JSON-RPC server 行**（`@deepseek-ai/dsh-sdk-app` 或 `dsh-sdk-jsonrpc-server`）。现在的 `headless` 模板没有 → 现有 `mailbot` profile 不能直接复用。
4. **`dsh plugin add` 仍需 `pnpm`**（"ordinary SDK/profile execution does not"）→ 能不用插件就不用。
5. **`DeepSeekHarness` 懒启动并复用 runtime 到 `close()`**，且提供 `resume_session(saved_id)`（"restores persisted history… sends no new input… Missing, unreadable, incompatible, or occupied sessions raise"）→ 比"每封信 spawn 一个 dsh"更好的续接语义。

## 5. 许可

- `deepseek-harness-sdk`：`license_expression = MIT`
- `runtime-bin`：MIT，wheel 内带 `THIRD_PARTY_NOTICES.md`
- 结论：**可以随包分发**，保留 notices 即可。

## 6. 来源

- <https://pypi.org/pypi/deepseek-harness-sdk/json>（访问 2026-10-25）
- <https://pypi.org/pypi/deepseek-harness-runtime-bin/0.2.1rc2/json>（访问 2026-10-25）
- <https://deepseek-harness.github.io/deepseek-harness/en/guide/python-sdk>（访问 2026-10-25）
- 本机事实：npm 全局 `dsh` 为 `0.2.0-rc.2`，Node `v24.20.0`，`~/.dsh` 下已有 `profiles/{mailbot,web}`。

## 7. 未验证

- 干净 venv 里 `pip install deepseek-harness-sdk` 后 `dsh --version` 是否可用（未装）。
- `sdk` 模板 + 自定义 patch 能否得到等价于现在 `mailbot` profile 的沙箱与权限行为。
- `runtime-bin` 内的会话格式版本是否与 npm `0.2.0-rc.2` 完全兼容（两者都写 v4，但未交叉验证）。
- Windows 上 `dsh` 控制台命令对 Ctrl+C / 退出码的转发行为（README 说"waits and forwards exit status"，未实测）。
