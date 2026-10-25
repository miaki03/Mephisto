# 微信 4.x 本地库：已实测的核心事实（F1–F10）

> **生成方式**：`python E:\study\wxread\tools\probe_facts.py > E:\study\research\chatlog\evidence\wxread-sharding-wal.txt`
> **环境**：WeChat 4.1.15.56 · 账号 `wxid_gg7tf5htsvdo32_13d0` · Python 3.14.7 · SQLite 3.50.4
> **快照时刻**：2026-10-25 13:11（微信在运行，库是活的）
>
> ⚠️ 下面的行数是**某一刻**的值。同一小时内两次采集已出现 89,210 → 89,215 的漂移。
> 引用数字时请标采集时刻，不要把它当常量。

---

## 0. 三条自我更正（先看这段，否则会按旧结论写代码）

| 旧结论（错的） | 实测真相 | 代价 |
|---|---|---|
| 「多数内容走 `compress_content`，是 zstd 帧」 | **`compress_content` 恒为空**（0/89,215）。zstd 负载在 **`message_content`**，由 `WCDB_CT_message_content == 4` 标记 | 按旧逻辑读正文会全部读空 |
| 「只解主库，WAL 里未 checkpoint 的最新消息会缺」 | WAL 帧可以用同一套页解密（已证），但**当前四个库的 WAL 头盐代比所有帧都新**，即 0 个可用帧。现在读是**完整**的 | 会误以为有数据缺失，去做无用的补偿 |
| 「一个会话只落在一个分片，按表是否存在来探测」 | 分片**按时间滚动**，**40 个会话跨两个分片**。`Msg_0bccbd00…` 两片合计 **21,216** 条，只读一片只见 8,473 | 丢一半历史 |

---

## F1 · 分片按时间滚动，一个会话可以跨片

| 分片 | 表数 | 行数 | 时间跨度 |
|---|---|---|---|
| `message_0.db` | 120 | 57,179 | 2026-07-11 21:31 → 2026-10-25 13:11 |
| `message_1.db` | 64 | 32,036 | 2025-08-04 09:18 → 2026-07-11 20:47 |

- 40 个表在**两个分片里同名共存**；80 个只在 `message_0`，24 个只在 `message_1`。
- 跨片行集的 `server_id` **交集为 0**（抽 8 个表全部为 0）→ 是时间切分，不是复制。
- **编号不含时间语义**：`message_0` 才是**最新**的。按 mtime 枚举，别看编号。

**证据**：`evidence/wxread-sharding-wal.txt` 的 F1 段。
**影响**：读取必须 `glob message_*.db` **全枚举并取并集**；不能用"找到就返回"。

---

## F2 · `Name2Id` 每分片独立且互不一致

| 项 | 值 |
|---|---|
| `message_0.Name2Id` / `message_1.Name2Id` 行数 | 462 / 223 |
| 同一 rowid 指向不同人 | **223**（即 message_1 全部） |
| 同一人拿到不同 rowid | **129** |

**影响**：`real_sender_id` **只能在行所在的分片内解析**成 wxid，解析完再跨片合并；先合并再查必然张冠李戴。

---

## F3 · `sort_seq` 是可靠水位，`local_id` 不是

- `sort_seq = create_time * 1000 + 毫秒`，实测 `sort_seq // 1000 == create_time` 恒成立 → **跨分片全局可比**。
- `sort_seq` 排序后 `create_time` 逆序：0–1 处（极少数迟到写入）。
- `local_id` 逆序：最多 **47** 处 → **绝对不能当水位**。
- 水位写入规则：取 `max(sort_seq)`，每次回看 6 小时重叠窗口兜住迟到写入。

**影响**：增量主键 = `sort_seq`；`uid` 兜底用 `(sort_seq, local_id)`，不要带分片名（分片会轮转改名）。

---

## F4 · WAL：帧可解，但当前 0 帧可用

WAL 头是**明文**，页内容是**密文**，且与主库同一套页布局：

```
magic=377f0682  psize=4096  header_salt=<32bit>/<32bit>  frames=1018
frame = [pgno(4)][ntrunc(4)][salt1(4)][salt2(4)][cksum(8)] + encrypted_page(4096)
```

四个库的一致现象：

| 库 | 头部 salt1 | 帧的 salt1 范围 | 头部是否比所有帧新 |
|---|---|---|---|
| `message_0` | `28deb44f` | `28deb0ec..28deb44e` | **是** |
| `message_1` | `31022be8` | `31022be4..31022be7` | **是** |
| `session` | `e92c8e27` | `e92c8955..e92c8e26` | **是** |
| `contact` | `342bfa10` | `342bf5f7..342bfa0f` | **是** |

即：WAL 刚被 reset（头换成新一代盐），**新一代还没写任何帧**；文件里 4 MB 内容是**多代旧帧的残留**（预分配，不截断）。

**解密能力已证**：`message_0.db-wal` 第 9 帧写入第 1 页，用同一套页解密后头部正是 `53514c69746520666f726d6174203300` = `"SQLite format 3\0"`。

**正确算法**（已实现于 `tools/wxsource.py`）：
1. 读头 32 字节 → `psize`、`salt1`、`salt2`；
2. 顺序遍历帧，**盐不匹配立即停止**（不是跳过）；
3. 只应用 `≤ 最后一个 commit 帧`（`ntrunc != 0` 为 commit）；
4. 第 1 页帧按 page-1 布局解（保留 salt 槽）。

**影响**：把全部 1,018 帧照单全收会**静默写坏库**。这条是 F4 唯一必须记住的禁令。

---

## F5 · 压缩负载在 `message_content`

| 项 | 值 |
|---|---|
| 总行数 | 89,215（快照时刻） |
| `WCDB_CT_message_content = 0` | 64,038 → `message_content` 是**明文 str** |
| `WCDB_CT_message_content = 4` | 25,177 → `message_content` 是**zstd 帧 bytes**（`28 b5 2f fd`） |
| `compress_content` 非空 | **0 / 89,215** |
| `message_content` 空但 `compress_content` 有值 | **0** |

**影响**：正文只读 `message_content`；`compress_content` 保留为兜底但不进主路径。

---

## F6 · 同一列可能返回 `str` 或 `bytes`

| CT | `message_content` | `compress_content` | 条数 |
|---|---|---|---|
| 0 | `str` | `str`（空串） | 64,038 |
| 4 | `bytes` | `str`（空串） | 25,177 |

**影响**：必须按 storage class 分派。`bytes(str)` 会抛 `TypeError`；空串 `''` 会被 `if value` 判为假——两个坑都要显式处理。

---

## F7 · 语料规模与正文长度

- 89,215 条消息 / **144 个有消息的会话**（共 184 张 `Msg_` 表）/ 210 个会话登记。
- 正文长度：均值 **551**、p95 **1,824**、最大 **115,126**、空 **5**。
- 类型分布（base，含实测子类型）：

| base | 条数 | 子类型 |
|---|---|---|
| 1 | 64,986 | 文本 |
| 49 | 8,376 | 57 引用 ×4,990 / 5 链接 ×1,070 / 2000 转账 ×431 / 33 小程序 ×370 |
| 47 | 7,159 | 表情 |
| 3 | 5,211 | 图片 |
| 10000 | 1,767 | 系统提示 |
| 34 | 1,175 | 语音 |
| 43 | 389 | 视频 |
| 50 | 107 | 音视频通话 |
| 48 | 28 | 位置 |
| 42 | 12 | 名片 |
| 11000 | 5 | 子类型 17（未识别） |

**影响**：正文均值 551 字符 → 全量入库约 **50–60 MB**；`max=115,126` 说明**必须做截断**（默认 2,000 字符），否则单条就能拖垮摘要。

---

## F8 · 性能（单机 Python，实测）

| 操作 | 耗时 |
|---|---|
| 解密+落盘 `message_0.db`（33.5 MB） | 270 ms |
| 解密+落盘 `message_1.db`（16.3 MB） | 204 ms |
| 解密 `session.db`（0.15 MB） | **121 ms** |
| 解密 `contact.db`（2.25 MB） | 111 ms |
| 全量解码 89k 条 + 建索引 | 545 ms |
| `LIKE '%公告%'` 全表（89k 行） | 32 ms |
| 单会话扫描 | 6 ms |
| 最近 24 小时切片 | 0.3 ms |

**注意**：`session.db` 只有 0.15 MB 却要 121 ms —— 时间全在 **PBKDF2-SHA512 256,000 次**（每个库各自的 salt 要单独派生，约 100 ms/库）。
**影响**：① 换语言没有性能收益，整条链 < 1.5 s；② 派生密钥要按 `(路径, salt, mtime)` 缓存，否则每库白付 100 ms；③ 中文检索用 `LIKE` 足够，不必上 FTS。

---

## F9 · `contact.db` 全是小写表名

- 18 张表，关键三张：`contact`（2,560）、`chat_room`（67）、`chatroom_member`（3,153）。
- `contact` 关键列：`username / remark / nick_name / alias / local_type / is_in_chat_room / chat_room_type`。
- **群显示名 = `remark` 优先，否则 `nick_name`**（抽样：`remark` 空、`nick_name` 12 字，如 `Mi**作台`）。
- `contact_label` 19 条是微信自带标签（初中同学/家人/朋友），只作参考，不自动导入。

**影响**：会话显示名从 `contact` 取；发送者显示名同样走这张表，查不到就回落 wxid。

---

## F10 · 会话类型分布

| kind | 数量 |
|---|---|
| group（`@chatroom`） | 58 |
| direct（`wxid_`） | 96 |
| official（`gh_`） | 40 |
| openim（`@openim`） | 4 |
| work_wechat（`@qy_*`） | 1 |
| other | 11 |
| 其中 `is_hidden=1` | 2 |

`other` 里是 `weixin` / `notifymessage` / `filehelper` / `brandsessionholder` 这类系统会话。
**影响**：`SessionTable.type` 恒为 0，分类只能靠 username 规则；`@openim`、`@qy_*`、`brandservice*` 三种必须在规则表里显式处理，否则会被误判成 `direct`。

---

## 对存储设计的直接影响

| 事实 | 设计决定 |
|---|---|
| F1 | 分片按 mtime **全枚举**取并集，`shard` 只作溯源字段 |
| F2 | 发送者**先**在片内解析成 wxid，再合并；库里只存 wxid + 显示名快照 |
| F3 | 增量水位 = `sort_seq`；`uid` 兜底用 `(sort_seq, local_id)`，不带分片名 |
| F4 | WAL 按盐代过滤 + 首帧即停 + 只取到最后 commit；`frames_applied` 入账可审 |
| F5/F6 | 正文只读 `message_content`，按 storage class 分派，跳过空串 |
| F7 | 正文截断 2,000 字符，全量入库约 50–60 MB |
| F8 | 无需换语言；按库缓存派生密钥；检索用 `LIKE` |
| F9 | 会话名/发送者名从 `contact` 取（`remark` → `nick_name` → wxid） |
| F10 | 分类规则覆盖 `@openim` / `@qy_*` / `brandservice*` / 系统会话 |

---

## 边界与未验证

| 项 | 状态 |
|---|---|
| 微信 4.1.15.56 的全部结论 | 已实测 |
| 其它微信版本的加密参数是否相同 | **未验证**（改 `wxsource.decrypt_page()` 的参数即可） |
| `message_2.db` 出现时的行为 | **未验证**（按 glob 枚举，理论上无需改代码） |
| `local_type=11000/17` 是什么 | **未识别**（5 条，保留原文不丢弃） |
| WAL 有真实待应用帧时的端到端效果 | **未验证**（当前无法制造该状态；算法按 SQLite 语义实现并已单测到"帧可解"） |
| 媒体（图片/语音/视频）二进制 | 不解密，仅占位符 |
| 分片轮转是否改名 | 未知（uid 设计已规避） |
