# 30 · HTTP API：接口全表、鉴权、推送、ChatLab，以及「能否当纯数据服务用」

> 一、证据分层：接口清单与字段来自 `docs/HTTP-API.md`（仓库自带，632 行）+ 源码路由表核对；行号来自 `electron/services/httpService.ts`（2416 行）。
> 二、**我没有连上真实服务**（需微信库与授权），因此所有响应体都是"源码字段名 + 官方文档示例"，标 `[确认-源码]` 或 `[确认-文档]`，不标 `[确认-实测]`。

---

## 1. 启用方式（两条路）

### 1.1 正常路径：UI 设置

`docs/HTTP-API.md:5-14`：设置页启用 `API 服务`；默认 `127.0.0.1:5031`；状态与端口持久化，重启自动恢复。

对应的持久化键（真实配置文件 `%APPDATA%\WeFlow\WeFlow-config.json` 里可见）：

```json
{ "httpApiEnabled": false, "httpApiPort": 5031, "httpApiHost": "127.0.0.1",
  "httpApiToken": "", "messagePushEnabled": false }
```

> 我在这台机器上读到的是 `httpApiEnabled: false`、`httpApiToken: ""` —— 即**默认关闭且默认无 token**。

### 1.2 程序化路径：IPC

`main.ts:5448` 有 `ipcMain.handle('http:start', ...)` → `httpService.start(port || 5031, bindHost)`；`main.ts:5452` 是 `http:stop`；`main.ts:5458` 返回 `{ running, port, mediaExportPath }`。
`main.ts:5664` 在 `app.whenReady()` 里调 `httpService.autoStart()`，它读配置决定要不要起（`httpService.ts:353-365`）。

**这意味着：只要配置文件里 `httpApiEnabled=true` 且 token 非空，应用一启动 HTTP 服务就起来了，不需要人点 UI。**（`[确认-源码]`）

---

## 2. 鉴权（三条通道，缺 token 一律拒绝）

`httpService.ts:406-425`：

```ts
private verifyToken(req, url, body): boolean {
  const expectedToken = String(this.configService.get('httpApiToken') || '').trim()
  if (!expectedToken) {
    console.warn('[HttpService] Access denied: httpApiToken not configured')
    return false                       // ← 未配置 token = 拒绝一切，不是"放行"
  }
  // 1) Authorization: Bearer <token>
  // 2) ?access_token=<token>
  // 3) POST body {"access_token": "..."}
}
```

- 比较用 `crypto.timingSafeEqual`（`httpService.ts:399-404`），先比长度再比内容 —— 常量时间。
- **"未配置即全拒"** 是个好设计，但要注意：这意味着"忘了设 token"表现为服务 401，而不是裸奔。
- 健康检查是唯一豁免（`httpService.ts:458`：`pathname !== '/health' && pathname !== '/api/v1/health'` 才做鉴权）。
- CORS 只允许 `localhost` / `127.0.0.1` 来源（`httpService.ts:431-436`）。

---

## 3. 接口全表（文档 + 路由表双向核对）

路由分派在 `httpService.ts:458-519`，与 `docs/HTTP-API.md:24-34` 一致。**共 15 条（含 `:id` 与通配 2 条）。**

| # | 方法 | 路径 | 作用 | 源码行 |
|---|---|---|---|---|
| 1 | GET/POST | `/health` | 健康检查，无鉴权 | 465 |
| 2 | GET/POST | `/api/v1/health` | 同上 | 465 |
| 3 | GET | `/api/v1/push/messages` | **SSE 推送流** | 467 |
| 4 | GET/POST | `/api/v1/messages` | 取消息 | 469 |
| 5 | GET/POST | `/api/v1/sessions` | 会话列表（`format=chatlab` 时为 ChatLab Pull） | 471 |
| 6 | GET | `/api/v1/sessions/:id/messages` | ChatLab Pull 单会话消息 | 474-483 |
| 7 | GET/POST | `/api/v1/contacts` | 联系人 | 484 |
| 8 | GET/POST | `/api/v1/group-members` | 群成员 | 486 |
| 9 | GET | `/api/v1/sns/timeline` | 朋友圈时间线 | 488 |
| 10 | GET | `/api/v1/sns/usernames` | 朋友圈发布者 | 491 |
| 11 | GET | `/api/v1/sns/export/stats` | 朋友圈导出统计 | 494 |
| 12 | GET | `/api/v1/sns/media/proxy` | 朋友圈媒体代理 | 497 |
| 13 | POST | `/api/v1/sns/export` | 导出朋友圈 | 500 |
| 14 | GET/POST | `/api/v1/sns/block-delete/{status,install,uninstall}` | 朋友圈防删开关 | 503-511 |
| 15 | DELETE | `/api/v1/sns/post/:postId` | 删单条朋友圈 | 512 |
| 16 | GET | `/api/v1/media/*` | 访问已导出的媒体文件 | 515, 570 |

> 「朋友圈防删」与「删朋友圈」这两组接口值得单独注意：前者会在朋友圈库上装触发器（同反撤回思路），后者**会写你的数据库**。委托方只要"读群消息"，这两个能力应当被明确排除。

### 3.1 `/api/v1/messages` 参数

`docs/HTTP-API.md:127-141`：

| 参数 | 必填 | 说明 |
|---|---|---|
| `talker` | 是 | 会话 ID；私聊 `wxid_*`，群聊 `*@chatroom` |
| `limit` | 否 | 默认 100，范围 1~10000 |
| `offset` | 否 | 默认 0 |
| `start` / `end` | 否 | `YYYYMMDD` 或时间戳；纯日期 `end` 会扩到当天 23:59:59 |
| `keyword` | 否 | 按显示文本过滤 |
| `chatlab` / `format` | 否 | 输出 ChatLab 格式 |
| `media` | 否 | 导出媒体并返回地址（别名 `meiti`） |
| `image` / `voice` / `video` / `emoji` | 否 | `media=1` 时逐类开关（`voice` 有别名 `vioce`，是错拼，说明历史兼容） |

**注意 `talker` 是必填** —— 没有"给我所有群最近 N 条"的批量接口。多群摘要必须自己循环会话。

### 3.2 `/api/v1/messages` 响应

顶层：`success` / `talker` / `count` / `hasMore` / `media.{enabled,exportPath,count}` / `messages[]`。

单条（`docs/HTTP-API.md:165-182` + `Message` 接口 `chatService.ts:71-145`）：

| 字段 | 说明 |
|---|---|
| `localId` | 库内自增 ID |
| `serverId` | 服务器 ID（**字符串**语义，见 `40-*.md` §6.4） |
| `localType` | 类型枚举 |
| `createTime` | **秒** |
| `isSend` | 1 = 自己发的；0 = 别人；`null` = 未知 |
| `senderUsername` | 群聊成员 wxid |
| `content` | 归一化后的可读文本（图片显示为 `[图片]`） |
| `rawContent` | 原始内容（XML / 压缩原文） |
| `parsedContent` | 解析后文本 |
| `replyToMessageId` | 引用目标消息的 `serverId`，仅引用消息有 |
| `quote{platformMessageId,sender,accountName,content,type}` | 被引用消息快照 |
| `mediaType` / `mediaFileName` / `mediaUrl` / `mediaLocalPath` | `media=1` 时才有 |

`quote` 的结构是**去规范化快照**（把被引消息的内容抄了一份），这对摘要很友好：不需要二次查询就能知道"在回复什么"。

---

## 4. 主动推送：只有 SSE，没有 webhook

这是委托方最关心的能力之一，结论明确：

- **只有 SSE**：`GET /api/v1/push/messages`，`text/event-stream`（`httpService.ts:543-568`）。
- **没有**"配置回调 URL"的字段：我把 `WeFlow-config.json` 的键全列了一遍（`messagePushEnabled/FilterMode/FilterList/Position`），**没有任何 `webhook`/`callback`/`url` 键**。`[确认-源码]`
- 事件名：`message.new`、`message.revoke`（`docs/HTTP-API.md:77`；`messagePushService.ts:28` 的类型定义 `type MessagePushEventName = 'message.new' | 'message.revoke'`）。
- 事件 payload 字段（`docs/HTTP-API.md:80-109`）：

```json
{"event":"message.new","sessionId":"xxx@chatroom","sessionType":"group",
 "rawid":"1234567890123456789","avatarUrl":"https://example.com/group.jpg",
 "sourceName":"李四","groupName":"项目群","content":"[图片]","timestamp":1760000123}
```

- **去重键 = `event + rawid`**（文档明写"建议接收端按 event + rawid 去重"）。`rawid` 就是消息的 serverId 字符串。
- **SSE 有重放缓冲**（这是文档没写的关键能力）：`httpService.ts:146-147` 定义 `messagePushReplayLimit = 1000`、`messagePushReplayTtlMs = 10 分钟`；客户端可用 `Last-Event-ID` 头或 `?lastEventId=` 拉回最近事件（`httpService.ts:305-312`）。**断线 10 分钟内可补事件，超过就永久丢失。**
- 心跳：每 25 秒发 `: ping`（`httpService.ts:525-541`），防止中间设备掐连接。
- 开关：`messagePushEnabled !== true` 时直接 403（`httpService.ts:544-547`）。

**判断**：想要"新消息 → 发邮件"，必须有一个常驻进程订阅 SSE。做不到"配个 URL 让 WeFlow 主动 POST"。`[确认-源码]`

---

## 5. ChatLab 格式

ChatLab 是第三方聊天归档/分析工具的数据交换格式，WeFlow 实现了它的 Pull 协议，把自己伪装成"远程数据源"。

### 5.1 差异：ChatLab 是"给分析用的扁平模型"

原生 JSON 是**库行直出**（保留 XML、hex、zstd 原文、`localType` 数字）；ChatLab 是**归一化后的语义模型**：

| 维度 | 原生 JSON | ChatLab |
|---|---|---|
| 成员 | 无（要另调 group-members） | 内嵌 `members[]`（`platformId/accountName/groupNickname/avatar`） |
| 消息类型 | `localType` 数字 | `type`（0 起的小整数） |
| 群昵称 | 无 | `groupNickname`，群聊优先取群昵称 |
| 引用 | `replyToMessageId` + `quote` | `replyToMessageId`（**无 quote 快照**） |
| 媒体 | `mediaUrl` + 本地路径 | `mediaPath` |
| 分页 | `offset` | `sync.{hasMore,nextSince,nextOffset,watermark}` |

### 5.2 增量拉取的官方姿势

`GET /api/v1/sessions/:id/messages?since=<秒>&limit=5000`（`docs/HTTP-API.md:367-440`）：

- `limit` 默认且最大 **5000**。
- 返回带 `sync` 块：`hasMore` / `nextSince` / `nextOffset` / `watermark`。
- 循环：`hasMore ? since=nextSince&offset=nextOffset : done`。

**这是整套 HTTP API 里唯一"为增量设计"的接口**，比 `/api/v1/messages` 的 `offset` 分页更适合长期采集。

### 5.3 哪个格式最适合喂 LLM

| 格式 | 适合度 | 理由 |
|---|---|---|
| ChatLab JSON | **最高** | 已是语义模型；成员表内嵌（省一次 join）；`content` 已归一化；无 XML/hex 噪声 |
| `/api/v1/messages` 原生 | 中 | 有 `localType` 与 `quote`，但需自己处理 `[图片]` 之类的占位、XML 残留 |
| Markdown 导出 | 中低 | 人类可读，但丢字段、解析成本高 |
| Sql / Excel | 低 | 面向表格分析，不适合对话上下文 |

（导出器的实现：`electron/services/export/parsers/messageParser.ts` 与 `formatters/{Html,Json,Markdown,Sql,Excel,Txt,WeClone,ChatLab}Formatter.ts`。`[确认-存在]`，未逐一读实现。）

---

## 6. 媒体链路（只讲原理，不实现）

`docs/HTTP-API.md:706-745`：

- `media=1` 时，服务端把图片/语音/视频/表情**先导出到本地缓存目录**，再返回 `http://127.0.0.1:5031/api/v1/media/<talker>/<kind>/<file>`。
- 目录形如 `...\api-media\<talker>\{images,voices,videos,emojis}\`。
- 路径穿越有防护：`path.resolve` 后必须仍在 base 内（`httpService.ts:570-579`）。
- 支持的 Content-Type 只有 7 种（png/jpg/jpeg/gif/webp/wav/mp3/mp4），其它一律 `application/octet-stream`。

解密链路的原理（分别在 `imageDecryptService.ts`、`nativeImageDecrypt.ts`、`videoService.ts`、`voiceTranscribeService.ts`）：

| 媒介 | 原理 | 依赖 |
|---|---|---|
| 图片 `.dat` | XOR key 解头部 + AES key 解主体；`WXGF` 容器还要再 unwrap | 原生插件 `weflow-image-native-*.node`（导出 `decryptDatNative(inputPath, xorKey, aesKey?)`） |
| 视频 | 先解 `.dat`，再用 **wasm** 解 H.265/自定义封装 | `electron/assets/wasm/wasm_video_decode.wasm`（3.6 MB） |
| 语音 | `.silk` → 转码 → ASR | `silk-wasm` + `sherpa-onnx-node`（含 onnxruntime） |
| 表情 | CDN URL + AES key 直接拉 | 无 |

两个 key 的来源：`imageXorKey` / `imageAesKey`，存在配置里、以 `safe:v10` 前缀加密（Electron `safeStorage`），见 `41-weflow-keyprovider.md`。

---

## 7. 核心判断：能不能把 WeFlow 当纯 HTTP 数据服务，绕开 UI？

### 7.1 结论

**能，但不是"脱离 Electron"。** 准确表述：

> WeFlow 的 HTTP API 层与 UI 层**没有耦合**，可以在**完全不打开窗口**的情况下提供全部数据接口；但 HTTP 服务本身跑在 Electron 主进程里，因此仍然需要 `WeFlow.exe` 常驻。

### 7.2 证据

1. **服务自己会预热数据库，不依赖 UI 的 connect**（`httpService.ts:196-205`）：

```ts
this.server.listen(this.port, this.host, () => {
  this.running = true
  // 主动预热数据库连接与消息库索引：HTTP 服务可能经 http:start 独立启动
  // （未走 GUI 的 connect/warmup），避免首批请求因原生消息库缓存为空而整页丢消息。
  void this.ensureDbReady().catch(...)
  try { this.getApiMapperPool().warmup() } catch {}
  ...
})
```

注释里"未走 GUI 的 connect/warmup"是作者自己承认存在"无 GUI 启动"这条路径。

2. **有静默启动开关**（`main.ts:5534`）：

```ts
const startInBackground = onboardingDone && isSilentStartupEnabled()
```

配合 `silentStartup` 配置键（真实配置里存在，我读到为 `false`），可以不显示主窗口。

3. **有托盘**（`main.ts:5580-5589` 创建 `Tray`），关窗口不等于退出。

4. **HTTP 服务与 UI 的唯一耦合是配置读写**（`ConfigService`），而配置是磁盘上的 JSON。

### 7.3 但仍然绕不开的三件事

| 绕不开的东西 | 原因 | 证据 |
|---|---|---|
| `WeFlow.exe` 必须运行 | HTTP 服务是 Electron 主进程里的 `http.createServer` | `httpService.ts:165` |
| 授权握手（`wcdb_authenticate`） | 每次 worker 调用都带 `protectionAuth` | `main.ts:230100` 附近的 `callWorkerWithProtectionAuth` |
| 微信必须登录且已同步 | 数据源是本地 SQLCipher 库 | `wcdb_open_account` 需要 key |

### 7.4 对委托方的实际含义（只陈述，不建议）

- 想要"一个 API 就能拿全量群消息" → **成立**，且官方的 `ChatLab Pull` 接口已经内置增量游标。
- 想要"没有 Electron 的 100MB 二进制" → **不成立**，除非自己实现 `40-*.md` 那 115 个导出。
- 想要"新消息 → webhook" → **不成立**，只有 SSE，必须自建订阅端。
- 想要"绕过过期" → **不成立**，见 `50-*.md`：过期判定在 DLL 里，HTTP 层只是被它拖死（`-101` 会让所有接口返回空/错误）。

---

## 8. 一次成功的调用序列（照着做就能通）

> 前提：WeFlow 已成功连过库（`wcdb.log` 里有 `native protection ok` 与 `open ok`）。

```bash
# 0) 确认服务在跑（无需 token）
curl -s http://127.0.0.1:5031/api/v1/health
# {"status":"ok"}

# 1) 拿 token（从配置读，别猜）
#    %APPDATA%\WeFlow\WeFlow-config.json -> httpApiToken

# 2) 找群会话
curl -s -H "Authorization: Bearer $TOKEN" \
  "http://127.0.0.1:5031/api/v1/sessions?keyword=项目群&limit=5"

# 3) 取最近 50 条（原生格式）
curl -s -H "Authorization: Bearer $TOKEN" \
  "http://127.0.0.1:5031/api/v1/messages?talker=<xxx@chatroom>&limit=50"

# 4) 增量拉（ChatLab，推荐）
curl -s -H "Authorization: Bearer $TOKEN" \
  "http://127.0.0.1:5031/api/v1/sessions/<xxx@chatroom>/messages?since=1760000000&limit=5000"

# 5) 订阅实时
curl -N "http://127.0.0.1:5031/api/v1/push/messages?access_token=$TOKEN"
```

`[未验证]`：以上 5 条我**没有在真机上跑过**（本机 `httpApiEnabled=false`、无授权上下文）。字段与行为均来自源码与文档；一旦真机跑通，请把输出补进 `evidence/`。

---

## 9. 本章未解问题

1. `hasMore` 的计算方式（是 `count === limit` 还是真去 count 一次）。
2. `media=1` 的导出是同步阻塞还是后台任务（大批量时可能超时）。
3. SSE 在 `messagePushEnabled=false` 时 403，但 `httpApiEnabled=false` 时服务根本不起 —— 两个开关的组合语义文档没写。
4. `/api/v1/messages` 的 `offset` 深翻页性能（底层是 `LIMIT/OFFSET` 还是游标）未验证。
5. ChatLab `type` 的枚举值域（文档只给了 `0`）。
6. 官方 `wcdb_ai_*` 那族接口**没有暴露到 HTTP**，是否另有入口未查。
