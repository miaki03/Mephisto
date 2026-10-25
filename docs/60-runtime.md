# 60 · 语言、分发与可迁移性

> 你的三个问题：**为什么选 Python？分发会不会不方便？全部用 Python 会不会太臃肿？**
> 第三个问题最值得答，因为"臃肿"这个词其实指四件不同的事，混在一起就会得出错误结论。

---

## 1. 结论（分三层，别混）

| 层 | 结论 | 依据 |
|---|---|---|
| **语言** | 服务层继续 Python，不重写 | 它只有几千行 I/O 胶水；重写不减少任何东西，只损失 333 个用例 |
| **分发** | 按目标分四档，不要一个方案打天下 | §4。给自己用是"复制目录"，给别人用才需要单文件 exe |
| **臃肿** | **纯标准库的 Python 是依赖面最瘦的方案** | §2。真正臃肿的是"引一堆第三方库的 Python"，不是 Python 本身 |

先说一件容易被忽略的事：**这套东西的体量不由语言决定，由它要干多少事决定。**

你要做的是"拉起 DSH 会话"——不是自己实现 harness。那么服务层的全部工作就是：收信、解析、路由、拼 prompt、起子进程、读 `manifest.json`、发信、调度提醒、存状态。**这些是几千行 I/O 胶水，任何语言写出来都是几千行。** 换语言不会让它变小，只会让它重新长一遍。

真正大的是采集层（微信解密/解码，1000+ 行已实测）——那已经是 Python，而且没有换的理由。

## 2. "臃肿"到底指什么

拆成四个可测的维度：

| 维度 | 含义 | Python（纯标准库） | Node/TS | Go |
|---|---|---|---|---|
| **依赖面** | 有多少第三方包、多少传递依赖 | **0**（邮件/IMAP/SQLite/MIME 全在标准库）；仅微信解密要 `cryptography` | SMTP/IMAP/MIME 要 `nodemailer` + `imapflow` + `mailparser`，传递依赖约几十个 | SQLite 要 CGO 或第三方；MIME 要第三方 |
| **运行时** | 目标机要多装什么 | Python 3.12+（约 30 MB 量级） | Node（**你本来就有**，因为 DSH 要它） | 无（单二进制） |
| **运行足迹** | 常驻内存、启动 | 约 30–60 MB RSS；冷启 50 ms 量级 | 通常更高一档 | 最低 |
| **产物体积** | 交付给别人的东西 | 源码几百 KB + 解释器 | 源码 + `node_modules`（几十 MB）或打包后更大 | 单文件 10–20 MB |

**关键判断**：你说"臃肿"，直觉指向的是第 2 项（多装一个 Python）。但前一项恰恰相反——**Node 方案会把 MIME 与 IMAP 交给三个 npm 包，依赖面反而变大**。

而"能不能少装一个运行时"，只有在**目标机不是你自己的机器**时才是问题。你是给自己搭一个常驻助手，机器上已经有 Python 3.14（`refs/wxread/core-facts.md` 的环境行）。这一项的边际成本是零。

### 2.1 那什么情况下 Python 真的会臃肿

三种，都不是你现在的情况：

| 情况 | 会臃肿成什么样 |
|---|---|
| 引了 ORM、Web 框架、任务队列、SDK | `SQLAlchemy + FastAPI + Celery + openai` 起步 100+ 个包，且每个都要跟着升 |
| 用 PyInstaller 打包给非技术用户 | 单文件 15–30 MB、首启要解包、杀软误报率不低 |
| 需要嵌入到别的应用里（比如安卓 App） | Python 在 Android 上只能靠 Chaquopy/Termux，属于"能跑但不该跑" |

**你现在这条路线（19 个模块、零第三方、自带 `yamlmini`）恰好是最瘦的那种。** 这是过去两周攒下来的资产，不是负担。

## 3. 为什么不是 Node（把最强的那个理由也说清楚）

反对 Node 的最强理由不是性能，是**邮件解析**。

| 项 | Python | Node |
|---|---|---|
| MIME 解析 | 标准库 `email`，二十年战场验证，编码/嵌套附件/畸形头都覆盖 | 第三方；常见 bug 集中在编码与嵌套 multipart |
| IMAP | 标准库 `imaplib`（简陋但可靠） | `imapflow`（好，但是依赖） |
| SMTP | 标准库 `smtplib` | `nodemailer` |
| 时区/日历 | 标准库 `zoneinfo` + 手写 RRULE 子集 | 要 `luxon`/`date-fns` |
| 子进程管理 | `subprocess` | `child_process` |
| SQLite | 标准库 | `node:sqlite` 还年轻 |

**"一个运行时"这个理由是真的**（DSH 就在 Node 上），但它的收益只在"我要直接 import DSH 的内部模块"时才兑现——而我们刚刚决定**不依赖 DSH 内部**（`70-runner-and-cache.md` §2）。既然只走 `dsh` 命令行，用哪个语言起子进程完全等价。

所以：**为了"省一个运行时"去重写服务层，是拿确定性换便利性。**

## 4. 分发：分发单元是 venv，不是 exe

> **这一节的最初版本算错了。** 我按"Node 是既有依赖、Python 是额外运行时"来比较，但 `deepseek-harness-runtime-bin` 已经把 `dsh` 与它的 Node 依赖树打成了一个 80 MB 的 wheel——**目标机不需要装 Node**。详细数据与迁移步骤见 [`65-distribution.md`](65-distribution.md)。

修正后的四档：

| 档 | 做法 | 目标机需要 | 体积量级 | 代价 |
|---|---|---|---|---|
| **A 源码 + venv** | 复制目录 → `uv sync`（会装上 80 MB 的 dsh wheel） | Python 3.12+ | ~120–150 MB | 目标机要装 Python（**给自己用就到这一档为止**） |
| **B uv 托管工具链** | `uv run --python 3.14 -m mephisto daemon` | **无**（uv 自己下解释器） | 同 A + uv 30 MB | 首次要联网；离线部署要预置 |
| **C 单文件 exe** | PyInstaller / Nuitka | 无 | 15–30 MB **+ 仍需 dsh** | **这条路不再成立**：它能打进去的只有 Python 部分，dsh 的 80 MB 原生体打不进去 |
| **D 容器** | Dockerfile（Linux VPS） | 无 | 镜像 200 MB+ | Windows 主机上跑不了（除非 WSL） |

**建议路径**：

1. **现在：A**，但把 `requirements` 从"系统 dsh"改成"`deepseek-harness-sdk`"。这一步顺带解决了版本漂移。
2. **换机器 / 换到一台没 Python 的 Windows：B**。`uv` 顺手解决"Python 版本绑定"。
3. **给不懂技术的人：D 或"托管"**。C 已经被 dsh 的体积排除。
4. **要搬到 VPS 常驻：D**。

> 需要实测确认的：`uv python install` 在离线/受限网络下的行为、它装出来的解释器是否含 `compression.zstd`（3.14 才有）、以及干净 venv 里不装 Node 能否跑起 `dsh`。**三条都未验证**，先跑 [`65`](65-distribution.md) §7 的清单。

## 5. 如果一定要换，换成什么

不劝你"永远别换"。但先记住：[`65`](65-distribution.md) 之后，**"只有一个运行时"这个理由已经消失了**——不换也能只有一个生态（Python）。所以下面的路线只按"别的收益"来评估。

### 5.1 路线一：混合——Python 采集 + TS 服务

```
chatlog（Python，子进程 CLI）      ← 已有的 1000+ 行，不动
服务层（TS/Node）                  ← 收信、路由、会话、提醒、HTTP
```

**它还成立，但收益变小了**：原来选它的理由是"只有一个常驻运行时（Node，DSH 本来就要）"，现在这个理由不成立——dsh 以 wheel 形式进 Python，Node 反而不需要了。
剩下的好处只有"你更熟 TS"。代价是服务层重写（19 个模块 / 333 用例 → 2–3 周）。

### 5.2 路线二：Go 单二进制

好处：真正的零依赖交付、内存最低。
代价：全部重写，且 MIME 解析要自己写或引第三方；**而且它带不了 dsh**——你仍然要为一个 Go 二进制额外部署 Python 或一个 80 MB 运行时。
**所以这条路现在更不划算。**

### 5.3 路线三：不换语言，换边界

把"分发"问题从"打包"变成"部署"：服务跑在一台常开的机器上（家里的旧笔记本 / VPS），其他设备全是客户端。这样**你永远不需要分发服务端**。

这条路线的隐含前提与你的安卓计划一致——见 `90-android.md`。

## 6. 决策表：什么信号出现时该动

| 信号 | 动作 |
|---|---|
| 要给非技术用户用 | 先试 C（单文件 exe），不够再谈重写 |
| 目标机装不了 Python 且不能联网 | Go 单二进制（路线二），或路线三（远程部署） |
| 你要在服务层长期做功能迭代，且更熟 TS | 路线一（混合），在动 P1 之前做，别等状态长起来 |
| 要在手机上**本地执行** Agent | 换 Kotlin，重写的是"呈现 + 本地提醒"，不是采集 |
| 采集层被微信升级打死 | 换的是"取密钥"那一步，不是语言 |

**没有"Python 太臃肿"这一条。** 如果哪天觉得慢或大，先量一下：`mephisto status` 的内存、启动时间、`pip list` 的包数。数据会告诉你是不是语言的问题——大概率不是。

## 7. 进程形态

```
mephisto daemon                      # 常驻，一个进程，多线程
├─ thread: mail-watch       IMAP IDLE（或轮询）
├─ thread: mail-send        轮询 spool/（2s 足够，inotify 在 Windows 不可靠）
├─ thread: scheduler        提醒
├─ thread: chat-sync        每 60s 采集
├─ thread: chat-flush       防抖状态机
└─ thread: job-runner       消费 agent 任务队列（并发 1–2）

mephisto run <task_id>               # 一次性，由 daemon spawn
    └─ 内部调 dsh --profile mailbot --session-id <id>

mephisto <skill> ...                 # 一次性 CLI，Agent 调用
```

**为什么 daemon 是一个进程**：SQLite 单写者、额度账本要原子、提醒要唯一 claim。拆成四个服务就要处理四个进程间的锁——把简单问题变复杂。
**为什么 Agent 是独立进程**：模型可能死循环、可能跑十分钟、可能 OOM。隔离才能"设超时、杀进程树、重试"。

## 8. 依赖清单（目标是一张纸）

| 用途 | 依赖 | 必需 |
|---|---|---|
| HTTP / JSON / 时间 / SQLite / 进程 / MIME | 标准库 | — |
| 微信解密 | `cryptography` | 是（AES-256-CBC） |
| zstd（Python < 3.14） | `zstandard` | 否（3.14 有 `compression.zstd`） |
| 中文分词（若将来上 FTS） | `jieba` | 否 |
| 模型调用 | 标准库 `urllib` 直接打 HTTP | —（不引 SDK） |

**明确不引入**：ORM、Web 框架、任务队列、APScheduler、pydantic、click/typer。

理由不是"讨厌库"，是这些每一个都能用 50 行标准库替代，而它们的升级会打断一个跑了三年的系统。**个人系统的最大成本是维护，不是开发。**

## 9. "可迁移"的四层

### 9.1 换机器

```powershell
git clone <repo> mephisto
python -m venv .venv; .venv\Scripts\pip install -e .
copy config.example.toml config.toml     # 改 5 个字段
copy state\mephisto.sqlite3 ...          # 或从备份
python -m mephisto doctor
```

硬约束：代码零绝对路径（`grep -rn "E:\\\|C:\\" mephisto/` 必须为空）、一切路径从 `MEPHISTO_HOME` 推导、无全局安装状态、数据库单文件、密钥与代码分离、全 UTF-8 显式指定。

### 9.2 换 OS

Windows → Linux 的障碍只有两处：DPAPI（解密微信 passphrase）与计划任务。做成平台适配器即可。DPAPI 在 Linux 上无解——但那本来就只在"读微信库"时需要，而微信只跑在 Windows/macOS。

### 9.3 换 Agent 运行时

**Agent 运行时是一个接口，不是一个依赖。**

```python
class Runner(Protocol):
    def run(self, *, cwd: Path, task: str, session_id: str | None,
            timeout_s: int) -> RunResult: ...
```

你已经决定了：**不自研 harness，拉起 DSH 会话**。这个决定在架构上的含义是——服务层永远不需要模型调用代码、工具循环、上下文压缩，它只负责"把正确的 prompt 交给一个子进程，把结果读回来"。这让服务层保持在几千行，也让它换起来便宜。

**关键：不依赖 DSH 的会话恢复。** 每次运行的上下文由 `session.md` + `memory/` 拼出来。固定 `--session-id` 只是省事（见 `70-runner-and-cache.md` §4.1）。

### 9.4 换信道

见 `10-transport-mail.md`。只强调一句：**产物格式要选"十年后还能打开"的**——Markdown、JSON、CSV、纯文本、PNG/PDF。

## 10. 配置

```toml
[mail]
address   = "m@example.com"
smtp      = { host = "...", port = 465, user = "...", secret = "mail.smtp" }
imap      = { host = "...", port = 993, user = "...", secret = "mail.imap" }

[runner]
kind = "dsh";  profile = "mailbot";  timeout_min = 10;  concurrency = 1

[remind]
grace_hours = 2
```

密钥不写在配置里，写 `.secrets/*.key` 或环境变量，配置只放**引用名**。这样 `config.toml` 可以进 git。

## 11. 可观测性

个人系统不需要 Prometheus，但需要能回答"为什么没动静"。

| 输出 | 内容 |
|---|---|
| `state/logs/mephisto.log` | JSON Lines，每条带 `trace_id` |
| `state/logs/audit.jsonl` | 只记改变外部世界的动作：发信、调模型、写 spool |
| `mephisto status` | 各线程心跳、队列深度、今日额度、待发提醒 |
| `mephisto doctor` | 配置、密钥、数据库、SMTP/IMAP 连通性、工作区权限 |

`trace_id` 从一封信用进来到回信出，贯穿所有日志。**没有它，排障就是猜。**

## 12. 反面清单（别做）

- ❌ 用 APScheduler/Celery 做提醒 —— 一个 `heapq` + 一条 SQL 就够
- ❌ 用 SQLAlchemy —— 你需要的是 `ON CONFLICT DO NOTHING`，ORM 会把它藏起来
- ❌ 为"将来可能的多用户"做抽象 —— 这个系统只有一个用户
- ❌ 为了"少一个运行时"现在重写服务层 —— 契约还没被真实流量验证过，重写会把不确定性放大
- ❌ 用 PyInstaller 打包之后再在上面叠自动更新 —— 单文件 + 自更新是最容易把自己锁死的组合
