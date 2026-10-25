# 65 · 分发：分发单元是环境，不是程序

> 你的问题：**"我们分发也是需要带上 dsh 的不是吗？"**
> 是。而且这恰恰把上一轮关于语言的争论终结了——但结论和你想的方向相反：**不用带 Node，因为 dsh 自己就是 Python 包。**

---

## 1. 直接回答

| 问题 | 答案 |
|---|---|
| 分发要带 dsh 吗 | **要**。没有 dsh 就没有 agent，Mephisto 只剩一个转发邮件的壳 |
| 要带 Node 吗 | **不要**。`deepseek-harness-runtime-bin` 已经把 `dsh` CLI 和它的 Node 依赖树打成一个**原生可执行体**塞进 wheel |
| 那分发单元是什么 | **一个 Python venv**（或 uv 管理的环境），不是"一个 exe"，也不是"Node + Python 两套" |

实测数据（PyPI，2026-10-17 发布，MIT）：

| 包 | 作用 | 体积 |
|---|---|---|
| `deepseek-harness-sdk` 0.2.1rc2 | Python 侧 SDK（`DeepSeekHarness`） | **18 KB** |
| `deepseek-harness-runtime-bin` 0.2.1rc2（win_amd64） | **`dsh` 命令 + 闭源 Node 依赖树，打包成原生 exe** | **80 MB**（Linux x64 ≈ 90 MB，macOS ≈ 86–89 MB） |
| `pydantic` >=2.12 | SDK 依赖 | 数 MB |
| Mephisto（我们自己） | 服务层 | 几百 KB + 标准库 |

官方措辞（`runtime-bin` 的 README）："It packages the normal `dsh` CLI and its closed Node dependency tree into a native executable, **so SDK use requires no system Node.js**."

**所以"带上 dsh"的成本是 80 MB 的一个 pip 依赖，不是你维护的第二套运行时。**

> 诚实补一句：Node 当然还在里面运行，只是它由 DeepSeek 打包、签名、随 wheel 升级，**不再由你安装、不再由你排障**。这正是"托管依赖"的含义。

## 2. 这件事改变了什么

上一轮我按"Node 是既有依赖、Python 是额外运行时"来算账。现在要改：

| 之前的算法 | 现在的事实 |
|---|---|
| 部署 = Node(~100 MB) + DSH + Python(~30 MB) + 服务 | 部署 = **Python(~30 MB) + 80 MB 的 dsh wheel + 服务** |
| "Python 多一个运行时"是真实成本 | **不存在了**，Node 不需要 |
| 选 Python 是"没有更好选择" | 选 Python 是"**它正好是 DSH 官方分发进去的那个生态**" |

而且它还解决了另外两个隐形问题：

1. **`dsh` 的版本被钉死了**。`deepseek-harness-sdk==0.2.1rc2` 通过 `requires_dist: deepseek-harness-runtime-bin==0.2.1rc2` 精确锁版本——不会出现"用户机器上那个全局 dsh 是另一个版本"这种最难查的问题。
2. **`DSH_HOME` 必须显式给**。wheel 里的 `dsh` 拒绝空 `DSH_HOME`、**永远不回落 `~/.dsh`**。这正好是我们要的隔离（见 §4.3）。

## 3. 目标形态

```
E:\mephisto\
├─ .venv\                        ← 部署单元（uv 管理，锁版本）
│  ├─ deepseek_harness\          ← SDK
│  ├─ deepseek_harness_runtime\  ← runtime-bin：80 MB，内含 dsh 可执行体
│  └─ mephisto\                  ← 我们的服务
├─ pyproject.toml / uv.lock      ← 唯一的"安装清单"
├─ config.toml                   ← 5 个字段
├─ secrets\                      ← 邮件凭据（不进 git）
├─ state\                        ← SQLite / spool / 日志
└─ dsh-home\                     ← 专用 DSH_HOME（profile / 会话 / 凭据）
```

运行：

```
mephisto daemon
  └─ Runner = DeepSeekHarness(dsh_home=".../dsh-home", profile="mephisto",
                              patches=(".../mephisto.patch.yml",))
       └─ 懒启动、进程常驻、resume_session 续接
```

**比现在更好的一点**：`DeepSeekHarness` 是"懒启动 + 复用同一个 runtime 直到 close"，也就是**常驻子进程**，而不是每封信 spawn 一次。冷启动成本从"每封一次"变成"启动一次"。

## 4. 四个迁移障碍（每个都有解法）

### 4.1 profile 必须有 JSON-RPC server 行

`DeepSeekHarness` 能选的 profile，**必须包含 `@deepseek-ai/dsh-sdk-app` 或另一个 `@deepseek-ai/dsh-sdk-jsonrpc-server` 行**，否则启动即失败。

你现在的 `mailbot` profile 是 `--from-default-profile headless` 建的——**headless 没有 JSON-RPC server 行，不能直接用**。

解法：profile 模板从 `headless` 换成 **`sdk`**（它挂在 `dsh-base` 上，工具集接近"全量"：read/write/edit、web、subagent 都在），然后把你原来的 `cordis.patch.yml` 照搬过去（sandbox-policy、fs-sandbox、approval、permission、agent-default-model 那几段）。

**这一步是这次迁移的主要工作量，大概半天。** 好处是顺手把"profile 资产"从"写进 `$DSH_HOME`"改成"作为文件放在仓库里、启动时用 `patches=` 传进去"——**profile 变成代码，而不是机器状态**。这一条直接改善了可迁移性。

### 4.2 `dsh plugin add` 还需要 pnpm

`runtime-bin` 的 README 明说：外部插件管理要 `pnpm` 在 PATH 上，普通运行不需要。

**建议：干脆不要插件。** 你的 README 已经写了"不跑 `bootstrap.py` 也能用（agent 靠 `manifest.json` 交付）"——那就把 `dsh-mail-shim` 降级为可选增强，v1 只保留 `manifest.json`。收益是三重的：

- 不需要 pnpm；
- 不需要在目标机重建 `link:E:/mephisto/dsh-mail-shim` 这个**绝对路径软链**（换机器必断的东西）；
- 少一个"状态藏在 Node 侧"的组件（`70-runner-and-cache.md` §2 那条 ❌）。

代价：agent 失去"任务中途立刻发信"的能力（`sends`），只剩结束时交付。**这个代价可以接受**：中途发信本来就是低频需求，而它换掉的是三个部署期故障源。

### 4.3 `DSH_HOME` 从"系统目录"改成"部署目录"

现在凭据在 `~/.dsh/.credentials.yaml`，profile 在 `~/.dsh/profiles/mailbot`。wheel 版 `dsh` 根本不读 `~/.dsh`。

两个选择：

| 选择 | 做法 | 适合 |
|---|---|---|
| **A 用部署自己的 home** | `DSH_HOME=<部署>/dsh-home`，登录一次生成凭据 | 推荐。整个部署自包含，拷走就能跑 |
| B 继续共用 `~/.dsh` | 显式 `DSH_HOME=%USERPROFILE%\.dsh` | 想复用现有登录态与 Web 端 |

**选 A**，但要接受：换机器时要重新登录一次（或者把 `.credentials.yaml` 一起安全搬运——它是密钥，别进 git、别进压缩包发给别人）。

### 4.4 不要同机混装两个 dsh

npm 全局的 `dsh` 是 `0.2.0-rc.2`，wheel 里的是 `0.2.1rc2`。两套 profile、两套会话格式（都是 v4 但现在能对上，将来未必）。

**规则：一台机器只保留一种 `dsh` 来源，并在 `pyproject.toml` 里钉死版本。** 迁移期可以并存，但只用 wheel 那一个跑 Mephisto。

## 5. 三种"分发"场景，三种答案

| 场景 | 该做什么 | 成本 |
|---|---|---|
| **自己换机器** | `uv sync`（装 80 MB wheel）+ 拷 `state/` + 登录一次 | 一条命令，5 分钟 |
| **给别人用** | 打成 venv 目录（`uv pip install --target`）或 Docker 镜像 | 不需要再要求对方装 Node——这是最大的简化 |
| **多设备** | **不复制执行器**。一台常开机器跑，其他设备是客户端（`70-runner-and-cache.md` §5） | 零 |

第三条最重要：**"多设备"不等于"多份部署"。** 一旦接受"一个家节点 + 多客户端"，分发问题在大多数情况下直接消失。

## 6. 体积、更新与许可

| 项 | 说明 |
|---|---|
| 体积 | 部署约 120–150 MB（Python 30 + wheel 80 + 其他）。**这就是"带上 dsh"的真实价格**，比"Node + 两套包管理"低一个档 |
| 更新 | 80 MB 每次版本升级都要重下。**钉版本、显式升级**，不要追最新 |
| 升级流程 | 改 `pyproject.toml` 的 pin → `uv sync` → `mephisto doctor` → 跑一次端到端 → 才切生产。**别让 uv 自动升** |
| 许可 | MIT，wheel 里带 `THIRD_PARTY_NOTICES.md`。**可以随包分发，保留 notices 即可** |
| 平台 | Windows **只有 x64**（没有 arm64）、Linux x64/arm64、macOS arm64/x64。**没有 Android 轮子** → 手机只能是客户端（与 `90-android.md` 一致） |

## 7. 动手前的验证清单（按顺序，每步都能独立失败）

1. 干净 venv 里 `uv pip install deepseek-harness-sdk`，**不装 Node**，确认 `dsh --version` 有输出。
2. `DSH_HOME=<临时目录> dsh --profile sdk --dump-default-config` 能跑通（说明 profile 初始化正常）。
3. 用 `sdk` 模板 + 我们的 patch 建 `mephisto` profile，`--dump-config` 里能看到 `sandbox-policy` / `fs-sandbox` / `agent-default-model`。
4. `DeepSeekHarness(dsh_home=..., profile="mephisto", patches=(...)).run("只回 ok")` 能返回。
5. `resume_session(<上一封的 id>)` 能续接，且模型记得上一轮。
6. 确认会话文件落在 `DSH_HOME` 下（而不是 `~/.dsh`）。
7. 确认与现有 `~/.dsh` 的 profile / 凭据**没有互相污染**（这一条要专门验，因为它最容易"看起来正常"）。

**这七条我一条都没跑过**（本机没装 SDK，且不应在此改动你的运行环境）。它们标注为"待验证"，请在确认第 1 条通过之后再改代码。

## 8. 结论

| 问题 | 结论 |
|---|---|
| 分发要带 dsh 吗 | 要，但它是一个 **80 MB 的 pip 依赖**，不是你要维护的第二套运行时 |
| 要带 Node 吗 | 不要。Node 在 wheel 里，由 DeepSeek 管 |
| 那 Python 的选择还成立吗 | **比之前更成立**：Python 成了唯一的生态，而不是"额外的那个" |
| 分发单元是什么 | **一个 venv**。不是 exe，不是安装器，不是容器（容器只是它的一种打包方式） |
| 最大的迁移成本 | profile 从 `headless` 模板换到 `sdk` 模板（半天），以及**决定不要插件**（省掉 pnpm、绝对路径软链、Node 侧状态三个坑） |
| 最该先做的一件事 | 验证清单第 1 条：干净 venv 里能不能不装 Node 跑起 `dsh` |
