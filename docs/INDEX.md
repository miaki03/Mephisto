# 模块与函数索引

> 目的：**改东西时不用通读全项目**。先在这里定位，再打开对应文件。
> 两个仓库：服务端 `E:\study\mephisto`（Python），客户端 `E:\study\Mephisto-Android`（Kotlin）。
> 更新纪律：新增/改名模块时同步改这张表，否则下次又得靠 grep。

---

## 一、服务端分层

```
HTTP 适配器 ─┐
MCP 适配器  ─┼─→ app/（编排） ─→ domain/（纯规则，零 IO）
DSH 运行器  ─┘         │
                       └→ store/（SQLite 读写）
```

铁律：`domain/` 不 import 任何 IO；端口（`ports.py`）是副作用边界；状态只存在文件/数据库里。

| 层 | 文件 | 职责 | 关键符号 |
|---|---|---|---|
| 入口 | `mephisto/cli.py` (331) | 命令分发：doctor / db / device / run / serve / remind / mcp | `main`, `cmd_*` |
| 配置 | `mephisto/config.py` (142) | 从 `pyproject.toml` 向上找根；相对路径解析 | `find_root`, `load`, `Config.data_dir`, `Config.dsh_home` |
| 时钟 | `mephisto/clock.py` (71) | 可注入时间 | `SystemClock`, `FakeClock` |
| ID | `mephisto/ids.py` (114) | ULID（单调）、令牌、`client_msg_id` 校验 | `new_ulid`, `new_token`, `hash_token` |
| 端口 | `mephisto/ports.py` (59) | 运行器契约 | `Task`, `RunEvent`, `RunResult`, `Runner` |
| 领域 | `mephisto/domain/envelope.py` (289) | 邮件信封（S14 用，当前未接线） | `Envelope`, `Auth`, `Route` |
| 领域 | `mephisto/domain/session.py` (84) | 会话 | `Session.touch` |
| 领域 | `mephisto/domain/notice.py` (148) | 通知状态机 | `ack`, `dismiss`, `snooze`, `mark_missed` |
| 领域 | `mephisto/domain/reminder.py` (113) | 提醒规则 | `fired_once`, `origin_for` |
| 领域 | `mephisto/domain/values.py`, `codec.py` | 值对象 + 严格编解码 | `as_utc`, `expect_keys`, `Address`, `Attachment` |
| 编排 | `mephisto/app/chat.py` (290) | **一轮对话**：落库→跑 runner→落库→发事件 | `ChatService.post_message`（同步）、`enqueue_message` + `execute_job`（异步）、`_message_event` |
| 编排 | `mephisto/app/daemon.py` (130) | 队列 worker + 启动恢复 + 取消 | `Daemon.start/stop/drain_once/cancel/recover` |
| 编排 | `mephisto/app/scheduler.py` (135) | 到点展开 notice、过期归档、每日汇总 | `Scheduler.tick` |
| 编排 | `mephisto/app/reminders.py` (168) | 一句话→规则→notice | `create_from_text`, `materialize`, `reschedule` |
| 编排 | `mephisto/app/timeparse.py` (92) | 时间解析（看不懂就报错） | `parse_when` |
| 编排 | `mephisto/app/dto.py` (63) | 领域对象 → JSON | `message_to_dict` 等 |
| 存储 | `mephisto/store/db.py` (145) | 单连接 + RLock、WAL、迁移、体检 | `Database`, `open_db`, `migrate`, `to_ms/from_ms` |
| 存储 | `store/sessions.py` (133) | 会话 CRUD、别名、决策记录 | `SessionRepo` |
| 存储 | `store/messages.py` (199) | 消息 + 附件 | `MessageRepo.append/list_by_session/by_client_msg_id` |
| 存储 | `store/events.py` (67) | 事件日志（SSE 的数据源） | `EventRepo.append/list_since/last_seq` |
| 存储 | `store/jobs.py` (134) | 队列行；**单条 SQL 抢占** | `JobRepo.claim_next`, `cancel_queued`, `requeue_stale` |
| 存储 | `store/notices.py` (94) | 通知；`origin` 唯一索引做幂等 | `insert`（返回是否新增） |
| 存储 | `store/reminders.py` (93) | 提醒规则 | `list_due` |
| 存储 | `store/devices.py` (112) | 设备与令牌（只存 sha256） | `add`（返回明文一次）, `authenticate` |
| 存储 | `store/uploads.py` (137) | 分片上传元数据 | `mark_chunk`, `find_done` |
| 附件 | `mephisto/blobs.py` (131) | 内容寻址 + 分片拼接 | `BlobStore.adopt`, `UploadArea.assemble` |
| HTTP | `adapters/http/server.py` (269) | 线程服务器、鉴权、SSE 分流 | `serve`, `build_server`, `build_service` |
| HTTP | `adapters/http/routes.py` (371) | 17 个端点的实现 | `Api`（22 个方法） |
| HTTP | `adapters/http/router.py` (73) | 正则路由 | `Router.match`, `StreamResponse` |
| HTTP | `adapters/http/sse.py` (70) | 事件流格式与心跳 | `stream_events` |
| 运行器 | `adapters/runner/dsh.py` (415) | `dsh --json` 子进程；NDJSON 解析；**Job Object 收尾进程树** | `DshRunner.run/cancel`, `WindowsJob`, `kill_tree` |
| MCP | `adapters/mcp/server.py` (254) | stdio JSON-RPC，5 个工具 | `McpServer.handle`, `serve_stdio` |

---

## 二、数据库（13 表 → 归属）

| 表 | 语义 | 读写它的 repo |
|---|---|---|
| `session` | 会话 | `sessions.py` |
| `session_alias` | 会话别名（邮件主题路由用） | `sessions.py` |
| `message` | 消息（含 `client_msg_id` 幂等） | `messages.py` |
| `attachment` | 消息附件（内容寻址） | `messages.py` |
| `event_log` | 事件流（SSE 数据源，`seq` 单调） | `events.py` |
| `job` | 队列（状态机 + 单条 SQL 抢占） | `jobs.py` |
| `notice` | 通知/提醒的投递单元（`origin` 唯一） | `notices.py` |
| `reminder` | 提醒规则 | `reminders.py` |
| `device`, `device_token` | 设备与令牌 | `devices.py` |
| `upload` | 分片上传会话 | `uploads.py` |
| `route_decision` | 路由决策记录（S14 用） | `sessions.py` |
| `device_cursor` | 设备同步游标 | 未接线 |

---

## 三、HTTP 契约速查（17 端点，`step-4` 冻结，只加不改）

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/healthz` | 免鉴权；版本、会话数、待办通知数 |
| GET/POST | `/api/v1/sessions` | 列表（`since_seq`/`limit`）/ 新建 |
| GET | `/api/v1/sessions/{id}` | 单个会话 |
| GET/POST | `/api/v1/sessions/{id}/messages` | 历史 / 发消息（**202** + `job_id`） |
| POST | `/api/v1/messages/{id}/ack` | 已读回执 |
| POST | `/api/v1/jobs/{id}/cancel` | 排队中删行 / 运行中打断（`queued` / `interrupted` / 409 `too_late`） |
| GET | `/api/v1/events` | **SSE**：`id:` / `event:` / `data:`，15 秒 `: ping` |
| GET | `/api/v1/notices` | 未来 7 天待提醒 |
| POST | `/api/v1/notices/{id}/state` | `acked` / `dismissed` / `missed` / `snoozed` |
| POST | `/api/v1/attachments` | 创建上传（声明大小与 sha256） |
| PUT | `/api/v1/attachments/{id}/chunks/{i}` | 上传分片（4 MB/片） |
| POST | `/api/v1/attachments/{id}/complete` | 校验并入库 |
| GET | `/api/v1/attachments/{digest}` | 下载（`sha256:<hex>`） |
| DELETE | `/api/v1/attachments/{uid}` | 放弃上传 |
| DELETE | `/api/v1/devices/{id}` | 吊销设备 |

事件类型（SSE `event:`）：`message`（带 `client_msg_id`）、`job`（`state` ∈ queued/running/done/failed/cancelled）、`notice`、`message.acked`。

---

## 四、安卓客户端分层

```
MainActivity（Compose 界面）
   └─ core/（纯 JVM，可单测）
        ├─ Protocol/Parsers   契约与解析
        ├─ Api/MephistoApi    HTTP
        ├─ ChatStore          消息缓存（进会话不重拉）
        ├─ Outbox             本机发件箱（发送中/排队/执行中/失败）
        ├─ Sse/SseClient      事件流解析与重连
        ├─ ProgressTracker    秒表+阶段+tokens
        ├─ ReminderPlan       提醒→闹钟
        ├─ TimeText           相对时间
        └─ KeepAlive          常驻/重排决策
```

| 文件 | 行 | 职责 | 关键符号 |
|---|---|---|---|
| `MainActivity.kt` | 733 | 三个界面 + 状态装配 | `App`, `SessionRow`, `Bubble`, `PendingRow`, `ConnectionPanel` |
| `core/Protocol.kt` | 163 | 模型 + 解析 | `Session`, `Message`, `Parsers.messageEvent/receipt` |
| `core/Api.kt` | 195 | HTTP 与错误映射 | `MephistoApi`, `ApiResult`, `HttpTransport`, `attempt`（重试一次） |
| `core/ChatStore.kt` | 90 | 消息缓存与合并 | `put`, `merge`, `stale`, `version` |
| `core/Outbox.kt` | 143 | 发件箱状态机 | `enqueue`, `markQueued/Running/Done/Failed`, `cancel`, `retry` |
| `core/Sse.kt` | 184 | 行解析 / 退避 / 进度 | `SseParser`, `Backoff`, `ProgressTracker` |
| `core/SseClient.kt` | 82 | 长连接读取 | `start`, `stop` |
| `core/ReminderPlan.kt` | 105 | notice→闹钟计划 | `plan`, `requestCode`（FNV-1a，幂等） |
| `core/TimeText.kt` | 85 | 相对时间 / 时间分隔 | `relative`, `showSeparator` |
| `core/KeepAlive.kt` | 56 | 常驻与重排判断 | `shouldStart`, `shouldReschedule` |
| `data/Credentials.kt` | 47 | 地址+令牌存取 | `load`, `save`, `api()` |
| `notify/Notifier.kt` | 83 | 通知渠道 | `CHANNEL_ALARM`, `CHANNEL_SERVICE` |
| `alarm/Alarms.kt` | 86 | 精确闹钟/窗口降级 | `schedule`, `cancel`, `canScheduleExact` |
| `alarm/AlarmReceiver.kt` | 16 | 到点弹通知 | — |
| `alarm/RescheduleReceiver.kt` | 63 | 开机/时区/时间变更重排 | — |
| `service/KeepAliveService.kt` | 98 | `remoteMessaging` 前台服务 + SSE | — |
| `ui/Components.kt` | 160 | 状态条/横幅/空态/骨架/头像 | `StatusStrip`, `Banner`, `EmptyState`, `SkeletonRow` |
| `ui/SystemScreen.kt` | 217 | 提醒与权限页 | 通知权限/精确闹钟/电池优化/常驻开关 |

---

## 五、“我要改 X，看哪里”

| 想改 | 先看 | 再动 |
|---|---|---|
| 回复的生成方式（换模型/换 CLI） | `ports.py` 的 `Runner` 契约 | `adapters/runner/dsh.py` |
| 一轮对话的行为（落库顺序、事件） | `app/chat.py` 顶部三条不变量 | `post_message` / `execute_job` |
| 排队/并发/取消 | `store/jobs.py` 的 `claim_next`（单条 SQL） | `app/daemon.py` |
| 提醒何时触发 | `app/scheduler.py` 的 `tick`（先清理后触发） | `app/reminders.py` |
| 新加一个 HTTP 端点 | `adapters/http/routes.py` 的 `_wire` | 对应 handler + `tests/unit/test_api.py` |
| 事件流格式 | `adapters/http/sse.py` | 客户端 `core/Sse.kt` |
| 鉴权 | `store/devices.py` + `server.py` 的 `_bearer` | `cli.py` 的 `device add` |
| 客户端某屏的布局 | `MainActivity.kt` 里对应 Composable | `ui/Components.kt` 尺寸/颜色 |
| 客户端“进会话卡” | `core/ChatStore.kt`（缓存与 `staleMillis`） | `App.ensureMessages` |
| 发件箱状态 | `core/Outbox.kt` | `MainActivity.send/cancelItem/retryItem` |
| 闹钟排不出来 | `core/ReminderPlan.plan` + `alarm/Alarms` | `ui/SystemScreen` 的权限按钮 |
| 通知不响 | `notify/Notifier` 渠道重要性 | 系统通知权限 |

---

## 六、文档地图

| 文档 | 内容 | 什么时候读 |
|---|---|---|
| `docs/INDEX.md` | 本文件：模块/函数/契约索引 | 每次动手前 |
| `docs/AGENT-BRIEF.md` | 任务书：铁律、命令、坑、下一步 | 接续工作前 |
| `docs/CLIENT-CONTRACT.md` | 客户端职责与逻辑（交 UI 设计） | 设计 UI 前 |
| `docs/TEST-PLAN.md` | 测试流程与配合方式 | 联调前 |
| `docs/STATE.md` | 进度台账 + 环境事实 + 已定决策 | 判断“现在在哪” |
| `docs/99-implementation-plan.md` | 14 步总计划（含每步实测结果与偏差） | 规划下一步 |
| `docs/00–90-*.md` | 各专题设计（架构/传输/会话/提醒/运行器/安卓…） | 需要背景理由时 |
