# 20 · 数据模型：库布局、表结构、`localType` 枚举、内容编码

> 证据等级说明：库布局部分有**真机枚举**（`evidence/wechat-db-layout.txt`，来自这台机器上 `F:\wxcache\xwechat_files\wxid_*` 的只读列目录）；表结构部分有**DLL 内 SQL 原文**（`evidence/wcdb_api-ascii-strings.txt`）与仓库源码读取键（行号可点）；解密后的真实行数据**没有**（无授权上下文，见 `50-*.md`）。

---

## 1. 账号目录与分库布局（真机）

### 1.1 账号目录

```text
<dbPath>\                                  ← 配置项 dbPath，本例 F:\wxcache\xwechat_files
  wxid_gg7tf5htsvdo32_13d0\                ← 账号目录 = <wxid>_<4位后缀>
    db_storage\
    FileStorage\Image / Image2\            ← 媒体（accountDirResolver.ts:127-133 用它们判断"像不像账号目录"）
```

`accountDirResolver.ts:59-119` 的解析规则（`[确认-源码]`）：

- 兼容 `wxid` 与 `wxid_xxxx` 两种写法：先按 `_` 后 4 位剥后缀得到前缀，再在 `dbPath` 下按"精确或前缀+任意后缀"扫。
- 候选排序：**有 `session.db` > 没有** → **带后缀 > 精确** → **mtime 新 > 旧**。
- `session.db` 兼容两种布局：`db_storage/session/session.db`（嵌套）与 `db_storage/session.db`（扁平）。

### 1.2 `db_storage` 下的 15 个子目录（真机）

```text
bizchat  chatbot  contact  emoticon  favorite  general  hardlink
head_image  message  migrate  MMKV  session  sns  solitaire  third_app_icon
```

### 1.3 库文件命名（真机实测，`evidence/wechat-db-layout.txt`）

| 目录 | 文件 | 说明 |
|---|---|---|
| `session/` | `session.db` | 会话表；**打开账号的入口**（`wcdb_open_account` 会先找它，找不到 `-3`） |
| `contact/` | `contact.db`、`contact_fts.db` | 联系人 + 全文索引 |
| `message/` | `message_0.db`、`message_1.db` | **消息分库，按序分片** |
| `message/` | `biz_message_0.db` | 公众号/服务号消息，独立分片 |
| `message/` | `media_0.db`、`message_resource.db` | 媒体元数据、资源 |
| `message/` | `message_fts.db` | 消息全文索引 |
| `message/` | `*.kvdb` | KV 存储（`message_1.kvdb`） |
| `message/` | `weclaw.db` | **未知来源**，见 §1.5 |
| `sns/` | `sns.db` | 朋友圈 |
| `favorite/` | `favorite.db`、`favorite_fts.db` | 收藏 |
| `emoticon/` | `emoticon.db` | 表情包 |
| `general/` | `general.db` | 通用（含 `-7` 报错里提到的"会话排序时间字段"所在？未验证） |
| `hardlink/` | `hardlink.db` | 图片/视频硬链接映射 |
| `head_image/` | `head_image.db` | 头像缓存 |
| `solitaire/` | `solitaire.db` | 接龙 |
| `bizchat/` | `bizchat.db` | 企业微信会话 |
| `chatbot/` | `chatbot_message.db` | 聊天机器人消息 |
| `migrate/` | `unspportmsg.db` | 迁移期不支持的消息 |
| `third_app_icon/` | `third_app_icon.db` | 第三方应用图标 |

除 `migrate/` 外，每个库都带 `-wal`（有的还有 `-shm`），且 `message_*.db` 还带 `.db-first.material` / `.db-incremental.material` / `.db-last.material` —— 这是 SQLCipher/WCDB 的增量校验材料文件（`[推断]`，WCDB 的 material 机制）。

### 1.4 「一个群的消息落在哪个库」

**不确定，且这是设计上的关键不确定性。** 两个事实：

1. 原生侧有 `wcdb_list_message_dbs`（导出序号 91）与 `wcdb_get_message_tables`（66）—— 说明"哪个会话在哪个库/表"是**运行期查出来的**，不是固定规则。
2. 每一行都带 `db_name` / `table_name` / `_db_path`（`apiMessageMapping.ts:24-34`），`buildMessageKey` 用它们做去重作用域（`apiMessageMapping.ts:56-72`）。

`[推断]` 微信 4.x 的规则是「消息表按会话 ID 的哈希落在某个 `message_N.db`，表名形如 `Msg_<hash>`」；`[未验证]` 具体哈希算法与表名格式（需要解密后 `sqlite_master` 才能确认）。

### 1.5 `weclaw.db` 是什么

`message/weclaw.db`（4 KB，`-wal` 为 0 字节）。名字像 "we-claw"。可能是：某个第三方工具建的空库、或 WeFlow/OpenClaw 相关组件留下的。`[未验证]`，但**建议委托方在自己的环境里先确认它是不是 WeFlow 自己建的** —— 如果是，说明工具**会往微信数据目录写文件**。

---

## 2. 消息表结构（权威：DLL 内触发器 SQL）

`wcdb_api.dll` 里装反撤回触发器时用的 SQL 直接暴露了列集（`evidence/wcdb_api-ascii-strings.txt`）。把 `OLD.` 前缀去掉就是**消息表的真实 17 列**：

```sql
local_id, server_id, local_type, sort_seq, real_sender_id, create_time,
status, upload_status, download_status, server_seq, origin_source, source,
message_content, compress_content, packed_info_data,
WCDB_CT_message_content, WCDB_CT_source
```

| 列 | 类型 | 语义 | 备注 |
|---|---|---|---|
| `local_id` | INTEGER | 库内自增 | 会话内唯一；`get_message_by_id` 用它 |
| `server_id` | INTEGER(64) | 服务器消息 ID | **> 2^53**，必须按字符串处理（`40-*.md` §6.4） |
| `local_type` | INTEGER | 类型 | 见 §3 |
| `sort_seq` | INTEGER | 同秒排序 | 缺省回退 `create_time*1000` |
| `real_sender_id` | INTEGER | 发送者 rowid | 群聊里用于区分成员 |
| `create_time` | INTEGER | **秒** | 不是毫秒 |
| `status`/`upload_status`/`download_status` | INTEGER | 状态位 | 媒体上传/下载状态 |
| `server_seq` | INTEGER | 服务端序号 | 更细的排序依据 |
| `origin_source` / `source` | INTEGER | 来源标记 | `WCDB_CT_source` 是其压缩类型 |
| `message_content` | TEXT/BLOB | 内容 | 可能是 hex/base64 |
| `compress_content` | BLOB/TEXT | **zstd 压缩内容** | 优先于 `message_content` |
| `packed_info_data` | BLOB | 打包信息 | 视频真实文件名等 |
| `WCDB_CT_message_content` | INTEGER | WCDB 列压缩类型 | 决定要不要解 zstd |

> 这 17 列是**实测过的微信 4.x 版本**的列集。`-7` 错误码（"message schema mismatch"）正是因为**不同微信版本列会变**。

---

## 3. `localType` 枚举（含"复合值"解码，这是最容易搞错的地方）

### 3.1 基础类型（`[确认-源码]`）

来源：`agentModelLedService.ts:1018-1025`、`chatService.ts:6531-6598`、`httpService.ts:1752-2436`、`messagePushService.ts:757`。

| `localType` | 语义 | 证据行 |
|---|---|---|
| `1` | 文本 | `chatService.ts:7441` |
| `3` | 图片 | `chatService.ts:6538` |
| `34` | 语音 | `chatService.ts:6556` |
| `42` | 名片 | `chatService.ts:6562` |
| `43` | 视频 | `chatService.ts:6549` |
| `47` | 表情/贴纸 | `chatService.ts:6531` |
| `48` | 位置 | `chatService.ts:6568` |
| `49` | appmsg（链接/文件/小程序/引用/转账/视频号…） | `chatService.ts:6576` |
| `50` | 音视频通话 | `chatService.ts:5519` |
| `62` | 视频（另一变体，`agentModelLedService.ts:1021` 与 43 并列） | — |
| `10000` / `10002` | 系统消息（含"撤回"提示） | `messagePushService.ts:757` |
| `266287972401` | 系统消息变体（`httpService.ts:2090` 与 10000 并列处理） | — |

### 3.2 复合值的解码规则（**这是本报告的一个独立发现**）

代码里出现过几个"怪数"：`244813135921`、`8589934592049`、`266287972401`。拆开看，它们都是 **`(高32位 << 32) | 低32位`** 的形式：

```text
244813135921  = 57   * 2^32 + 49   →  appmsg 子类型 57，基类型 49
8589934592049 = 2000 * 2^32 + 49   →  appmsg 子类型 2000，基类型 49
266287972401  = 62   * 2^32 + 49   →  子类型 62，基类型 49? 或独立类型
```

**为什么这不是巧合**：`chatService.ts:6594` 的判断把两者写成等价：

```ts
} else if (localType === 244813135921 || (content && content.includes('<type>57</type>'))) {
  const quoteInfo = this.parseQuoteMessage(content)       // 引用消息
```

也就是说 `244813135921` 与「XML 里 `<type>57</type>`」是同一件事 —— **高 32 位就是 appmsg 的 type，低 32 位是基类型 49**。

**推论（`[推断]`，但证据很强）**：

| 复合值 | 解码 | 语义 |
|---|---|---|
| `244813135921` | `(57<<32)\|49` | 引用/回复消息（appmsg type 57） |
| `8589934592049` | `(2000<<32)\|49` | 转账（appmsg type 2000） |
| `266287972401` | `(62<<32)\|49` | appmsg type 62（视频号/直播类） |
| `10000` / `10002` | 低 32 位 | 纯系统消息 |

**实务含义**：**判断类型必须先 `localType & 0xFFFFFFFF`，再看高 32 位作为子类型。** 直接拿 `localType` 跟 `1/3/34/49` 比会漏掉一大类消息 —— 这是很多解析器出错的地方。委托方自研时，这一段是最容易踩的坑。

### 3.3 覆盖率自评（对应验收标准 #2）

- 代码里显式处理的 `localType` 值：`1, 3, 34, 42, 43, 47, 48, 49, 50, 62, 10000, 10002` + 3 个复合值。
- 微信官方类型的完整值域（如 `37` 好友验证、`51` 视频号、`52` 位置共享…）**没有在代码里穷举**，说明 WeFlow 本身也不追求全枚举。
- **`[未验证]`：我无法给出"覆盖 ≥95% 实际出现类型"的统计**，因为没有解密后的数据。统计脚本已写好（`snippets/msgtype-stats.py`，走 HTTP API），一次真机运行即可产出。**这是本报告唯一一个"脚本已就绪、数据没拿到"的验收项。**

---

## 4. 内容编码：zstd / hex / base64 / XML 四层

### 4.1 解压顺序（`[确认-源码]`）

`apiMessageMapping.ts:523-530`：

```ts
function decodeMessageContent(messageContent, compressContent) {
  let content = decodeMaybeCompressed(compressContent)   // 优先 compress_content
  if (!content || content.length === 0) {
    content = decodeMaybeCompressed(messageContent)      // 回退 message_content
  }
  return content
}
```

`decodeMaybeCompressed`（`apiMessageMapping.ts:487-521`）的判定链：

```text
Buffer/Uint8Array ──▶ decodeBinaryContent
string:
  全 hex 且长度>16 且偶数 ──▶ Buffer.from(hex) ──▶ decodeBinaryContent
  全 base64 且长度%4==0 ──▶ Buffer.from(base64) ──▶ decodeBinaryContent
  其它 ──▶ 原样返回
```

`decodeBinaryContent`（`apiMessageMapping.ts:448-485`）：

```text
前 4 字节 == 0xFD2FB528（zstd magic，LE 或 BE 都试）
    ──▶ fzstd.decompress() ──▶ UTF-8
否则 UTF-8 解码；替换字符(�)占比 < 20% 就用它
否则 Latin-1
```

> 结论：**文本消息并不是"全量 zstd"**。zstd 只出现在 `compress_content` 或 hex/base64 包裹的二进制里。`fzstd` 这个依赖就是为它准备的（`package.json:70`）。

### 4.2 XML 结构消息

媒体与 appmsg 的 `content` 是一段 XML，例如图片：

```xml
<msg><img aeskey="..." encrypver="1" cdnthumburl="..." md5="..." .../></msg>
```

WeFlow 的解析方式（`chatService.ts` 里的 `extractXmlValue` / `extractXmlAttribute` / `parseImageInfo` / `parseVideo` / `parseType49Message`）：

- 用**正则**抠标签/属性，不引 XML 解析器（因为内容是"近似 XML"，经常不规范）。
- 图片抠 `md5` / `aeskey` / `encrypver` / `cdnthumburl`。
- 视频优先从 `packed_info_data` 取真实文件名（32 位 hex），失败才回退 XML（`chatService.ts:6550-6551`）。

### 4.3 引用/回复的三种表示（`[确认-源码]`）

| 形态 | 触发条件 | 解析函数 | 产出字段 |
|---|---|---|---|
| appmsg type 57 | `localType===244813135921` 或 content 含 `<type>57</type>` | `parseQuoteMessage` | `quotedContent` / `quotedSender` |
| 媒体消息内的引用 | 图片/视频/语音 | `parseMediaQuoteMessage` | 同上 |
| HTTP 层结构化的 quote | 上述任一 | `apiMessageMapping` + `httpService` | `replyToMessageId` + `quote{}` |

注意 `quotedContent`/`quotedSender` 与 `replyToMessageId`/`quote` 是**两套并行字段**：前者是内部 `Message` 接口的（`chatService.ts:95-96`），后者是 HTTP API 的（`HTTP-API.md:176-177`）。自己实现时选一套即可。

### 4.4 @ 提及

`[未验证]`。代码里没有专门的"@某人"字段；群聊里的 @ 应该编码在 XML（`<atuserlist>` 之类）。搜索 `atuserlist` / `atuser` 无命中。这意味着 **WeFlow 对外没有暴露结构化的 @ 信息**，只能从 `rawContent` 里自己抠。这对"摘要里标明谁被 @"是一个实际缺口。

---

## 5. 会话表与会话 ID 约定

| 会话 ID 形态 | 类型 | 判据 |
|---|---|---|
| `wxid_*` | 私聊（个人） | 前缀 |
| `*@chatroom` | **群聊** | 后缀（`chatService.ts:13175` 用它筛出所有群） |
| `gh_*` | 公众号 | 前缀（`bizService.ts:66`） |
| `filehelper` | 文件传输助手 | 精确匹配（`BUILTIN_OFFICIAL_HELPER_USERNAMES`） |

会话行字段见 `40-*.md` §6.2。`type` 的实际取值：文档示例给的是 `2`（群）。`[未验证]` 私聊的 `type` 值（`[推断]` 为 0 或 1）。

---

## 6. 反撤回：一个会**改写微信数据库**的能力

这是本章最需要提醒委托方的一点。

`wcdb_install_message_anti_revoke_trigger`（导出序号 88）会在**微信的消息库上创建 SQLite 触发器**，实现链路（从 DLL 内 SQL 原文还原）：

1. 建两张辅助表：

```sql
CREATE TABLE IF NOT EXISTS _weflow_anti_revoke_pending (
  session_id TEXT PRIMARY KEY, updated_at INTEGER);

CREATE TABLE IF NOT EXISTS _weflow_anti_revoke_deleted_cache (
  id INTEGER PRIMARY KEY AUTOINCREMENT, tbl TEXT NOT NULL,
  local_id INTEGER, server_id INTEGER, local_type INTEGER, sort_seq INTEGER,
  real_sender_id INTEGER, create_time INTEGER, status INTEGER,
  upload_status INTEGER, download_status INTEGER, server_seq INTEGER,
  origin_source INTEGER, source TEXT, message_content TEXT,
  compress_content TEXT, packed_info_data BLOB,
  WCDB_CT_message_content INTEGER, WCDB_CT_source INTEGER, deleted_at INTEGER);
```

2. 在消息表上装 `AFTER DELETE` 触发器：把被删的行（撤回时微信会 `DELETE` 原消息）**先抄进 `_weflow_anti_revoke_deleted_cache`**，再用 `INSERT` 拦截器把原消息**重新插回去**。

3. 于是"撤回"在库里表现为：原消息还在 + 多一条系统提示。

**关键事实**：

- 触发器的名字带 `_weflow_` 前缀，且**建在用户自己的微信库内**。这与任务书 §8.5「不写微信/QQ 的数据目录」**直接冲突**。委托方若只想要"读群聊摘要"，这个能力**必须排除**。
- 卸载接口是 `wcdb_uninstall_message_anti_revoke_trigger`，但**是否清干净、残留表会不会影响微信自身**，`[未验证]`。
- 同样的机制还有一个朋友圈版：`_weflow` 前缀的 sns 防删触发器（`wcdb_install_sns_block_delete_trigger`）。

---

## 7. 复现清单

| 目标 | 方法 |
|---|---|
| 看真实库布局 | 直接列 `<dbPath>\<wxid>\db_storage`；我这份在 `evidence/wechat-db-layout.txt` |
| 看消息表列集 | 从 `evidence/wcdb_api-ascii-strings.txt` 搜 `_weflow_anti_revoke_deleted_cache` |
| 统计 `localType` 分布 | `snippets/msgtype-stats.py`（需真机 HTTP API 或直连库） |
| 验证复合类型解码 | 对任一消息算 `t & 0xFFFFFFFF` 与 `t >>> 32` |

## 8. 本章未解问题

1. `message_*.db` 的分片规则与表名（`Msg_<hash>`？）—— 需解密后看 `sqlite_master`。
2. 一个群是否可能跨多个分片（消息量大时是否拆分）。
3. `weclaw.db` 的创建者。
4. `localType` 值的完整官方枚举（代码没穷举）。
5. @ 提及的存储位置（`atuserlist`？在 message XML 还是别处）。
6. `packed_info_data` 的二进制格式。
7. `WCDB_CT_*` 的取值含义（哪种值代表 zstd / 哪种代表明文）。
