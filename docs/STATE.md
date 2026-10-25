# STATE · 进度台账（上下文被压缩后先读这一页）

> 这份文件是给"未来的我"看的。目的只有一个：**上下文压缩后，不用问人也能接着干**。
> 每次提交前更新「进度」与「本次新增的事实」，其它章节只在决策变化时改。

---

## 1. 现在在哪

| 步 | 内容 | 状态 | tag |
|---|---|---|---|
| S0 | 骨架与纪律 | ✅ | `step-0` |
| S1 | 领域模型 | ✅ | `step-1` |
| S2 | 存储与迁移（13 表） | ✅ | `step-2` |
| S3 | 运行器（DSH 子进程） | ✅ | `step-3` |
| S4 | HTTP API + SSE（16 端点，契约冻结） | ✅ | `step-4` |
| S5 | 队列与守护进程 | ✅ | `step-5` |
| S6 | 提醒内核 + MCP 工具 | ✅ | `step-6` | |
| S7 | 安卓工具链 + 空壳 APK（**在 Mephisto-Android 仓库**） | ✅ | `step-7`（Android 仓库） |
| S8 | 安卓只读：会话列表 + 历史消息（Android 仓库） | ✅ | `step-8`（Android 仓库） |
| S9 | 安卓发送：排队 + 三级撤销 + SSE（Android 仓库） | ✅ | `step-9`（Android 仓库） |
| S10 | 安卓常驻通知 + 精确闹钟（Android 仓库） | ✅ | `step-10`（Android 仓库） | |
| S11–S13 | 记忆 / 第二信道 / 聊天摘要 | | |
| S14 | 邮件 | 搁置 | |

分支 `main`，远端 `origin = git@github-mephisto:miaki03/Mephisto.git`（账号级 SSH key `~/.ssh/id_ed25519_mephisto`，无 passphrase，用户已确认）。
每步一个提交 + 一个 **annotated tag**，tag message 就是该步验收清单。

## 2. 环境事实（踩过坑，别再踩）

| 事实 | 后果 |
|---|---|
| Python 3.14.7 在 `C:\Python314`，`python` 在 PATH | 直接用 `python` |
| 沙箱已放开（danger-full-access），**`pip` 可用**，pytest 9.1.1 已装 | 两条测试入口都能用 |
| 只有 Windows PowerShell 5.1，**`pwsh` 不在 PATH** | 调脚本用 `powershell -NoProfile -ExecutionPolicy Bypass -File x.ps1` |
| PS 5.1 把无 BOM 的 UTF-8 `.ps1` 当 GBK 读 | `.ps1` 必须存 **UTF-8 with BOM**；`.cmd` 用 CRLF |
| `tempfile.mkdtemp()` 建的目录**不可写也删不掉**（`os.makedirs` 正常） | 代码里禁止 mkdtemp；测试用 `tests/**/_tmp/`（已 gitignore） |
| `data_dir` 相对**仓库根**解析，不是相对配置文件 | 测试给绝对路径，否则写进仓库 `state/` |
| `dsh` 在 `C:\Program Files\nodejs\dsh.CMD`，版本 `0.2.0-rc.2` | 子进程调用要经 `.CMD`，用 `shutil.which` |
| `%TEMP%` 里 mkdtemp 残留 8 个空目录删不掉 | 已 gitignore，无需处理 |

## 3. 硬约束（写代码时先看这段）

1. **运行时零第三方依赖**（`pyproject.toml` 的 `dependencies = []`）。测试可用 pytest，但用例本身只用标准库。
2. `mephisto/domain/` **不 import 任何 IO**（无 sqlite/socket/subprocess/open）。
3. 领域对象 `frozen=True, slots=True`；时间一律 **UTC 感知**，naive 一律拒绝。
4. **数据库时间列一律 epoch 毫秒**（`store/db.py: to_ms/from_ms`）。
5. 唯一约束、外键、状态机**写进 DDL / `__post_init__`**，不靠调用方自觉。
6. `null` 表示"未知"，不用 `false`/`0` 冒充。
7. 单文件 200 行是**提醒不是禁令**；拆分由职责驱动，禁止为凑行数硬拆。
8. 提交信息 `feat(scope): ...`；每步绿色（测试+doctor）才 tag。

## 4. 已定的技术决策（会反复用到）

| 主题 | 定案 | 出处 |
|---|---|---|
| 接入方式 | **Cloudflare Tunnel**（VPS+frp 为退路）；HTTPS，服务只听 127.0.0.1 | docs/15、16 |
| Runner | `dsh --profile <p> --json`，**每轮一个子进程**，prompt 走 **stdin**（Windows 命令行 32k 上限），取消 = `taskkill /F /T /PID` | docs/25 §6 |
| 会话连续性 | 每轮新会话 + 我们自己装配上下文（不用 `--session-id`） | docs/25 §2 |
| 流式 | **不承诺逐 token**；三档：步骤级事件 / 过程可见（工具名）/ 视觉打字机 | docs/25、15 §10 |
| 插队 | 同会话只许一个 running job（部分唯一索引）；默认排队，可打断；三级后悔（0–3s 后悔期 / 排队可撤 / 运行中可打断） | docs/25 §5 |
| 附件 | 分片 4 MB、单文件 64 MB、超限走 `workspace/inbox/` | docs/17 |
| 提醒 | 服务端 `notice` 是真值，手机本地闹钟执行；`origin` 唯一索引做幂等 | docs/15 §5、99 §5 |
| 工具面 | MCP（stdio），工具名 `mcp__mephisto__*`；`mephisto mcp` 秒起、无副作用 | docs/55 |
| QQ | 只读本地库（qqflow-server 优先，QQFlow 导出兜底） | docs/40 §9 |

## 5. dsh 的真实接口（本机实测，S3 结论）

- 调用形态：`dsh --profile <name> --json`，任务文本从 **stdin** 读（无位置参数时）。
- `--json` 输出按行 JSON 事件：`session` / `status` / `text` / `thinking` / `tool_call` / `tool_result` / `final` / `error`。
- **`text` 与 `thinking` 是步骤级提交，不是逐 token**（官方 README 明写）。
- 退出码：`turn/end` 完成 → 0；aborted/error/无轮次 → 1。**失败时事件流会有 `final` 但退出码是 1，所以判定必须以退出码 + `final` 为准。**
- 客户端取消 = 杀进程（SDK 协议没有 cancel 方法）。
- `DSH_HOME` 必须非空且不使用 `~/.dsh` 默认；本机 profile 目录现有 `mailbot`、`web`。

## 5.1 HTTP 契约（S4 冻结，之后只加不改）

- 鉴权：`Authorization: Bearer <token>`；`/healthz` 与 `/` 免鉴权。
- 17 个端点：healthz / sessions(3) / messages(3) / events(SSE) / notices(2) / attachments(5) / devices(1)
  + **jobs/{id}/cancel**（S9 增补，只加不改：排队中删行，运行中杀进程树）。
- 游标：`since_seq`（单调整数）；SSE 支持 `Last-Event-ID`，每 15 秒一个 `: ping`。
- 响应体形状：`{items:[...]}`、`{session:{...}}`、`{message:{...}, reply:{...}, run:{...}}`、
  错误 `{error:{code,message}}`。
- **失败映射**：400 参数/校验、401 未授权、404 不存在、405 方法不对、409 状态非法/分片不全、
  413 过大、502 运行器失败、500 内部错误。
- 附件：`attachment_id` 就是 `sha256:<hex>`（内容寻址，天然去重）。

## 5.2 实测事实（S4）

- 端到端 2.2s：建会话 → 发消息 → 真 dsh → 回复 `1 + 1 = **2**`；`tokens=6059 cacheRead=5504`。
- 中文往返无损（用 python 发，PowerShell 的 `ConvertTo-Json` 会把中文变成 `?`——那是客户端问题）。
- **浏览器的 EventSource 不能带 Bearer 头**，所以调试页的 SSE 按钮改成打印 curl 命令；真客户端（OkHttp）可以带头。

## 5.3 队列（S5 实测）

- **同步 vs 异步两条路径共存**：`ChatService.post_message`（同步，跑在调用线程）与
  `enqueue_message`+`execute_job`（异步，worker 跑）。`serve` 默认异步，`build_api(async_jobs=False)` 可回同步。
- **异步时 POST 立刻 202**（实测 0.00s），回复经 SSE 的 `message` 事件到客户端；真 dsh 那一轮 4.1s。
- 抢占是**一条 SQL**（`UPDATE ... RETURNING`），`WHERE NOT EXISTS (同会话 running)` 保证不串；
  DDL 里的部分唯一索引是最后一道保险。
- 启动恢复：`running` 且 `started_at` 超过 30 分钟的 job 会被放回 `queued`（实测重排 1 个）。
- 失败会写一条 `kind=system` 的 notice，`origin=job:<id>:failed`——不静默失败。
- SSE 事件序实测：`message → job×10 → message…`，客户端能据此显示"排队中/执行中/完成"。

## 5.4 提醒与 MCP（S6 实测）

- 时间解析只认少数写法：`3分钟后` / `2 小时` / `明天15:00` / `明天下午3点` / `15点半` / ISO。
  **看不懂就报错**——猜时间会让提醒在错误的日子响。
- 提醒 = 规则（`reminder`）+ 投递单元（`notice`），`origin = reminder:<id>@<due_iso>` 唯一索引做幂等。
- 调度器 tick 顺序：**先清理旧 pending，再触发本轮到期的**。反过来会把刚触发的提醒立刻判成"错过"。
- 宽限 2 小时：宽限内补发并标"（延迟）"；超过则**不单独发**，合并成一条"你错过了 N 条提醒"（一天一条）。
- MCP stdio 实测（真 subprocess）：`initialize` → `serverInfo={mephisto,0.0.1}`；`tools/list` → **5 个工具**；
  `tools/call remind_add` → 落库成功，`due_local=2026-10-25T19:52:16+08:00`。
- 工具集：`remind_add` / `remind_list` / `remind_cancel` / `remind_reschedule` / `memory_append`。
  `chatlog_search` / `chatlog_digest` 留到 S13（那时才有数据源，现在声明等于骗模型）。
- CLI：`mephisto remind "3分钟后 喝水"`、`mephisto mcp`；`serve` 会同时起 worker 与调度器。

## 5.5 安卓侧（S7–S8 实测）

- **两个仓库**：服务端 `E:\study\mephisto`（S0–S6），安卓 `E:\study\Mephisto-Android`（S7 起）。
  tag 名一致（step-N），但各指各仓库的提交。
- 工具链全在 `E:\study\.toolchain`（不进仓库）：JDK 21.0.12.1、Gradle 9.8.2、AGP 9.4.1、
  platforms android-36 + android-37.0、build-tools 36.0.0、platform-tools 37.0.1。
- **AGP 9 起 Kotlin 内置**：再应用 `org.jetbrains.kotlin.android` 会直接报错；
  只保留 `org.jetbrains.kotlin.plugin.compose`。
- **三个环境坑**（都已绕开，写在 Android 仓库 README）：
  1. `sdkmanager` / `android sdk` 连不上仓库 → `tools/install-sdk-packages.py` 直接下 Google 仓库 zip 并手工解压；
  2. Maven Central TLS 握手被中断 → `settings.gradle.kts` 优先走阿里云镜像；
  3. `%TEMP%` 与 `%LOCALAPPDATA%` 不可写 → `java.io.tmpdir`、Kotlin（`in-process`）、`adb` 全部改道。
- 构建结果：APK 28.22 MB，`dev.mephisto.app` / minSdk 29 / targetSdk 37 / MainActivity 可启动。
- **客户端逻辑放在 `core/`（纯 JVM）**，17 个 JVM 单测在 `gradlew test` 里跑，不需要手机。
  其中 `RealPayloadTest` 用真服务端抓下来的四个响应当夹具。

## 5.6 发送 / 撤销 / 打断（S9 实测）

- **三段后悔**：0–3 秒在客户端本地（一个请求都不发）→ 排队中删行 → 运行中杀进程树。
  前两段是状态机（`Outbox`），第三段靠 `POST /api/v1/jobs/{id}/cancel`。
- **打断必须连孙子进程一起死**：实测 `taskkill /T` 之后仍见过 `dsh-subprocess-local` 存活，
  所以父进程被塞进 **Windows Job Object**（`KILL_ON_JOB_CLOSE`），取消时 `TerminateJobObject`。
  验证方法：打断前后数 `dsh-subprocess-local` 进程数，2 → 2（没有多出来）。
- 真服务端实测：进入 running 0.5s 后打断，返回 `interrupted`，job 状态 `cancelled`，**没有落回复**。
- 排队中取消：返回 `queued`，job 状态 `cancelled`，重复取消返回 409 `too_late`。
- 客户端：`Outbox` / `SseParser` / `Backoff` / `ProgressTracker` 全是纯 JVM，
  **53 个单测**（后悔期、编辑换 id、排队深度 5、SSE 多行 data、心跳注释、退避封顶、状态行文案）。

## 5.7 闹钟与常驻（S10 实测）

- 服务端 
otice → ReminderPlan → FUTURE（排闹钟）/ OVERDUE（立刻弹，宽限 2h）/ SKIP（过期太久，
  服务端已汇总成"你错过了 N 条"）。
- equestCode 由 
otice.id 经 FNV-1a 派生：**重排是覆盖不是叠加**，取消能真的取消掉。
  2000 个 ULID 实测零碰撞。
- setAlarmClock 需要精确闹钟权限；没有就退 setWindow（10 分钟窗口），并在界面上提示去开。
- 开机/时区变更/时间被改/App 更新 → RescheduleReceiver 重排（AlarmManager 的闹钟**不跨重启**）。
- 常驻服务用 emoteMessaging（无 6 小时上限）+ IMPORTANCE_LOW 通知渠道（无声）。
- **三个必须手动开的开关**：通知权限、精确闹钟、电池优化。做成「提醒与权限」页，少开任何一个都没有报错。
- **验不了的**（要真机）：到点响不响、重启后闹钟回不回来、常驻会不会被杀。

## 6. 本机已验证的验收命令

```powershell
cd E:\study\mephisto
python tests\run.py            # unittest，当前 107 个
python -m pytest -q            # pytest，同一批用例
python -m mephisto doctor      # 8 项体检
python -m mephisto db migrate  # 迁移到最新
python -m mephisto db check    # 完整性 + 行数
python -m mephisto serve        # 起服务（默认 127.0.0.1:8787）
python -m mephisto device add --name 手机   # 发令牌
python -m mephisto remind "3分钟后 喝水" # 建提醒
python -m mephisto mcp                    # MCP stdio（给 dsh 当工具）

# 安卓仓库（E:\study\Mephisto-Android）
tools\build.ps1                           # 构建 debug APK
tools\build.ps1 -Task testDebugUnitTest   # 17 个 JVM 单测
tools\build.ps1 -Task installDebug -Install  # 装到手机（需先 adb devices 看到设备）
git push -u origin main --tags # 推代码与标签
```

## 7. 待办与风险

- ~~S3 风险最高~~ **已解除**：真调用跑通（2.2s / 6024 tokens / 91% cache hit），事件字段已按实测写死；测试用假 dsh（`sys.executable -c`）保证 hermetic。结论：DSH_HOME 独立后 profile 要自己建（`dsh --profile mephisto --from-default-profile headless`），凭据文件（`.credentials.yaml`/`.env`）要复制进去。
- **S7 风险**：本机无 java/gradle/adb，工具链要从零装（约 1–1.2 GB 下载）。
- **Cloudflare Tunnel 未实测**：SSE 是否真的实时必须 `curl -N` 验一次（用户尚未配置）。
