# 55 · Agent 工具面（MCP）

> **决定（2026-10-25）**：agent 与 Mephisto 之间的唯一接口是 **MCP（stdio）**。
> agent **碰不到手机**——它只写服务端的真值，手机自己去同步。

---

## 1. 三层别混

| 层 | 是什么 | 我们做几个 |
|---|---|---|
| **Profile** | dsh 的配置栈：能用哪些工具、什么权限、哪个模型 | 1 个 |
| **MCP tool** | 真正的函数调用，有 schema、有超时 | 1 个 server，6–7 个 tool |
| **Skill** | 给模型看的说明书：怎么用这些工具、输出格式 | 1 份 `SKILL.md` |

**skill 不承载业务逻辑**。它只写「什么时候该调 `remind_add`」「摘要要几段」「不要主动催用户」这类约定。逻辑在 tool 里。

## 2. v1 的工具清单

| 工具 | 作用 | 幂等键 |
|---|---|---|
| `remind_add` | 新建提醒 | `origin`（见 §3） |
| `remind_update` | 改时间/标题 | notice id |
| `remind_cancel` | 取消 | notice id |
| `remind_list` | 查未来 7 天 | — |
| `chatlog_search` | 在已归档的微信/QQ 里搜 | — |
| `chatlog_digest` | 手动对某会话出摘要（你会用到「总结一下 X 群」） | (conv, window) |
| `memory_append` | 往 `memory/` 追加一条（只追加，不改旧的） | — |

**不做**的工具：发邮件、发 QQ/微信消息、删文件、花钱。**agent 没有任何对外发送能力**——这是安全边界，不是偷懒。

## 3. `remind_add` 的形状

```json
{
  "name": "remind_add",
  "description": "在指定时刻提醒用户。用于用户明确要求『几点提醒我』的场合；不要用它表达你自己的想法。",
  "input_schema": {
    "type": "object",
    "required": ["title", "at_local"],
    "properties": {
      "title":    {"type": "string", "maxLength": 80},
      "body":     {"type": "string", "maxLength": 1000},
      "at_local": {"type": "string", "description": "本地时刻，如 2026-10-26 15:00；也接受 明天15:00 / 周三下午3点"},
      "tz":       {"type": "string", "default": "Asia/Shanghai"},
      "repeat":   {"type": "string", "enum": ["none", "daily", "weekly", "monthly"]},
      "byday":    {"type": "string", "description": "weekly 时用，如 MO,WE"},
      "session_id": {"type": "string", "description": "来源会话，用于回跳"}
    }
  }
}
```

四条规则：

1. **时区由服务端算**。模型说「明天下午三点」，它填 `at_local` + `tz`，**转换在服务端做**。不让模型算 UTC——它会算错。
2. **幂等键**：`origin = reminder:<id>@<occur_utc>`。同一个提醒的同一时刻，无论模型调几次、job 重试几次，只产生一条 notice。
3. **返回值要能让模型复述**：
   ```json
   {"ok": true, "id": "n_01J...", "due_local": "2026-10-26 15:00", "note": "已加入提醒，到点会在手机上响"}
   ```
   模型据此回一句人话。**不让模型自己编时间**。
4. **过去时刻**拒绝（返回 `ok:false, error:"in_past"`），避免「提醒我昨天要交表」。

## 4. 从工具调用到手机闹钟

```
模型调用 mcp__mephisto__remind_add
        │
        ▼
mephisto mcp（stdio 子进程）
        │  调本地 API（127.0.0.1 + 内部 token）
        ▼
服务端：解析时区 → 写 reminder(规则) + notice(due_utc) → 返回 id
        │
        ▼
手机下次同步 GET /notices → 看到未来那条
        │
        ▼
setAlarmClock(10-26 15:00)，requestCode 由 notice.id 派生
        │
        ▼
到点响（电脑关机也响）→ 用户点「知道了」→ POST /notices/{id}/state
```

**为什么绕一圈**：手机可能不在线、可能关机，但提醒必须响。所以真值在服务端，手机是执行器。

## 5. Profile 配置片段

```yaml
- id: mcp-mephisto
  name: '@deepseek-ai/dsh-mcp-client'
  config:
    serverName: mephisto          # 工具名变成 mcp__mephisto__remind_add
    transport: stdio
    command: python
    args: ['-m', 'mephisto', 'mcp', '--config', '.mephisto/mcp.json']
    cwd: '.mephisto'               # 相对工作区
    toolCallTimeoutMs: 120000      # 默认 60s 不够 chatlog_digest
    failOnStartupError: true       # 起不来就报错，别静默降级
```

三个坑：

| 坑 | 事实 | 对策 |
|---|---|---|
| **环境变量被清洗** | stdio 子进程的环境会删掉匹配 `KEY\|PASSWORD\|SECRET\|TOKEN` 的变量，以及所有 `DSH_*` | 配置走 `--config <path>` 文件，**不要用环境变量传 token** |
| **会先起一个探测进程** | 「stdio 协商会先启动临时探测进程，再启动实际服务进程」 | `mephisto mcp` 必须秒起、无副作用、不能在建索引或扫库 |
| **工具名固定** | `mcp__<serverName>__<tool>` | `serverName` 一旦发布不要改，否则历史会话里的工具名对不上 |

## 6. 工具怎么落地（两种部署形态，取第一种）

| 形态 | 做法 | 结论 |
|---|---|---|
| **stdio（采用）** | 每轮 dsh 拉起 `python -m mephisto mcp`，它再调 127.0.0.1 的 API | 无端口、无额外鉴权、进程随任务生命周期 |
| streamable-http | MCP 服务挂在我们的 HTTP 端口上 | 以后如果有常驻需求再改，配置一行 |

**为什么 stdio 的子进程还要走 HTTP**：SQLite 单写者。让 `mcp` 进程直接写库会和主进程抢锁；走 API 则所有写都在一个进程里发生。

## 7. 不做

| 不做 | 理由 |
|---|---|
| 写 DSH 插件来暴露工具 | 需要 pnpm + patch 加载本地包，把分发绑死在 DSH 内部 API |
| 让 agent 直接操作手机 | 手机不在线时提醒就失效，与设计目标冲突 |
| 给 agent 发消息的能力 | 越界；需要发送的一律走审批（后续 A4） |
| 工具超过 10 个 | 工具越多模型选错越多；宁可组合（`remind_list` 里带状态筛选） |

## 8. 验收（S6 结束时）

```powershell
# 1. MCP server 能被 dsh 发现
dsh --profile mephisto --dump-config | findstr mcp-mephisto
# 2. 一句自然语言 → 服务端出现一条 notice
python -m mephisto run "明天下午三点提醒我交表"
curl -H "Authorization: Bearer $T" http://127.0.0.1:8787/api/v1/notices
# 3. 同一句话连跑三次 → notice 只有一条（幂等）
# 4. 说「提醒我昨天交表」→ 工具返回 in_past，模型回一句人话而不是崩溃
# 5. 拔掉服务端 → 工具调用失败但 dsh 正常收尾（不卡死）
```
