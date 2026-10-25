# 25 · 会话引擎：上下文、压缩、插队、Runner

> **本次决定（2026-10-25）**：
> ① Runner = `dsh --profile <ours> --json`，**每轮一个子进程**，取消 = 杀进程；
> ② 会话连续性 = **每轮新会话 + 我们自己装配上下文**（不用 `--session-id`）；
> ③ 会话内**同一时刻只允许一个 job**，新消息默认排队，可手动打断。

---

## 1. 一轮的完整时序

```
手机 POST /sessions/{id}/messages {client_msg_id, text}
  │
  ├─ 幂等：client_msg_id 已存在 → 直接返回旧消息，不再跑
  ├─ 落库：message(direction=in, seq=k)
  ├─ 抢锁：INSERT job(session_id, state='running')  ← 唯一索引挡住并发
  │        └─ 冲突（已有 running）→ 按设置：排队 / 拒绝并提示
  │
  ├─ SSE 推 job{state=running}
  ├─ 装配上下文（§3）→ 拼成 prompt
  ├─ subprocess: dsh --profile mephisto --json   ← prompt 走 stdin
  │     ├─ NDJSON 逐行读：status / text / thinking / tool_call / tool_result
  │     ├─ 每条写 event_log(seq++) 并发 SSE
  │     └─ 5 秒一次探活（进程还在吗）
  │
  ├─ 收到 final 事件 → message(direction=out) 落库 → job.state='done'
  ├─ 退出码非 0 或超时 → job.state='failed' + notice(kind=system)
  └─ 释放锁 → 若队列有货，立刻起下一轮（把排队的消息合并成一条 prompt）
```

**两条硬约束写在 DDL 里，不靠代码自觉**：

```sql
CREATE UNIQUE INDEX job_one_running ON job(session_id) WHERE state = 'running';
```

## 2. 为什么是「每轮新会话」

| 维度 | 新会话（采用） | `--session-id` 续接 |
|---|---|---|
| 历史归谁 | 我们（`message` 表 + `session.md`） | dsh 自己的会话日志 |
| 上下文满了 | 我们按 §4 的规则压 | dsh 自己压，**我们不知道压掉了什么** |
| 出错面 | 进程失败就重试，无状态 | 会话被删 / cwd 变了 / preset 换了 / 格式升级 → **直接拒绝运行** |
| 可调试 | `--dry-run` 能逐字节看 prompt | 看不到 |
| 成本 | 每轮重发全文，缓存命中后约 ¥0.02/M | 略省 |

**成本不是决策因素**：30k token 的会话，每轮多花约 0.0006 元，一天 50 轮 = 3 分钱。

留一个配置 `runner.continuity = fresh | resume`，默认 `fresh`。**表结构和 API 两种都兼容**，所以这个决定是可逆的。

## 3. 上下文四段与预算

```
[A] 冻结前缀      persona + AGENTS.md + 工具说明 + MEMORY 索引       ~2–4k，永不变
[B] 半稳定        会话摘要 + 工作区要点 + 未完成待办                 ≤1.2k，压缩时更新
[C] 转录          最近的消息（只追加，不重排）                       ≤9.6k，超了压 §4
[D] 当轮          这次的用户消息 + 当前时间 + 时区 + 通知           全部保留
```

| 参数 | 默认值 | 含义 |
|---|---|---|
| `context_budget_tokens` | 16000 | B+C+D 的总预算（A 另算） |
| `transcript_soft_limit` | 9600 | C 超过它触发压缩 |
| `summary_max_tokens` | 1200 | B 的硬上限 |
| `keep_recent_turns` | 6 | 最近 6 轮**永不压缩** |
| `pinned` | — | 被标星的消息永不压缩（含「记住」类指令） |

**为什么 A 必须冻结**：DeepSeek 的前缀缓存按字节算，A 一动，缓存全废。所以 A 的内容一旦发布就不再改，改人设 = 手动发版。

## 4. 压缩规则（具体算法）

```
每轮装配前检查：
  if tokens(C) <= transcript_soft_limit: 直接拼
  else:
    1. 把 C 里最老的 (tokens(C) // 2) 条，且不在 keep_recent_turns 与 pinned 里的，
       交给模型做一次「结构化摘要」
    2. 摘要与旧 B 合并 → 新 B（>1200 token 再压一次，只留结论）
    3. 从 C 里删掉这些老消息（原文留在数据库，UI 仍可翻看）
    4. 被删的那段记一句：「[已压缩 12 条，摘要见上]」——模型知道这里有空隙
```

**摘要不是散文，是四个字段**（这样它才可复用、可校验）：

```json
{
  "narrative": "用户在准备一份秋游报名表，已确认截止 10-28",
  "decisions": ["用周三上午的时间", "交表给自己不发群"],
  "open_loops": ["还没算人数"],
  "prefs": ["不希望我主动催"]
}
```

**三条保守规则**（宁可多花 token，也不丢记忆）：

1. `keep_recent_turns = 6`：最近的对话永远原样保留。
2. 超过 `transcript_soft_limit` 才压，**不是每轮都压**。
3. 压缩只发生在**轮与轮之间**，不在轮内——一次跑动里的工具结果由 dsh 自己管（它会裁剪旧的 tool_result）。

## 5. 插队：三级后悔 + 排队 + 打断

### 5.1 从按下发送那一刻起，你有三次机会

| 阶段 | 时间窗 | 你能做什么 | 实现位置 |
|---|---|---|---|
| **① 后悔期** | 0–3 秒 | 撤销这条，**它还没发出去**，零成本 | 客户端本地缓冲 + 倒计时环，不产生任何网络请求 |
| **② 排队中** | 已提交、未开跑 | 编辑 / 撤回 / 立即执行 | 服务端的队列行，删掉即取消 |
| **③ 运行中** | 已开跑 | 打断（会丢这次输出） | `taskkill /T /F` |

三条细节：

1. **后悔期默认 3 秒**，可设 0–10 秒；**长按发送 = 跳过后悔期**直接发。
2. **后悔期里 App 被杀**：草稿持久化到 DataStore，下次打开提示「有 1 条未发出的消息，要发送吗？」——不会静默消失。
3. **排队项可编辑**：编辑等于「删旧 + 建新」，**必须换一个新的 `client_msg_id`**，否则会被幂等逻辑当成重复而忽略。

### 5.2 为什么不能起第二个 job

**如果运行中又发消息，不能起第二个同会话的 job**——上下文会互相污染，钱还白花。三种处理取前两种：

| 策略 | 行为 | 什么时候用 |
|---|---|---|
| **排队（默认）** | 消息立刻落库并上屏（显示「已排队」），当前任务结束后**合并成一条**再跑 | 你只是补充信息 |
| **打断** | 杀掉当前子进程，把这句和已排队的内容合并，**重开一轮** | 你说错了，或想改方向 |
| 并行 | 同会话两个 job 同时跑 | **不做** |

细节五条：

1. **排队深度上限 5**，超了拒绝并提示（防止误触刷屏）。
2. **合并规则**：下一轮的 D 段 = 所有排队消息按顺序拼接 + 一句「以上是你在上一轮进行中补充的」。
3. **打断会丢已产生的输出**，但不会丢已写入 `event_log` 的内容——UI 上保留那部分并标注「已中断」。
4. **UI 必须显式**：运行中输入框可发，发出去显示「已排队 · 第 1 条」，旁边一个「打断并重来」。**不允许静默排队**——那会让用户以为自己的话被忽略了。
5. **排队项的撤回要即时**：点了撤回，服务端立刻删队列行，UI 立刻从列表里消失，不留"幽灵条目"。

## 6. Runner：`dsh --profile <ours> --json`（已定）

**怎么调**：

```python
proc = subprocess.Popen(
    ["dsh", "--profile", "mephisto", "--json"],
    stdin=subprocess.PIPE,          # prompt 走 stdin，不走 argv ★
    stdout=subprocess.PIPE,
    cwd=workspace_dir,              # 必须是工作区，且每轮一致
    env={**os.environ, "DSH_HOME": str(dsh_home)},
    creationflags=subprocess.CREATE_NEW_PROCESS_GROUP,
)
```

**为什么 prompt 必须走 stdin**：Windows 命令行长度上限约 32k 字符，而我们的 prompt 是 30–60 KB。headless 支持「无位置参数时从 stdin 读」，正好。

**事件流**（每行一个 JSON）：

| 事件 | 用途 |
|---|---|
| `session` | 本次运行的 dsh 会话 id（只记日志，不复用） |
| `status` | 运行状态切换 → 推给手机的 `job` 事件 |
| `text` | **已提交**的助手文本（步骤级，不是逐字）→ 手机上就是一条条消息 |
| `thinking` | 推理摘要 → 手机上显示为「思考中」的灰色小字 |
| `tool_call` / `tool_result` | 工具名与结果 → 手机上显示「正在 read_file」 |
| `final` | 最终答案（**不限长**），用它做 `message(out)` 的正文 |
| `error` | 运行期失败；此时流没有 `final` |

**判完成**：退出码 0 = `turn/end` 完成；非 0 = 失败。别只看有没有 `final`——文档明写「格式良好的事件流也可能描述一次失败的运行」。

**取消**：`taskkill /PID <pid> /T /F`（`/T` 连子进程一起杀，否则 dsh 拉起的工具进程会残留）。

## 7. 失败与降级

| 场景 | 处理 |
|---|---|
| dsh 起不来（没装/profile 错） | job 立即失败，notice 里给一句人话 + 日志路径；`doctor` 里也检查 |
| 子进程 5 秒探活失败 | 立刻 `job_failed`，不等超时 |
| 单轮超时（默认 10 分钟） | 杀进程树，job 失败，提示「要不要拆小一点」 |
| 事件流解析失败（非 JSON 行） | 忽略该行并计数；连续 20 行失败 → 判定异常，杀掉 |
| 上下文装配失败 | 降级：只用 [A]+[D]，记 `context.degraded`，并在回复末尾告诉用户 |

## 8. 验收（S3 + S5 结束时）

```powershell
# 1. 一句话跑通（prompt 走 stdin，能处理 40KB 输入）
python -m mephisto run --stdin < big.txt
# 2. --dry-run 打印完整 prompt，人能逐段读出 A/B/C/D
python -m mephisto run --dry-run "我刚才说了什么"
# 3. 同一会话连发 20 轮，第 20 轮能答出第 1 轮的内容（压缩后仍记得结论）
# 4. 运行中再发一条 → 显示「已排队」，结束后自动接上，且上下文里含这条
# 5. 运行中点「打断」→ 进程树被杀干净（任务管理器里没有残留 dsh 或 node）
# 6. 并发提交两条到同一会话 → 只有一个 job 处于 running
```
