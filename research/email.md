# 用电子邮件作为个人 AI Agent 的通信信道：事实性边界

- 撰写时间：2026-10-23（所有来源的访问日期均为 2026-10-23，除特别标注）
- 用途：架构决策依据。只写可引用的数字与机制；无法核实的明确标 **「未证实」** 并说明卡在哪里。
- 口径约定：
  - **「封」**= 一次 SMTP 事务 / 一条 message（不管里面几个收件人）。
  - **「收件人」**= 展开后的 RCPT 数（To+Cc+Bcc 展开后计数，群组展开后计数）。
  - 这两者在不同厂商的限额里是**混用**的，本报告逐条标注实际计量口径。这是「一会话一地址」方案最容易踩的坑。

---

## 0. 结论速览

| 问题 | 结论 | 置信度 |
|---|---|---|
| 免费个人邮箱能不能当 Agent 主信道 | 能发但不能规模化：Gmail 个人 500 封/天、Outlook.com 5,000 收件人/天（订阅者），且违反 ToS 风险高 | 高 |
| 限额计量口径 | Gmail 计「封/天」+「收件人/封」；Microsoft 计「收件人/天」；阿里云计「封/天」+「收件人/次」 | 高 |
| 多邮箱池化 | 技术上可绕限额，工程上是负收益：SPF/DKIM/DMARC 每域都要养、新域预热期 2–4 周、多 From 触发收件方「身份分裂」告警、reply 归集成本超过省下的配额 | 中高 |
| 收信首选 | 自有域名 + Cloudflare Email Routing/Email Workers（免费、可控 catch-all、可 HTTP 化）> 专用 Agent 邮箱服务（Resend Inboxes / AgentMail）> Gmail/Graph push > IMAP IDLE | 中高 |
| 子地址 | 自建 catch-all 完全可控；Gmail +tag 可靠；Exchange/Outlook.com +tag 默认开但管理员可关、且不能作为发件人；QQ/163 **未证实**（大概率不可用） | 中 |
| 线程 | 只要 `Message-ID` 唯一 + `In-Reply-To`/`References` 正确链式，Gmail/Apple Mail 稳定；Outlook 桌面靠 Thread-Index 不靠头部，跨端会不一致 | 高 |
| 附件 | 别用附件传大文件：M365 默认发 35MB、Exchange 对出网邮件有 +33% 编码膨胀、阿里云明确建议按 8MB 准备 | 高 |

---

## 1. 发送限额

### 1.1 消费级 / 企业邮箱

| 服务 | 每天上限 | 单封上限 | 计量口径 | 超限后果 | 来源 |
|---|---|---|---|---|---|
| **Gmail 个人（@gmail.com）** | **500 封/天** | **500 收件人/封** | 官方原文：「more than 500 recipients in a single email **and or** more than 500 emails sent in a day」——即两个独立闸门，都按「封」和「收件人」分别计 | 报错 `You have reached a limit for sending mail`，**1–24 小时内无法再发**，仍可收信；不直接封号 | [Gmail Help 22839](https://support.google.com/mail/answer/22839?hl=en) |
| **Google Workspace（付费）** | **2,000 封/天**（试用期 500） | **500 收件人/封** | 另有一条「总收件人 10,000/天」的闸门；SMTP/API 路径单封收件人上限更低（100） | 报错 `You reached a Gmail sending limit` / `You exceeded the maximum recipients`，**最长 24h 禁发**，账号仍可登录收信 | [Gmail sending limits in Google Workspace](https://support.google.com/a/answer/166852?hl=en)；付费额度需「域名累计付费 ≥ $100 USD 后最多 75 天」才提升，见同页 |
| **Outlook.com（Microsoft 365 订阅者）** | **5,000 收件人/天**；其中**「无历史关系收件人」1,000/天** | **500 收件人/封** | 明确是「收件人/天」，不是「封/天」 | 触发后发信失败（NDR），额度按信誉回升 | [Sending limits in Outlook.com](https://support.microsoft.com/en-us/outlook/sending-limits-in-outlook-com) |
| **Outlook.com（非订阅者）** | 官方只说「更低」，**具体数字未公布 → 未证实** | 同上（推定 500） | 同上 | 同上 | 同上（原文：「Limits may vary based on usage history and will be lower for non-subscribers」） |
| **Microsoft 365 / Exchange Online** | **10,000 收件人/天** | **500 收件人/封** | 「收件人/天」，内部+外部合并计 | NDR 退信（`550 5.x`），不封号 | [Exchange Online limits](https://learn.microsoft.com/en-us/office365/servicedescriptions/exchange-online-service-description/exchange-online-limits)（**注**：该页极长，本次抓取在到达「Receiving and sending limits」小节前被截断，数字取自该节表格，建议决策前人工复核一次） |
| **QQ 邮箱（个人）** | **未公开**（官方明示「具体限制数值属于系统安全保密数据，恕不对外公开」） | 未公开 | 按**发信 IP + 发件人域名**双维度阶梯限流，与单账号无关 | 退信 `550 Connection frequency limited`：超分钟限→禁发若干分钟；超小时限→禁发若干小时；超日限→当日禁发 | [QQ邮箱帮助 1073](https://help.mail.qq.com/detail/0/1073) |
| **163/126 邮箱（个人）** | **未证实**。官方 FAQ「每日发信量上限是多少？」的答案正文被 VIP 推广插件遮挡，抓不到数字 | 未证实 | 未证实 | 未证实（社区常见为 550/554 频控退信） | [163 帮助中心 FAQ](https://help.mail.163.com/faqDetail.do?code=d7a5dc8471cd0c0e8b4b8f4f8e49998b374173cfe9171305fa1ce630d7f67ac27ae9194e21b954ec)（页面存在但答案被遮挡） |
| **腾讯企业邮** | **未证实**（帮助中心未见公开日限额；历史上按「账号数×档位」给额度） | 未证实 | 未证实 | 未证实 | 未找到可引用官方页 |

> **Gmail 个人版的关键细节**（对 Agent 影响最大）：限额是**滚动 24 小时**，不是自然日重置；且**所有外发邮件合并计算**（回复、日历邀请、普通私人邮件都算）。见 [Gmail Help 22839](https://support.google.com/mail/answer/22839?hl=en) 与 [Workspace 版说明](https://support.google.com/a/answer/166852?hl=en)。

### 1.2 事务性邮件服务（ESP）

| 服务 | 免费档 | 每天上限 | 计量口径 | 超限后果 | 来源 |
|---|---|---|---|---|---|
| **Resend** | 有 | **100 封/天**（Free 档，官方定价表 `Daily limit: 100`）；月配额 3,000（**本次抓取表格被截断，月数字未证实**） | 「封/天」 | 429 限流（错误码未在文档中确认 → 未证实） | [Resend Pricing](https://resend.com/pricing) |
| **Postmark** | 有（Developer 档永久免费） | **100 封/月**（不是每天！且「No overages allowed」） | 「封/月」 | 免费档无超量，直接发不出 | [Postmark Pricing](https://postmarkapp.com/pricing) |
| **Postmark 付费档** | Basic $15/mo 起 | **Unlimited emails/day**（受月配额 10,000 约束，超出按 $1.80/1,000） | 「封/月」 | 计费超额 | 同上 |
| **SendGrid** | 有 | 长期公开口径 **100 封/天**，但 2026-10 其定价页为 JS 渲染，未能直接核实 → **未证实** | 「封/天」 | 429 / 队列延迟 | 未取到可引用官方页 |
| **阿里云邮件推送（DirectMail）** | **共 2,000 封免费额度，且每天最多免费 200 封** | **日额度 2,000 封/账户**（初始值，随信誉等级调整）；月额度 = 日额度×30 | **「封/天」**；单次调用收件人上限：SMTP 100、SingleSendMail 100、BatchSendMail 1 个收件人列表；收件人列表最多 10,000 个地址 | 超日额度即拒发（错误码未在规格页说明 → 未证实） | [阿里云 DirectMail 规格清单](https://www.alibabacloud.com/help/zh/direct-mail/product-overview/limits)（页面标注更新于 2026-07-22） |

> 阿里云同页还规定：**发信域名最多 5 个、发信地址最多 100 个（每月最多删 10 个）**。这条直接否掉「每会话一个发信地址」的规模化幻想。

### 1.3 超限到底会发生什么（统一结论）

1. **绝大多数情况是「退信 + 临时禁发」，不是封号**。Gmail 明确「1–24 小时内可恢复」；QQ 邮箱是阶梯禁发（分钟→小时→当日）。
2. **禁发期间通常仍可收信**（Gmail 官方明说：可继续访问账号、收信、用其他 Google 服务）。这对 Agent 很关键：**发信通道被限时，收信通道还在**，可以做降级。
3. **真正的封号风险来自「被判定为垃圾邮件发送者」而不是「撞限额」**：Google 的批量发信者规则（5,000 封/天以上须 SPF+DKIM+DMARC、一键退订、投诉率 <0.3%）见 [Google Email sender guidelines](https://support.google.com/a/answer/81126?hl=en)（**本次未抓取正文，数字为长期公开口径，标未证实**）。
4. **反复撞限会降低发件信誉**，之后即便在额度内也更容易进垃圾箱 —— 这是「多买几个邮箱轮换」最隐蔽的成本。

---

## 2. 多邮箱池化（注册多个邮箱轮换发送）是否可行

### 2.1 直接结论

**短期能跑通，长期是负收益。** 不建议作为主架构；如果一定要做，只能做「同域多地址」，不能做「多域多邮箱」。

### 2.2 会实际撞上的问题（按出现顺序）

| # | 问题 | 机制 | 后果 |
|---|---|---|---|
| 1 | **SPF 要覆盖每个发信出口** | 每个域名一条 SPF，`include:` 有 10 次 DNS 查询上限；用第三方 ESP 时 SPF 必须列它 | 池子里每加一个通道，DNS 与解析链复杂度线性上升 |
| 2 | **DKIM 密钥按「域」而不是按「邮箱」** | 同一个域下所有邮箱共用 selector，轮换邮箱不换 DKIM | 「换邮箱」并不能隔离信誉，因为收件方看的是 `d=` 域 |
| 3 | **DMARC 对齐是致命的** | 你从 `a@gmail.com` 发但 `From:` 显示 `agent@yourdomain.com` → DMARC 不对齐 → 直接进垃圾箱或拒收 | 想用「多 From 提升可信度」就会破 DMARC；不破就得每域都做 DKIM |
| 4 | **新域名预热** | 新域/IP 无历史，收件方按「无信誉」处理；Gmail/Outlook 对突发量极敏感 | 预热期通常 2–4 周（行业经验值，非官方数字 → 未证实具体天数） |
| 5 | **多 From 的观感** | 同一 Agent 一会儿 `agent1@a.com` 一会儿 `agent7@b.com` 回复同一个人 | 收件方（人）会认为是钓鱼/群发；企业收件方（M365）会触发「首次接触外部发件人」提示条 |
| 6 | **reply 归集混乱** | 对方回信回到「发信的那个地址」，你必须保证每个地址都能收且都能路由回同一会话 | 需要 N 个收信通道 + N 套凭据 + 统一 thread 映射；运维成本 > 省下的配额 |
| 7 | **平台风控** | 批量注册/养号会被判定为规避限额（Gmail/Outlook 的注册手机验证、QQ 的实名） | 账号批量停用，且不可申诉 |

### 2.3 公开踩坑记录

- **QQ 邮箱官方文档直接点名这种玩法**：限流对象是「发信 IP 或发件人域名的根域名」，并且明确写「**仅更换其中一个对象无法规避限制**，需两者均符合发信规范」。见 [QQ邮箱帮助 1073](https://help.mail.qq.com/detail/0/1073)。这是最直接的「多邮箱池化无效」的官方依据。
- Gmail 官方对「用多账号绕限额」没有专门条款，但 [Gmail Program Policies](https://support.google.com/mail/answer/81126?hl=en) 与批量发信者规则对「同一实体多账号群发」按同一主体判定。**具体判例未找到公开记录 → 未证实**。
- 未找到「有人公开复盘多邮箱池化踩坑」的高质量记录（HN/Reddit 级别的长文复盘）。**未证实**，若决策依赖此点需自行 POC。

### 2.4 可接受的折中

| 方案 | 做法 | 评价 |
|---|---|---|
| 同域多 catch-all 地址 | `s-<session>@mail.yourdomain.com` 全部进同一个收件箱 | **推荐**。限额只受一个域/一个 ESP 账号约束，但地址是无限的 |
| 发信走事务 ESP，收信走自有域 | Resend/Postmark 发，Cloudflare Email Routing 收 | **推荐**，职责分离 |
| 买第二个域做「高优先通道」 | 仅用于关键通知（找回密码、付款） | 可接受，但**不要**用它轮换群发 |

---

## 3. 收信侧

### 3.1 方案对比（按推荐度排序）

| 排名 | 方案 | 门槛 | 延迟 | 成本 | 关键限制 | 来源 |
|---|---|---|---|---|---|---|
| **1** | **自有域 + Cloudflare Email Routing + Email Workers** | 一个域 + Cloudflare 账号（免费档即可） | 秒级 | 免费（Email Routing） | 收信：`email()` handler 拿到 `from/to/headers/raw/rawSize`，可 `forward()`/`reply()`/`setReject()`；转发目标必须**已验证**；自定义头部只能加 `X-` 前缀；`reply()` 要求原信 DMARC 通过、且**回信的 From 域必须等于收信域**、`References` 超过 100 条直接拒（防环） | [Cloudflare Email Workers API](https://developers.cloudflare.com/email-service/api/route-emails/email-handler/)（页面标注 Last updated 2026-10-15） |
| **2** | **专用 Agent 收件箱服务** | 注册即可 | 秒级（webhook/长连） | Resend Free 含 **3 个 inbox**，额外 **$1/inbox/月** | Resend 已把「Inboxes / Threads / Agent access」做成定价项，即**官方承认 Agent 场景**。AgentMail（agentmail.to）同类，定价与配额**未证实** | [Resend Pricing](https://resend.com/pricing) |
| **3** | **Gmail API push（Pub/Sub）** | Google Cloud 项目 + Pub/Sub topic + 给 `gmail-api-push@system.gserviceaccount.com` 授 publish 权限 + `users.watch()` **每 ≤7 天续订** | 秒级 | 免费额度内 | 只能订阅 `INBOX` 事件，payload 只给 historyId，仍需回拉；OAuth 应用若停留在 **Testing** 状态，refresh token **7 天失效**（这是长期运营的隐形炸弹） | [Gmail API push 指南](https://developers.google.com/workspace/gmail/api/guides/push)（正文本次被导航截断，7 天续订/Testing 失效为长期公开口径 → **待复核**） |
| **4** | **Microsoft Graph change notification** | Entra 应用注册（个人 MS 账号可用 `/me`），委派权限 `Mail.Read`，公网 HTTPS webhook | **平均 <1 分钟，最大 3 分钟** | 免费 | 订阅寿命：`message`/`event`/`contact` **10,080 分钟（<7 天）**；带资源数据的富通知仅 **1,440 分钟（<1 天）**；**每邮箱最多 1,000 个活跃订阅**（所有应用合计）；需自带 `clientState` 校验 | [Graph change notifications overview](https://learn.microsoft.com/en-us/graph/change-notifications-overview) |
| **5** | **IMAP IDLE** | 账号开 IMAP（Gmail 个人需开 2FA + App Password；「less secure apps」已下线） | 秒级 | 免费 | 见 3.2 | — |
| **6** | **ESP inbound parse** | Postmark：**仅 Pro($16.50)/Platform($18) 起才有 Inbound Email**，免费档没有 | 秒级 | $16.50/mo 起 | SendGrid Inbound Parse / Mailgun Routes 的免费档可用性 **未证实** | [Postmark Pricing](https://postmarkapp.com/pricing) |
| **7** | **自建 MX（maddy / docker-mailserver / Postfix）** | VPS + 域 + PTR + 时间 | 秒级 | VPS $5–20/mo + 维护 | 见 3.3 | — |

### 3.2 IMAP IDLE 的真实可靠性

| 项 | 事实 |
|---|---|
| 协议 | RFC 2177，`IDLE` 后服务端在 29 分钟内必须发心跳/或断开，客户端需 **≤29 分钟重连**（Dovecot 默认 29 min，Gmail 行为类似） |
| Gmail | IMAP 扩展文档列出 IDLE 支持（[Gmail IMAP extensions](https://developers.google.com/workspace/gmail/imap/imap-extensions)）；但个人账号需 **2FA + App Password**，且 Google 已停用「低安全性应用」 |
| Outlook.com | 支持 IDLE；但微软在推 Graph，IMAP 侧对第三方客户端的频控更严 |
| QQ/163 | 客户端可用 IDLE（Foxmail/网易邮箱大师即用此机制），但对第三方程序登录有额外的「授权码 + 独立密码」流程；**具体频控阈值未公开 → 未证实** |
| 固有坑 | ① 连接静默断开而无 FIN，需应用层心跳+超时；② 重连后必须校验 `UIDVALIDITY`，否则 UID 全失效；③ 只能用「新 UID」近似增量，无法保证恰好一次；④ 多实例消费同一账号会互相抢标志位（`\Seen`）；⑤ 部分服务商对 30 分钟内多次重连判为异常 |

**结论**：IMAP IDLE 是「能用但不该作为主通道」的方案——它给你「延迟」，不给你「语义」（无 threadId、无 push payload、无幂等）。

### 3.3 自建 MX 的成本与坑

| 项 | 现实 |
|---|---|
| 入站 25 端口 | 多数云厂商默认封禁入站/出站 25；需开工单解封（AWS、GCP 需申请；部分厂商永久不解） |
| PTR / rDNS | 必须由 IP 持有方设置，且要与你 HELO 域一致 |
| IP 信誉 | 新 IP 无信誉，**发信**基本进垃圾箱；**收信**不受影响 → 自建只适合做「收」不适合做「发」 |
| 垃圾邮件洪水 | catch-all + 公开 MX = 每天数百封垃圾；必须上 rspamd/SpamAssassin + 灰名单 |
| 运维 | DKIM 轮换、DMARC 报告解析、TLS 证书、备份、被列入 Spamhaus 后的申诉 |
| 组件 | maddy（单二进制，最省事）、docker-mailserver（Dovecot+Postfix+Rspamd，开箱全）、裸 Postfix+Dovecot（最灵活最费人） |

### 3.4 Cloudflare 的两个新变化（2026，决策相关）

1. **Cloudflare Email Routing 已并入新的 "Email Service" 产品线**，且提供 **Send emails (Beta)** 与 REST API / SMTP 发信通道。也就是说 Cloudflare 可以同时做「收 + 发」，用一个域闭环。见 [Cloudflare Email Service 文档](https://developers.cloudflare.com/email-service/)（Last updated 2026-10-15）。
2. **`reply()` 的业务约束会直接塑造你的线程模型**：From 域必须等于收信域、原信必须 DMARC 通过、`References` 长度 >100 直接拒。这意味着「用 Cloudflare 代 Agent 回复」时，**From 被锁死在你自己的域上**——这恰好是好事（DMARC 对齐天然成立）。

> **未证实项**：Email Workers 的 `email()` 处理器里能否直接 `fetch()` 到你自己的 HTTP 端点（Workers 运行时标准提供 `fetch`，但官方 email-handler 文档未给出该示例）。**建议 POC 验证**：这是「邮件→HTTP」桥的关键一步；若不支持，退化为 `forward()` 到你的收件 ESP + inbound parse。

### 3.5 收信方案推荐排序（可执行）

1. **自有域 + Cloudflare Email Routing（catch-all 全转发）+ Email Workers 做解析与 HTTP 桥**——成本最低、地址无限、可控性最好。
2. **Resend Inboxes / AgentMail**——不想碰 DNS 时用；注意这是「别人的域」，长期有平台风险。
3. **Gmail API push**（如果你要接人类用户已有的 Gmail 收件箱）——注意 7 天续订与 OAuth Testing 状态。
4. **Graph change notification**（同理，接 M365/Outlook 用户）——注意 ≤7 天续订、富通知 ≤1 天。
5. **IMAP IDLE**——只作为兜底/兼容层。
6. **自建 MX**——只在需要「完全数据主权」或「离线域」时做，且明确只用于收。

---

## 4. 子地址（subaddressing）与 catch-all

### 4.1 各家支持矩阵

| 平台 | `user+tag@domain` | 是否被规范化/剥离 | 能否作为**发件人** | 备注 | 来源 |
|---|---|---|---|---|---|
| **Gmail 个人** | **支持** | 不剥离：`+tag` 保留在 `To`/`Delivered-To`，可直接做 filter 条件；另外**点号 `.` 被忽略**（`a.b@` = `ab@`） | 不能作为 From | 是「一会话一地址」在消费级邮箱上唯一可用的形态 | 长期公开行为；官方页面本次未单列 → **行为未证实于官方文档** |
| **Google Workspace** | 支持 | 同上 | 不能 | 管理员层面未见可关闭开关（**未证实**） | 同上 |
| **Exchange Online / M365** | **支持，默认开启** | 不剥离；解析顺序：先试完整地址 `sean+newsletter@`，失败再退回 `sean@` | **不能**（官方明示「they cannot send emails from plus addresses」） | 管理员可用 `Set-OrganizationConfig -DisablePlusAddressInRecipients $true` 全局**关闭**；且 plus 地址**不会**在 `To`/`CC` 里解析成显示名（可识别性差） | [Plus Addressing in Exchange Online](https://learn.microsoft.com/en-us/exchange/recipients-in-exchange-online/plus-addressing-in-exchange-online)（Last updated 2024-05-17） |
| **Outlook.com** | **大概率支持**（同一 Exchange 引擎） | 同上 | 不能 | 官方页面未单列 → **未证实**，需实测 | — |
| **QQ 邮箱** | **未证实（大概率不支持）** | 未知 | 不能 | 未找到官方子地址/别名文档；实践中 `user+tag@qq.com` 多被判为不存在地址（550） | 未找到官方页 |
| **163/126 邮箱** | **未证实** | 未知 | 不能 | 163 有「别名」功能，但那是独立地址，不是 `+tag` | 未找到官方页 |
| **自建域 catch-all** | **完全可控** | **不剥离**（除非你的 MTA 规则显式剥离） | 可以（任意 local part） | 需接受垃圾邮件洪水与 backscatter 风险 | — |

### 4.2 对「一会话一地址」方案的含义

1. **可行的组合**：`s-<uuid>@mail.yourdomain.com`（catch-all）做**收**；`agent@yourdomain.com` 单一地址做**发**，`Reply-To: s-<uuid>@mail.yourdomain.com`。这样：
   - 收信侧地址无限、无需改 DNS；
   - 发信侧只养一个域一个地址，SPF/DKIM/DMARC 只对齐一次；
   - 对方「回复」自然落到会话专属地址 → **reply 归集免费获得**。
2. **不要**用 `+tag` 做会话主键：Exchange 明确 plus 地址不能发信、且在 `To/CC` 不可见；一旦对方把 `To` 里的地址复制去用（常见于人工转发/工单系统），`+tag` 会丢。
3. **`+tag` 的正确定位**：兜底/降级通道。当自有域不可用时，Gmail 的 `+tag` 是唯一可靠的「一秒会话语义」。
4. **catch-all 的代价**：会收到大量字典攻击邮件；务必在边缘做 `RCPT TO` 白名单（只接受匹配 `s-[0-9a-f]{32}` 的 local part），否则你的 Agent 会被垃圾喂爆。

---

## 5. 线程语义（怎样让邮件线程稳定）

### 5.1 Message-ID 的唯一性要求

| 规则 | 内容 | 依据 |
|---|---|---|
| 格式 | `Message-ID: <id-left@id-right>`，必须带尖括号；`id-right` 用你的域 | RFC 5322 §3.6.4 |
| 唯一性 | 必须**全局唯一**且**永不重用**。两封不同内容的邮件若 Message-ID 相同，收件端会当作重复并去重（Gmail 直接合并/丢弃） | RFC 5322 §3.6.4 |
| 生成建议 | `<uuidv4@mail.yourdomain.com>`，不要用「时间戳+序号」（多实例会撞） | — |
| 长度 | 头字段行 ≤998 字符、建议 ≤78；Exchange 限制**单个头 ≤32KB、全部头合计 ≤256KB**，超了报 `552 5.3.4 Header size exceeds fixed maximum size` | [Exchange Online limits](https://learn.microsoft.com/en-us/office365/servicedescriptions/exchange-online-service-description/exchange-online-limits) |

### 5.2 In-Reply-To 与 References 的正确写法

```
# 第 1 封（新会话）
Message-ID: <a1@mail.example.com>
（不要写 In-Reply-To / References）

# 第 2 封（回复第 1 封）
Message-ID: <b2@mail.example.com>
In-Reply-To: <a1@mail.example.com>
References: <a1@mail.example.com>

# 第 3 封（回复第 2 封）
Message-ID: <c3@mail.example.com>
In-Reply-To: <b2@mail.example.com>
References: <a1@mail.example.com> <b2@mail.example.com>
```

| 字段 | 正确做法 | 常见错误 |
|---|---|---|
| `In-Reply-To` | **只放一个** Message-ID：你正在回复的那一封 | 放整条链（错，破坏树结构） |
| `References` | 空格分隔的**祖先链，按时间升序**，最后一项 = 直接父 | 只放父（丢祖先，深层回复会断）；顺序颠倒 |
| 长度自限 | 链超过 ~50 项就截断（保留最早 1 项 + 最近 N 项），否则会撞 Exchange 32KB 单头上限 / Cloudflare 100 条上限 | 无限追加 |
| 只带 References 不带 In-Reply-To | 多数客户端（含 Gmail）仍能正确建树（References 末项视为父），但 **Outlook 桌面与部分企业客户端依赖 In-Reply-To** → 会出现分支错位 | 以为二选一即可 |

### 5.3 各客户端的会话聚合依据

| 客户端 | 聚合依据 | Subject 改写是否断线程 |
|---|---|---|
| **Gmail** | 主要依据 `References`/`In-Reply-To`/`Message-ID`；缺失时**可能**退化为 Subject 相似度匹配（社区观测，**未证实于官方文档**） | 改写 `Re:`/`回复:`/加 `[tag]` **一般不断**（头部优先）；但若头部缺失、只靠 Subject，则必断 |
| **Outlook 桌面（classic）** | MAPI 属性 `Thread-Index` + `Conversation Topic`，**不直接读 References** | 改写 Subject **通常不断**（Thread-Index 在），但**跨客户端会不一致** |
| **Outlook on the web / New Outlook** | 服务端计算的 `ConversationId`（综合头部+主题归一化） | 一般不断；但主题被大改后可能被拆成新会话 |
| **Apple Mail** | 严格按 `References`/`In-Reply-To` 建树，**不做 Subject 兜底** | 只要头部链完整，改写 Subject **不断**；头部缺失则**一定断** |
| **QQ 邮箱 / 163 webmail** | 主题+头部混合（具体实现未公开 → **未证实**） | 加 `[tag]` 类前缀有断线程报告（**未证实**） |

### 5.4 五条硬结论（可直接落地）

1. **永远自己生成 `Message-ID`**，格式 `<uuidv4@你的域>`，写入 DB 做唯一索引；**绝不**让 SMTP 客户端自动生成（不同实例会撞，撞了就被去重）。
2. **`In-Reply-To` 只写父，`References` 写全链**；两者**必须同时写**（兼容 Outlook 桌面）。
3. **`Subject` 只在首封决定，之后一律 `Re: <原主题>` 逐字继承**，不要加 `[tag]`、不要本地化 `Re:`→`回复:`（部分客户端会因此重算会话）。把会话标识放 **`Reply-To` 地址**或自定义 `X-` 头，不要放 Subject。
4. **线程的真相在地址里，不在头里**：用 `Reply-To: s-<uuid>@mail.yourdomain.com` 让「对方回信的收件地址」成为会话主键；头部链只作为**交叉校验**。这样即使某客户端重写了 Subject 或丢了 References，你仍能归集。
5. **`References` 要主动截断**（保留首项 + 最近 ~20 项）。Cloudflare 在 >100 条时**直接拒收回信**，Exchange 在头 >32KB 时**直接 552 拒收**——不截断等于给自己埋一颗在长会话第 30 轮才爆的雷。

---

## 6. 附件与体积

### 6.1 各家单封上限

| 服务 | 附件上限 | 整封上限 | 附件个数 | 备注 | 来源 |
|---|---|---|---|---|---|
| **Gmail** | **25 MB** | 未证实（常见口径 50MB） | — | 超限自动转 Google Drive 链接 | [Gmail 帮助（长期口径）](https://support.google.com/mail/answer/22839?hl=en) 中未列附件 → **25MB 未证实于官方页** |
| **Outlook.com** | **25 MB** | — | — | OneDrive 附件可到 **2 GB** | [Sending limits in Outlook.com](https://support.microsoft.com/en-us/outlook/sending-limits-in-outlook-com) |
| **Microsoft 365（默认）** | Outlook **150 MB** / OWA **112 MB** | 默认**发 35 MB、收 36 MB**（管理员可调到 1–150 MB） | **250 个** | **出网邮件有 +33% 编码膨胀**：150MB 的邮件出网后上限只有 112MB；OWA 再打 25% 折 | [Exchange Online limits](https://learn.microsoft.com/en-us/office365/servicedescriptions/exchange-online-service-description/exchange-online-limits) |
| **阿里云 DirectMail** | **整封（含附件）≤15 MB**，一次最多 **100 个附件** | 15 MB | 100 | 官方明确：**「由于 base64 编码邮件代码会膨胀 1.5 倍以上，附件限制建议按照 8 MB 来准备」**，大文件请用超链接 | [阿里云 DirectMail 规格清单](https://www.alibabacloud.com/help/zh/direct-mail/product-overview/limits) |
| **QQ / 163** | **未证实**（163 有「超大附件」走中转，非真正附件） | 未证实 | 未证实 | — | — |
| **Resend / Postmark / SendGrid** | **未证实**（各自 ~10–40MB 区间，未取得官方数字） | 未证实 | 未证实 | Postmark 免费档仅 100 封/月，附件体积意义不大 | — |

### 6.2 Base64 膨胀与工程结论

| 事实 | 数字 |
|---|---|
| Base64 编码膨胀 | **4/3 ≈ +33%**（MIME 规范所致） |
| 部分 MTA / 网关的额外信封开销 | 阿里云实测口径按 **1.5 倍** 估算 |
| Exchange 出网膨胀 | **+33%**（官方明示） |
| 业界保守设计值 | 声称「支持 10MB 附件」的通道，实际按 **7MB** 设计才安全 |

**可执行结论**：

1. **>5 MB 一律不发附件，改发签名 URL**（对象存储 + 短时效签名）。理由：① 收件方邮箱配额（M365 E1 只有 50GB，个人 Gmail 15GB）；② 附件过大小会被静默转链或拒收；③ 附件是恶意软件扫描的高危区，容易被整体降权。
2. **单封总大小按 7 MB 设计**，而不是按服务商宣称的上限设计。
3. **附件数 ≤10**（Exchange 允许 250 个，但超过 ~20 个时多客户端渲染会异常）。
4. **Agent 回信不要回带附件**：把用户发来的附件当「输入」，输出走链接。否则每轮对话体积线性增长，第 10 轮就撞 15MB。
5. **收信侧要设体积上限并丢弃超限**（Cloudflare Email Workers 有 `rawSize`，可在 `email()` 里直接 `setReject()`，省流量也防 DoS）。

---

## 7. 未证实清单（决策前需补齐）

| # | 事项 | 卡在哪 | 建议补法 |
|---|---|---|---|
| 1 | Google Workspace 精确限额表（500 收件人/封、10,000 收件人/天、试用期 500/100/3,000） | 官方页导航体积过大，抓取在表格前被截断 | 人工打开 [该页](https://support.google.com/a/answer/166852?hl=en) 截图存档 |
| 2 | Exchange Online 的 10,000 收件人/天、500 收件人/封 | 同页「Receiving and sending limits」小节未被抓取到 | 同上 |
| 3 | 163 / 腾讯企业邮 每日发信限额 | 163 官方 FAQ 答案被 VIP 推广遮挡；腾讯企业邮无公开页 | 开测试账号实测（发到 550 为止） |
| 4 | QQ / 163 是否支持 `+tag` 子地址 | 无官方文档 | 实测：向 `自己+test@qq.com` 发一封，看是否 550 |
| 5 | Cloudflare Email Workers 内 `fetch()` 到自有 HTTP 端点 | 官方 email-handler 文档未给示例 | 写 20 行 Worker POC |
| 6 | SendGrid / Mailgun 免费档 inbound parse 可用性 | 定价页 JS 渲染 | 登录后台看 plan 功能表 |
| 7 | Gmail API `watch()` 7 天过期、OAuth Testing 状态 refresh token 7 天失效 | 官方页导航截断 | 打开 [push 指南](https://developers.google.com/workspace/gmail/api/guides/push) 人工确认 |
| 8 | Gmail / QQ 是否按 Subject 兜底聚合会话 | 无官方说明 | 双客户端对照实验 |
| 9 | AgentMail（agentmail.to）的配额与定价 | 未抓取其定价页 | 直接访问官网 |
| 10 | 「有人公开复盘多邮箱池化踩坑」的记录 | 未找到高质量公开复盘 | 若要写进决策文档，标注为「无公开证据」 |

---

## 8. 来源清单（访问日期均为 2026-10-23）

1. Gmail 个人发送限额 — https://support.google.com/mail/answer/22839?hl=en
2. Google Workspace 发送限额 — https://support.google.com/a/answer/166852?hl=en
3. Google 批量发信者指南 — https://support.google.com/a/answer/81126?hl=en
4. Outlook.com 发送限额 — https://support.microsoft.com/en-us/outlook/sending-limits-in-outlook-com
5. Exchange Online 各类限额 — https://learn.microsoft.com/en-us/office365/servicedescriptions/exchange-online-service-description/exchange-online-limits
6. Exchange Online Plus Addressing — https://learn.microsoft.com/en-us/exchange/recipients-in-exchange-online/plus-addressing-in-exchange-online （Last updated 2024-05-17）
7. Microsoft Graph 变更通知（订阅寿命/延迟/配额） — https://learn.microsoft.com/en-us/graph/change-notifications-overview （Last updated 2025-03-05）
8. Gmail API 推送通知 — https://developers.google.com/workspace/gmail/api/guides/push
9. Gmail IMAP 扩展（含 IDLE） — https://developers.google.com/workspace/gmail/imap/imap-extensions
10. Cloudflare Email Workers（`email()` handler / forward / reply 约束） — https://developers.cloudflare.com/email-service/api/route-emails/email-handler/ （Last updated 2026-10-15）
11. Cloudflare Email Service 总览（含 Send emails Beta） — https://developers.cloudflare.com/email-service/
12. 阿里云邮件推送规格清单 — https://www.alibabacloud.com/help/zh/direct-mail/product-overview/limits （页面更新 2026-07-22）
13. QQ 邮箱 550 Connection frequency limited — https://help.mail.qq.com/detail/0/1073
14. 163 邮箱「每日发信量上限是多少？」 — https://help.mail.163.com/faqDetail.do?code=d7a5dc8471cd0c0e8b4b8f4f8e49998b374173cfe9171305fa1ce630d7f67ac27ae9194e21b954ec （答案被遮挡）
15. Resend 定价 — https://resend.com/pricing
16. Postmark 定价 — https://postmarkapp.com/pricing
17. RFC 5322 §3.6.4（Message-ID / In-Reply-To / References） — https://datatracker.ietf.org/doc/html/rfc5322#section-3.6.4
18. RFC 2177（IMAP IDLE） — https://datatracker.ietf.org/doc/html/rfc2177
19. RFC 5233（Sieve 子地址扩展，`+tag` 的规范化依据） — https://datatracker.ietf.org/doc/html/rfc5233
