# Mephisto · 设计评审与下一阶段方案

> 输入：你在 `E:\mephisto\mailbot` 已经跑通的第一阶段（Python 纯标准库、333 用例、两周真实日志），
> 以及 `E:\study\research\chatlog` 的微信/QQ 技术内核调研。
> 本文档集不是从零设计，是**在能跑的东西上定下一阶段的形状**，并回答你提出的六个问题。
>
> **当前实施状态（2026-10-25 更新）**：已决定 **邮件模块搁置、安卓客户端优先**。分步方案以
> [99 实施计划](docs/99-implementation-plan.md) 的 **v2** 为准；第一节关于邮件额度/路由/分发的结论仍然成立
> （那是设计结论，不是排期），但**排期与优先级以 v2 为准**。

---

## 快速开始（第 0 步已交付）

```powershell
tasks.cmd setup     # 自检：Python 版本、能否 import、doctor
tasks.cmd test      # 跑测试
tasks.cmd doctor    # 体检：Python / 目录 / DSH / 磁盘 / 时间 / 监听地址
```

零安装：包在仓库根的 `mephisto/`，从仓库根 `python -m mephisto doctor` 直接可用，运行时只用标准库。
（`tasks.cmd` 只是帮忙绕过 PowerShell 执行策略，不改系统设置。）

---

## 一、先给结论

| 你的问题 | 结论 | 详见 |
|---|---|---|
| 邮件发送上限是难题吗？注册多邮箱能解决吗 | **不是难题，多邮箱是错的解法。** 个人助理一天 20–50 封，免费档普遍 100–500/天；而限流是按**域和 IP**算的，同域多邮箱不增加任何额度，跨域多邮箱会毁掉 DMARC 对齐与线程聚合 | [10](docs/10-transport-mail.md) |
| 脚本语言怎么选，产物怎么便于迁移 | **Python 3.12+ 沿用，不换。** 纯标准库的 Python 是**依赖面最瘦**的方案（邮件/IMAP/MIME/SQLite/时区全在内置）；换语言只会把已实测的采集层重写一遍 | [60](docs/60-runtime.md) |
| 分发要带上 dsh 吗 | **要，但不用带 Node。** `deepseek-harness-runtime-bin` 已经把 `dsh` CLI + 它的 Node 闭包打成一个 **80 MB 的 wheel**（win_amd64 实测），所以**分发单元是一个 venv（约 120–150 MB），不是 exe**。Python 因此成了唯一生态，而不是"额外那个" | [65](docs/65-distribution.md) |
| 会话管理（主题分类）体验会不会很差 | **会，所以别让它做主路径。** 主路径是"点回复"（Message-ID 自带会话号）与"选地址"（Reply-To 一会话一地址）；分类器只做兜底，且兜底落到**代价最小的错误**——杂谈，不是并错会话 | [20](docs/20-sessions.md) |
| 工作区结构（共享 + 全会话完全权限） | **结构对，但要补两样**：写边界从"任务"上移到"工作区"（否则记忆无处可写）；共享区只追加 + 每小时 git 快照（这是"完全权限"的兜底） | [30](docs/30-workspace-memory.md) |
| 微信消息整理 | **能力照旧，换实现。** 技术来源仍是 WeFlow；停掉的是它的**运行时**（写死 2026-10-16 截止），它的数据层已经被提取成自持的 `wxread`。采集与总结解耦：采集常开、总结防抖、投递聚合 | [40](docs/40-chatlog.md) |
| QQ 消息整理 | 官方 Bot 首选（能读全量、合规、有实时推送；没有历史接口不是问题，因为我们自己就是归档）；NapCat 只在必须读非自己群时用，且要套只读网关 | [40](docs/40-chatlog.md) §9 |
| 安卓端要不要现在想 | **要，而且现在就想**。手机是客户端不是执行节点；有五件事现在定下来才不会返工（会话一等对象、`delivered_at`/`seen_at`/`e2ee` 可空、设备令牌表、`sink_ref`、`client_msg_id`）。另有一个今天就能拿到 80% 效果的招：**用 `.ics` 附件把提醒塞进系统日历**（v2 更新：安卓已是第一优先级；提醒改由 App 的本地精确闹钟承担，另加一条常驻连接做秒级到达，`.ics` 降为兜底方案） | [90](docs/90-android.md) |
| 定时提醒 | 不能交给模型等待，也不能用 DSH 的 `dsh-schedule`（它要求在 Web Host 里跑，会锁死分发形态）。自己的 `reminder`+`occurrence` 两表 + sink 抽象 | [50](docs/50-reminders.md) |
| DSH 到底扮演什么角色 | **执行器，不是分发形态。** 只依赖它三样：工具集、上下文压缩、模型适配。**绝不把状态存在它里面** | [70](docs/70-runner-and-cache.md) |
| 缓存能省多少，该怎么组织 prompt | **主战场是 0%→90%（月成本 ¥138 → ¥32），不是 90%→99%（只省 10 元）。** 头部逐字节冻结、`tools[]` 也算前缀、动态内容永远放最后；到 90% 之后该优化的是**回信长度**与错峰，不是命中率 | [70](docs/70-runner-and-cache.md) |

## 二、一句话架构

```
邮件（未来：自建信道 / App）
   │  Envelope（统一信封）
   ▼
路由 ── K0 会话号在 Message-ID 里 / K1 别名表 / K2 Reply-To 地址 / K3 标签 / K4 分类器兜底
   │
   ▼
Runner ── dsh --profile mailbot --session-id <id>（一次一进程，可超时、可杀）
   │
   ▼
工作区 ── sessions/<sid>/ + memory/ + shared/ + AGENTS.md（全权限，git 每小时快照）
   │
   ▼
outbox/manifest.json ── 唯一出口（agent 没有 SMTP 凭据）
   │
   ▼
发信脚本 ── 额度账本 + 幂等 + 线程头 + 重试 + 留档
```

三条铁律：**信道是适配器**、**出口只有投递区**、**状态在文件与 SQLite 里，不在框架里**。

## 三、已经做对、不要动的四件事

1. **内部 `threadId` 是主键**，邮箱地址只是别名 → 换邮箱不断线。
2. **DSH 会话是缓存不是账本** → 续接失败就开新会话重跑，有 `session.degrade`。
3. **agent 没有 SMTP 凭据** → 只能写 `manifest.json`。
4. **`manifest.json` 是唯一交付契约**，失败也必须写。

这四条是第一阶段最值钱的遗产，后面的改动全部建立在它们之上。

## 四、要改的三件事

| # | 改什么 | 为什么必须改 |
|---|---|---|
| 1 | 写边界：`jobs/<task>` → `workspace/` | 现在 agent 写不了 `context.md`，`AGENTS.md` 里只能写"写不了就算了"。没有跨会话记忆，"会话"就只是聊天记录 |
| 2 | 路由：标签 → Message-ID 自带号 + Reply-To 地址 | 你自己说的"敲标签太麻烦"；而且分类器不该做主路径 |
| 3 | 微信源：WeFlow 运行时 → wxread | 运行时已过期（2026-10-16），且会外发聊天正文、改写微信数据库。技术内核已经提取自持 |

## 五、文档地图

| 文档 | 内容 | 什么时候读 |
|---|---|---|
| [00 总览](docs/00-architecture.md) | 分层、进程、状态表、不做什么、与现有实现的逐层对照 | 先读 |
| [10 邮件传输](docs/10-transport-mail.md) | 额度实况、为什么不能池化、地址三层、线程五条硬结论、附件、无缝切换 | 要动发信/收信时 |
| [20 会话路由](docs/20-sessions.md) | K0–K4 五级路由、地址即会话、Message-ID 带号、分类器策略、修错命令、标签的正面回答 | **最该先看的一份** |
| [30 工作区与记忆](docs/30-workspace-memory.md) | 人格/世界模型/会话叙事三分、全权限的兜底、上下文预算、记忆分层与整理、检索不上向量 | 要动目录结构时 |
| [40 聊天记录](docs/40-chatlog.md) | 为什么不能用 WeFlow、采集/清洗/摘要/投递四段、防抖算法、接龙折叠、QQ 三路线、画像的边界 | 要做微信/QQ 时 |
| [50 提醒](docs/50-reminders.md) | 状态机、幂等、补触发、sink 抽象、安卓映射、静默失效的检测 | 要做提醒时 |
| [60 运行时](docs/60-runtime.md) | 语言选型与否决理由、"臃肿"的四个维度、分发四档、进程形态、"可迁移"的四层含义、反面清单 | 要装/要迁移/纠结语言时 |
| [65 分发](docs/65-distribution.md) | **"分发要不要带 dsh"的正面回答**：80 MB wheel 的实测数据、venv 作为分发单元、四个迁移障碍与解法、7 条待验证清单 | **准备换机器/打包之前** |
| [70 Runner 与缓存](docs/70-runner-and-cache.md) | DSH 的定位、该依赖/不该依赖、缓存三条规则、续接 vs 换代、多设备的硬结论 | **你插话问的那件事** |
| [80 路线](docs/80-roadmap.md) | 现状盘点、三件改动、P0–P5 分期与验收、不做清单、风险表 | 要排期时 |
| [90 安卓端](docs/90-android.md) | 手机=客户端、五条现在必须定的设计、`.ics` 的 80% 方案、权限与推送的硬约束、A0–A5 分期 | **要做 App / 要提醒变闹钟时** |
| [15 接入方式](docs/15-http-channel.md) | **校园网怎么出去**：为什么结构只能是"主动出站"、为什么选 Cloudflare Tunnel（以及它的两条代价）、心跳四态、断线续传、鉴权与限流、夜间断网的影响 | **要理解为什么这样选时** |
| [16 接入施工手册](docs/16-network-runbook.md) | **你手动执行的清单**：VPS+frp 与 Cloudflare Tunnel 两条完整路线（在哪买、怎么挑参数、配置文件全文、六条验收、故障表、回滚），数字全部来自官方文档 | **部署隧道时照着做** |
| [17 附件](docs/17-attachments.md) | **分片上传**：为什么单请求只能 4 MB（由 CF 125 秒超时反推）、单文件 64 MB、三端点协议、内容寻址存储、超限走工作区 `inbox/` | **做附件功能时** |
| [25 会话引擎](docs/25-session-engine.md) | 一轮的完整时序、上下文四段与预算、压缩算法与阈值、**插队（排队/打断）**、Runner 为什么用 CLI `--json` | **要动会话/上下文时** |
| [55 Agent 工具面](docs/55-agent-tools.md) | MCP（stdio）7 个工具、`remind_add` 的 schema 与幂等、从工具调用到手机闹钟的链路、profile 配置片段、env 清洗坑 | **要给 agent 加能力时** |
| [99 实施计划](docs/99-implementation-plan.md) | **待审阅的分步方案 v2（安卓优先、邮件搁置）**：S0–S13 共 14 步、每步验收命令与 git tag、`notice` 通知架构、工具链脚本、数据模型与失败模式 | **先读这份再决定要不要开工** |
| [contracts/](contracts/README.md) | Envelope / outbox manifest / 归一化消息 / 提醒 四份 JSON Schema + 投递区协议 | 要写代码时 |

## 六、参考件

| 目录 | 是什么 |
|---|---|
| [`refs/chatlog/`](refs/chatlog/) | 上一轮微信/QQ 技术内核调研的**全文副本**（含 `90-risks.md` 风险清单、`80-field-mapping.md` 字段对照） |
| [`refs/wxread/`](refs/wxread/) | 自持微信读取器的 README、核心事实 F1–F10、决策记录 D1–D6 |
| [`research/`](research/) | 本次新增的五份调研：[记忆管理](research/memory.md)、[邮件传输边界](research/email.md)、[邮件当聊天传输层](research/email-chat.md)、[缓存与多设备](research/caching-and-devices.md)、[DSH 的 Python 分发与体积](research/dsh-python-sdk.md) |
| [`contracts/`](contracts/) | 可以拿去直接写代码的 schema |

`refs/` 里的原件仍在 `E:\study\research\chatlog`（含 `evidence/` 19 MB 原始证据与 `snippets/` 复现脚本）；这里只复制了文本报告，避免把证据目录搬进来。

## 七、怎么用这套文档

- **只想知道"这事能不能做成"** → 读第一节的表，加上 [70](docs/70-runner-and-cache.md) §5（多设备）。
- **准备动手改代码** → [20](docs/20-sessions.md) + [30](docs/30-workspace-memory.md) + [80](docs/80-roadmap.md)，然后照着 [contracts/](contracts/) 写。
- **要决策某个技术点** → 每份文档都有"什么情况下该推翻"；先看那一节，别在没有新证据时反复。
- **要复用上一轮的调研** → `refs/chatlog/README.md` 的十条结论速览是最省时间的入口。

## 八、这份文档的边界

- 我**没有**跑通"发一封信 → 收到回复"的完整闭环（你的 README 也标着这一项未验证）；本文档里的邮件侧结论来自官方文档与社区记录，不是本机实测。
- 我没有改 `E:\mephisto\mailbot` 的任何文件，也没有在 `E:\study` 之外写东西。
- 文中的阈值（0.72 / 0.80 / 120s / 20min / 7MB / 200 行）都是**起点值**，全部标了"需要真实数据校准"。不要把它们当结论。
