# 决策记录：语言、数据库与读取口径

> 状态：**已定稿并开工**。每条都写了「什么情况下该推翻」——不是不可讨论，是别在没新证据时反复。
> 依据：`docs/core-facts.md`（F1–F10）+ `research/chatlog/55-weflow-persistence.md`（四层分解）+ 本机实测。

---

## D1 · 脚本语言：**Python，不重写**

**决定**：采集/解密/存储/摘要/邮件全部用 Python。

**理由**
1. **零依赖已达成**：`sqlite3`、`compression.zstd`（3.14+）、`ctypes`（DPAPI）、`smtplib` 都是标准库；只有 AES 需要 `cryptography`。
2. **性能不是瓶颈**（F8）：解密 33.5 MB=270 ms、全量解码 89k 条=545 ms、整链 < 1.5 s。换语言收益为零。
3. **DPAPI 只要 6 行 ctypes**。Node 走这条路要背 `koffi` 原生 FFI —— 那正是 WeFlow 必须 `electron-rebuild` 的最脆一环，我们不接这个包袱。
4. 已有约 1,000 行经过实测的代码（SQLCipher 自解、WAL 盐代、分片枚举、内容解码），重写等于把踩过的坑再踩一遍。

**唯一前提**：Python **3.14+**（`compression.zstd`）。若需要在 3.12/3.13 上跑，`pip install zstandard` 即可，`wxsource.py` 已写了回退分支。

**什么情况下推翻**：需要做成给普通人用的图形客户端，且安装包体积/运行环境成为硬约束。即便如此，也应该是「GUI 用 TS、采集仍走 Python」，而不是重写采集层。

---

## D2 · 数据库：**SQLite 单文件 + 手写 SQL + 规则收口进视图**

**决定**
- 引擎：SQLite（单文件 `data/chatlog.sqlite3`）。
- 访问方式：**手写 SQL，不用 ORM**。
- 分类/过滤口径：**写成 SQL 视图**（`v_message_feed`、`v_session_stats`），CLI、导出、将来的摘要都读同一口径。

**理由**
1. 场景正好是 SQLite 的主场：**单写入者 + 事务性水位 + 幂等 upsert**。`INSERT ... ON CONFLICT DO NOTHING` 就是去重本体。
2. ORM 会把 `ON CONFLICT` 语义藏起来，而我们恰恰靠它做增量幂等；5 张表、1 条写路径，不值得引入一层映射。
3. 口径散在 CLI/导出/摘要三处，三个月后必然不一致；收进视图后**改分类只改一处**，不用回填历史。
4. 89k 条 / 十年内量级 10⁵–10⁶，SQLite 毫无压力；列存（DuckDB）的优势要到 10⁷ 才体现，而它写并发弱、无成熟 upsert。

**已否决的方案**
| 方案 | 否决理由 |
|---|---|
| DuckDB | 写并发弱、无成熟 upsert；数据量用不上列存 |
| JSONL 文件 | 增量、去重、跨会话聚合全得手写一遍 = 重写数据库 |
| PostgreSQL | 为本机脚本装服务，纯负担 |
| ORM（SQLAlchemy） | 藏住幂等语义，且只有 5 张表 |

**什么情况下推翻**：需要多机共享、或有人要并发写同一份数据。那时迁移到 Postgres 的成本主要在 DDL 和三处 SQL，可控。

---

## D3 · 入库口径：**全量入库，读时过滤**

**决定**：`sync` 把**所有会话**（含私聊、公众号）写入库；群/噪声/时间窗的筛选放在**读时**。

**理由**：一次全量 89k 条成本 545 ms，反而比"写时筛选"更省心——将来想加口径不用重扫源库。代价是库里多几十 MB，可接受。

**什么情况下推翻**：隐私上要求"私聊永不落库"。那就在 `sync` 加 `--group-only`，属于策略开关，不改结构。

---

## D4 · 中文检索：**先用 `LIKE`，暂不上 FTS**

**决定**：v1 用 `LIKE '%关键词%'`；不建 FTS5 索引。

**理由（实测）**
- 89k 行全表 `LIKE` = **32 ms**，够用。
- FTS5 的 `trigram` 分词器**匹配不了 2 字中文**（实测 `公告` 命中 0 条）；`unicode61` 对无空格中文把整段当一个 token，同样不行。这正是 WeFlow 要带 `jieba-wasm` 的原因。

**升级路径（真需要时）**：`jieba` 分词写入一张 `message_token` 表 + FTS5 `unicode61`，独立一步，不动主表与口径。

---

## D5 · 数据落地与安全

| 项 | 决定 |
|---|---|
| 规范库 | `E:\study\wxread\data\chatlog.sqlite3` |
| 明文暂存 | `E:\study\wxread\_decrypted\`（或 `%TEMP%`），**同步结束即删** |
| 版本控制 | `data/`、`_decrypted/` 全部进 `.gitignore` |
| 源库 | **只读**：只 `open('rb')`，绝不写入 `F:\wxcache`、`C:\Users\Miaki\Documents\Tencent Files` |
| passphrase | 等同账号凭据；不打印、不入库、不写日志（只打掩码 `23a3…1cf8`） |
| 明文库加密 | **不做**（本机单用户）。若要加密，应 DPAPI 包一层密钥——独立决策，本次不引入 |

---

## D6 · 为 QQ 预留（本轮唯一的结构性前瞻）

**决定**：`session` 与 `message` 增加 `channel TEXT`（`wechat` / `qq`）与 `account TEXT`，现在只写 `wechat`。

**理由**：QQ 的库是「单库多表」，微信是「每会话一表」，会话 id 的来源不同。预留两个字段的成本几乎为零，等 QQ 结论回来再改主键则要迁移。这是**唯一**为 QQ 做的结构让步，其余一律等结论。

**什么情况下推翻**：QQ 侧最终确认完全读不了（见 `research/chatlog/72-qq-local-recon.md`），那这两个字段留着也不会造成负担。
