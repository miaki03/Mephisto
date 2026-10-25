# 71 · NTQQ 本地消息库直读：可行性结论

> **本章的性质**：全部结论来自「读公开项目的源码 + 多源交叉」，**没有一个字节是我亲手解开的**（本机无 QQ、无样本库）。
> 因此每条都标了证据形态；凡需亲手解密才能确认的，标 `[未验证]`。
> 证据目录：`evidence/qq/ntqq-localdb-*.md`（5 份，含 fetch 日志与 3 个失败 URL）。

---

## 1. 先纠正任务书里的一个混淆

任务书 §4.1 把 `Msg3.0.db` 当作 NTQQ 的候选库名。**这不是 NTQQ 的库。**

| 架构 | 库文件 | 路径 |
|---|---|---|
| 旧版 PCQQ（9.7.x 及更早，Electron 之前） | `Msg3.0.db` | `…\Tencent Files\<QQ>\Msg3.0.db` |
| **NTQQ（9.9.x，当前版本）** | **`nt_msg.db`** | `…\Tencent Files\<QQ>\nt_qq\nt_db\nt_msg.db` |

证据：`evidence/qq/ntqq-localdb-path.md`（三级独立来源一致，含可复现命令 `type nt_msg.db | more +1025 >`）。

多一层坑：**两个库都在真实 SQLite 内容前加了 1024 字节私有头**，必须先剥掉，否则任何 SQLite 库都打不开。

> 这条"1024 字节头"是三级来源交叉确认的（`qq-nt-db` 的 README 给了 `more +1025` 命令；`nt_msg_db_util` 的文档写了偏移；`ntdb_unwrap` 用 VFS 自动跳过）。

---

## 2. 加密与密钥

### 2.1 加密参数（`[确认-源码]`）

| 参数 | QQ（NTQQ） | 微信（4.x，对照） |
|---|---|---|
| 加密方案 | SQLCipher 4 | SQLCipher 4 |
| `page_size` | 4096 | 4096 |
| **`kdf_iter`** | **4000** | **256000**（差 64 倍） |
| 页 MAC | **HMAC_SHA1** | **HMAC_SHA512** |
| KDF | PBKDF2_HMAC_SHA512 | 同 |
| 私有头 | **1024 字节** | 无 |
| 密钥形态 | **16 字节 ASCII 口令**（形如 `#8xxxxxxxxxxx@uJ`） | 32 字节 hex |

**两个直接后果**：

1. **微信的解密代码不能直接套到 QQ**：至少要把 `kdf_iter` 从 256000 改成 4000、把页 MAC 从 SHA512 改成 SHA1，并且要跳过 1024 字节头。
2. `PRAGMA` **顺序敏感**：`page_size` 必须在 `key` 之前设置，否则解出来是乱码（这是 SQLCipher 的常识，`nt_msg_db_util` 的文档也强调了）。

### 2.2 密钥从哪来：**不是内存扫描，是 hook**

这一点很关键，因为它决定稳定性。

`evidence/qq/ntqq-localdb-crypto-key.md` 记录了 `QQBackup/qq-win-db-key` 的实现原理（**读了全量源码**）：

```text
1. 静态定位：在 wrapper.node 里搜字符串 "nt_sqlite3_key_v2: db=%p zDb=%s"
2. 从该字符串的引用出发找 LEA 指令，定位到使用它的代码
3. 用 PE 的 .pdata 段做二分查找，反推出该函数的 RVA（这样能适配版本漂移）
4. 动态截获：以 DEBUG_ONLY_THIS_PROCESS 启动 QQ，在函数入口下 0xCC 断点
5. 断点命中后读 R8 寄存器（x64 fastcall 第 3 个参数 = pKey），
   校验"长度 16 且全部是可打印 ASCII"
```

关键事实：

- **每次登录都必然经过 `nt_sqlite3_key_v2`** —— 所以"抓一次"是可行的。
- 这解释了为什么 QQ 侧取 key **反而比微信稳**：微信 4.1.10+ 之后密钥不再常驻内存，被动扫描失效；QQ 是"打开库时必然调用这个函数"，主动断点就能拿到。
- 有个**易踩的坑**：同样会抓到长度 20 的 key，那是 `login.db` 的**硬编码**密钥，与 `nt_msg.db` 无关。只按"长度/可打印"过滤会拿错。

`[未验证]`：密钥是否跨重启稳定、是否与账号绑定（三份来源都没写）。这直接决定"抓一次还是每次抓"。

---

## 3. 表结构（`[确认-源码]`，来自 `nt_msg_db_util` 的字段文档）

两张主表：

| 表 | 含义 | 参考规模（某样本库） |
|---|---|---|
| `c2c_msg_table` | 私聊 | 约 131.9 万行 |
| `group_msg_table` | **群聊** | 约 54 万行 |

**列名是纯数字字符串**（这是 NTQQ 最反直觉的地方）：

| 列名 | 语义 |
|---|---|
| `40001` | `msgId` |
| `40021` | `peerUid`（会话对方/群号） |
| `40010` | `chatType`（`1` = c2c，`2` = group） |
| `40050` | `msgTime`（**Unix 秒**） |
| `40011` | `msgType` |
| `40013` | 方向（自己发/别人发） |
| `40020` / `40033` | 发送者 |
| `40030` | 对方 QQ 号 |
| **`40800`** | **消息体 BLOB（Protobuf）** |

消息体 `40800` 内部是 `MsgBody → repeated MsgContent`，其中：

- `45002` = 子类型
- `45101` = 文本内容

> **外部资料常写的 `48000` 已被排除，canonical 是 `40800`。** 这一点在 `ntqq-localdb-schema.md` 里做了辨析。

**这意味着解析工作量的大头在 Protobuf**：不是"取出一列文本"，而是"解一层 BLOB，里面还有未知字段"。

`QQDecrypt` 自己承认"完整实现暂无" —— 也就是说**没有现成的、字段全覆盖的解析器**。

---

## 4. 公开项目清单（真实 fetch 到的，star 为 2026-10-25 实取）

| 仓库 | star | 最近 push | 能力 | 可信度 |
|---|---|---|---|---|
| [QQBackup/qq-win-db-key](https://github.com/QQBackup/qq-win-db-key) | 1110 | 2026-10-13 | **取 key**（调试器断点） | 高（读过源码） |
| [QQBackup/QQDecrypt](https://github.com/QQBackup/QQDecrypt) | 237 | 2026-10-13 | 教程站（4 篇正文，带版本确认） | 高 |
| [Tealina28/QQNT_Export](https://github.com/Tealina28/QQNT_Export) | 79 | 2026-07-17 | 导出**已解密**的库 | 中高 |
| [QQBackup/nt_msg_db_util](https://github.com/QQBackup/nt_msg_db_util) | 27 | 2026-08-19 | **解密 + 结构化导出**（PRAGMA 与字段文档最全） | **最高** |
| [artiga033/ntdb_unwrap](https://github.com/artiga033/ntdb_unwrap) | 19 | 2026-10-11 | 一键解密 + **直读 VFS 扩展**（不落明文盘） | 高 |
| [mobyw/GroupChatAnnualReport](https://github.com/mobyw/GroupChatAnnualReport) | 74 | 2025-02-10 | 年度报告（停更 20 个月） | 中 |

**"能解密"的判据**：有可执行实现 + 明确 PRAGMA + 版本确认（`nt_msg_db_util`、`ntdb_unwrap`、`qq-win-db-key`）。
**"只能导出"**：`QQNT_Export`、`GroupChatAnnualReport`（需要先有解密后的库）。

> 全部仓库在 2026-10-25 仍然存活（没有像微信侧 `chatlog` 那样被 DMCA/律师函清掉）。这是 QQ 侧生态相对"安全"的一个侧面证据。

---

## 5. 三档判断

| 档位 | 判断 | 理由 |
|---|---|---|
| **无密钥直读**（以为库没加密） | ❌ **卡死** | SQLCipher 4 + 1024 字节私有头，没有 key 连 schema 都读不出 |
| **取 key 后解密读** | ✅ **能通** | 参数、偏移、字段、公开实现齐备；已交叉确认到 QQ 9.9.32（2026-07） |
| 长期稳定运行 | ⚠️ **取决于取 key 的跟随能力** | 函数 RVA 随版本变；注入/调试器可能被检测 |

**所以"本地库直读"作为一条路线是成立的，但它不是"无痛路线"** —— 痛点在 §6。

### 5.1 卡点清单（具体到"卡在哪"）

| 卡点 | 具体表现 | 严重度 |
|---|---|---|
| 取 key 需要调试器或 Frida | 要提权（`DEBUG_ONLY_THIS_PROCESS`）；RVA 随版本变（脚本用"字符串+LEA+.pdata"做自适配缓解） | 高 |
| 注入检测 / 封号风险 | 调试器 attach 到 QQ 进程，可能被检测 | 中高 |
| `40800` 是嵌套 Protobuf，且含大量未知字段 | 文本能解（`45101`），但图片/语音/文件/引用等需要逐字段猜 | 高 |
| 群库可能页损坏 | 需要"跳过损坏页"的读法 | 中 |
| 1024 字节头的结构 | **没有任何公开描述**（magic、reserve、IV/HMAC 偏移都不明） | 低（只需跳过，不需理解） |

---

## 6. 与微信 WCDB 路线对比（工程量 / 稳定性 / 风险）

| 维度 | 微信（WeFlow/WCDB） | QQ（NTQQ 直读） |
|---|---|---|
| **取 key** | 微信 4.1.10+ 内存不再常驻 → 只能 hook/断点或降版本；WeFlow 内置 `wx_key.dll` 做扫描 | **每次登录必经 `nt_sqlite3_key_v2`** → hook 必中，**反而更稳** |
| **加密参数** | kdf_iter 256000 / HMAC_SHA512 | kdf_iter 4000 / HMAC_SHA1（弱 64 倍，**更快**） |
| **私有头** | 无 | 1024 字节（已知需跳过） |
| **表结构** | 命名列（`local_id`/`server_id`/`message_content`…）+ XML/zstd | **纯数字列名** + 嵌套 Protobuf |
| **解析生态** | 成熟（`chatlog`、各类 formatter、WeFlow 自己 8 种导出格式） | 不成熟（`QQDecrypt` 自认无完整实现） |
| **工程量** | 中等（有现成生态可抄） | **解析层要自己写** |
| **合规/生态风险** | 高（2025-10 起多起律师函删库） | 低（仓库全存活） |
| **"实时性"** | 原生管道推送（`monitor_pipe`） | 只能轮询文件 mtime / 库内容 |

**一句话总结（陈述，不建议）**：
- **取 key**：QQ 比微信**容易**。
- **解内容**：QQ 比微信**难**。
- **总工程量**：两者**相近**，但难的方向相反。

---

## 7. 复现清单

| 目标 | 做法 | 前置 |
|---|---|---|
| 确认库存在 | `dir "%USERPROFILE%\Documents\Tencent Files\<QQ>\nt_qq\nt_db"` | 本机登录过 NTQQ |
| 剥 1024 头 | `type nt_msg.db | more +1025 > nt_msg.stripped.db` | cmd |
| 取 key | `qq-win-db-key` 的 `windows_ntqq_get_key.ps1` | 管理员 + 关闭 QQ |
| 解密 | 用 `nt_msg_db_util` 的 PRAGMA 顺序 | key + 16 字节 ASCII |
| 读群消息 | `SELECT * FROM group_msg_table WHERE 40010=2` | 解密后 |

## 8. 本章未解问题

1. 1024 字节私有头的字节结构与 magic（**无任何公开描述**）。
2. SQLCipher 的 reserve 区大小、IV/HMAC 偏移。
3. 密钥是否跨版本/跨重启稳定（决定"抓一次还是每次抓"）。
4. QQ 9.9.32 之后是否换加密（issues API fetch 失败，未查）。
5. `40800` 里各消息类型（图片/语音/文件/引用/合并转发）的字段布局。
6. 群库页损坏的处理办法。
7. **我没有亲手复现任何一步** —— 本章全部为源码阅读 + 多源交叉。
