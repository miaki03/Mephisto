# wxread —— 自己持有的微信消息读取器

> 不依赖 WeFlow、不需要联网、没有授权校验、没有到期日。
> 目前状态：**能读真实群消息**（P0/P1 完成），摘要与邮件未做。
> 核心事实（F1–F10，含**三条已推翻的旧结论**）见 [`docs/core-facts.md`](docs/core-facts.md)。

---

## 1. 已验证跑通的东西

| 能力 | 状态 | 证据 |
|---|---|---|
| 从 WeFlow 配置解出 DB passphrase（DPAPI + AES-GCM，不加载任何组件） | ✅ | `research/chatlog/evidence/wechatdb-key-probe.txt` |
| 用自研 SQLCipher 4 解密微信库到明文副本 | ✅ | `tools/discover_cipher.py --dump` |
| 会话表读取（210 个会话） | ✅ | `tools/read_messages.py sessions` |
| 定位任意会话的消息表 | ✅ | `Msg_<md5(session_id)>`，16/16 命中 |
| 解码消息正文（zstd / 明文 / appmsg XML 标题） | ✅ | `tools/read_messages.py messages` |
| 还原发送者（`real_sender_id` → `Name2Id`） | ✅ | 同上 |
| 归一化 JSON 导出 | ✅ | `_decrypted/demo-export.json`（50 条） |

实测样例（`45365759247@chatroom`，2026-10-25 当天数据）：

```
[10-25 09:53] lt=1(1/0)            趣味猜口令🤔猜"我"的诞生地 …
[10-25 09:53] lt=141733920817(49/33) [appmsg] 🎁  0 元抽美味小方砖
[10-25 09:53] lt=141733920817(49/33) [appmsg] 🥰周年庆·赢万杯免单！
```

---

## 2. 三条命令

```powershell
cd E:\study\wxread\tools

# 1) 列群会话
python read_messages.py sessions --groups-only --limit 20

# 2) 读某群最近 20 条
python read_messages.py messages --group "12345678@chatroom" --limit 20

# 3) 导出归一化 JSON（下游摘要可直接吃）
python read_messages.py export --group "12345678@chatroom" --limit 200 --out out.json
```

首次运行会自动解密所需库到 `_decrypted/`（按 mtime+size 缓存，库没变就不重复解）。

要重新生成 [`docs/core-facts.md`](docs/core-facts.md) 背后的全部数字：

```powershell
python probe_facts.py > E:\study\research\chatlog\evidence\wxread-sharding-wal.txt
```

（只打印计数、长度与脱敏 id，不打印任何聊天正文；明文副本在退出时删除。）

依赖：Python 3.12+（3.14 自带 `compression.zstd`）+ `cryptography`。**不需要装 SQLCipher。**

---

## 3. 逆向出来的关键事实（都已实测）

### 3.1 加密参数

| 项 | 值 |
|---|---|
| 算法 | SQLCipher 4，AES-256-CBC |
| 页大小 / 保留区 | 4096 / 80（IV 16 + HMAC 64） |
| 密钥派生 | `PBKDF2-HMAC-SHA512(passphrase, salt=page1[0:16], iter=256000, dklen=32)` |
| passphrase 形态 | **配置里存的 64 hex 解码成 32 字节**（不是 enc_key，也不是 64 字符 ASCII） |
| 第 1 页布局 | `[0:16]` salt（顶替 SQLite 魔数）、`[16:4016]` 密文、`[4016:4032]` IV、`[4032:4096]` HMAC |

**踩过的坑**：`WeFlow-config.json` 里的 `decryptKey` 名字有误导性，它不是 `enc_key`。另外微信的库是 **WAL 模式**，明文首页第 18/19 字节是 `02 02` 而不是 `01 01`——我最初的断言按 legacy 模式写死，导致正确参数被判失败。

### 3.2 结构

- 消息表：`Msg_<md5(session_id)>`，例如 `Msg_e3bee5e8fbb3ba84b46859ba71c297f3`（对应 `45365759247@chatroom`）。
- **分片按时间滚动，一个会话可以跨片**：`message_1.db` = 2025-08-04→2026-07-11，`message_0.db` = 2026-07-11→今；**40 个会话两片同名共存**，`server_id` 不重叠。**编号不代表新旧**（`message_0` 才是最新的），必须全枚举 `message_<n>.db` 取并集（注意排除同目录的 `message_fts.db` / `message_resource.db`，它们不是分片）。例：`Msg_0bccbd00…` 两片合计 21,216 条，只读一片只见 8,473 条。
- **增量水位用 `sort_seq`**：`sort_seq = create_time*1000+ms`，跨片可比；`local_id` 最多 47 处逆序，**不可**作水位。
- 发送者：`real_sender_id` → **同一分片**的 `Name2Id.rowid` → `user_name`。`Name2Id` 每片独立（223 个 rowid 指向不同人），**不能先合并再解析**。
- 会话表 `SessionTable` 的 `type` 字段**恒为 0**，区分群/私聊要看 `username` 后缀（`@chatroom`）。
- `local_type` 是复合值：`base = lt & 0xFFFFFFFF`，`sub = lt >> 32`。真实数据里 `141733920817 = (33<<32)|49`，即 appmsg 子类型 33。

### 3.3 内容

- 群文本消息正文带 `wxid:\n` 前缀，需剥掉（且只在确认是有 wxid 前缀时才剥）。
- 正文在 **`message_content`**：`WCDB_CT_message_content==4` 时是 **zstd 帧 bytes**（25,177 条），`==0` 时是**明文 str**（64,038 条）。**`compress_content` 恒为空**（0/89,215 条），不要读它。
- 同一列可能返回 `str` 或 `bytes`，必须按 storage class 分派——对 `str` 调 `bytes()` 会抛 `TypeError`。
- appmsg（基类型 49）是 XML，`<title>` 里是可读标题。

---

## 4. 还没做的

| 项 | 说明 |
|---|---|
| **WAL 增量** | 帧能用同一套页解密（已证：第 9 帧写入 page 1，解出 `SQLite format 3`），但必须**按盐代过滤**：盐不匹配立即停、只取到最后 commit 帧。当前四个库的 WAL 头都是**新一代盐、0 个可用帧**，文件里 4 MB 是多代旧帧残留 → 现在读是完整的。详见 `docs/core-facts.md` F4 |
| 媒体 | 图片/语音/视频不解密（`[图片]` 占位） |
| 落库 | 目前直接导出 JSON，尚未建 `chatlog.sqlite3` 与增量水位 |
| 摘要 | 未接模型 |
| 邮件 | 未接 SMTP |
| 其他库 | `contact.db`（联系人/群名）、`sns.db`（朋友圈）已能解，但还没做字段映射 |

---

## 5. 安全与边界

- **passphrase 等同账号凭据**：拿到它 + 库文件就能读全部聊天记录。不要提交、不要外传。
- 本工具**只读**：只读源库、只写 `_decrypted/` 下的明文副本。不解密媒体、不装触发器、不改微信数据目录。
- 明文副本落在 `_decrypted/`，用完应清理。
- 微信升级大版本后，若 passphrase 仍有效则本工具照常工作；若加解密参数变了，改 `decrypt_file()` 里的参数即可（不影响其他模块）。

---

## 6. 与 WeFlow 的关系

读取链已完全不依赖 WeFlow 组件。当前**唯一的耦合**是 passphrase 的来源：它存在 WeFlow 的配置文件里（DPAPI 保护）。

- 只要配置文件还在，本工具与 WeFlow 是否可用、是否过期、是否被卸载都无关。
- 若配置被清理，需要重新取一次 passphrase（那一步才需要 hook/调试器）。
