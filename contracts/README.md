# contracts · 四份契约

Mephisto 内部组件之间只靠这几份 JSON 契约说话。**改契约 = 改版本号**，不做隐式兼容。

| 文件 | 谁写 | 谁读 | 用途 |
|---|---|---|---|
| [`envelope.schema.json`](envelope.schema.json) | 传输适配器（收） | 路由、runner | 一封消息的统一形态，收发共用 |
| [`manifest.schema.json`](manifest.schema.json) | **Agent** | 执行器（actuator） | 任务交付：正文、附件、提醒、中途发信 |
| [`message.normalized.schema.json`](message.normalized.schema.json) | 渠道适配器（微信/QQ） | 清洗、摘要 | 聊天消息的统一形态 |
| [`reminder.schema.json`](reminder.schema.json) | Agent（调 skill） | 调度器 | 一条提醒的落库结构 |

校验放两处：**写入方自检**（早失败）+ **读取方强校验**（不信任上游）。两边用同一份 schema 文件，不各写一遍。

---

## 1. 两层"出口"，不要混

现行实现里 **agent 的出口** 是 `<任务目录>/outbox/manifest.json`。这一层**不改**：它已经在跑，有 333 个用例，`AGENTS.md` 里写清楚了。

需要新增的是**传输层的投递区**，它在执行器内部，agent 看不到：

```
                 agent 能看到的范围
                 ┌──────────────────────────────┐
<workspace>/     │ sessions/<sid>/              │
                 │   outbox/manifest.json  ←────┼── 交付契约（现有，不改）
                 │   outbox/汇总.xlsx            │
                 └──────────────┬───────────────┘
                                │ actuator 读取、校验、渲染
                                ▼
state/spool/                    │ 传输投递区（新增，agent 不可见）
├─ .tmp-<ULID>/                 │ 写一半的目录，发送器必须忽略
│  ├─ envelope.json             │ 出站信封（见 envelope.schema.json，direction=out）
│  ├─ body.md                   │ Markdown 源
│  ├─ body.html                 │ 可选
│  └─ attachments/…
├─ <ULID>/                      │ rename 后成为待发
├─ sent/<ULID>/                 │ 已发（含实际发出的 .eml）
└─ failed/<ULID>/ + error.json  │ 校验/发送失败
```

**为什么分两层**：

- Agent 侧只关心"我要回什么、带哪些文件"，用最简单的字段；
- 传输侧关心"怎么拼 MIME、怎么带线程头、怎么限额重试"，用结构化信封；
- 中间由执行器做一次转换。**Agent 永远不需要知道 Message-ID 怎么生成。**

如果让 Agent 直接写传输信封，它会写错线程头、写坏附件路径——那正是现有设计用 `manifest.json` 挡住的东西。

## 2. 投递区的四条规则

### 2.1 原子性

写入方先写 `state/spool/.tmp-<ULID>/`，全部写完、自校验通过后 `rename` 成 `state/spool/<ULID>/`。同盘 `rename` 是原子的，所以发送器**永远看不到写了一半的目录**。

### 2.2 互斥

发送器 `rename` 成 `.sending-<ULID>/` 来"抢占"。两个进程同时抢，只有一个能成功——**比文件锁便宜，也比状态文件可靠**。

### 2.3 幂等

`envelope.json` 里的 `idempotency_key`（建议 `manifest.taskId + 序号`）在 `state/mephisto.sqlite3` 里唯一。重复的投递直接移进 `sent/` 并标 `duplicate`，不重发。

### 2.4 过期即作废

摘要类消息带 `expires_at`（默认 6 小时）。过期直接移 `sent/` 并标 `expired`，**不发**——昨晚的群聊摘要今早发出没有价值，只会消耗额度和注意力。

## 3. 发送器的责任（Agent 的责任到此为止）

| 检查 | 不通过时 |
|---|---|
| `manifest.json` 通过 schema 校验 | 回一封"缺字段"给用户，任务标 `failed` |
| `files[]` 的每个路径解析后仍在任务目录内（无 `..`、无绝对路径、无符号链接逃逸） | 拒绝并把告警写进 `logs/audit.jsonl`（现有行为，保留） |
| 单文件 ≤ `maxAttachmentMB` | 不发附件，改在正文里给路径与大小（现有行为，保留） |
| 总大小 ≤ 传输上限（默认 7 MB，见 `10-transport-mail.md` §8） | 同上 |
| 目标 lane 当日额度未满 | 留队列，按优先级延后 |
| SMTP 成功 | 移 `sent/` 并存 `.eml` |
| SMTP 失败 | 指数退避重试 3 次；仍失败 → `failed/` + 通知 |

## 4. 失败怎么让人知道

`failed/` 里的东西**不能静默堆积**。daemon 每小时检查一次，有新增就往 `notify` lane 发一封汇总（每天最多一封）。如果连发信都坏了，就在下一次用户来信的回复末尾加一行"最近有 N 条消息没能发出"。

## 5. 为什么正文用 Markdown

- 可 diff、可被 Agent 直接改、可渲染成 HTML 或纯文本；
- 邮件必须同时有 `text/plain` 与 `text/html` 两个 part（纯文本 part 是反垃圾评分的一部分）；
- 让 Agent 写 HTML 会引入无穷的转义错误。

渲染规则固定在传输层：Markdown → 纯文本（去标记、保留列表与链接 URL）与 → HTML（内联极简样式，不用外部 CSS）。

## 6. 版本

所有 schema 都有 `"v": 1`。加字段是兼容变更；改字段语义、改必填性是破坏性变更，要 `v: 2` 并保留读旧版的能力至少一个版本。

**不要在 `v: 1` 上偷偷改语义。** 这个系统会跑很多年，唯一会咬人的就是这类隐性变更。
