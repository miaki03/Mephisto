# 00 · 总览：分层、进程、状态

> 本文只回答「系统由哪几块组成、它们靠什么契约说话」。
> 具体的邮件/会话/记忆/聊天记录/提醒分别见 `10`–`60` 号文档。
> 标记约定沿用调研报告：`[确认-实测]` / `[确认-文档]` / `[判断]` / `[未验证]`。

---

## 1. 一句话定位

Mephisto 是一个**无界面常驻的个人 Agent**：用户通过邮件（未来是自建信道）跟它说话，它在一个共享工作区里干活，把产物投递到一个约定目录，由脚本负责送达。

**Agent 从来不"发邮件"。** 它只会往 `outbox/` 里写一个目录。发信是另一个进程的事。这条边界是整个设计里最值钱的一条，理由见 §2.2。

### 1.1 与现有实现的关系

`E:\mephisto\mailbot` 已经是一个能跑的第一阶段：Python 纯标准库 19 个模块、333 个用例、DSH 会话能续接、从 2026-10-09 起有连续的运行日志。

**所以这份文档不是从零设计，是在它之上定下一阶段的形状。** 逐层对照：

| 层 | 现有实现 | 本文档的结论 |
|---|---|---|
| L5 呈现 | 邮件（IMAP/SMTP） | **保持** |
| L4 传输 | `channels/in_email.py`、`out_email.py`（`kind` 可扩展） | **保持**，补 Reply-To 会话地址与 Message-ID 自带会话号 |
| L3 路由 | `router.py` + `threads.py`：`[T:名称]` 标签 + `aliases.json`（Message-ID→threadId） | **加强**：K0（ID 自带号）优先，标签降为 K3；补分类器兜底 |
| L2 运行 | `runner.py` 调 `dsh --profile mailbot`，`sessionMode: continue` 复用会话 | **保持**，"会话只是缓存"的判断已经写对了 |
| L1 能力 | `commands.py`（`/帮助` 等斜杠指令）、薄插件四工具、`manifest.json` 的 `reminders`/`sends` | **保持**，补 chatlog skill |
| L0 底座 | `state/*.json`（registry/aliases/reminders/jobs）+ JSONL 日志 | **改**：状态从散装 JSON 收敛到 SQLite；工作区加 `memory/` 与 git 快照 |

**已经做对、不要动的四件事**（这是第一阶段最值钱的遗产）：

1. **内部 `threadId` 是主键，邮箱地址只是别名** —— 所以换邮箱/换账号不断线（`threads.py` 文件头注释）。
2. **DSH 会话是缓存不是账本** —— 续接失败就开新会话重跑，任务不判失败，有 `session.degrade`。
3. **agent 没有 SMTP 凭据** —— 只能写 `manifest.json`。
4. **`manifest.json` 是唯一交付契约** —— 失败也必须写（`status: failed` + `notes`）。

**要改的三件事**：工作区粒度（任务→会话，见 `30`）、路由主键（标签→ID/地址，见 `20`）、第二阶段的微信源（WeFlow 已失效，见 `40`）。

## 2. 三条铁律

### 2.1 铁律一：信道是适配器，不是框架

Agent 内核只认识**信封（Envelope）**，不认识邮件。邮件只是 `transport=mail` 的一个实现。将来换自建信道、换定制 App，换的是适配器，内核不动。

判断标准：**如果把邮件换成微信服务号，需要改的东西是否只在一个 `transports/` 目录里？** 只要答案是否定的，就是设计漏了。

### 2.2 铁律二：出站只走投递区，Agent 不持有发信凭据

Agent 进程的环境变量里**没有 SMTP 密码**。它唯一的出口是写一个清单文件：

```
<会话目录>/outbox/manifest.json      ← Agent 写这个（正文、附件清单、提醒）
        │  由 actuator 读取并校验
        ▼
state/spool/<ulid>/envelope.json     ← 传输层写这个（线程头、收件人、附件实体）
```

- 好处一：**凭据不出宿主进程**。Agent 被提示注入、跑飞、删库，也拿不到邮箱密码。
- 好处二：**发送可重试、可审计、可限流**。重试逻辑只有一处；额度账本只有一处。
- 好处三：**产物形态统一**。附件、正文、收件人是一个原子单元，不会出现"邮件发了但附件没带上"。

**为什么不让 Agent 直接写信封**：生成 `Message-ID`、拼 `In-Reply-To`/`References`、校验附件路径、算额度——这四件事它每一件都会写错。清单文件只有七八个字段，信封有几十个，**难度不同的问题不该交给同一个角色**。

### 2.3 铁律三：所有状态在 SQLite + 文件里，不在任何框架的内存里

会话、水位、额度、提醒、路由决策、已发消息，全部落盘。进程重启 = 重新读盘，不丢上下文。

**推论**：不依赖 harness 的会话恢复能力。即使换一个 Agent 运行时（甚至换模型厂商），`session.md + memory/ + sqlite` 就能重建对话。这一点直接决定了「可迁移」。

## 3. 分层

```
┌──────────────────────────────────────────────────────────────┐
│ L5  呈现     邮件（v1） · 自建信道 · 安卓 App（远期）          │
├──────────────────────────────────────────────────────────────┤
│ L4  传输适配 transports/                                      │
│      mail_imap_smtp · (gmail_api) · (graph) · (jmap) · (http) │
│      职责：收发、去重、线程头、额度、重试                       │
├──────────────────────────────────────────────────────────────┤
│ L3  路由     router/                                          │
│      References → 地址 → 主题标记 → 分类器 → 兜底             │
│      职责：inbound 落到哪个 session                            │
├──────────────────────────────────────────────────────────────┤
│ L2  运行     runner/                                          │
│      把 (session, envelope) 拼成一次 Agent 调用；限并发        │
├──────────────────────────────────────────────────────────────┤
│ L1  能力     skills/                                          │
│      chatlog · reminder · files · memory · web                │
│      形态：CLI 子命令 + 一个 SKILL.md（人/模型都能读）          │
├──────────────────────────────────────────────────────────────┤
│ L0  底座     state/  workspace/                               │
│      SQLite（结构化状态） + Markdown（记忆与叙事） + git（版本）│
└──────────────────────────────────────────────────────────────┘
```

**只有 L4 知道邮件存在。** L1 的能力全部是「给我参数，返回 JSON」，与信道无关，所以同一个 `chatlog` 既能被邮件触发，也能被定时任务触发，将来还能被 App 的按钮触发。

## 4. 一次往返

```
用户发信
   │
   ▼
[mail-watch]  IMAP IDLE / 轮询  ── 存原始 .eml + 解析
   │            UIDVALIDITY+UID 水位；Message-ID 去重；自环拦截
   ▼
state/inbox/<msg_id>/envelope.json
   │
   ▼
[router]  ── K0 Message-ID 自带会话号？ → 直接得 session_id
   │        K1 别名表命中？            → 直接得 session_id
   │        K2 命中会话地址？          → 直接得 session_id
   │        K3 主题标签？              → 按标签查/建
   │        都不是 → K4 分类器         → existing / new / chatter
   ▼
[session_ctx]  组装上下文：AGENTS.md + session.md 尾部 + MEMORY.md 索引 + 本封信封
   │
   ▼
[runner]  dsh --profile mailbot --session-id <id>（一次一进程，可超时、可杀）
   │       Agent 干活：读文件、调 skill、写产物
   ▼
<任务目录>/outbox/manifest.json   ← Agent 的唯一出口（交付契约）
   │
   ▼
[actuator]  校验 manifest → 渲染正文 → 落 state/spool/<ulid>/
   │
   ▼
[mail-send]  校验信封 → 幂等去重 → 加线程头 → 额度账本 → SMTP → 归档
   │
   ▼
spool/sent/ 或 spool/failed/（failed 进重试；超过 N 次转人工提醒）
```

关键点：**入站和出站是两条独立流水线**，中间只靠 SQLite 和文件系统耦合。任何一段挂了，另一段继续跑。

另外注意这里**两层出口**：Agent 只写 `manifest.json`（简单、稳定、它写不错），传输层才生成带线程头的信封（复杂、易错、只写一处）。详见 `contracts/README.md` §1。

## 5. 进程

| 进程 | 形态 | 职责 | 挂了会怎样 |
|---|---|---|---|
| `mephisto daemon` | 常驻单进程（多线程） | 收信 watcher、发信 watcher、提醒调度、聊天采集 | 全部停摆；由计划任务拉起 |
| `mephisto run` | 一次性 | 处理一封信（由 daemon fork 或队列调度） | 该封信卡在 `queued` |
| `mephisto <skill>` | 一次性 CLI | Agent 调用，返回 JSON | Agent 重试或降级 |

**为什么 daemon 是一个进程而不是四个服务**：个人系统，SQLite 单写者，锁和额度账本天然串行；四个进程会让"谁持有锁"变成新的 bug 源。代价是单点，用 Windows 计划任务「登录时启动 + 失败重启」兜住即可。

**Agent 运行是独立进程**（`dsh --profile headless`）：这样 Agent 崩溃/死循环不会拖死 daemon，daemon 能给它设超时、杀进程、重试。

## 6. 目录布局（workspace）

```
workspace/
├─ AGENTS.md                # 宪法：身份、边界、写作规范、工具约定（人可改，Agent 只读）
├─ MEMORY.md                # 记忆索引（≤200 行，人工与整理任务共同维护）
├─ memory/
│  ├─ facts/                # 稳定事实，一主题一文件
│  ├─ people/               # 人的画像（受 §05 的隐私约束）
│  ├─ projects/             # 项目状态
│  ├─ journal/YYYY-MM.md    # 追加式流水
│  └─ inbox/                # 待整理候选（Agent 只能说"这条值得记"）
├─ sessions/<sid>/
│  ├─ session.md            # 会话叙事（追加 + 定期压缩）
│  ├─ context.md            # 本会话的长期记忆（人可读，Agent 可写）
│  ├─ notes/
│  ├─ artifacts/            # 本会话产物
│  ├─ inbox/                # 本会话收到的附件
│  ├─ outbox/               # 交付区：manifest.json + 产物（Agent 唯一出口）
│  └─ transcript/           # 落盘的信封副本（可重建）
├─ shared/                  # 跨会话复用
├─ state/                   # 不进 git
│  ├─ mephisto.sqlite3
│  ├─ spool/                # 传输投递区：<ulid>/ + sent/ + failed/
│  ├─ files/                # 附件块、原始 eml
│  └─ logs/
└─ tools/                   # 脚本入口（软链到 mephisto 包，或就是包）
```

命名一律 ASCII、无空格、小写：**这是为了跨平台迁移时不用改脚本**。

## 7. 状态（SQLite 表）

| 表 | 作用 | 关键约束 |
|---|---|---|
| `mailbox` | 逻辑地址 ↔ 后端账号 | 逻辑地址是主键，后端可换 |
| `mail_in` | 入站信封 | `message_id` 唯一（去重的根） |
| `mail_out` | 出站信封 | `idempotency_key` 唯一 |
| `delivery` | 每次投递尝试 | 与 `mail_out` 一对多，含 `attempt/status/error` |
| `quota_ledger` | 每账号每日已发数 | `(mailbox, date)` 唯一，发送前原子自增 |
| `session` | 会话 | `id/slug/title/state/created/last_active` |
| `session_alias` | 地址、主题标记、Message-ID 链 | 三种键都能查到 `session_id` |
| `route_decision` | 每次路由的输入与结果 | 用于复盘分类器，必须留 `confidence` 与 `evidence` |
| `chat_conv` / `chat_msg` / `chat_digest` | 聊天记录与摘要 | `chat_msg.uid` 唯一（跨分片稳定） |
| `watch` | 监听哪些会话、什么策略 | 含防抖参数与静默时段 |
| `cursor` | 各通道的拉取水位 | 含 `uidvalidity`，变了就全量重扫 |
| `reminder` / `occurrence` | 提醒与每次触发 | `(reminder_id, occurrence_ts)` 唯一 |

**所有"唯一"都写进 DDL**，不靠应用层检查。幂等是数据库的事。

## 8. 明确不做的事

| 不做 | 原因 |
|---|---|
| 不用 WeFlow 的内置 Agent | 它会把聊天正文发给第三方端点（`90-risks.md` A3），且自带遥测 |
| 不装反撤回触发器 | 会改写微信数据库（`90-risks.md` A4），与只读前提冲突 |
| 不让 Agent 直接持有 SMTP/IMAP 凭据 | 见 §2.2 |
| 不把会话状态只存在邮件头里 | 换信道就丢；邮件头是"线索"，不是"状态" |
| 不做"每条消息触发一次 Agent" | 成本与噪声都不可接受，见 `40-chatlog.md` |
| 不追求拟人化人格 | 稳定角色 + 明确边界 > 人设；见 `30-workspace-memory.md` |

## 9. 关键取舍一览

| 选择 | 取了什么 | 放弃了什么 | 何时该推翻 |
|---|---|---|---|
| 信封抽象 | 换信道只改适配器 | 多一层转换代码 | 永远不推翻 |
| outbox 投递区 | 凭据隔离、可重试、可审计 | 一次额外落盘 | Agent 与发送方必须同进程时 |
| SQLite 单写者 | 简单、事务、幂等 | 多机并发 | 要多端同时写 |
| daemon 单进程 | 锁简单 | 单点故障 | 有多个用户/多账号高并发 |
| 邮件优先 | 零客户端成本、天然异步 | 延迟、额度、体验割裂 | 定制 App 上线后 |
| Python | 复用已实测的采集代码、标准库够用 | 打包体积、安卓端不可用 | 手机端要本地跑 Agent |
