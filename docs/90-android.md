# 90 · 安卓端：先把"不返工"的设计做完

> 你说把安卓端产物先提上设计列表。这份文档就干两件事：
> **① 现在必须定下什么，才不至于到时候推倒重来；② 哪些事其实不用等 App，今天就能拿到 80% 效果。**
>
> **v2 修订（2026-10-25）**：安卓已从"远期"变成**第一优先级**，实施顺序见 [99 实施计划](99-implementation-plan.md) v2。
> 本文 §3 的五条现在必须定的设计**仍然全部有效，且已进 schema**。
> 已被 v2 取代的有两处：**§4.1 的 `.ics`**（App 直接用 `ACTION_INSERT` 零权限写日历，`.ics` 降为兜底）、
> **§6.1 的推送结论**（不是"不要常驻"，而是"常驻用 `remoteMessaging` 类型 + 本地闹钟兜底"，见 99 §5）。
> **注意**：6 小时/24 小时的前台服务上限只适用于 `dataSync` 与 `mediaProcessing`，消息类应用不受此限。

---

## 1. 定位：手机是客户端，不是第二个执行节点

这条必须先定，因为它决定后面全部接口的形状。

| 方案 | 可行性 | 结论 |
|---|---|---|
| 手机也跑服务 + DSH | ❌ Android 12+ 的 phantom killer 全系统合计 **32 个子进程 / 5 分钟 CPU 检查**，超限静默 `SIGKILL`；Doze 下连接被系统直接切掉 | 不做 |
| 手机只做客户端，一台常开机器/软路由/VPS 执行 | ✅ | **选这个** |
| 手机端跑一个 DSH 会话，与电脑共享 | ❌ harness 会话是本地文件且绑 cwd，跨设备无同步 | 不做（见 `70-runner-and-cache.md` §5） |

**"执行在哪"与"你在哪"解耦**，这是双栈（邮件 + App）能成立的前提。

## 2. 它到底解决什么问题

不是为了"有个 App 好看"。它解决四件现在解决不了的事，按价值排序：

| # | 现在的问题 | App 能解决的方式 |
|---|---|---|
| 1 | **提醒只是邮件**，会被淹没在收件箱里 | 真闹钟（`AlarmManager.setAlarmClock`）+ 系统日历事件，可改可删 |
| 2 | **审批等于没有**：现在 `approval: never`，需要审批的动作被自动拒绝 | 弹窗审批，把"需要人确认"的能力还回来——这是能力上限的实质提升 |
| 3 | **没有推送**：Android 上邮件没有可靠推送，Doze 下 15–20 分钟才醒一次 | App 内长连/厂商通道，秒级 |
| 4 | **多轮交互笨**：一封封邮件来回，引用折叠、附件找不着 | 对话框、历史检索、附件面板 |

第 2 条最容易被低估：**它不是一个体验优化，是解锁一整类任务。** 现在"发邮件给外部、删文件、花钱"这类动作全部被硬拒；有了审批，它们变成"可以问一下"。

## 3. 现在必须定下的五件事

这五条一旦定了，App 什么时候做都不返工；没定，做到一半就要改数据模型。

### 3.1 会话是一等对象，不是"邮件的副产品"

客户端必须能 `GET /sessions/<id>` 打开一条会话，而不是靠邮件头去猜。这意味着：

- `session.id` 是稳定主键（你已经做对了：`threadId` 是主键，邮箱只是别名）；
- 邮件头（References / Reply-To）只是**输入适配器**的一部分，不是模型的一部分；
- 数据结构里不能有"必须靠解析主题才能找到会话"的路径。

### 3.2 `delivered_at` / `seen_at` / `e2ee` 必须可空

邮件没有送达回执、没有已读、没有前向保密。如果这三个字段现在写成 `NOT NULL`，将来接自建信道时全部要迁移。

```jsonc
"delivery": { "delivered_at": null, "seen_at": null },   // null = 未知，不是"未送达"
"e2ee": null                                             // null = 无加密；将来填算法标识
```

**用 `null` 表示"未知"，不要用 `false`/`0` 表示"没有"。** 这是整个 schema 里最容易错、也最贵的一处。

### 3.3 设备与令牌是独立的一组表

```sql
CREATE TABLE device (
  id          TEXT PRIMARY KEY,     -- 客户端生成的稳定 id
  name        TEXT,                 -- "Pixel 8" / "家里的笔记本"
  kind        TEXT,                 -- 'android' | 'desktop' | 'mail'
  created_at  INTEGER, revoked_at INTEGER
);
CREATE TABLE device_token (
  token_hash  TEXT PRIMARY KEY, device_id TEXT, expires_at INTEGER,
  last_seen   INTEGER
);
CREATE TABLE device_cursor (
  device_id TEXT, stream TEXT, cursor TEXT, PRIMARY KEY (device_id, stream)
);
```

三个要点：

- **令牌存 hash 不存明文**，可吊销、可过期；
- **每设备独立游标**：手机和笔记本各自同步到自己需要的位置，互不影响；
- **`kind='mail'` 也当成一个设备**：这样"邮件"不是特殊路径，而是同一个抽象里的一个客户端。将来加第二个邮件账号也不用改模型。

### 3.4 提醒的闹钟/日历必须在**客户端**实现

这是最容易设计错的一点：**Python 服务端在 Android 上设不了闹钟。**

正确的分工：

```
服务端 reminder（真值） ──到点──> 推一条 "reminder_due" 事件
                                        │
                                        ▼
                              手机 App 收到后：
                                AlarmManager.setAlarmClock()   → 返回 pendingIntent id
                                CalendarContract 写事件         → 返回 event uri
                                        │
                                        ▼
                              POST /reminders/<id>/sink_ref    → 服务端记下 external_id
```

所以 `sink_ref` 表现在就要建（`50-reminders.md` §5），存的是**客户端侧**的 id。用户"改错了要删"，删的是客户端那个闹钟，服务端负责把 `sink_ref` 传下去。

### 3.5 客户端发的东西要带 `client_msg_id`

手机可能弱网、可能重试。客户端生成的 `client_msg_id` 是幂等键，服务端 `UNIQUE` 约束去重。**没有它，弱网下必然重复执行任务**——而任务是"跑一次 DSH"，重复的代价是钱。

## 4. 不用等 App：今天就能拿到的 80%

> **v2：本节降为兜底方案。** App 已是第一优先级，且能零权限写日历（`ACTION_INSERT`），所以不必借邮件塞 `.ics`。
> 保留本节的理由：App 崩了、换手机、或还没做完的那段时间，`.ics` 仍是最省事的降级路径。

### 4.1 用 `.ics` 附件把提醒塞进系统日历

服务端发提醒邮件时，附一个 `invite.ics`：

```ical
BEGIN:VCALENDAR
VERSION:2.0
METHOD:PUBLISH
BEGIN:VEVENT
UID:<reminder-id>@mephisto.example.com
DTSTAMP:20261105T070000Z
DTSTART:20261105T150000
SUMMARY:交表
DESCRIPTION:下周三下午三点提醒我交表
BEGIN:VALARM
TRIGGER:PT0M
ACTION:DISPLAY
DESCRIPTION:交表
END:VALARM
END:VEVENT
END:VCALENDAR
```

收件人点一下"添加到日历"，就有了**带闹钟的系统日程**——可改、可删、不依赖任何 App、不需要权限申请。

**代价接近零，覆盖率却很高。** 建议直接排进 P3，而不是等 App。

局限（要写在心里）：`.ics` 是**单向快照**——用户在服务端改了时间，手机上的旧事件不会自动更新（除非用 `METHOD:REQUEST` + `SEQUENCE` + `ORGANIZER` 走 iTIP 协议，那是另一个量级的复杂度）。所以 v1 的承诺只能是"点一下就进日历"，不是"双向同步"。

### 4.2 邮件里的"回复即命令"已经够用

`/提醒`、`/线`、`/清空` 这些斜杠指令在手机上已经能用，只是不够顺。App 的第一版甚至可以不实现这些——因为邮件仍然在。

## 5. 客户端工程清单

做的时候按这个顺序，每步都能独立交付：

| 阶段 | 内容 | 依赖 |
|---|---|---|
| **A0** | 设计冻结：§3 的五条 + schema 字段 | 本文 |
| **A1** | 只读客户端：会话列表、会话详情、附件下载 | 服务端 HTTP API |
| **A2** | 双向：发消息（`client_msg_id`）、已读、离线草稿 | 增量同步 + 游标 |
| **A3** | 提醒：`AlarmManager` + `CalendarContract` + `sink_ref` 回传 | 权限（见 §6） |
| **A4** | 审批：`ask` 策略的弹窗、超时、拒绝 | 服务端 approval 接口 |
| **A5** | 推送与后台 | 见 §6，这是最脏的一块 |

### 5.1 同步协议

不要设计新的 RPC。用最简单的一套：

```
GET  /api/v1/sessions?since=<cursor>          → 变化了的会话（含最后一条）
GET  /api/v1/sessions/<id>/messages?since=…   → 增量消息
POST /api/v1/sessions/<id>/messages           → 发消息 {client_msg_id, text, attachments}
GET  /api/v1/events                           → SSE 长连（前台时用）
```

**只在打开 App 时全量对齐、在后台不做增量**，是 v1 最省心的策略——因为后台同步正是 Android 上最容易出问题的地方。

### 5.2 离线

客户端缓存最近 N 天（N 默认 30）；服务端是唯一真值；离线只能读 + 写草稿，不能触发任务。**离线派活不在 v1 范围内**（见 `70-runner-and-cache.md` §5.3）。

## 6. 安卓的两个硬约束（必须提前知道）

### 6.1 国产 ROM 没有 GMS，FCM 不可用

这一条会决定推送方案，而它经常被忽略：

| 方案 | 可行性 |
|---|---|
| FCM | ❌ 国内设备多数无 Google 服务；即使有，也需要 Google 账号与网络 |
| 厂商推送（小米/华为/OPPO/vivo） | ⚠️ 各自 SDK、各自上架审核；个人开发者成本高 |
| 自建长连（MQTT/WebSocket + 前台服务） | ✅ 可行，但要常驻通知、耗电，且仍会被部分 ROM 杀 |
| 只在打开 App 时同步 | ✅ 最可靠，代价是"没有实时提醒" |

**v2 结论：常驻可以做，但要选对类型。** 用 `remoteMessaging` 前台服务（无时长上限）承载长连，配一条无声常驻通知；开机、精确闹钟、免电池优化三条都是官方允许的后台拉起路径。同时**保留本地闹钟**：PC 关机时它照样响，这是常驻给不了的。真正的能力缺口只有一个——App 被彻底杀掉后的离线推送，那需要厂商通道（见 [99](99-implementation-plan.md) §5.1–§5.2）。

### 6.2 权限

| 能力 | 权限 | 备注 |
|---|---|---|
| 精确闹钟 | `SCHEDULE_EXACT_ALARM`（Android 12+，用户可撤） | 或用 `USE_EXACT_ALARM`（仅限闹钟类应用，上架审核严） |
| 写日历 | `WRITE_CALENDAR` | 运行时申请 |
| 前台服务 | `FOREGROUND_SERVICE` + 类型声明 | Android 14 起要声明具体类型 |
| 通知 | `POST_NOTIFICATIONS`（13+） | |

`setAlarmClock()` 是唯一在 Doze 下仍可靠的方式，代价是状态栏会显示闹钟图标——这是它的"税"，接受即可。

## 7. 复用与不重复造

| 需要的东西 | 能复用吗 |
|---|---|
| 邮件模式下的 IMAP 同步内核 | 有 **Delta Chat core**（Rust，FFI/JSON-RPC，三端在用）——但只在"客户端直连 IMAP"时才需要 |
| 自建 HTTP 信道的客户端 | 没有现成的；自己写，量不大 |
| 对话框 UI | 自写；`Compose` + `Paging` 足够 |

**关键选择：客户端走 HTTP（服务端 API），不要走 IMAP。**

理由：IMAP 直连意味着手机要存凭据、要处理 IDLE 被杀、要自己解析 MIME、要处理"发件箱"语义——那是把服务端已经解决的问题在手机上重做一遍。Delta Chat 之所以直连 IMAP，是因为它**没有服务器**；你有。

## 8. 双栈，不是替换

邮件永远保留，理由三条：

1. **网络效应**：任何人都能给你发信，这是自建信道永远给不了的。
2. **兜底**：服务端挂了、App 崩了、换手机了，邮件还在。
3. **归档与举证**：邮件天然在两端各留一份副本。

所以 `transports/mail_*` 永远不删。App 上线后，形态是"同一批 Envelope，两个前端"——这正是 `00-architecture.md` 里那条"信道是适配器"的兑现。

## 9. 现在就要写进 schema 的字段（清单）

做完这七条，App 阶段基本不会返工：

| # | 字段/表 | 位置 |
|---|---|---|
| 1 | `delivery.delivered_at` / `seen_at`，可空 | `envelope.schema.json` |
| 2 | `e2ee`，可空 | `envelope.schema.json` |
| 3 | `client_msg_id`（幂等） | 入站消息表 |
| 4 | `device` / `device_token` / `device_cursor` | SQLite |
| 5 | `sink_ref(reminder_id, sink, external_id)` | SQLite |
| 6 | `transport` 枚举留 `http` | `envelope.schema.json`（已有） |
| 7 | `session.id` 是稳定主键、不依赖邮件头 | 已满足（现行 `threadId`） |

前两条我今天就已经写进 `contracts/envelope.schema.json` 了吗——**没有**，那份 schema 里 `delivery` 与 `e2ee` 都还没加。**这是本文给出的第一批改动**，见 §10。

## 10. 落地顺序建议

1. **现在（P3 之前）**：把 §9 的 7 条补进 schema 与表；`.ics` 邮件附件做掉。
2. **P4 之后**：A1 只读客户端（这时 HTTP API 已经被 App 之外的东西验证过——比如你自己用 curl 或用浏览器看）。
3. **A3 提醒**与 A1 可以并行，因为它只依赖 §3.4 的 `sink_ref` 协议。
4. **A4 审批**建议最后做：它要求服务端把"卡住的任务"持久化，那是唯一一处需要新状态机的地方。

## 11. 不做什么

| 不做 | 理由 |
|---|---|
| iOS | 你自己定的，且省掉一半适配成本 |
| 手机本地执行 Agent | §1，Android 会静默杀进程 |
| 客户端直连 IMAP | §7，等于把服务端的问题重做一遍 |
| 离线派活 | 需要本地模型或 API 直连 + 可合并队列，收益/成本不划算 |
| 双向日历同步（iTIP） | 复杂度高一个量级，`.ics` 单向快照够用 |
| 自建推送服务器上架各厂商通道 | 个人项目撑不起审核与维护成本，先用长连 + 系统日历兜底 |
