# 40 · 原生数据层：真实 C ABI、JSON 结构、游标语义

> 本章的依据有两个来源，务必分清：
> - **二进制真实导出表**：`snippets/pe-inspect.js exports` 解析官方分发包 `wcdb_api.dll`（`evidence/wcdb_api-dll-exports.txt`，115 个按名导出）。
> - **调用点与解析逻辑**：官方构建 `/dist-electron/wcdbWorker.js`，以及仓库源码 `electron/services/wcdbCore.ts`（行号可点）。
>
> 仓库的 `docs/third-party-components.md` **与真实二进制不一致**，差异见 §2。

---

## 1. 加载链路（顺序不能乱）

从 `evidence/asar-dist-electron/wcdbWorker.js`（bootstrap 段，偏移 25185 起）还原：

```text
koffi.load(wcdb_api.dll)
   ↓
wcdb_runtime_policy_version()  必须 === 1464206081        ← 不满足即 -103 / 透传负值
   ↓
wcdb_authenticate(token, port, issuedAtMs)  必须 === 0     ← 缺 grant 即 -1008
   ↓
wcdb_init()
   ↓
wcdb_set_my_wxid(handle, wxid)        （旧文档写的是 set_my_account_id）
   ↓
wcdb_open_account(path, key, &handle)
   ↓
wcdb_get_sessions / wcdb_open_message_cursor …
   ↓
wcdb_close_account(handle) → wcdb_shutdown()
```

依赖注意：`wcdb_api.dll` 需要同目录的 `WCDB.dll`（9.2 MB，腾讯 WCDB 本体）与 `SDL2.dll`（2.4 MB）。`WCDB.dll` 本身**不导出任何 `wcdb_*` 符号**（它的导出全是 C++ mangled，见 `evidence/wcdb-dll-exports.txt`），它是被 `wcdb_api.dll` 静态/动态使用的实现库，不是 ABI 层。

> 这解释了仓库 `docs/third-party-components.md` 的说法："WCDB 实现 = 一个动态库，通过 koffi 以 C ABI 加载"。**真正要实现的 ABI 在 `wcdb_api.dll` 这一层**，`WCDB.dll` 只是它的依赖。

---

## 2. 文档声明 vs 二进制真实导出（差异表）

`evidence/abi-doc-vs-dll-diff.txt`：文档声明 95 个，DLL 实际 115 个。

**只在 DLL 里、文档完全没提的 21 个：**

| 符号 | 为什么重要 |
|---|---|
| `wcdb_authenticate` | 授权握手，见 `50-weflow-expiry.md` |
| `wcdb_runtime_policy_version` | 策略版本常数闸门 |
| `wcdb_set_my_wxid` | 文档写作 `wcdb_set_my_account_id`，**名字不对** |
| `wcdb_open_message_cursor_with_key` | 带 key 的游标，可能用于「不先 open_account 也能读单库」 |
| `wcdb_set_monitor` | 旧式监视回调（新式是 `*_monitor_pipe`） |
| `wcdb_probe_fts_schema` | FTS 表结构探测 |
| `wcdb_ai_*` 共 13 个 | 一整族「面向 AI 的聚合查询」接口：`ai_get_recent_messages`、`ai_get_messages_before/after`、`ai_get_message_context`、`ai_get_conversation_between`、`ai_get_search_message_context`、`ai_get_session_messages`、`ai_get_session_summaries`、`ai_search_sessions`、`ai_query_timeline`、`ai_query_topic_stats`、`ai_query_session_candidates`、`ai_query_source_refs` |
| `wcdb_add/update/delete_custom_emoticon` | 表情包写操作 |

**只在文档里、DLL 没有的 1 个：** `wcdb_set_my_account_id`（已改名）。

> 结论：**不要拿仓库文档当 ABI 契约**。要以 `evidence/wcdb_api-dll-exports.txt` 为准。

### 2.1 `wcdb_ai_*` 这一族值得单独说

委托方的目标是"读群聊 → 生成摘要"。官方把"给 AI 用的读取接口"直接做进了原生层，而且**不是简单分页**：

- `ai_get_recent_messages` / `ai_get_messages_before` / `ai_get_messages_after` —— 以某条消息为锚点向前/向后取上下文（比 offset 分页更适合"围绕事件"的取数）。
- `ai_get_message_context` / `ai_get_search_message_context` —— 给定命中消息取上下文窗口。
- `ai_get_conversation_between` —— 两个参与者之间的对话。
- `ai_query_topic_stats` / `ai_query_timeline` —— 话题聚合与时间线。
- `ai_query_session_candidates` —— 会话候选（源码 `wcdbCore.ts:50` 有对应绑定）。

`[推断]` 这些是官方"Agent/摘要"功能的后端；对委托方而言，它们比 `get_messages(limit,offset)` 更贴合"按时间窗取一批消息做摘要"的需求。签名未拿到（文档没有、反编译只见到符号名），标 `[未验证]`。

---

## 3. 核心函数签名（文档有 + 二进制有，可信）

```c
int32 wcdb_init();
int32 wcdb_shutdown();
int32 wcdb_purge_memory();
int32 wcdb_open_account(const char* path, const char* key, _Out_ int64* handle);
int32 wcdb_close_account(int64 handle);
void  wcdb_free_string(void* ptr);                 // 所有 out_json 都由它释放

int32 wcdb_set_my_wxid(int64 handle, const char* wxid);
int32 wcdb_get_sessions(int64 handle, _Out_ void** outJson);
int32 wcdb_get_messages(int64 handle, const char* username, int32 limit, int32 offset, _Out_ void** outJson);
int32 wcdb_get_message_count(int64 handle, const char* username, _Out_ int32* outCount);
int32 wcdb_get_message_by_id(int64 handle, const char* sessionId, int32 localId, _Out_ void** outJson);
int32 wcdb_get_message_by_svrid(int64 handle, const char* sessionId, const char* svrid, _Out_ void** outJson);

int32 wcdb_open_message_cursor(int64 handle, const char* sessionId, int32 batchSize, int32 ascending,
                               int32 beginTimestamp, int32 endTimestamp, _Out_ int64* outCursor);
int32 wcdb_fetch_message_batch(int64 handle, int64 cursor, _Out_ void** outJson, _Out_ int32* outHasMore);
int32 wcdb_close_message_cursor(int64 handle, int64 cursor);
int32 wcdb_set_message_cursor_projection(int64 handle, int64 cursor, int32 projection);
int32 wcdb_open_message_cursor_with_key(...);      // 签名未知 [未验证]

int32 wcdb_search_messages(int64 handle, const char* sessionId, const char* keyword, int32 limit,
                           int32 offset, int32 beginTimestamp, int32 endTimestamp, _Out_ void** outJson);

int32 wcdb_get_contact(int64 handle, const char* username, _Out_ void** outJson);
int32 wcdb_get_group_members(int64 handle, const char* chatroomId, _Out_ void** outJson);
int32 wcdb_get_group_member_count(int64 handle, const char* chatroomId, _Out_ int32* outCount);
int32 wcdb_get_group_nicknames(int64 handle, const char* chatroomId, _Out_ void** outJson);
int32 wcdb_get_chat_room_ext_buffer(int64 handle, const char* chatroomId, _Out_ void** outJson);

int32 wcdb_start_monitor_pipe();
void  wcdb_stop_monitor_pipe();
int32 wcdb_get_monitor_pipe_name(_Out_ void** outName);

// 保护/授权（文档未列）
int32 wcdb_runtime_policy_version();
int32 wcdb_authenticate(const char* token, int32 port, int64 issuedAtMs);
void  VerifyUser(int64 hwnd, const char* message, _Out_ char* outResult, int maxLen);
```

`VerifyUser` 的签名是从调用点原样抄的（`wcdbWorker.js` 偏移 42818）：

```js
this.wcdbVerifyUser = this.lib.func('void VerifyUser(int64 hwnd, const char* message, _Out_ char* outResult, int maxLen)')
```

它接收一个窗口句柄 —— 意味着**它可能会弹 UI**。委托方若要无头运行，必须绕开或接受它永远不被调用。

---

## 4. 状态码（`[确认-源码]`）

`wcdbCore.ts:412-445` 的 `formatInitProtectionError()` 是唯一权威映射，`0` 为成功：

| 码 | 含义 | 是否"初始化期" |
|---|---|---|
| `-1` | 未配置 WCDB 实现（库路径为空） | 是 |
| `-3` | 未找到 `session.db`；**也用于"消息数据库缓存为空"** | 是/运行期 |
| `-4` | 找到 session.db 但打不开（密钥/权限/损坏） | 是 |
| `-5` / `-6` / `-7` / `-8` / `-9` | schema 层：读不到结构 / 没有会话表 / 没有排序时间列 / 没有最后消息时间列 / 两列配置冲突 | 是 |
| `-10` / `-11` | 读会话行数失败 / 回写排序时间失败且事务回滚 | 是 |
| `-101` | 保护上下文不可用（**不是"杀毒软件"**） | 是 |
| `-102` | 检测到系统时间回拨 | 是 |
| `-103` | 组件完整性/策略版本校验失败 | 是 |
| `-104` | 检测到调试或分析环境 | 是 |
| `-105` | 离线防回拨状态损坏或无法保存 | 是 |
| `-1000` `-1005`…`-1010` | 同 `-101` 一类的"环境"错误族 | 是 |
| `-1008` | **缺授权 grant**（`wcdb_authenticate` 未调用/未拿到凭证） | 是 |
| `-2301` / `-2302` / `-2303` | 原生实现调用异常 / 初始化异常 / 未成功初始化 | 是 |
| `-3001` / `-3002` | 找不到 `db_storage` / 找不到 `session.db` | 是 |
| `-3003` / `-3004` | 句柄无效 / 恢复连接失败 | 运行期 |

`-7` 在 `wcdbCore.ts:4089-4091` 被单独翻译成 `message schema mismatch：当前账号消息表结构与程序要求不一致` —— 这说明**消息表结构会随微信版本变化**，是升级时的第一个断点。

---

## 5. 游标（cursor）语义 —— 增量拉取能不能用

### 5.1 机制

`wcdbCore.ts:4033-4146` 是完整的客户端封装：

```ts
openMessageCursor(sessionId, batchSize, ascending, beginTimestamp, endTimestamp, projection)
  → { cursor }                         // 失败返回 { success:false, error }
fetchMessageBatch(cursor) → { rows, hasMore }
closeMessageCursor(cursor)
```

- `beginTimestamp` / `endTimestamp` 由 `normalizeRange()`（`wcdbCore.ts:1593-1603`）归一化：`end <= 0` 时取 `Date.now()`；`begin > end` 时把 end 拉平到 begin。
- `normalizeTimestamp()`（`wcdbCore.ts:1583-1591`）：`> 1e12` 视为毫秒并除以 1000；结果 `clamp` 到 int32 上限 `2147483647`。
- **时间戳精度是「秒」**（int32），API 层也是秒（`HTTP-API.md` 的 `timestamp` 字段注释为"秒级 Unix 时间戳"）。
- `projection`：`'full' | 'journey'`，`'journey'` 走 `wcdb_set_message_cursor_projection(handle, cursor, 1)`；若该符号不存在或返回非 0，**静默回退 full**（`wcdbCore.ts:4094-4102`）—— 这是个隐蔽的降级，不要假设拿到的一定是 journey 投影。

### 5.2 `-3` 的特殊处理

`openMessageCursor` 返回 `-3`（`WCDB_STATUS_NO_MESSAGE_DB`，消息库缓存为空，常见于重装后）时，会 `forceReopen()` 并重试一次，且有 15s 冷却（`wcdbCore.ts:4024-4031`, `4058-4078`）。

### 5.3 能否用于「只取上次之后的新消息」

**能用，但有三个边界必须自己处理**（`[确认-源码]` + 推理）：

1. **同秒多条**：时间戳只到秒，`beginTimestamp = 上次最大 timestamp` 会把"同一秒里已经取过的那几条"再取一遍 → 必须用 `messageKey` 去重。去重键的构造见 §6.1，它包含 `localId` 与 `sortSeq`，因此在同一秒内仍可区分。
2. **撤回/删除**：撤回在微信里通常是**新增一条 `10000/10002` 系统消息**（`messagePushService.ts:757` 用 `localType === 10000 || 10002` 且内容含"撤回"来识别），原消息被删除。游标按 `create_time` 推进，删除不会让游标错位；但**"被删掉的那条"你会永远拿不到**，只能靠反撤回触发器（见 `20-weflow-datamodel.md` §6）。
3. **`sort_seq` 才是排序真身**：`buildMessageKey` 与 `getRowInt(row, ['sort_seq'], createTime*1000)`（`apiMessageMapping.ts:543`）表明，同秒排序靠 `sort_seq`，不是 `create_time`。只按时间做增量会漏掉"服务器补写、时间戳早于水位线"的消息（例如漫游消息回填）。

### 5.4 实测未做

没有真实库密钥 → 没有跑过"断点续拉"的实测。`[未验证]` 的具体项：`batchSize` 上限、单游标寿命、`beginTimestamp` 精确到秒时的边界是否含端点（`>=` 还是 `>`）。

---

## 6. JSON 结构

### 6.1 原生行（`fetchMessageBatch` / `getMessages` 的 `rows[]`）

字段名由 `apiMessageMapping.ts` 的真实读取键确定（该文件是 `chatService` 映射逻辑的逐行拷贝，`apiMessageMapping.ts:5-17` 自述）：

| 字段 | 类型 | 来源/说明 |
|---|---|---|
| `local_id` | number | 库内自增 ID，**会话内唯一** |
| `server_id` | number \| string | 服务器 ID，**16 位以上整数**；见 §6.4 |
| `local_type` | number | 见 `20-weflow-datamodel.md` 的枚举 |
| `create_time` | number | **秒**级 Unix 时间戳 |
| `sort_seq` | number | 同秒排序用；缺省回退 `createTime*1000` |
| `message_content` | string \| Buffer | 明文或 hex/base64 |
| `compress_content` | string \| Buffer | **zstd** 压缩内容（优先使用） |
| `is_send` | number | 1 = 我发出的 |
| `computed_is_send` | number | 原生侧已修正过的 is_send，优先于 `is_send` |
| `sender_username` | string | 群聊里是成员 wxid；私聊可能为空 |
| `real_sender_id` | number | 反撤回缓存里有；疑似行内发送者 rowid |
| `status` / `upload_status` / `download_status` | number | 消息状态位 |
| `server_seq` / `origin_source` / `source` | number | 服务端序号与来源 |
| `packed_info_data` | BLOB | 打包信息（视频文件名等） |
| `WCDB_CT_message_content` / `WCDB_CT_source` | number | WCDB 列压缩类型标记 |
| `db_name` / `table_name` / `_db_path` | string | 该行来自哪个库/表；用于构造 messageKey |
| `deleted_at` | number | 仅反撤回缓存表有 |

**权威列清单可直接从 DLL 里的触发器 SQL 读出**（`evidence/wcdb_api-ascii-strings.txt` 中的反撤回触发器）：

```sql
OLD.local_id, OLD.server_id, OLD.local_type, OLD.sort_seq, OLD.real_sender_id, OLD.create_time,
OLD.status, OLD.upload_status, OLD.download_status, OLD.server_seq, OLD.origin_source, OLD.source,
OLD.message_content, OLD.compress_content, OLD.packed_info_data,
OLD.WCDB_CT_message_content, OLD.WCDB_CT_source
```

这 17 列就是**消息表的真实列集**（至少微信 4.x 这一版），比任何博客都可靠。

### 6.2 会话行（`getSessions` 的 `sessions[]`）

`chatService.ts:1256-1358` 的读取键：

| 字段 | 说明 |
|---|---|
| `username` | 会话 ID：私聊 `wxid_*`，群聊 `*@chatroom`，公众号 `gh_*` |
| `type` | 会话类型（2 = 群，见 API 文档示例 `"type": 2`） |
| `remark` / `nick_name` / `alias` | 展示名优先级：`remark > nick_name > alias > username` |
| `summary` / `digest` / `last_msg` | 会话摘要 |
| `last_msg_type` | 最后一条消息类型 |
| `last_timestamp` / `last_msg_time` / `sort_timestamp` | 时间（秒） |
| `unread_count` | 未读数 —— **实时检测的关键信号**，见 `10-weflow-arch.md` |
| `last_msg_sender` / `last_sender_display_name` | 最后发言人 |

### 6.3 HTTP 层消息对象

见 `30-weflow-http-api.md` §3。这里是原生行 → API 对象的映射函数 `mapRowsToMessagesLite()`（`apiMessageMapping.ts:536-585`）。

### 6.4 一个必须知道的精度陷阱

`server_id` 是 16 位以上整数（例如 `6116895530414915131`，约 6.1e18），**超过 JS `Number.MAX_SAFE_INTEGER`（9.007e15）**。WeFlow 用了两道防线：

1. `wcdbCore.ts:1564-1577` 的 `parseMessageJson()`：检测到 `"server_id": <16+ 位整数>` 时，**在 `JSON.parse` 之前用正则把它改写成字符串**。原文：

```ts
const needsInt64Normalize = /"server_id"\s*:\s*-?\d{16,}/.test(raw)
if (!needsInt64Normalize) return JSON.parse(raw)
const normalized = raw.replace(/("server_id"\s*:\s*)(-?\d{16,})/g, '$1"$2"')
return JSON.parse(normalized)
```

2. `apiMessageMapping.ts:232-293` 的 `normalizeUnsignedIntegerToken()` 额外保留一份 `serverIdRaw` 字符串。

我实测了 koffi 对 `int64` 出参的行为（`evidence/weflow-abi-probe.txt`）：

```text
OK   int32 (_Out_ int64*) rc=1 out[0]=126437556710 typeof=number -> written
```

`typeof` 是 `number` —— 即 koffi 把 int64 落到 JS 双精度浮点。`126437556710` 还在安全区内，但 `6116895530414915131` 会丢精度。**所以：自己写客户端时，凡是 int64 字段必须按字符串处理，不能信任 JSON 数字。**

---

## 7. 监视管道（实时性的底座）

```c
int32 wcdb_start_monitor_pipe();                     // 启动原生侧监视
int32 wcdb_get_monitor_pipe_name(_Out_ void** outName);  // 拿到管道名
void  wcdb_stop_monitor_pipe();
int32 wcdb_set_monitor(...)                          // 旧式回调，仍在导出表里
```

客户端侧（`wcdbCore.ts:290-355`）用 `net.createConnection(pipePath)` 连上去，按行读 JSON：

- `\0` 被归一化成 `\n`，`}\s*{` 被拆成两行 —— 说明管道两端的分隔符约定不统一（注释明写"macOS 侧可能使用 '\0' 或无换行分隔"）。
- 解析出 `parsed.action || 'update'` 作为事件类型。
- 断线后 3 秒重连（`scheduleReconnect`，`wcdbCore.ts:358-372`），带 `generation` 防串代。

`[未验证]`：管道里 `action` 的取值全集、单条消息的 payload 结构。源码只把它当 `(type, json)` 透传，没有 schema 校验。

---

## 8. 复现清单

| 目标 | 命令 |
|---|---|
| 拿真实导出表 | `node snippets/pe-inspect.js exports "<wcdbDir>\wcdb_api.dll"` |
| 拿文档/二进制差异 | 见 `evidence/abi-doc-vs-dll-diff.txt` |
| 验证 `_Out_ int64*` 行为 | `node snippets/weflow-abi-probe.js` |
| 看初始化顺序 | `node snippets/ctx-grep.js <wcdbWorker.js> 'runtime_policy|authenticate'` |

## 9. 本章未解问题

1. `wcdb_open_message_cursor_with_key` 的签名与用途（是否允许"只给单库 key、不开账号"）。
2. `wcdb_ai_*` 全族的签名与返回结构。
3. 监视管道 `action` 的全集与 payload schema。
4. `packed_info_data` 的二进制布局（视频文件名、图片元数据都在里面）。
5. `get_messages` 的 `limit` 上限（HTTP 层写 1~10000，原生层未验证）。
6. `-101` 之后的 DLL 是否还允许 `wcdb_free_string` 等无状态调用（我的探针没试，怕影响判断）。
