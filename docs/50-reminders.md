# 50 · 定时提醒

> 提醒是 Mephisto 里**唯一由 Agent 主动发起**的能力。对话可以晚回、摘要可以晚发，提醒不行。
> 所以它的设计目标不是"能跑"，是**"不会静默失败"**。

---

## 1. 边界：提醒是脚本的，不是模型的

你把这一点说对了，而且这是关键：**Agent 只负责"理解意图并落库"，触发是脚本的事**。

| 谁 | 做什么 | 不做什么 |
|---|---|---|
| Agent（模型） | 把"下周三下午三点提醒我交表"解析成结构化字段；调 skill 落库；能查/能改/能删 | **不负责等待，不负责触发** |
| 调度器（脚本） | 到点触发、投递、记账、补触发 | 不理解自然语言 |

理由：模型进程会退出、会超时、会被杀掉。**把"等待"交给模型，等于让提醒依赖一次 LLM 调用的存活**——这必然不可靠。

## 2. 数据模型

```sql
CREATE TABLE reminder (
  id             TEXT PRIMARY KEY,          -- ulid
  owner          TEXT NOT NULL,             -- 'user' | 'system'
  kind           TEXT NOT NULL,             -- 'reminder' | 'job'（同一引擎，两类用途）
  title          TEXT NOT NULL,
  body           TEXT,
  due_at_utc     INTEGER NOT NULL,          -- 首次触发（epoch 秒）
  tz             TEXT NOT NULL,             -- 'Asia/Shanghai'
  rrule          TEXT,                      -- NULL = 一次性；子集见 §3
  origin_session TEXT,                      -- 在哪条会话里被创建的（见 §6）
  channels       TEXT NOT NULL,             -- JSON: ["mail"] / ["mail","alarm"]
  state          TEXT NOT NULL,             -- scheduled|fired|cancelled|missed
  created_at     INTEGER NOT NULL,
  fire_count     INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE occurrence (
  reminder_id    TEXT NOT NULL REFERENCES reminder(id),
  occur_utc      INTEGER NOT NULL,
  state          TEXT NOT NULL,             -- pending|claimed|sent|failed|skipped
  claimed_at     INTEGER,
  sent_at        INTEGER,
  attempts       INTEGER NOT NULL DEFAULT 0,
  last_error     TEXT,
  external_id    TEXT,                      -- 闹钟/日历侧的 id（见 §5）
  PRIMARY KEY (reminder_id, occur_utc)
);

CREATE TABLE sink_ref (
  reminder_id TEXT, sink TEXT, external_id TEXT,
  PRIMARY KEY (reminder_id, sink)
);
```

`occurrence` 的主键 `(reminder_id, occur_utc)` 是**幂等的全部**：重复调度插不进去，重试只改状态。不需要额外的"已发送"标志。

**为什么 `occurrence` 要和 `reminder` 分开**：重复提醒每次都是独立的一行，这样"发过没有""第几次"是数据而不是推断。补触发也只补没发的那些。

## 3. 重复规则：只做子集，不做 RFC 5545

完整 RRULE 有上百种组合，实现它要几千行且必然有 bug。个人使用只需要五种：

```
once
daily
weekly:MO,WE,FR
monthly:15          （每月 15 日）
every:2:week        （每两周）
```

语法固定、可读、可 grep。**遇到不支持的写法就报错并让 Agent 拆成多条一次性提醒**——宁可笨，不要默默算错。

DST 处理：重复提醒按**本地墙钟时间**算下一次（"每天 8 点"就是每天 8 点，不管时区偏移变没变），用 `zoneinfo` 计算。一次性提醒按绝对时间。这个区别要写进代码注释，很容易错。

## 4. 调度器

单线程循环，最小堆 + 睡到下一个到期点：

```
loop:
  next = min(pending occurrences).occur_utc
  if next is None: sleep(60); continue
  wait = next - now
  if wait > 0: sleep(min(wait, 60))       # 60s 上限，防止新插入的提醒要等
  claimed = claim_due(now)                 # UPDATE ... SET state='claimed' WHERE state='pending' AND occur_utc<=now
  for o in claimed: deliver(o)
```

关键点：

- **`claim_due` 是一条 SQL 的原子更新**，不是"查出来再发"。多进程/重启都不会重复发。
- **睡眠上限 60 秒**：新提醒可能在睡的时候被插入，不能睡到天亮。
- **交付失败退避**：`attempts` 1/2/3 次，间隔 1min/5min/30min，之后转 `failed` 并在下一次用户来信时提示"有 3 条提醒没发出去"。

## 5. 交付接收器（sink）

```python
class Sink(Protocol):
    def deliver(self, r: Reminder, o: Occurrence) -> str:
        """返回外部 id，用于后续修改/取消。失败抛异常。"""
    def cancel(self, external_id: str) -> None: ...
```

| sink | v1 | 说明 |
|---|---|---|
| `mail` | ✅ | 走投递区（`state/spool/`），不是直接 SMTP——**复用同一套额度、重试、留档** |
| `alarm` | 远期 | Android `AlarmManager`，可修改、可删除 |
| `calendar` | 远期 | `CalendarContract`，带提醒的日程 |

**这个抽象就是为了你现在提的那个未来需求**：把提醒升级成闹钟/日历，"改错了能删"这件事依赖 `external_id`。所以 `sink_ref` 表现在就要建，哪怕只有一个 sink。

## 6. 提醒邮件应该长什么样

三个决定：

1. **主题**：`[提醒] <标题> — <本地时间>`。带日期，用户在收件箱列表里就能看到是什么事。
2. **正文**：标题、时间（含时区）、原始指令（`你当时说：下周三下午三点提醒我交表`）、相关产物链接、一行操作提示（`回信 cancel 取消；snooze 1h 延后`）。
3. **回信怎么路由**：`origin_session`。

第 3 条值得展开。提醒的 Message-ID 会被登记到 `session_alias`，但**指向的是创建它的那条会话**，不是"提醒会话"。这样用户直接回复提醒邮件说"做了"，就落回原来的项目会话，上下文是完整的。

如果没有 `origin_session`（比如用户从 `m@` 直接说"提醒我喝水"），回信落杂谈。

## 7. Agent 侧的 skill 契约

```bash
mephisto remind add --title "交表" --at "2026-11-05T15:00" --tz Asia/Shanghai \
                    --body "..." --origin-session s-3f9a1c
mephisto remind list [--state scheduled] [--limit 20]
mephisto remind cancel <id>
mephisto remind snooze <id> --by 1h         # 生成新 occurrence，不改原定义
mephisto remind done <id>
```

自然语言解析在 Agent 侧，落库在脚本侧。**脚本永远不接受"明天下午"这种字符串**——它只要 ISO 时间 + tz。歧义在进入脚本之前就要消掉；如果 Agent 解析不了，它应该反问用户，而不是猜。

这条很重要：`--at` 是唯一入口，意味着时区错误、解析错误都发生在"有上下文、可以追问"的那一侧。

## 8. 失败模式

| 场景 | 会不会出问题 | 处理 |
|---|---|---|
| daemon 重启 | 不会 | 启动时 `claim_due(now)` 自动捞起所有过期未发的 |
| 机器关机 3 天 | 会 | 启动补触发，但**超过宽限期（默认 2h）的不再补发**，合并成一封"这 3 天错过的 5 条提醒" |
| 同一时刻 20 条 | 会（刷屏） | 合并为一封"今日 20 条提醒"；只有 `priority=high` 的单发 |
| 用户改了提醒时间 | 不会 | 改 `due_at_utc` 并删掉未发的 `occurrence`；已发的保留 |
| 用户取消后又想恢复 | 会（数据已删） | 取消是 `state=cancelled`，不删行 |
| 时区搞错 | 会 | `tz` 必填且落库；邮件正文里显式写出来（`15:00 (UTC+8)`）|
| 重复提醒算错 DST | 会 | 按墙钟时间算；单测覆盖 3 个 DST 切换日 |
| 邮件被发到垃圾箱 | 会（最隐蔽） | ①固定 From；②每周一次"心跳"检查：如果连续 7 天无任何入站，发一封到备选地址；③提醒邮件带 `X-Mephisto-Priority: high` |

最后一条是**唯一能发现"整条链路静默失效"的机制**，别省。

## 9. 安卓的映射（远期，但现在要留好接口）

| 现在 | 将来 |
|---|---|
| `channels: ["mail"]` | `["mail","alarm","calendar"]` |
| sink = `mail` | sink = `alarm`：`AlarmManager.setAlarmClock()`（不会被 Doze 削） |
| `sink_ref.external_id` | 闹钟/日历的 uri，用于 `cancel()` |
| `remind cancel` | 同时删闹钟与日历事件 |

要注意的现实约束：Android 12+ 的精确闹钟需要 `SCHEDULE_EXACT_ALARM` 权限；国产 ROM 会杀后台，`setAlarmClock` 是唯一比较可靠的方式。**这不是"到时候再说"的事**——现在 `sink_ref` 不建，到时候就没法"改错了删掉"。

## 10. 待定

- 宽限期 2h 是否合适：取决于关机频率，可以做成配置。
- 重复提醒的"跳过已过期"策略：现在补发最近一次，不补全部（"每天 8 点"关了一周，不该收到 7 条）。
- 是否需要"提醒依赖"（完成 A 才提醒 B）——v1 不做，但 `reminder` 表留 `depends_on` 字段的成本是零。
