# 60 · QQ 四条路线：科普、能力矩阵、结论

> **本章先纠正任务书附录 A.1 的一条硬结论。** 任务书说「官方 Bot 只能收到 @ 消息，收不到全量群聊」——这在 2026-10 已不成立。
> 我亲自 fetch 了官方文档并核对（2026-10-25）：
> `https://bot.q.qq.com/wiki/develop/api-v2/autogen/event/group_message_create.html`
> 页面标题即 **《群消息（全量模式）》**，原文："当机器人开启了『接收所有消息』功能后，群里的每一条消息（不限于@机器人）都会推送此事件。"
> 页面「上次更新: 10/21/2026」—— 这是一项新近开放的能力。整页原文见 `evidence/qq/official-events-group-message-create-fullmode.md`。

---

## 1. 先把 QQ 生态的四个东西分清楚

| 名字 | 是什么 | 谁在用 | 与"群聊记录"的关系 |
|---|---|---|---|
| **QQ 群** | 普通群聊（最多 200/500/2000 人档） | 所有人 | 委托方要读的东西在这里 |
| **QQ 频道（guild）** | Discord 式组织：guild → 子频道（文字/语音/公告） | 游戏/兴趣社区 | 另一套产品，**不是群** |
| **QQ 机器人** | 开放平台注册的 bot，有 AppID/Secret | 开发者 | 官方通道，只能读"授权给它的"消息 |
| **协议端**（NapCat 等） | 用真人号登录，模拟客户端收发 | 个人开发者 | 能读该号能看到的一切，但违规 |

关键认知：**"群"和"频道"是两套独立体系**。频道能做全量消息订阅，但频道不是群；群的全量消息要靠"群主授权"这个新开关。

---

## 2. 四条路线 × 八个问题（对照任务书 §4.2）

### 2.1 路线① 官方 QQ 机器人（群）

| 问题 | 答案 | 证据 |
|---|---|---|
| **能力**：能拿哪些消息 | **群里每一条消息**（含非 @），前提是群主授权"接收全量消息"。事件 `GROUP_MESSAGE_CREATE`；@ 触发另走 `GROUP_AT_MESSAGE_CREATE`；单聊走 `C2C_MESSAGE_CREATE`（**是用户↔机器人，不是用户↔用户**） | `official-events-group-message-create-fullmode.md`（我亲自 fetch 复核） |
| 消息内容覆盖 | 文本、图片/语音/视频/文件（`attachments[]`，含 `content_type`、`size`、`voice_wav_url`、`asr_refer_text`）、卡片（`ark_data`）、引用与合并转发（`msg_elements`，`message_type` 103=引用、102=聊天记录、101=并行消息） | 同上（字段表完整） |
| **实时性** | Webhook 推送（官方回调你的 HTTP 端点），非轮询 | `official-message-overview.md` |
| **历史** | **完全没有历史接口**。官方 API 索引里不存在任何 `GET .../messages`。只能靠"先收到事件再存" | `official-api-index-no-history.md` |
| **身份** | 机器人身份（不是真人）；用户侧看到的是 bot | `official-getting-started-subject-and-scope.md` |
| **风险** | 合规（官方通道）。但运营规范 5.10 明确：未经腾讯同意不得"收集、存储、抓取"QQ 信息内容与用户数据 —— **群主授权了全量，也不等于允许批量留存** | `official-business-compliance.md` |
| **数据落地** | JSON 事件体；`content` 里 @ 被替换为 `<@member_openid>` / `<@all>`；`mentions[]` 给被 @ 列表 | 同 2.1 首行 |
| **运行环境** | 任意服务端；无客户端依赖 | — |
| **可维护性** | 官方 API，稳定；但**新能力刚开放（页面 4 天前更新），字段可能变** | 页面时间戳 |

**接入门槛（这块容易误解）**：

- 个人主体**可以**创建群机器人（个人认证 5 个），但**不能公开使用**，只能加"自己是群主/管理员"的群（上限 500 个）。企业主体可公开，10 个，同主体合计 50。
- **"接收全量消息"不是后台权限项，是群主在手机 QQ 添加机器人那一刻的「设置权限」里开的开关**。原文（`official-faq-fullmessage-eligibility.md` 第 10-12 行）：

> 使用 **群主身份** 在添加 QQBot入群时，点击「设置权限」。
> 1.设置接收全量消息能力：当群主设定允许该机器人接收群内全部消息时，机器人可接收到群内所有成员在群内发言消息。

- 同页还给了两条旁证：①"设置主动推送消息能力"是另一个开关；②Agent 接入页里出现"qq 群内机器人设置为全量模式"的表述 —— 说明"全量模式"是产品内既有概念。
- 沙箱：新版管理端已取消，"管理员账号可直接测试使用，无需沙箱环境"。

> **这对委托方是重大变化**：如果目标群能说服群主加一个 bot 并勾选"接收全量消息"，那么**不需要真人号、不需要协议端、不违反用户协议**，就能拿到逐条实时群消息。代价是**没有历史**（从授权那一刻开始才有）。

### 2.2 路线② 频道机器人

| 问题 | 答案 | 证据 |
|---|---|---|
| 能力 | 频道内**全部**消息（`MESSAGE_CREATE`），但 intent `GUILD_MESSAGES (1<<9)` **仅私域机器人可订阅**；公域只有 `AT_MESSAGE_CREATE`（`PUBLIC_GUILD_MESSAGES 1<<30`） | `official-channel-message-events.md`、`official-events-intents-payload.md` |
| 实时性 | Webhook | 同上 |
| 历史 | 有限（频道消息接口） | `official-api-index-no-history.md` |
| 身份 | 机器人 | — |
| 风险 | 官方通道，合规 | `official-business-compliance.md` |
| 与群的区别 | **产品层就是两个东西**。频道有 guild→子频道结构、按 `channel_id` 分流、支持 shard（`guild_id >> 22`） | `official-guild-vs-group.md` |
| 迁移成本 | **极高**：要让"已有大量 QQ 群"的人把群搬到频道，等于重建社区 | 判断项 |
| 私密性 | 存在"私密子频道"（错误码 301004 提到关联人数上限）；频道整体能否私密 `[未验证]` | `official-channel-message-events.md` |
| 成员上限 | 返回 `member_count`/`max_members`，示例值 1000；**示例不等于平台上限** `[未验证]` | 同上 |

**结论**：对"已有大量 QQ 群"的场景，频道路线**不现实**。它解决的是"新建一个社区"，不是"读现有群"。

### 2.3 路线③ 协议端（NapCat / Lagrange.Core）

| 问题 | 答案 | 证据 |
|---|---|---|
| 能力 | **该号能看到的一切**：私聊/群/临时会话；文本/图片/语音/视频/文件/合并转发/回复/@ | `napcat-message-group-format.md`、`onebot11-events-and-api.md` |
| 实时性 | WebSocket 推送（`message.group` / `message.private`），亚秒级 | `napcat-onebot-event.md` |
| 历史 | `get_group_msg_history`（**不在 OneBot 11 标准内**，是 go-cqhttp 扩展），**有离线缺口** | `napcat-get-group-msg-history.md`、`napcat-issue-1838-offline-gap.md` |
| 身份 | **必须真人号**（建议小号） | `napcat-login-multiaccount.md` |
| 风险 | **违反用户协议**；封号风险；且**没有只读开关**（见 §4） | `napcat-config-schema.md` |
| 落地 | OneBot 11 事件 JSON，`message` 是 segment 数组或 CQ 码（每适配器 `messagePostFormat`） | `napcat-message-group-format.md` |
| 环境 | Windows/Linux + NTQQ 客户端（Docker 版自带）；可无头 | `napcat-architecture-install.md` |
| 可维护性 | 11459★ / 最近 push 2026-10-22 / open issue 7；**高频问题：NTQQ 升级导致注入崩溃** | `napcat-activity-and-group-api.md` |

细节见 `70-qq-napcat.md`。

### 2.4 路线④ 本地数据库直读

| 问题 | 答案 | 证据 |
|---|---|---|
| 能力 | 本机该号**已经同步到本地**的全部消息（含很久以前） | `ntqq-localdb-schema.md` |
| 实时性 | 只能轮询文件/库（无推送） | 推断 |
| 历史 | **最强**：本地留存多少就有多少，不受"离线缺口"影响 | 同上 |
| 身份 | 本机登录的真人号 | — |
| 风险 | 灰（不替代客户端、不发消息，但要注入取 key） | `71-qq-localdb.md` |
| 落地 | `group_msg_table` / `c2c_msg_table`，**列名是数字字符串**（40001=msgId … 40800=消息体 Protobuf） | `ntqq-localdb-schema.md` |
| 环境 | Windows + 已登录 NTQQ + 调试器/Frida 取 key | `ntqq-localdb-crypto-key.md` |
| 可维护性 | 加密参数稳定（SQLCipher4/kdf_iter 4000），但**取 key 的 RVA 随版本变** | 同上 |

细节见 `71-qq-localdb.md`。

---

## 3. 能力矩阵（每格带出处）

| 维度 | 官方 Bot（群） | 频道 Bot | NapCat | 本地库直读 |
|---|---|---|---|---|
| **读全量群消息** | ✅ 需群主授权开关（`GROUP_MESSAGE_CREATE`）¹ | 频道内 ✅（`MESSAGE_CREATE`，**仅私域**）²；群 ❌ | ✅（该号可见即所得）³ | ✅（本地已留存部分）⁴ |
| **需要真人账号** | ❌ | ❌ | **✅（建议小号）**⁵ | ✅（本机登录）⁶ |
| **合规性** | ✅ 官方通道；但留存他人内容受运营规范 5.10 约束⁷ | ✅ 官方通道⁷ | ❌ 违反用户协议⁸ | ⚠️ 灰色（取 key 需注入）⁹ |
| **历史回溯** | ❌ **无历史接口**，只有从授权起的实时流¹⁰ | 有限¹⁰ | `get_group_msg_history`；**离线期间永久空洞**¹¹ | 取决于本地留存¹² |
| **实时性** | Webhook 推送¹³ | Webhook 推送¹³ | WS 推送（亚秒）¹⁴ | 轮询¹²（推断） |
| **部署环境** | 任意服务端¹⁵ | 任意¹⁵ | Win/Linux + NTQQ；可 Docker¹⁶ | 同左 + 调试权限¹⁷ |
| **运维成本** | 低（回调端点要公网可达）¹⁵ | 低 | 中：掉线、版本升级、风控¹⁸ | 高：逆向、RVA 跟踪¹⁹ |
| **封号风险** | 无（bot 身份）²⁰ | 无²⁰ | **有**²¹ | 低（不替代客户端）²² |

出处索引：

1. `evidence/qq/official-events-group-message-create-fullmode.md` + 我本人 2026-10-25 fetch 复核
2. `evidence/qq/official-channel-message-events.md`、`official-events-intents-payload.md`
3. `evidence/qq/napcat-message-group-format.md`
4. `evidence/qq/ntqq-localdb-schema.md`
5. `evidence/qq/napcat-login-multiaccount.md`
6. `evidence/qq/ntqq-localdb-crypto-key.md`
7. `evidence/qq/official-business-compliance.md`
8. `evidence/qq/napcat-architecture-install.md`（官方文档自述"驱动真实客户端"）；违规属性为常识性判断
9. `evidence/qq/ntqq-localdb-crypto-key.md`（调试器断点）
10. `evidence/qq/official-api-index-no-history.md`
11. `evidence/qq/napcat-issue-1838-offline-gap.md`
12. `evidence/qq/ntqq-localdb-path.md`（本地库即数据源）
13. `evidence/qq/official-message-overview.md`
14. `evidence/qq/napcat-onebot-event.md`
15. `evidence/qq/official-getting-started-subject-and-scope.md`
16. `evidence/qq/napcat-architecture-install.md`
17. `evidence/qq/ntqq-localdb-crypto-key.md`
18. `evidence/qq/napcat-activity-and-group-api.md`
19. `evidence/qq/ntqq-localdb-crypto-key.md`（RVA 随版本变）
20. 判断项（bot 无"真人号"可封）
21. `evidence/qq/napcat-security-incident-2025-09-05.md` + 常识
22. 判断项（不改客户端协议、不发消息）

---

## 4. 三处必须纠正的流行说法

### 4.1 "NapCat 可以配置成只读"

**没有。** `OneBotConfigSchema` 顶层只有：`network` / `musicSignUrl` / `enableLocalFile2Url` / `parseMultMsg` / `imageDownloadProxy` / `timeout`；适配器级也没有 `enableSend` / `disableSend` / `readOnly`。
`reportSelfMessage` 只管"自己发的消息要不要上报"，不是"能不能发"。
**只读只能在外部网关做白名单**（禁止 `send_*` 类 action 通过）。
证据：`evidence/qq/napcat-config-schema.md`。

### 4.2 "2025-09 事件后官方要求只监听 127.0.0.1，并导致连带封号"

**原文里没有这两句。** 复盘页（`https://napneko.github.io/develop/security`，日期 **2025 年 9 月 5 日**）的要点是：

- 起因：OneBot 协议**无 Token**，一键包被暴露到公网后被扫描；
- 问题清单里明写"NapCat 未提供随机 OneBot Token 默认填充"、"多数服务以 `0.0.0.0` 全监听"；
- 改进意见写的是"**可禁止局域网外访问**"，**没有**"必须监听 127.0.0.1"这句；
- 全文**未提**"连带封号"。

证据：`evidence/qq/napcat-security-incident-2025-09-05.md`。
任务书附录 A.4 里"大量机器人账号被连带封禁"的说法**没有在官方复盘中出现**，属外部传闻，本报告不采信为事实（标 `[未验证]`）。

> 「必须只监听 127.0.0.1 + 必开 token」仍然是**正确的工程建议**，但它是合理推断，不是官方原文。报告里不要把推断写成引文。

### 4.3 "Lagrange.Core 可以当 NapCat 的零改造备选"

**不成立。** 两个硬事实：

- V2 已改主线，出口协议是 **Milky**；**OneBot 11 只在已 sunset 的 V1**。
- V1 必须配 `SignServerUrl`（默认 `sign.lagrangecore.org/api/sign`），且官方明示"本项目不主动提供 SignServer，请前往 README 加入 Telegram 群聊获取信息"——即**签名服务不是公开可用的**。
- 官方文档站 `lagrangedev.github.io/Lagrange.Core/` 已 **404**（迁至 Lagrange.Doc/v2）。

证据：`evidence/qq/lagrange-repo-and-architecture.md`、`lagrange-signserver.md`。

---

## 5. 结论（按"能不能稳定读一年"排序）

任务书 §F 说得对：QQ 侧的核心矛盾不是"能不能读到"，而是"能不能稳定读一年"。按这个标准：

| 路线 | 能读 | 能稳定读一年 | 真正的敌人 |
|---|---|---|---|
| **官方 Bot（群）** | ✅ 全量（需群主开关） | ✅ 稳定性最好，但**没有历史**，一断线就永久空洞 | 群主关开关、bot 掉线期间的**服务端是否重推**（`[未验证]`）、接口还在演进 |
| **频道 Bot** | 频道内 ✅ | ✅ 稳 | 群迁不过去 |
| **NapCat** | ✅ | ❌ | ①离线缺口（已确认是设计不做）②NTQQ 升级导致注入崩溃 ③风控 |
| **本地库直读** | ✅ | ⚠️ 取决于取 key 能否跟上版本 | ①微信/QQ 更新换 RVA ②注入检测 ③Protobuf 未知字段 |

**两个非直觉的排序结论**：

1. **官方 Bot 是唯一"合规且能读全量群消息"的路线**，且 2026-10 才开放 —— 任务书因为旧结论把它排除了，这个前提要推翻重估。它的致命短板是**没有历史**，所以它只能做"从现在开始"的归档。
2. **NapCat 的离线缺口不是 bug 而是产品决策**（维护者原话"是 feature，不打算实现"）。所以任何"掉线后补拉"的设计在它上面都不成立 —— 这决定了 NapCat 只能做"常驻在线"的采集器，做不到"按需回捞"。

---

## 6. 本章未解问题

1. `GROUP_MESSAGE_CREATE` 是否对**个人主体**开放（FAQ 未按主体区分，实操未验证）。
2. 官方 Bot **掉线期间**的消息，重新上线后服务端会不会补推（这是"官方 Bot 能否长期归档"的关键，`[未验证]`）。
3. `GROUP_MSG_RECEIVE` 事件与"接收全量消息"开关是否同一个东西（措辞不同）。
4. 群主关闭全量后，`GROUP_AT_MESSAGE_CREATE` 是否仍可达（推断可达）。
5. 全量模式是否有独立 QPS/积压上限。
6. 频道成员上限、频道能否整体私密。
7. NapCat 在 2026-05-23 之后是否补了"按区间手动拉离线历史"（维护者说"晚点检查"）。
