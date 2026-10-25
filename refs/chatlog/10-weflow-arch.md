# 10 · 架构：从磁盘到 UI 的完整链路

> 本章回答一个问题：**数据从微信的加密库里出来，到你能看见/能调用，中间经过了几层，每层能拦住什么。**
> 证据：官方构建 `/dist-electron/*.js`（asar 解包，`evidence/asar-dist-electron/`）+ 仓库源码行号。

---

## 1. 进程/模块结构

```text
WeFlow.exe（Electron 主进程 = main.js）
│
├─ 主线程
│   ├─ ConfigService           配置读写（%APPDATA%\WeFlow\WeFlow-config.json）
│   ├─ HttpService             http.createServer，127.0.0.1:5031
│   ├─ MessagePushService      会话基线比对 + 撤回识别 + 事件广播
│   ├─ CloudControlService     遥测上报（经原生层）
│   └─ Tray / Window / IPC     界面与托盘
│
├─ worker_threads（11 个独立 entry，官方 asar 实测）
│   ├─ wcdbWorker.js            ← 唯一持有 WCDB 句柄的进程
│   ├─ apiMessageWorker.js      HTTP 消息映射（hex/zstd 解码，可并行）
│   ├─ imageDecryptWorker.js    图片解密
│   ├─ exportWorker.js          导出（HTML/JSON/MD/SQL/Excel…）
│   ├─ transcribeWorker.js      语音转写（sherpa-onnx）
│   ├─ keyRecoverWorker.js      ← 取 DB key（调 wx_key.dll）
│   ├─ imageSearchWorker.js
│   ├─ agentRunWorker.js / agentTitleWorker.js
│   └─ annualReportWorker.js / dualReportWorker.js
│
└─ 原生组件（resources/resources/）
    ├─ wcdb/win32/x64/wcdb_api.dll   1.65 MB  ← C ABI 层（115 个导出）
    ├─ wcdb/win32/x64/WCDB.dll       9.22 MB  ← 腾讯 WCDB 本体（无 wcdb_* 导出）
    ├─ wcdb/win32/x64/SDL2.dll       2.38 MB
    ├─ key/win32/x64/wx_key.dll      0.23 MB  ← 取密钥（8 个导出）
    ├─ wedecrypt/.../weflow-image-native-*.node  0.28 MB ← 图片解密 addon
    └─ welive/win32/x64/welive.exe   2.66 MB  ← 批量导出引擎
```

worker 清单来自 `evidence/app-asar-filelist.txt`（asar 内 `/dist-electron/*.js` 共 16 个，其中 11 个是 worker/入口）。

---

## 2. 三条数据路径（都汇到 `wcdbWorker`）

```text
                    ┌──────────────────────────────┐
   UI（渲染进程）───▶│ IPC invoke/handle（preload） │──┐
                    └──────────────────────────────┘  │
                                                      │
   HTTP 客户端 ─────▶ HttpService（主线程）───────────┼──▶ chatService（主线程）
                                                      │        │
   SSE 客户端 ◀────── HttpService ◀── MessagePushService ◀──────┤
                                                               ▼
                                                    wcdbService（worker 门面）
                                                               │ postMessage + protectionAuth
                                                               ▼
                                                    ┌─────────────────────┐
                                                    │  wcdbWorker（线程）  │
                                                    │  koffi → wcdb_api.dll│
                                                    └──────────┬──────────┘
                                                               │ SQLCipher
                                                               ▼
                                              <dbPath>\<wxid>\db_storage\*.db
```

三个要点：

1. **只有一个进程持有 DB 句柄**（`wcdbWorker`）。UI 与 HTTP 都通过 `chatService` → `wcdbService` 排队过去。这是为什么 HTTP 请求会与 UI 抢同一个队列。
2. **每次跨进 worker 都要带 `protectionAuth`**（`main.js` 的 `callWorkerWithProtectionAuth`）。这是保护的"卡点"位置：**授权是 per-call 的，不是 per-session 的**。
3. **HTTP 层不缓存**：`HttpService` 直接 `await chatService.getMessages(...)`。所以接口延迟 ≈ 原生查询延迟。

---

## 3. 实时性：不是 `fs.watch`，是原生监视管道

这一点任务书猜错了（它假设 `chatService.ts` 里的 `fs.watch` 是实时机制）。

**实测**：`chatService.ts` 虽然 `import { watch } from 'fs'`（第 2 行），但**全文件没有任何 `watch(` 调用**。真正的事件源是原生监视管道：

```text
wcdb_start_monitor_pipe()  →  wcdb_get_monitor_pipe_name(&name)
        │
        ▼  （Windows 命名管道 / Unix socket）
   wcdbCore.connectMonitorPipe()：net.createConnection(pipePath)
        │  按行读 JSON；'\0' 归一化为 '\n'；'}{' 拆行
        │  断线后 3 秒重连，带 generation 防串代
        ▼
   chatService.addDbMonitorListener(cb)     ← main.ts:5569 注册
        │
        ├─▶ messagePushService.handleDbMonitorChange(type, json)   ← 推送
        └─▶ insightService.handleDbMonitorChange(type, json)       ← AI 洞察
```

证据：`wcdbCore.ts:290-355`（连接与解析）、`358-372`（3 秒重连）、`main.ts:5569-5572`（注册点）、`wcdbCore.ts:390-392`（`setMonitor` 兼容旧签名）。

### 3.1 推送判定的算法（`messagePushService.ts`）

不是"有新行就推"，而是**比对会话基线**：

| 步骤 | 逻辑 | 行号 |
|---|---|---|
| 去抖 | 350 ms 合并多次管道事件 | `messagePushService.ts:61, 198-206` |
| 取基线 | 上次 `getSessions()` 的快照（`sessionBaseline`） | `180-187, 256` |
| 选候选 | `shouldInspectSession(previous, session)`：未读数/时间/摘要变化 | `259-269` |
| 判撤回 | `hasUnreadCountDecreased(prev,cur)` 或摘要含撤回特征 | `275-277` |
| 拉消息 | 对候选会话拉最近消息 | `278+` |
| 去重 | `messageKey` + `recentlyRevokedOriginalTokens` | `56, 500-509` |
| 广播 | `httpService.broadcastMessagePush(payload)` → SSE | `httpService.ts:326-346` |

**延迟构成**：管道事件（亚秒级）→ 350 ms 去抖 → 一次 `getSessions` + 一次 `getMessages` 的原生查询时间。**抬升延迟的是查询，不是监听。**

### 3.2 漏消息的三个已知风险（`[确认-源码]` + 推断）

| 风险 | 机制 | 后果 |
|---|---|---|
| 基线竞态 | 两次 `getSessions` 之间连发多条，只按"最后一条"比时间 | 只推最后一条，前面几条靠"拉最近消息"兜（取决于 `limit`） |
| 撤回窗口 | `recentRevokeScanSeconds = 150`（`messagePushService.ts:66`） | 只回看 150 秒内的撤回；更早的撤回不补 |
| 管道断连 | 断线 3 秒重连 | 断连期间的事件不补发（SSE 有 10 分钟重放缓冲，但那是"已广播"的，不是"未收到"的） |

### 3.3 撤回是怎么识别的（`scanRecentRevokes`）

```ts
const scanRecentRevokes =
  this.hasUnreadCountDecreased(previous, session) ||
  (this.hasUnreadCountChanged(previous, session) && this.isRevokeSessionSummary(session)) ||
  (Boolean(sessionId) && messageTableTargetSessionIds.has(sessionId))
```

真正的召回靠 `getRecentRevokeMessagesFromTables()` 直接下 SQL（`messagePushService.ts:592-615`）：

```sql
WHERE (local_type IN (10000, 10002)
   OR message_content LIKE '%撤回%'
   OR message_content LIKE '%revokemsg%'
   OR message_content LIKE '%<replacemsg%'
   OR compress_content LIKE '%撤回%' ...)
LIMIT 20
```

时间窗是 `anchor - 150s`（`getRecentRevokeScanSince`，`582-590`）。`directRevokeScanLimit = 20`（第 67 行），上下文消息再取 4 倍（第 629 行）。

> 所以**撤回 = "系统消息 + 文本特征 + 未读数下降"三信号**，不是解析协议。这样一个纯文本匹配的召回方式，在"撤回提示被本地化/改文案"时会失效。

---

## 4. HTTP 服务的独立性（详见 `30-weflow-http-api.md` §7）

`HttpService.start()` 自己 `ensureDbReady()` 并预热映射线程池（`httpService.ts:196-205`），注释明写"HTTP 服务可能经 http:start 独立启动（未走 GUI 的 connect/warmup）"。加上 `silentStartup` 配置与 Tray，**窗口可以从不显示，HTTP 照常工作**。

---

## 5. 模块地图（仓库源码 ↔ 官方构建）

| 仓库文件 | 作用 | 官方构建里的位置 |
|---|---|---|
| `electron/main.ts`（214 KB） | 主进程：窗口/IPC/生命周期 | `/dist-electron/main.js`（2.87 MB） |
| `electron/services/wcdbCore.ts`（225 KB） | FFI 绑定 + 解析 + 游标 | 打进 `wcdbWorker.js`（129 KB） |
| `electron/services/wcdbService.ts` | worker 门面（`callWorker`） | `main.js` 内 |
| `electron/services/chatService.ts`（587 KB） | 会话/消息/联系人业务层 | `main.js` 内 |
| `electron/services/httpService.ts`（98 KB） | HTTP/SSE | `main.js` 内 |
| `electron/services/messagePushService.ts`（55 KB） | 推送逻辑 | `main.js` 内 |
| `electron/services/keyProviderService.ts` | **仓库版**的外部密钥协议 | `main.js` 内（外包路径） |
| （仓库无） | **官方版**的取密钥 worker | `/dist-electron/keyRecoverWorker.js` |
| `electron/services/apiMessageMapping.ts` | 行→消息纯函数映射 | `apiMessageWorker.js`（15 KB） |

注意最后两行：**官方构建比仓库多一个 `keyRecoverWorker`**，这直接印证了"仓库版被抽掉了原生能力"。

---

## 6. 进程退出与清理

- `main.ts:5706` 在退出时 `httpService.stop()`；`cloudControlService.stop()` 会 `wcdbService.cloudStop()`（`cloudControlService.ts:233-239`）。
- `stop()` 会强制断开所有连接与 SSE 客户端（`httpService.ts:219-257`）。
- 托盘常驻：关窗口不退出（`windowCloseBehavior` 配置项，默认 `ask`）。

---

## 7. 本章未解问题

1. `wcdb_start_monitor_pipe` 的管道名规则（是否含 PID/账号），多开会冲突吗。
2. `chatService` 里 `import { watch }` 是死代码还是用在别处（我 grep 不到 `watch(`，但可能有别名调用）。
3. `apiMessageWorker` 线程池并发度（`apiMessageMapperPool.ts`，5.4 KB，未读）。
4. 官方构建的 `main.js` 是否还含仓库版那么多注释/调试日志（压缩后无法判断）。
