# 99 · Mephisto 实施计划 v2

> **状态：待审阅，尚未批准。** 代码一行未写，仓库未初始化，`E:\mephisto\mailbot` 一个字节未动。
> 生成时间：2026-10-25 · 本文件**取代** v1（v1 的差异见 §0）。
> 本次调整依据你的一句话：**"邮件模块搁置，安卓客户端优先。"**
> 旧 mailbot 继续跑，它现在是唯一可用的信道，所以**既不迁也不停**；新系统用独立 `DSH_HOME`，两边会话文件互不干扰。

**怎么读**：
只想拍板 → §3 · 想看安卓怎么落地 → §5–§8 · 想看分几步 → §10 · 邮件以后怎么办 → §14
信道怎么落地（无公网 IP）→ [15](15-http-channel.md) · 上下文/压缩/插队/Runner → [25](25-session-engine.md) · 工具与提醒链路 → [55](55-agent-tools.md)

---

## 0. 这次改了什么（v1 → v2 对照）

| 项 | v1 | v2 | 为什么 |
|---|---|---|---|
| 邮件 | 第 9 步重写 | **搁置，不排期**（§14 留档） | 你的决定 |
| 路由链 | 第 3 步建 K0–K4 五条 | **只建 K1（别名）+ K4（默认会话）**，K0/K2/K3 随邮件一起回来 | 五条里有三条是"邮件寻址问题"（Message-ID 令牌、会话地址 HMAC、主题标签），没有邮件就没有问题可解 |
| 安卓 | 第 5 步先用浏览器，第 8 步才原生 | **原生提到 S7–S10**，浏览器页降级为约 80 行的调试工具 | 你要安卓优先 |
| 提醒投递 | SMTP + `invite.ics` | **`notice` 表 + 设备本地精确闹钟** | 邮件没了，`.ics` 这条路不存在了；而且本地方案更可靠（见 §5） |
| 推送 | "自建长连 + 前台服务兜底" | **常驻保留且默认开**（`remoteMessaging` 前台服务 + 断线看门狗 + 三档可调）；本地闹钟作为**独立兜底层** | 6 小时上限只打 `dataSync`/`mediaProcessing`，消息类类型不受限；两层失效条件不重叠（§5.1、§5.3） |
| 步骤数 | 12 | **14**（S0–S13；邮件为 S14，搁置不排期） | 安卓拆成 4 步 |
| 工具链体积 | 3–4 GB | **下载约 1.0–1.2 GB、展开约 2 GB** | v1 那个数字是"装 Android Studio 整套"的量；只用 cmdline-tools 约为三分之一（§8.4） |
| 第二信道 | Telegram（验证端口抽象） | 保留，退到 S12 | 优先级让位给安卓 |
| **Runner** | SDK 优先、CLI 兜底 | **定案：`dsh --profile <ours> --json`，每轮一个子进程** | 不需要 Python SDK（80 MB 未知消失）、取消=杀进程、无残留 |
| **会话连续性** | 未定 | **定案：每轮新会话 + 我们装配上下文**（`--session-id` 留作开关） | 账本在我们、压缩可调、失败面小。见 [25](25-session-engine.md) |
| **插队** | 未提 | **定案：同会话只允许一个 job；默认排队，可打断** | 你提的问题；DDL 唯一索引挡并发。见 [25](25-session-engine.md) §5 |
| **工具面** | "加几个 skill" | **定案：MCP（stdio）**，7 个工具 | 有 schema、可中途查询。见 [55](55-agent-tools.md) |
| **QQ** | 官方 Bot 首选 | **定案：qqflow-server（只读本地库）** | 你无法把 Bot 拉进已有群。见 [40](40-chatlog.md) §9 |
| **接入方式** | 未定 | **定案：Cloudflare Tunnel**（VPS+frp 作退路） | 校园网只能主动出站；零月费 + 真证书。见 [15](15-http-channel.md) |
| **附件** | 未提 | **分片 4 MB / 单文件 64 MB**；超限走工作区 `inbox/` 投递 | CF 的 100 MB 不是真实瓶颈，**125 秒超时**才是；分片大小由超时反推。见 [17](17-attachments.md) |

**一处自我纠正**：v1 里我说安卓"需要 3–4 GB 下载"，那是在假设走 Android Studio 图形界面。既然本机连 java 都没有，正确的做法是从命令行工具链开始——不装 Studio，因此量级降到三分之一。但这条也意味着 §8 的安装步骤必须写死，不能一句"装个 Studio"带过。

---

## 1. 目标、非目标、成功标准

### 1.1 目标

一个可常驻的个人 Agent 内核 + 一个自己用的安卓 App。**手机是唯一的日常入口，电脑是唯一的执行节点。**

```
        手机 (Kotlin/Compose)                家里的机器 (Python)
   ┌──────────────────────────┐        ┌───────────────────────────┐
   │ 会话列表 / 对话 / 设置    │  HTTPS │ HTTP API + SSE            │
   │ 本地精确闹钟 / 系统日历   │◄──────►│ Pipeline → DSH Runner      │
   │ SQLite 缓存（离线可读）   │  (经 VPS/frp · HTTPS) SQLite (真值)     │
   └──────────────────────────┘        └───────────────────────────┘
```

### 1.2 非目标（写下来是为了以后不被"顺手加一下"拖死）

| 不做 | 原因 |
|---|---|
| 手机本地跑 Agent / DSH | Android 12+ phantom killer：全系统 32 子进程 / 5 分钟 CPU 检查，超限静默 `SIGKILL` |
| 手机与电脑共享同一个 harness 会话 | 会话是本地文件且绑 cwd，跨设备无同步机制 |
| iOS | 你已排除 |
| 迁移旧 mailbot 的任何代码 | 你已判定它不可维护；按端口重写更便宜 |
| 邮件的临时替代（"先用 Gmail API 顶一下"） | 顶一下会留下第二个要拆的东西 |

### 1.3 八条可执行验收（每条都能用一条命令或一次操作确认）

| # | 验收 | 判定方式 |
|---|---|---|
| 1 | 手机装上 APK，打开看到会话列表，发一句话，**3 秒内出现「思考中 · 12s」与当前工具名**，回答以**整段**出现 | 手动，看屏幕 |
| 2 | 同一会话第二轮，模型记得第一轮内容 | 问"我刚才说了什么" |
| 3 | 提醒在**电脑关机 + 手机飞行模式**下仍准时响铃 | 这是最能证明架构正确的一条 |
| 4 | 点提醒里的"加入日历"，系统日历多一条**带提前提醒**的事件，不需授予任何权限 | 手动 |
| 5 | `python -m pytest -q` 全绿；`python -m mephisto doctor` 一条命令体检通过 | 命令 |
| 6 | 每步一个附注 tag，`git checkout step-5` 能回到任意一步的可运行状态 | 命令 |
| 7 | 服务端运行时**零第三方依赖**（只用标准库）；App 只依赖 Jetpack + OkHttp | `pip list` / `gradlew dependencies` |
| 8 | 手机锁屏放置 2 小时后，电脑上触发的任务完成**秒级**弹出通知（L1 常驻）；关掉常驻则退化为 ≤15 分钟 | 手动，两个场景各做一次 |

> **两处降级（v2.2，已与你确认）**
> ① **不再承诺逐 token 流式**：`headless --json`、`acp`、`sdk` 三个自动化入口都是**提交级**事件（证据见 [25](25-session-engine.md) §6）。替代承诺是**过程可见**：秒表 + 当前工具名 + 「60 秒无进展」提示 + 断线重连。
> ② **不做 Cloudflare Tunnel，也不做 Tailscale**：改用 **VPS + frp 反向隧道**（校园网），并且是 **HTTPS**，见 [15](15-http-channel.md)。

---

## 2. 已核实的前提（含今天新测的四项）

| 事实 | 证据等级 | 对计划的影响 |
|---|---|---|
| Python 3.14.7（另有 3.11）、pip 26.2.1 | 实测 | 目标 3.12+，开发用 3.14 |
| 有 git / node / npm；**无 uv** | 实测 | 用 `venv + pip`，不引入 uv 这个前置 |
| `java` / `javac` / `gradle` / `adb` / `sdkmanager` **全部缺失**；`JAVA_HOME` / `ANDROID_HOME` 未设置 | **实测（今天）** | 安卓工具链必须从零装，§8 是硬前置 |
| `E:` 剩余 **57.7 GB**，`C:` 剩 16.7 GB | **实测（今天）** | 空间够，但工具链要装在 `E:` 且在工作区内（沙箱） |
| 系统代理 `HTTP_PROXY/HTTPS_PROXY=http://127.0.0.1:7890` | **实测（今天）** | 下载走代理，速度取决于代理 |
| **`curl` / `Invoke-WebRequest` 的 HTTPS 全部失败**：`schannel: AcquireCredentialsHandle failed: SEC_E_NO_CREDENTIALS` | **实测（今天）** | 沙箱挡住了 Windows 证书存储。**所有下载必须用 Python 脚本**，`pip` 也不受影响 |
| **Python 的 TLS 正常**：`api.adoptium.net` / `maven.google.com` / `services.gradle.org` / `dl.google.com` 四个端点全部 200 | **实测（今天）** | JDK、Gradle、Android SDK、Maven 依赖都能下载 → 整条工具链可脚本化 |
| 一个容易踩的坑：Adoptium API 不带 `User-Agent` 会 **403** | **实测（今天）** | 安装脚本必须带 UA，否则会误判"网络不可用" |
| **`pip` 装不了任何包**：`ensurepip` 与 `pip install` 都在临时目录上 `PermissionError`（写/改名被拦） | **实测（S0）** | 不能靠 `pip install -e .`；包必须"零安装可跑"（扁平布局），测试用标准库 `unittest` |
| **`tempfile.mkdtemp()` 建的目录不可写**（`os.makedirs` 建的就正常），且这些目录**无法删除** | **实测（S0）** | 代码里**禁止**用 `mkdtemp` / `TemporaryDirectory`；测试用仓库内固定目录 + 尽力删除 |
| `pwsh`（PowerShell 7）**不在 PATH**，只有 `powershell` 5.1；无 BOM 的 UTF-8 `.ps1` 会被当 GBK 读而报"缺少字符串终止符" | **实测（S0）** | `.ps1` 一律存成 **UTF-8 with BOM**；`.cmd` 用 CRLF |

**四条会影响计划的结论**：

1. **安卓可行**。工具链能下载、能装在沙箱内、空间够。§8 给的是可执行脚本，不是愿望。
2. **不能靠 curl 下载**。这条要写进 `tools/` 的约定，否则我下次一定会再踩一遍。
3. **`patch` 能力比 v1 更重要**。因为工具链是"能装"而不是"已在"，S0 就要做预检（下不了就当场改路线，而不是等到 S7 才发现）。
4. **"零安装"不是口号而是约束**。既然 `pip` 不可用，工程形态就必须能在"只有 Python 标准库"的环境里跑起来——这反而和第 7 条成功标准（运行时零第三方依赖）同向。

---

## 3. 需要你拍板的五件事（每条都有推荐）

### 3.1 仓库与工具链的位置

| 方案 | 说明 |
|---|---|
| **✅ 推荐：仓库 `E:\study\mephisto`；工具链 `E:\study\.toolchain\`（仓库外、工作区内）** | 沙箱只允许写 `E:\study`；工具链不进 git（`.gitignore`），删目录即卸载 |
| `E:\mephisto` 新目录 | 与旧 mailbot 同级更整齐，但**在工作区外，我写不了** |

### 3.2 安卓只做原生，浏览器页降级为调试工具

v1 说"先浏览器后原生"。你既然要安卓优先，那就**反过来**：原生 App 是产品，浏览器页只保留一个约 80 行的 `index.html`，用途是**在我做 App 之前用 curl 之外的方式验证 API，以及 App 崩了时的应急入口**。

代价：手机上第一次能**对话**要等到 S9 结束（阶段 A + B 合计约 17.5 天净编码），"提醒变闹钟 + 后台秒级"要等到 S10。v1 那个方案里手机在 S5（约 7 天）就能用浏览器发消息——**这是"安卓优先"实际付出的时间代价，我不粉饰它**。换成收益：S10 结束时你拿到的是**和微信同级的后台到达能力** + 真闹钟 + 系统日历，而不是浏览器书签。

### 3.3 通知：常驻 + 闹钟两层（这是本次最大的技术决策）

**推荐：两个都要。** 常驻连接解决"秒级到达"，本地闹钟解决"关机也准"，两者失效条件不重叠。细节见 §5，一句话：

> 常驻让 App 像微信一样在线收消息；而"准点"这件事压根不该依赖网络——用 `AlarmManager.setAlarmClock()` 让系统自己到点叫醒，服务端此刻关机也不影响。

**代价**：① 常驻要付一条状态栏通知 + 一点耗电（微信没有这条，因为它有系统特权）；② 手机必须至少同步过一次，提醒才会进本地闹钟；③ 在电脑上改了时间，要等下次同步才传到手机；④ **App 被彻底杀掉后的离线推送做不到**（要厂商通道，个人拿不到），那一段靠闹钟与下次打开补齐（§5.1 最后一行）。

### 3.4 手机怎么连到家里的机器（**定案：Cloudflare Tunnel**）

你的环境是**校园网**：NAT 后面、打洞不可靠、无公网 IP、夜间可能断网。所以结构只能是"**家里的机器主动向外建连接**"。两条符合的路都验证过了，你选了成本为 0 的那条：

| 路线 | 结论 |
|---|---|
| **✅ Cloudflare Tunnel** | **定案**。`cloudflared` 建一条出站连接，手机走 `https://你的域名`（标准 443） |
| VPS + frp | **退路**，保留在 [16](16-network-runbook.md)。切换时 App 只改一个地址 |
| IPv6 直连 | 不可用（实测本机无全局 IPv6） |
| Tailscale | 删掉（校园网必退回免费中继） |

**代价（已接受）**：TLS 在 CF 边缘终结，它看得到明文；CF 侧有 **125 秒读超时**与 **100 MB 单请求上限**——后者直接决定了附件的分片设计（[17](17-attachments.md)）。

**收益**：手机 URL 干净（无端口号）、真证书（Android 明文限制问题消失）、服务端不必绑 `0.0.0.0`、零月费。

施工步骤见 [16](16-network-runbook.md) §2。App 侧两件事：**只用 `https://`**；**地址可在设置里改**（不写死，换路不用改代码）。

### 3.5 第二信道（验证"可插拔"是真成立）

保留 Telegram，退到 S12，理由不变：如果加一个信道必须改 `domain/` 或 `pipeline.py`，说明前面的抽象是假的。

| 候选 | 结论 |
|---|---|
| **✅ Telegram Bot** | 约 200 行、长轮询不需要公网入口、手机推送现成。国内要代理 |
| Matrix / 企业微信 / 飞书 / QQ 官方 Bot | 不做（协议太大 / 要资质 / 无历史接口） |

**这一条你可以直接砍**：砍掉后 S12 换成"第二个客户端"（桌面 Web 页），同样能验证抽象。

---

## 4. 架构（邮件搁置后的形状）

### 4.1 一个内核 + 三个端口（不变）

```
            ┌──────────── domain（纯函数，无 IO）────────────┐
            │ Envelope · Session · Resolver · Reminder/Notice │
            └───────────────────────────────────────────────┘
   Source ──►   Pipeline   ──►  Store(SQLite，唯一真值)
   （收）        │   ▲             │
                 ▼   │             ▼
              Runner(DSH)      Sink(发) ──► Receipt
```

**关键设计不变**：`Pipeline` 只编排不含判断；判断在 `Resolver` 与 `Session`；副作用只在三个端口后面。加一个信道＝加一个 `Source`/`Sink`，不改内核。

**架构没白设计的地方**：邮件搁置后，`Source`/`Sink`/`Runner` 三端口照样成立，只是 `Source` 的第一实现从 IMAP 换成 HTTP POST，`Sink` 的第一实现从 SMTP 换成"写库 + SSE 推 + 落 notice"。**这正是端口的价值——它让"换信道"变成换适配器，而不是换架构。**

### 4.2 邮件搁置后，这些东西**先不建**（省下来的工时要认）

| 不建 | 原本为什么建 | 什么时候回来 |
|---|---|---|
| `K0 MessageIdToken` | 从回复的 Message-ID 里读出会话号 | 邮件 |
| `K2 AddressBinding` + HMAC | `s-<slug>.<hmac>@domain` 会话地址 | 邮件 |
| `K3 SubjectTag` | `[T:名称]` 兼容旧习惯 | 邮件 |
| `adapters/mail/*`（mime/imap/smtp） | 收发信 | 邮件 |
| `quota_ledger` / `delivery` | 发信额度、投递尝试 | 邮件 |
| `invite.ics` 生成器 | 无 App 时把提醒塞进系统日历 | **不需要了**：App 直接写日历（§5.7） |
| 发件人白名单 / 自环拦截 / UIDVALIDITY 水位 | 邮件特有的脏活 | 邮件 |

**但有一件事必须现在保留**：`session_alias` 表与 `Resolver` 链的形状。因为 Telegram（群/私聊 id → 会话）和将来的邮件都要靠它，且删表比加表贵。

### 4.3 一条消息的旅程（HTTP 信道）

```
手机 POST /sessions/{id}/messages {client_msg_id, text}
  → API 层校验 token → 幂等检查（client_msg_id 唯一）→ 落 message(direction=in)
  → 入 job 队列 → worker 取 job
  → 组装上下文（persona + AGENTS.md + 会话历史 + MEMORY 索引）→ Runner.run
  → DSH 流式输出 → 每段写 event_log(seq) → SSE 推给手机
  → 结束：message(direction=out) 落库；若有"稍后提醒"则写 notice
手机断线重连：GET /events?since=<最后 seq> 补齐
```

**没有轮询、没有邮件头解析、没有 MIME**。整条路径上唯一的"格式问题"是 JSON。

---

## 5. 通知架构

> **v2.1 更正。** 本节初版（v2）说"不能常驻前台服务"——**这个说法是错的**，我把 `dataSync` 的 6 小时限制错套到了消息类应用上。
> 官方原文限定得很清楚：*"Currently, this restriction only applies to `dataSync` and `mediaProcessing` foreground service types."*
> 消息类应用该用的是 `remoteMessaging`，它**没有运行时长上限**。所以结论改为：**能常驻，而且默认开。**

### 5.1 能常驻吗：能，但有一条永远追不上微信

| 约束 | 核实到的事实 | 对我们的结论 |
|---|---|---|
| 6 小时 / 24 小时上限 | **只适用于 `dataSync` 与 `mediaProcessing` 两种类型**（[foreground service timeouts](https://developer.android.google.cn/develop/background-work/services/fgs/timeout)）`[确认-文档-原文]` | 不是"前台服务都会被掐"，是"声明成 `dataSync` 才会被掐" |
| 该用哪个类型 | `remoteMessaging`（权限 `FOREGROUND_SERVICE_REMOTE_MESSAGING`，**无运行时权限前置**），官方描述即"在两台设备之间传文本消息"（[类型表](https://developer.android.com/develop/background-work/services/fgs/service-types)）`[确认-文档]` | 正是我们的场景，且无时长上限 |
| 能不能从后台拉起 | targetSdk≥31 禁止后台启动 FGS，但例外清单含：**用户触发的精确闹钟**、`BOOT_COMPLETED`、`MY_PACKAGE_REPLACED`、**用户关闭电池优化**（[restrictions-bg-start](https://developer.android.com/develop/background-work/services/fgs/restrictions-bg-start)）`[确认-文档]` | 三条全用得上：开机拉起、定时闹钟看门狗、免电池优化 |
| 国产 ROM | 省电策略会额外冻结后台，自启动白名单之外一律杀 | 用户手动开一次自启动即可（微信是出厂白名单，我们不是） |
| FCM / 厂商推送 | 国内多数设备无 GMS；小米/华为/OPPO/vivo 的通道要开发者资质与审核 | **这条追不上，放弃。** 影响面：App 被彻底杀掉时收不到推送 |

### 5.2 与微信的逐项对比（诚实版）

| 能力 | 微信 | 我们 | 差距 |
|---|---|---|---|
| 常驻长连 + 前台服务 | 有 | **有**（`remoteMessaging`） | 无 |
| 免电池优化白名单 | 出厂默认 | 用户手动开一次 | 一次性设置 |
| OEM 自启动白名单 | 出厂默认 | 用户手动开一次 | 一次性设置 |
| 多进程互拉 | 有（2–3 个进程） | 单进程 + 闹钟看门狗 | 弱一点，够用 |
| **厂商推送（进程死了也能收）** | 有 | **无** | **不可弥补**（个人拿不到通道） |
| 状态栏常驻通知 | 通常没有（系统特权 / 厂商通道） | **必然有一条** | 这是"像微信一样常驻"要付的税 |
| 服务端离线时仍能提醒 | 不适用（它自己就是服务端） | **本地闹钟** | 我们反而更强 |

**一句话：微信的"本地常驻"我们做得到，微信的"离线推送"我们做不到。** 而离线推送只在 App 被彻底杀掉时才起作用——那个场景我们用本地闹钟 + 下次打开补齐。

### 5.3 所以是两层，不是二选一

| 层 | 解决什么 | 失效条件 |
|---|---|---|
| **实时层：常驻连接**（FGS + SSE） | 秒级到达：新回复、任务完成 | 用户划掉 App / PC 睡了 / 网络断 |
| **准点层：本地闹钟**（`setAlarmClock`） | 准点：提醒、日程 | 换手机后没打开过 App / 用户删了闹钟 |

**两层失效条件不重叠。** 只做实时层 → PC 一睡，提醒就没了。只做准点层 → 任务完成要等下次打开。所以两层都做，各管一段。

准点层值得单独说明，因为它比"推送"更可靠：

```
服务端：notice 表（真值）     ──设备同步──►   手机：本地闹钟 + 本地缓存
   due_utc = 明天 15:00                         setAlarmClock(明天 15:00)
   （服务端此刻可以关机）                        （到点由系统叫醒，不需要网络）
```

所有"必须准点"的需求，本质上都是**可预先知道的时刻**：提醒、日程、每日摘要。时刻既然能提前知道，就提前搬到设备上。代价有三条，都得认：

| 代价 | 说明 | 缓解 |
|---|---|---|
| 设备必须同步过 | 新提醒在手机下次同步前不存在 | 打开 App 即同步；实时层在线时顺带保持最新 |
| 改动有传播延迟 | 在电脑上改了提醒时间，手机可能还是旧的 | 同步时以服务端为准覆盖尚未触发的本地闹钟（`notice.id` 派生 `PendingIntent` 的 requestCode，天然幂等） |
| 用户手动删了系统闹钟 | 服务端不知道 | 下次同步时若 `state` 仍是 `pending` 且 `due_utc` 已过 → 标 `missed` 并在 App 里提示（不重设，避免"删了又回来"） |

### 5.4 `notice` 表：唯一的投递单元

一张表管所有"要让你知道的时刻"，不再分 `reminder` / `occurrence` / `delivery` 三张（v1 的方案在这里合并了，幂等保证不变）：

```sql
CREATE TABLE notice (
  id            TEXT PRIMARY KEY,          -- ULID
  kind          TEXT NOT NULL,             -- reminder | job | reply | system
  session_id    TEXT REFERENCES session(id) ON DELETE SET NULL,
  title         TEXT NOT NULL,
  body          TEXT,
  due_utc       INTEGER NOT NULL,          -- 应该被看到的时刻（UTC 秒）
  state         TEXT NOT NULL,             -- pending | acked | dismissed | missed
  acked_at      INTEGER,
  snooze_until  INTEGER,
  origin        TEXT,                      -- 幂等键，如 'reminder:<id>@<occur_utc>'
  client_ref    TEXT,                      -- 客户端回传的闹钟/事件 id
  created_at    INTEGER NOT NULL
);
CREATE UNIQUE INDEX notice_origin ON notice(origin) WHERE origin IS NOT NULL;
```

- `origin` 唯一 = **提醒幂等本体**：同一提醒的同一时刻，无论扫描几次、重启几次，只可能有一条 notice。
- `client_ref` = v1 的 `sink_ref`：用户删了系统闹钟，服务端能对应上。
- `kind='job'` 让"任务完成了"和"提醒到点了"共用一条投递路径，客户端只需要写一套逻辑。

### 5.5 保活三档（App 里一个开关，默认 L1）

| 档 | 行为 | 常驻通知 | 到达时延 | 适合 |
|---|---|---|---|---|
| L0 省电 | 不常驻；打开才同步 | 无 | 打开时 / ≤15 分钟 | 只关心提醒 |
| **L1 常驻（默认）** | `remoteMessaging` FGS + SSE 长连 + 断线重连 | 一条（无声） | **秒级** | 你要的体验 |
| L2 强保活 | L1 + 开机拉起 + 15 分钟精确闹钟看门狗 + 免电池优化 | 一条（无声） | 秒级，且更抗杀 | 老被杀的机型 |

L2 的看门狗：每 15 分钟一个精确闹钟，回调里检查 FGS 是否存活，死了就 `startForegroundService()`。依据是官方例外清单里"用户触发的精确闹钟"那条——**这不是 hack，是文档允许的路径**。代价是耗电和每 15 分钟一次唤醒。

### 5.6 时延（四档，含退化路径）

| 场景 | 时延 | 机制 | 何时退化 |
|---|---|---|---|
| 前台对话 | 整段（提交级） | SSE | 无 |
| 后台 · 新回复 / 任务完成（L1、L2） | **秒级** | 常驻 SSE | 进程被杀 → 下一档 |
| 后台 · 同上（L0 或进程被杀） | ≤ 15 分钟 | `WorkManager` 周期拉取（最小 15 分钟） | 用户划掉且没开 L2 → 下次打开 |
| 提醒到点 | **精确到分，PC 关机也响** | 本地 `setAlarmClock` | 从没同步过 → 不响 |

**唯一不承诺的是"App 被彻底杀掉后的离线推送"**——那需要厂商通道，个人拿不到（§5.1 最后一行）。除此之外的实时性，和微信同级。

### 5.7 常驻服务的实现要点（S10 的一部分）

| 项 | 做法 |
|---|---|
| 服务类型 | `startForeground(id, notification, FOREGROUND_SERVICE_TYPE_REMOTE_MESSAGING)`；manifest 声明 `android:foregroundServiceType="remoteMessaging"` + `FOREGROUND_SERVICE_REMOTE_MESSAGING` 权限 |
| 常驻通知 | 渠道 `服务状态`，`IMPORTANCE_LOW`（无声不弹）；内容 = 连接状态 + 未读数；带"断开"按钮 |
| 不死 | `onStartCommand` 返回 `START_STICKY`；`onTaskRemoved` / `onDestroy` 里排一次 60 秒后的重试 |
| 重连 | 退避 1→2→5→15→60 秒封顶；`ConnectivityManager.NetworkCallback` 网络恢复立即重连；`Last-Event-ID` 续传 |
| 省电 | 申请免电池优化；**不做**双进程互拉（hack，收益低） |
| 关闭 | 用户关掉常驻 → `stopForeground(STOP_FOREGROUND_REMOVE)` + 取消看门狗闹钟 |
| 与 WorkManager 的关系 | 常驻活着时 WorkManager 空转（检查即返回）；常驻死了才真正拉取 |

### 5.8 闹钟/日历/通知的实现要点（S10 的验收清单）

| 能力 | API | 关键坑 |
|---|---|---|
| 精确闹钟 | `AlarmManager.setAlarmClock()` | 需 `SCHEDULE_EXACT_ALARM`；**Android 14 起默认不授予**（[官方文档](https://developer.android.com/about/versions/14/changes/schedule-exact-alarms)）`[确认-文档]`。必须先 `canScheduleExactAlarms()` 检查，false 时用 `ACTION_REQUEST_SCHEDULE_EXACT_ALARM` 引导，仍拒绝则降级 `setWindow()` 并明说"可能延迟" |
| 闹钟图标 | `setAlarmClock` 的副作用 | 状态栏会显示闹钟图标，这是它的"税"，接受 |
| 重启后重排 | `RECEIVE_BOOT_COMPLETED` + `BOOT_COMPLETED` 广播 | 不重排 = 重启后全丢，这是最常见的漏 |
| 时区/时间变更 | `ACTION_TIMEZONE_CHANGED` / `ACTION_TIME_CHANGED` | 用 UTC 存、`ZonedDateTime` 算，重新计算所有 pending 的触发时刻 |
| 加日历（零权限） | `Intent(ACTION_INSERT, CalendarContract.Events.CONTENT_URI)` + `beginTime`/`title`/`rrule` | 打开系统日历让用户点"保存"——**不需要 `WRITE_CALENDAR`**，比直接写 `CalendarContract` 少一次权限申请、多一次点击。v1 选这个 |
| 直接写日历（免点击） | `CalendarContract.Events` insert | 需 `WRITE_CALENDAR` 运行时权限 + 选日历账户。**不做** |
| 通知 | `NotificationChannel` + `POST_NOTIFICATIONS`（13+） | 渠道一旦创建，属性不可改（用户可见），所以 v1 就把渠道定好，别等改 |
| 免电池优化 | `ACTION_REQUEST_IGNORE_BATTERY_OPTIMIZATIONS` | 国产 ROM 还需要手动开"自启动"，App 里做一页引导 + 检测 |

**一页引导**（S10 交付）：检查精确闹钟权限、通知权限、电池优化白名单、自启动（只能引导，无法检测），四项各给一条跳转。

---

## 6. 客户端设计

### 6.1 屏幕：三个就够

| 屏 | 内容 | 状态 |
|---|---|---|
| 会话列表 | 会话 + 最后一条 + 未读数；右下角新建 | 空列表 / 加载 / 错误 / 离线（显示缓存） |
| 对话 | 消息流（用户右、Agent 左）、流式光标、附件卡片、失败重发 | 发送中 / 流式中 / 失败 / 重试 |
| 设置 | 服务端地址、token、设备名、同步开关、权限引导页、清缓存 | — |

**不做**：抽屉导航、主题市场、动画、多账号、搜索（v1 用系统滚动 + 服务端后续加）。

### 6.2 模块与文件（约 17 个 Kotlin 文件，3–3.5k 行）

```
android/
├─ settings.gradle.kts · build.gradle.kts · gradle.properties
├─ gradle/libs.versions.toml            # 版本集中一处
└─ app/
   ├─ build.gradle.kts
   └─ src/main/
      ├─ AndroidManifest.xml
      ├─ java/dev/mephisto/app/
      │  ├─ App.kt              # Application + 手工装配（不引 Hilt）
      │  ├─ MainActivity.kt     # 单 Activity + 状态驱动切屏
      │  ├─ data/Api.kt         # OkHttp 调用，DTO 与服务端一一对应
      │  ├─ data/Sse.kt         # okhttp-sse：事件解析 + 断线重连 + Last-Event-ID
      │  ├─ data/Db.kt          # Room：session/message/notice 三张缓存表
      │  ├─ data/Repo.kt        # 缓存优先 + 增量拉取（唯一懂"同步"的地方）
      │  ├─ data/Settings.kt    # DataStore：地址、token、设备 id
      │  ├─ ui/SessionsScreen.kt
      │  ├─ ui/ChatScreen.kt
      │  ├─ ui/SettingsScreen.kt
      │  ├─ ui/Components.kt    # 气泡、流式光标、附件卡
      │  ├─ alarm/Alarms.kt     # setAlarmClock / cancel / 幂等 requestCode
      │  ├─ alarm/AlarmReceiver.kt
      │  ├─ alarm/RescheduleReceiver.kt   # BOOT_COMPLETED / 时区变更 / 开机拉起常驻
      │  ├─ alarm/WatchdogReceiver.kt     # L2：15 分钟巡检，FGS 死了就拉起
      │  ├─ live/KeepAliveService.kt      # remoteMessaging 前台服务 + 常驻 SSE
      │  └─ sync/SyncWorker.kt  # 常驻不可用时的 15 分钟兜底拉取
      └─ res/                   # 图标、strings、network_security_config
```

### 6.3 依赖（5 个，多一个都要写理由）

| 依赖 | 用途 | 为什么不用别的 |
|---|---|---|
| Compose BOM + `activity-compose` + `material3` | UI | 官方、无 XML |
| `okhttp` + `okhttp-sse` | HTTP + SSE | `HttpURLConnection` 手写 SSE 要自己处理分块与重连；OkHttp 两个 artifact 解决 |
| `kotlinx-serialization-json` | JSON | 反射版（Gson）在 R8 下有坑；序列化插件编译期生成更稳 |
| `androidx.room` + KSP | 本地缓存 | 手写 SQLite 要自己写迁移；Room 的 `@Query` 也顺便当"离线读"的索引 |
| `androidx.datastore:preferences` | 存地址/token | 比 `SharedPreferences` 异步安全 |

**不引**：Hilt/Koin（手工装配就够，依赖注入框架在这里只增加编译时间）、Navigation-Compose（三个屏用 `when` 切）、Retrofit（一个 `OkHttp` 调用 + 一个手写 DTO 反而更短）、Paging（v1 分页用 `limit=100` + 滚动加载）。

### 6.4 鉴权与安全

| 项 | 做法 |
|---|---|
| 首次配对 | 服务端跑 `python -m mephisto device add --name 手机`，打印一次性 token，App 里粘贴 |
| token 存储 | DataStore，只在应用私有目录；`android:allowBackup="false"`（防止被云备份带走） |
| 传输 | **HTTPS**（Caddy 在 VPS 上用你的域名签真证书）；App 不写死地址 |
| 明文 HTTP | **仅 debug 构建**允许（本机调试）；release 禁止——有真证书就不需要白名单了 |
| 吊销 | 服务端删 `device` 行 → token 立刻失效；App 收 401 后清缓存回设置页 |

### 6.5 离线与缓存

- **缓存优先**：打开先出 Room 里的数据，再打网络。没有网络时列表与历史可读，发送排队等重连。
- 缓存窗口：最近 30 天或每会话 500 条，先到者为准；超出由 `Repo` 清理。
- **离线派活不在 v1 范围内**：离线只能读、只能存草稿；任务必须在线提交（重复执行要花钱，不能靠客户端猜）。

### 6.6 国产 ROM 适配（写进 App 的引导页，不写进 README 就算了）

| 厂商 | 需要用户手动做的事 |
|---|---|
| 小米 / HyperOS | 设置 → 应用 → 自启动；省电策略 → 无限制；后台弹出界面 |
| 华为 / HarmonyOS | 应用启动管理 → 手动管理（允许自启动、关联启动、后台活动） |
| OPPO / vivo / 一加 | 允许后台运行 + 锁定后台任务（多任务界面下拉） |
| 通用 | 关闭"电池优化"（`ACTION_REQUEST_IGNORE_BATTERY_OPTIMIZATIONS`） |

**验收标准不是"我点了设置"，而是"重启手机 + 放置 2 小时后，闹钟仍然准时"**。这条在 S10 必须实测。

---

## 7. 服务端 ↔ 客户端契约 v1（S4 冻结，之后只加不改）

### 7.1 端点

| # | 方法 | 路径 | 用途 | 幂等键 |
|---|---|---|---|---|
| 1 | GET | `/healthz` | 版本 + uptime（**无鉴权**，给 App 测连通） | — |
| 2 | GET | `/api/v1/sessions?since=` | 会话列表（增量） | — |
| 3 | POST | `/api/v1/sessions` | 新建 `{title}` | 无（允许重名） |
| 4 | GET | `/api/v1/sessions/{id}` | 详情 | — |
| 5 | GET | `/api/v1/sessions/{id}/messages?since=&limit=` | 消息增量 | — |
| 6 | POST | `/api/v1/sessions/{id}/messages` | 发消息 `{client_msg_id, text}` | `client_msg_id` 唯一 |
| 7 | POST | `/api/v1/messages/{id}/ack` | 已读回执 | 幂等 |
| 8 | GET | `/api/v1/events?since=` | SSE（`message` / `job` / `notice`） | `Last-Event-ID` |
| 9 | GET | `/api/v1/notices?since=&horizon_days=7` | notice 真值（含未来的） | — |
| 10 | POST | `/api/v1/notices/{id}/state` | `{state, snooze_minutes?, client_ref?}` | 幂等 |
| 11 | GET | `/api/v1/attachments/{id}` | 下载（支持 `Range`） | — |
| 12 | DELETE | `/api/v1/devices/{id}` | 吊销设备 | 幂等 |
| 13 | POST | `/api/v1/attachments` | 开上传会话 → `{upload_id, chunk_size}` | — |
| 14 | PUT | `/api/v1/attachments/{upload_id}/chunks/{i}` | 传一片（**≤4 MB**） | `(upload_id, i)` 覆盖式幂等 |
| 15 | POST | `/api/v1/attachments/{upload_id}/complete` | 校验 sha256 并落盘 → `attachment_id` | digest 去重 |
| 16 | DELETE | `/api/v1/attachments/{upload_id}` | 放弃上传 | 幂等 |

`POST /messages` 的 body 增加可选 `attachment_ids: []`。**分片大小 4 MB 是算出来的**（125 秒 × 最坏上行 512 kbps ÷ 8 ≈ 8 MB，取一半留余量），依据与单文件 64 MB 上限见 [17 附件](17-attachments.md)。

**v1 不支持上传附件**（只下载）。发图片/文件等 S13 之后再说——多段上传是一条独立的、容易长歪的路。

### 7.2 游标规则：单调整数，绝不用时间戳

每张可增量同步的表都有 `seq INTEGER PRIMARY KEY AUTOINCREMENT` 之外的独立列（或直接用 `seq`），客户端保存 `last_seq`，请求带 `since=<last_seq>`，服务端返回 `seq > since ORDER BY seq LIMIT n`。

**为什么不用时间戳**：时钟回拨、同秒多条、时区——三个坑，一个都不必踩。

### 7.3 SSE 事件形状

```
id: 1043
event: message
data: {"seq":1043,"session_id":"s_01J...","message":{"id":"m_...","direction":"out","text":"...","streaming":true}}

id: 1044
event: notice
data: {"seq":1044,"notice":{"id":"n_...","kind":"reminder","title":"交表","due_utc":1762430400}}

: keepalive        ← 每 25 秒一行注释，防中间设备超时断连
```

流式回复 = 同一个 `message.id` 的多次 `message` 事件，`text` 是**累计全文**（不是增量）。这样客户端不需要拼接逻辑，断线重连也只取最后一条。`[判断]` 代价是流量略大；对几十万字符以内的回复可忽略。

### 7.4 幂等三件套

| 场景 | 键 | 后果（没有它会怎样） |
|---|---|---|
| 弱网重发消息 | `client_msg_id`（客户端生成 ULID） | 重复执行 DSH = 重复花钱 |
| 重复同步 notice | `notice.origin` 唯一索引 | 同一提醒响两次 |
| 重复点"已完成" | `state` 状态机 + `acked_at` 幂等写 | 计数错乱、误删闹钟 |

### 7.5 兼容策略

- 路径带版本 `/api/v1/`；**v1 期间只加字段不改语义**。
- 客户端遇到未知字段忽略；服务端遇到未知字段报 400（早失败好过静默）。
- 服务端保留 `/api/v0/` 一条重定向**不做**——两个客户端都在自己手里，不需要兼容包袱。

---

## 8. 工具链（"没有 java"这件事的解法）

### 8.1 为什么装在 `E:\study\.toolchain\` 内

| 原因 | 说明 |
|---|---|
| 沙箱 | 我只被允许写 `E:\study`。装到 `C:\Program Files` 会失败，装到 `~/.gradle` 也会失败 |
| 可删 | 卸载 = 删目录。不污染系统 `PATH`、不注册服务、不留注册表项 |
| 可换 | 换 JDK 版本 = 改 `env.ps1` 一行 |
| 代价 | 每次构建要先 `. .\tools\env.ps1`（一条命令，写进 `tasks.ps1` 就不用手动） |

```
E:\study\.toolchain\
├─ jdk-21\                     # 解压的 Temurin 21（LTS）
├─ android-sdk\
│  ├─ cmdline-tools\latest\    # sdkmanager / avdmanager
│  ├─ platform-tools\          # adb
│  ├─ platforms\android-XX\    # 编译目标（版本见 8.3）
│  └─ build-tools\XX.0.0\
├─ gradle-home\                # GRADLE_USER_HOME（Gradle 发行版 + 依赖缓存）
└─ downloads\                  # 原始 zip，留档便于重装
```

### 8.2 `tools/env.ps1`（每次构建前点一次）

```powershell
$root = "E:\study\.toolchain"
$env:JAVA_HOME         = "$root\jdk-21"
$env:ANDROID_HOME      = "$root\android-sdk"
$env:ANDROID_SDK_ROOT  = $env:ANDROID_HOME
$env:GRADLE_USER_HOME  = "$root\gradle-home"
$env:PATH = "$env:JAVA_HOME\bin;$env:ANDROID_HOME\platform-tools;$env:ANDROID_HOME\cmdline-tools\latest\bin;$env:PATH"
```

### 8.3 `tools/setup-android.ps1` 的分步（S7 交付，幂等：已装则跳过）

| 阶段 | 动作 | 判定 |
|---|---|---|
| 1 | Python 脚本查 Adoptium API 拿 **最新 21 LTS** Windows x64 zip（**必须带 UA**）→ 下到 `downloads\` | 文件大小 > 150 MB |
| 2 | 解压到 `jdk-21\` | `java -version` 输出 `21.x` |
| 3 | 下 `commandlinetools-win-*.zip`（从 `dl.google.com` 的 repository XML 里解析最新 revision，不写死） | `sdkmanager --version` 有输出 |
| 4 | `sdkmanager --licenses`（自动 `y`）+ 安装 `platform-tools` / `platforms;android-<最新稳定>` / `build-tools;<同版本>` | `sdkmanager --list_installed` 三行齐全 |
| 5 | 生成 `local.properties`：`sdk.dir=E\:\\study\\.toolchain\\android-sdk` | 文件存在 |
| 6 | `gradlew.bat --version`（首次自动下 Gradle 发行版到 `gradle-home`） | 打印 Gradle 版本 |

**版本不写死**：`compileSdk` / `targetSdk` 用安装时 `sdkmanager --list` 里的最新稳定平台；`libs.versions.toml` 里锁实际装到的版本。`[判断]` 这样做比钉一个今天好看的数字更耐用。

### 8.4 体积与时间（估计值，S7 实测后写回真实数字）

| 项 | 下载 | 展开 |
|---|---|---|
| JDK 21 zip | ~190 MB | ~330 MB |
| cmdline-tools | ~130 MB | ~250 MB |
| platform + build-tools + platform-tools | ~250 MB | ~700 MB |
| Gradle 发行版 + 首次依赖解析 | ~500 MB | ~800 MB |
| **合计** | **~1.0–1.2 GB** | **~2 GB** |

时间：下载取决于代理（`[未验证]` 实测才知道），首次构建（含 KSP）约 3–8 分钟，之后增量 20–60 秒。

### 8.5 构建与装机

```powershell
. .\tools\env.ps1
cd android
.\gradlew.bat :app:assembleDebug          # 产出 app\build\outputs\apk\debug\app-debug.apk
adb devices                               # 手机开 USB 调试后出现
adb install -r app\build\outputs\apk\debug\app-debug.apk
```

**没有 adb 也能装**：把 APK 拷到手机（微信文件传输/数据线/网盘），点开允许"安装未知来源"。这条要写进给用户的步骤里，因为 `adb` 需要 USB 调试 + 驱动，经常反而是最麻烦的一步。

### 8.6 如果下载失败（退路，按优先级）

| 退路 | 代价 |
|---|---|
| 你自己在浏览器里下三个 zip 放到 `E:\study\.toolchain\downloads\`，脚本从第 2 步开始 | 需要你动手一次 |
| 只做"桌面 Web 页"，安卓延后 | 手机上体验降级为浏览器，闹钟只能靠系统日历手动加 |
| 换一台有 JDK 的机器构建 APK，拷贝安装 | 构建与运行分离，可接受但每次改代码都要复制 |

---

## 9. 工程纪律（沿用 v1，微调）

### 9.1 目录（`android/` 与 Python 同仓）

```
E:\study\mephisto\
├─ docs/  refs/  research/  contracts/     # 设计资产（第一批提交）
├─ pyproject.toml  .gitignore  .gitattributes
├─ tasks.ps1  tasks.cmd                    # 入口（见 §9.5）
├─ tools/                                  # fake-sse.py、probe-android.ps1、env.ps1
├─ mephisto/                               # ★ 扁平布局，见下方说明
│  ├─ __main__.py  cli.py  config.py  ids.py  clock.py  ports.py
│  ├─ domain/      envelope.py  session.py  routing.py  notice.py
│  ├─ store/       db.py  schema.sql  repo.py
│  ├─ adapters/    http/ (serve,sse,static/index.html)  runner/dsh.py
│  └─ app/         daemon.py  pipeline.py  jobs.py  notice_sink.py  scheduler.py
├─ android/                                # 见 §6.2
└─ tests/  run.py  unit/  integration/  fixtures/
```

> **布局变更（S0 落地时改的）**：原计划用 `src/mephisto/`，实际改成仓库根的 `mephisto/`。
> 原因：本机 `pip` 装不了包（§2），`src/` 布局会让 `python -m mephisto doctor` 必须先 `pip install -e .` 或设 `PYTHONPATH`，
> 而扁平布局**从仓库根直接可用**，零安装。代价是少了一层隔离，用 `.gitignore` 与代码评审兜住。

`adapters/mail/` 与 `adapters/telegram/` 现在**不存在**，等各自那一步再建。空目录是负担。

### 9.1.1 开发环境的三条硬约束（S0 实测）

| 约束 | 事实 | 写法 |
|---|---|---|
| 装不了包 | `pip` / `ensurepip` 在临时目录上失败 | 运行时与测试都只用标准库；`[dev] pytest` 只是给有网环境留的 |
| 建不了临时目录 | `tempfile.mkdtemp()` 建的目录**不可写**，且删不掉 | 禁止 `mkdtemp` / `TemporaryDirectory`；测试目录建在 `tests/_tmp/`（已 gitignore） |
| 脚本编码 | PS 5.1 把无 BOM 的 UTF-8 `.ps1` 读成 GBK，直接语法报错 | `.ps1` 存 UTF-8 **with BOM**；`.cmd` 用 CRLF |

### 9.2 硬规则

| 规则 | 为什么 |
|---|---|
| Python 单文件 ~200 行、Kotlin ~300 行是**提醒，不是禁令** | 超了先问"职责是不是混了"；**绝不为了行数硬拆**。`domain/envelope.py` 312 行是合理的——它装的是"一条消息 + 关于它的两个判断"，硬拆成三个文件只会让读者多点两次跳转。拆分要由职责驱动，不是由行数驱动 |
| `domain/` 不 import 任何 IO | 业务判断可纯单测，不需要假数据库 |
| 副作用只在三处：`Store` / `Source`+`Sink` / `Runner` | 其余全是纯函数 |
| 端口用 `typing.Protocol`，不用基类继承 | 适配器只"翻译"，不写业务分支 |
| 命名用领域词：`Envelope` `Session` `Notice` `Route` | 禁止 `Manager` / `Helper` / `Util` / `Service` |
| 所有外部调用必带超时；所有循环必带退避 | 个人系统最常见的死法是"卡住且无声" |
| 异常表达失败，不用返回码 | 只有跨线程边界才用 `Result` |
| **下载一律用 Python，不用 curl** | §2 实测：沙箱下 schannel 的 HTTPS 不可用 |
| 安卓侧不做多模块 Gradle | `:app` 一个模块；拆模块是为了多人协作，不是为了一人项目 |

### 9.3 Git

- **trunk-based**：直接提交 `main`，`main` 永远可构建。风险大的切片才开 `wip/`。
- 每步一个**附注 tag** `step-0` … `step-13`，tag message = 该步验收清单。
- 提交信息：`feat(core):` / `feat(android):` / `test(routing):` / `docs(steps):`
- 每步结束必跑：`pytest -q`（+ 该步验收命令），绿了才 tag。
- `.gitignore` 必须含：`.venv/ state/ secrets/ .toolchain/ android/.gradle/ android/local.properties android/**/build/ *.keystore *.apk`

### 9.4 测试策略

| 层 | 怎么测 | 约束 |
|---|---|---|
| Python domain | 纯单测，表驱动 | 无 IO、无 mock |
| Python 适配器 | 固定样本（录制 JSON、`.jsonl` 事件） | **不打真网** |
| Python 全链路 | 假 Source + 假 Runner + 临时 SQLite | 一次跑完 < 5s |
| 契约 | 用 `contracts/*.schema.json` 校验产出 | 手写极简校验器，不引 `jsonschema` |
| Android 纯逻辑 | JVM 单测（SSE 解析、游标合并、闹钟 requestCode 推导） | **不做仪器测试**（要设备/模拟器，收益低） |
| Android UI | 手动验收清单（每步一节） | 模拟器不做（需 hypervisor，且真机才是验收对象） |

---

## 10. 分步计划

> 每步结束我回报四件事：**改了什么 / 怎么验 / 验收命令 / tag 名**。你跑命令确认，我才进下一步。
> 天数是净编码时间。**阶段 A 是地基，阶段 B 是你要的安卓，阶段 C 是增值。**

### 阶段 A · 服务端内核（S0–S6，约 10 天）

#### 第 0 步 · 骨架 + 工具链预检（1 天）

| 项 | 内容 |
|---|---|
| 产出 | `git init`；`.gitignore`（含 `state/ workspace/ secrets/ .toolchain/ tests/_tmp/`）；`.gitattributes`；`pyproject.toml`（`requires-python>=3.12`，运行时零依赖，`[dev] pytest` 仅备用）；`mephisto/{__main__,cli,config,clock,ids}.py`（**扁平布局**）；`tasks.ps1` + `tasks.cmd`（PS 5.1 执行策略绕行）；`tests/run.py`（标准库 unittest 入口）；**`tools/probe-android.ps1`**（只查不装） |
| 关键决策 | 工具链预检放在第 0 步而不是第 7 步：**下不了就当场改路线**（§8.6），不浪费前面 6 步 |
| 验收 | `python -m mephisto doctor` 八项全 OK；`python tests\run.py` 23 个用例全绿（`python -m pytest -q` 在能装包的环境里同样可用）；`tools\probe-android.ps1` 打印磁盘与四个下载目标的结果 |
| 实测结果 | doctor 8/8 OK（含 DSH `0.2.0-rc.2`）；tests 23 passed；`tasks.cmd setup`/`test`/`doctor` 三个入口均 exit 0 |
| tag | `step-0` |

#### 第 1 步 · 领域模型（1 天）

| 项 | 内容 |
|---|---|
| 产出 | `domain/codec.py`（严格解码：未知键报错、错误带路径）、`domain/values.py`（`Address`/`Attachment`/`ThreadRef`/`Delivery`/`E2EE`）、`domain/envelope.py`（`Envelope`/`Auth`/`Route`/`normalize_subject`）、`domain/session.py`、`domain/notice.py`（含状态机）、`ids.py` 增加 `client_msg_id` 校验 |
| 关键决策 | `Envelope` 不可变（`frozen=True`）；时间一律 UTC 感知，naive 直接拒绝；字段名 `sender` 避开 `from` 关键字；`thread` 三字段可空（HTTP 信道没有邮件头）；**`null` 表示"未知"而不是"没有"**（`delivery.seen_at`、`e2ee`、`auth.*`） |
| 验收 | 单测：编解码往返、未知键/超界/坏枚举报错、notice 状态机非法跃迁报错、契约键集合与 `contracts/envelope.schema.json` 一致 |
| 实测结果 | **82 个用例全绿**（unittest 与 pytest 双跑）；domain 层零 IO 依赖；`serve/run/db` 仍返回 2 |
| 自我纠正 | 初次为了让 `envelope.py` 不超 200 行，把 `Auth`/`Route`/`normalize_subject` 硬拆成两个文件——是规则驱动而非职责驱动，已合回。规则同时改成"提醒不是禁令"（§9.2） |
| tag | `step-1` |

#### 第 2 步 · 存储与迁移（1.5 天）

| 项 | 内容 |
|---|---|
| 产出 | `store/migrations/001_init.sql`（13 张表全量 DDL）；`store/db.py`（连接 / PRAGMA / 迁移器 / 体检）；`store/{sessions,messages,events}.py`（按聚合拆，不按行数拆）；`mephisto db migrate|check` |
| 关键决策 | WAL + `foreign_keys=ON` + `synchronous=NORMAL`；一条连接 + `RLock`；`check_same_thread=False`；唯一约束与外键全写进 DDL；**时间列一律 epoch 毫秒**（与 ULID 同精度，跨表不用换算）；`message.seq`/`event_log.seq` 用 `INTEGER PRIMARY KEY AUTOINCREMENT`——**游标由库给**，不在应用层算 `max+1`；「同会话只许一个 running job」由部分唯一索引保证；`route_decision` 只追加 |
| 仓库只写用得上的 | 表全建（DDL 是契约，成本低），但 repo 只为当前需要的表写：`notice`/`reminder`/`job`/`device`/`upload` 分别留到 S6/S6/S5/S9/S9，不预先写没人调用的代码 |
| 验收 | 迁移幂等 ✓；`INSERT OR IGNORE` 去重生效（`client_msg_id` 与 `(channel, external_id)` 两组都测）✓；删会话时消息报 `RESTRICT` ✓；`python -m mephisto db check` 通过 ✓ |
| 实测结果 | **107 个用例全绿**（unittest + pytest 双跑）；真机 `db migrate` 打印「v0 → v1」，再跑打印「已是最新」；`db check` 显示 13 张表、`journal_mode=wal`、完整性 ok、外键问题 0 |
| 踩到的坑 | `data_dir` 相对**仓库根**解析，测试里给了相对路径 → 写进了仓库自己的 `state/`。已改测试给绝对路径，并在 `config.data_dir` 上写明这条语义 |
| tag | `step-2` |

#### 第 3 步 · 运行器（DSH）（1.5 天）

| 项 | 内容 |
|---|---|
| 产出 | `ports.Runner`；`adapters/runner/dsh.py`：**`CliRunner`**（`dsh --profile mephisto --json`，prompt 走 **stdin**，逐行读 NDJSON）；`context.py` 装配四段上下文 |
| 关键决策 | **不要 Python SDK**（`deepseek-harness-sdk` 那条 80 MB 的未知作废）；**每轮新会话**，不用 `--session-id`（留 `runner.continuity=resume` 开关）；prompt 必须走 stdin（Windows 命令行上限 32k，我们的 prompt 30–60 KB）；取消 = `taskkill /T /F`；独立 `DSH_HOME=state/dsh-home/` |
| 验收 | `mephisto run --dry-run` 打印命令/DSH_HOME/prompt 全文；`mephisto run "1+1"` 真跑通并打印事件流 |
| 实测结果 | **2.2s 跑通**，tokens=6024 / cacheRead=5504（91% 命中）；事件字段全部按实测写死（`session.sessionId`、`status.phase`、`turn_end.reason.kind`、`step_end.usage`、`final.text`）；会话落在 `state/dsh-home/sessions/--E-study-mephisto--/`，与 `~/.dsh` 隔离 |
| 详见 | [25 会话引擎](25-session-engine.md) §6 |
| tag | `step-3` |

#### 第 4 步 · HTTP API + SSE（2 天）★ 契约在此冻结

| 项 | 内容 |
|---|---|
| 产出 | `adapters/http/serve.py`（`ThreadingHTTPServer` + 薄路由）、`sse.py`、`static/index.html`（约 80 行：会话列表 + 发消息，**只是调试工具**）；`Source`（POST 入队）+ `Sink`（写库 + 推事件） |
| API | §7 的 16 条全部可用（含**附件分片上传三件套**；下载可留空实现） |
| 关键决策 | `Bearer token`（存 hash、绑设备）；默认只听 `127.0.0.1`，公网入口在 Cloudflare；**回复整段到达**（提交级）；SSE 每 15 秒 keepalive；**分片 4 MB 硬上限 8 MB** |
| 验收 | 浏览器打开 `index.html` 能对话；`curl` 打 `/healthz` 与各端点正确响应；4 MB 分片 → complete 校验通过；错误 token → 401 ✓ |
| 实测结果 | **141 个用例全绿**（unittest + pytest 双跑）；真起服务端到端 **2.2s**：healthz 公开、无令牌 401、建会话、真 dsh 回复 `1 + 1 = **2**`（tokens=6059 / cacheRead=5504）、`client_msg_id` 重发标记 `duplicate` 且不再跑模型、SSE 逐条推 `id/event/data`、中文往返无损 |
| 落地细节 | 16 端点全部实现；`attachment_id` 即 `sha256:<hex>`（内容寻址去重）；上传分片落 `state/uploads/<id>/`，拼装后流式校验再 `adopt` 进 `state/blobs/`；SSE 用 500ms 轮询 DB 换零跨线程状态 |
| 冻结物 | `docs/steps/04-api.md`：十六条端点的路径/方法/请求/响应/错误码，**写成表**。S8–S10 照它实现，之后只加不改 |
| tag | `step-4` |

#### 第 5 步 · 队列与守护进程（1.5 天）

| 项 | 内容 |
|---|---|
| 产出 | `app/daemon.py`（三个 worker 线程：source / job / notice）；`jobs.py`（`job` 表 + `UPDATE...RETURNING` 抢占）；进程内 `queue.Queue` 只做唤醒、真值在表里 |
| 关键决策 | 抢占靠一条 SQL（不用文件锁）；`attempts` 上限 3 + 指数退避；进程被杀后未完成 job 在启动时自动重排 |
| 验收 | 并发提交 5 条消息不串会话 ✓；强杀再启动未完成 job 自动重排 ✓；job 失败写 `kind=system` notice ✓ |
| 实测结果 | **151 个用例全绿**（双跑）；真服务异步端到端：POST 返回 **202（0.00s）**，worker 4.1s 后落回复，SSE 推送 `message→job×10→message`；`claim_next` 单条 SQL 保证同会话不并发 |
| 与计划的偏差 | 只用一个 worker 线程（不是计划写的三个）：source 是 HTTP 请求线程本身，notice 由 S6 的调度器负责；`attempts` 递增但**重试退避留到 S6 与调度器一起做**，现在失败即终态 + 写 notice（不静默） |
| tag | `step-5` |

#### 第 6 步 · 提醒内核 + notice 投递（1.5 天）

| 项 | 内容 |
|---|---|
| 产出 | `scheduler.py`（最小堆 + 60 秒睡眠上限 + 到点写 notice）；`reminder` 表（规则）+ 展开成 notice；**`mephisto mcp`（stdio MCP server，7 个工具）**；`python -m mephisto remind "3分钟后 提醒我喝水"` 一条命令；`GET /api/v1/notices` 可用 |
| 关键决策 | `notice.origin` 唯一索引 = 幂等本体；宽限 2 小时内补发并标"（延迟）"，超时合并成"你错过了 N 条提醒"；**不做 `.ics`、不做 SMTP**（邮件搁置后都不需要） |
| 验收 | 时间旅行测试：时钟推到 +3 小时，**只补发一次** ✓；同一提醒重复调度只产生一条 notice ✓；`curl` 能查到未来 7 天的 notice ✓ |
| 实测结果 | **194 个用例全绿**（双跑）；真 MCP subprocess：initialize / tools_list(5) / remind_add 全通；真库里 `GET /notices` 返回 2 条 pending；`mephisto remind "3分钟后 喝水"` 一条命令建好规则+notice |
| 与计划的偏差 | 工具做了 **5 个**（remind_add/list/cancel/reschedule + memory_append），`chatlog_*` 留到 S13——现在没有数据源，声明出来只会让模型调用一个必然失败的工具 |
| tag | `step-6` |

**阶段 A 结束时**：用 curl 能完成"建会话 → 发消息 → 收到 DSH 回复 → 拿到提醒真值"。服务端已经可用了，只是用起来要靠命令行。

### 阶段 B · 安卓客户端（S7–S10，约 7.5 天）

#### 第 7 步 · 工具链 + 空壳 App 打通（1 天）

| 项 | 内容 |
|---|---|
| 产出 | `tools/setup-android.ps1`（§8.3 六阶段，幂等）；`tools/env.ps1`；`android/` 工程（Compose 空壳，一个按钮打 `/healthz` 并显示版本） |
| 关键决策 | **不装 Android Studio**；SDK/JDK/Gradle 全部在 `E:\study\.toolchain\`；版本以 `sdkmanager --list` 实际输出为准 |
| 验收 | `.\gradlew.bat :app:assembleDebug` 产出 APK；`adb install` 或拷 APK 装上；**打开 App 点按钮，屏幕上出现服务端的版本号**——这一步只要通了，后面全是应用代码的事 |
| 风险 | 首次 Gradle 下载依赖慢；如果卡住，允许分两次跑（先 `--refresh-dependencies` 拿骨架） |
| tag | `step-7` |

#### 第 8 步 · A1 只读（1.5 天）

| 项 | 内容 |
|---|---|
| 产出 | `Api.kt`、`Db.kt`（Room）、`Repo.kt`、`SessionsScreen`、`ChatScreen` 的只读版；附件下载（`DownloadManager`） |
| 关键决策 | 缓存优先（先出 Room 再打网络）；`since=` 增量；分页 `limit=100` + 滚动加载 |
| 验收 | 真机上看到会话列表与历史消息；关掉 Wi-Fi 重启 App，**列表和历史上文还在**（来自缓存）；点附件能存到手机 |
| tag | `step-8` |

#### 第 9 步 · A2 发送 + 过程可见 + 排队/打断（2 天）

| 项 | 内容 |
|---|---|
| 产出 | `Sse.kt`（`okhttp-sse` + `Last-Event-ID` 重连）、发送框、失败重发、`client_msg_id` 生成、已读回执、**分片上传（4 MB/片，断点续传，逐片进度）**、拍照前压缩到 ≤2 MB |
| 关键决策 | 回复是**整段到达**（提交级），客户端直接替换不拼接；「思考中」是**秒表 + 工具名**，不是假动画；`client_msg_id` 在 App 生成、重试复用；乐观更新；运行中发消息显示「已排队」，另给「打断并重来」；**超过 64 MB 的文件不传，提示放工作区 `inbox/`** |
| 验收 | 发一句话看到**秒表 + 当前工具名**，回答整段出现；运行中再发一条显示「已排队」、结束后自动合并执行；点「打断」后任务管理器里无残留 `dsh`/`node`；飞行模式 3 秒再关，重发不产生两条；杀掉 App 再打开，`since=` 补齐；**8 MB 图片分 2 片传完、64 MB 文件分 16 片传完、90 MB 被客户端拦住** |
| tag | `step-9` |

#### 第 10 步 · A3 常驻 + 闹钟 + 日历 + 通知（3 天）

| 项 | 内容 |
|---|---|
| 产出 | `KeepAliveService`（`remoteMessaging` 前台服务 + 常驻 SSE + 重连退避 + `START_STICKY`）、`Alarms.kt`（`setAlarmClock`，`requestCode` 由 `notice.id` 派生 → 天然幂等）、`AlarmReceiver`、`RescheduleReceiver`（开机/时区/时间变更/拉起常驻）、`WatchdogReceiver`（L2 巡检）、`Notifier`（渠道）、**权限与保活引导页**（含 L0/L1/L2 开关）、`SyncWorker` |
| 关键决策 | 默认 **L1 常驻**；未来 notice → 精准闹钟；已过期未 ack → 立即通知；`canScheduleExactAlarms()` 为 false → 引导 + 降级 `setWindow`；日历用 `ACTION_INSERT`（零权限）；常驻通知渠道 `IMPORTANCE_LOW`（无声不弹） |
| 验收（本节最关键） | ① 建一条 5 分钟后的提醒，**电脑关机 + 手机飞行模式**，仍准时响；② 重启手机后闹钟仍在、常驻自动恢复；③ 改系统时区，时间正确；④ 点"加入日历"进系统日历并带提前提醒；⑤ 删掉闹钟后服务端不重设；⑥ 过期的提醒变成"你错过了"；⑦ **锁屏 2 小时后**在电脑上触发任务完成，通知**秒级**到；⑧ 手动划掉 App 后开 L2，看门狗在 15 分钟内把它拉回来 |
| tag | `step-10` |

**阶段 B 结束时**：手机就是你的日常入口。成功标准 1–4 全部达成。

### 阶段 C · 增值（S11–S13，约 7 天，顺序可换）

#### 第 11 步 · 工作区与记忆（3 天）

`workspace/` 布局、`AGENTS.md` 注入、`MEMORY.md` 索引（200 行硬失败）+ 夜间整理、git 每小时快照。写边界从 `jobs/<taskId>/` 上移到整个 `workspace/`，共享区只追加。详见 [30 工作区与记忆](30-workspace-memory.md)。

#### 第 12 步 · 第二信道（1 天）

Telegram Bot（`long polling`，无需公网入口）。**目的不是功能，是验证抽象**：如果这步需要改 `domain/` 或 `pipeline.py`，就地重构。验收：`git diff --stat` 显示改动只在 `adapters/telegram/` + 一行装配。

#### 第 13 步 · 聊天摘要：微信 + QQ（3 天）

| 项 | 内容 |
|---|---|
| 产出 | `adapters/chatlog/wechat.py`（`wxread` → `chatlog.sqlite3`）、`adapters/chatlog/qq.py`（`qqflow-server:5032` → 同一张表）、`watch` 表与自动发现、摘要流水线（防抖 + 冷却 + 晚间汇总） |
| 关键决策 | **采集全量、摘要挑着来**；新会话自动进 `candidate`，不自动摘要（[40](40-chatlog.md) §4.7）；一次摘要后 10 分钟冷却（§4.6）；**策略三层 + AI 只做脏活**（§4.8）：广告群自动静音但不自动开启；QQ **两个实现都做**（`q1_server` / `q2_export`），两周记分卡后留一个（§9.5） |
| 前置（手动一次） | 用 QQBackup/QQDecrypt 取 16 字节库密钥 → 注册给 `qqflow-server` → 跑通 `GET /api/v1/sessions` |
| 验收 | 微信真群消息进库；QQ 群消息进库；同一群连发 10 条只产生 1 条摘要；摘要发出后 2 分钟内又来消息**不重复发**；新出现的群在第二天「发现新会话」通知里 |
| tag | `step-13` |

### 阶段 D · 搁置

#### 第 14 步 · 邮件（**不排期**）

见 §14。设计文档全部保留，代码一行不写。

---

## 11. 数据模型 v1（12 张表）

| 表 | 关键列 | 关键约束 |
|---|---|---|
| `session` | id, title, kind, state, created_at, last_active_at, seq | `slug` UNIQUE |
| `session_alias` | kind, key, session_id | PK(kind,key)；kind ∈ {http, telegram, message_id, address, subject_tag}（后三者为邮件预留） |
| `message` | id, session_id, direction, channel, external_id, message_id, in_reply_to, refs, text, client_msg_id, seq, created_at | UNIQUE(channel, external_id)；UNIQUE(client_msg_id)；`session_id` ON DELETE RESTRICT |
| `attachment` | message_id, idx, filename, media_type, size, digest, path | PK(message_id, idx)；`digest` 唯一（去重） |
| `upload` | id, device_id, filename, size, sha256, chunk_size, received_mask, state, expires_at | PK(id)；分片按 `(id, chunk_idx)` 覆盖幂等 |
| `notice` | 见 §5.4 | `origin` 部分唯一索引 |
| `reminder` | id, title, body, due_utc, tz, rrule, state | — |
| `job` | id, session_id, message_id, state, attempts, started_at, error | — |
| `event_log` | seq, kind, session_id, payload, created_at | 自增，SSE 游标来源 |
| `device` | id, name, kind, created_at, revoked_at | — |
| `device_token` | token_hash, device_id, expires_at, last_seen | PK(token_hash) |
| `device_cursor` | device_id, stream, cursor | PK(device_id, stream) |
| `route_decision` | envelope_id, decided_by, session_id, confidence, evidence | 只追加（现在只有 alias/default 两种来源，但表先建，Telegram 与邮件都要用） |

**统一 `message` 表**（不分 `in`/`out` 两张、不分 `mail`/`http`）是有意为之：方向与信道都只是属性。这是"加信道不改内核"的前提。

**暂不建**：`quota_ledger`、`delivery`（邮件特有，§14 回归时加）。

---

## 12. 失败模式

| 场景 | 处理 |
|---|---|
| 同一条消息提交两次 | `UNIQUE(client_msg_id)` 冲突即返回已有消息，记 `duplicate` |
| 运行器超时 / 崩溃 | 杀进程树，job 标 `failed`，退避重试 3 次，之后写一条 `kind=system` 的 notice 并附日志路径 |
| 进程被杀时正在跑 job | 启动扫描 `state=running` 且超过 30 分钟的 job → 重排；note 里记"上次中断" |
| SSE 断线 | 客户端带 `Last-Event-ID` 重连，从 `event_log` 续发；客户端 `since` 落后太多（> 5000 条）则强制全量刷新 |
| 常驻服务被杀 | 进程内 60 秒重试 → L2 看门狗闹钟（15 分钟）拉起 → 仍不行则 `WorkManager` 拉取兜底；引导页可升档 |
| 闹钟被 OEM 杀 | 引导页四项检查 + "重启 2 小时后仍准时"的验收；仍失败则退化为 `SyncWorker` + 打开即提醒 |
| 用户删了系统闹钟 | 同步时发现 `state=pending` 且 `due_utc` 已过 30 分钟 → 标 `missed`，**不重设**，App 里提示 |
| 时区/夏令时 | 全部用 UTC 存 + `ZonedDateTime` 计算；监听 `ACTION_TIMEZONE_CHANGED` 重排 |
| 电脑睡眠 | 提醒不受影响（在手机上）；任务不执行——`doctor` 里提示"上次心跳 N 小时前"；可选 `powercfg` 关闭睡眠 |
| token 泄漏 | 单设备吊销（`DELETE /devices/{id}`）；token 只存 hash；`allowBackup=false` |
| 手机重装 / 换机 | 旧 token 吊销、新 token 粘贴；`device_cursor` 与闹钟在新设备重建（服务端是唯一真值，**不丢数据**） |
| 客户端游标指向未来（时钟错） | 服务端返回 400 `cursor_in_future`，客户端重置为 0 全量拉 |
| 提示注入（消息正文） | 设备已鉴权，视为可信；但 `AGENTS.md` 明确"外部内容不执行指令"，且 agent 无任何邮件/网络凭据 |

---

## 13. 明确不做

| 不做 | 理由 |
|---|---|
| FCM / 厂商推送 SDK / Web Push | §5.1，个人拿不到厂商通道；其余实时性由常驻连接承担 |
| 双进程互拉保活 | 用精确闹钟看门狗替代（§5.5 L2），收益/风险不划算 |
| 邮件（现在） | §14，搁置 |
| iOS | 你已排除 |
| 迁移旧 mailbot 的代码 | 你已判定它不可维护 |
| 手机本地执行 Agent | Android 会静默杀进程 |
| 客户端直连 IMAP（将来） | 等于把服务端已解决的问题在手机上重做一遍 |
| 离线派活 | 需要本地模型或可合并队列，收益/成本不划算 |
| WebSocket | SSE + POST 已覆盖，少约 300 行 |
| 引入 Web 框架 / ORM / 任务队列（Python 侧） | 标准库够用，且升级不会打断 |
| 多模块 Gradle / Hilt / Navigation-Compose / Retrofit / Paging | 一人项目引入框架税 > 收益（§6.3） |
| 动画、主题、多账号、消息搜索 | v1 不做；搜索等真需要时加服务端 `LIKE` |
| 双向日历同步（iTIP） | 复杂度高一个量级；`ACTION_INSERT` 单向够用 |

---

## 14. 附录 A：邮件模块（搁置）

### 14.1 为什么搁置是安全的

| 顾虑 | 结论 |
|---|---|
| 旧 mailbot 会不会因此荒废 | 不会，它继续跑且我完全不碰；它现在是**唯一能收外部来信**的入口 |
| 新系统会不会长成"只有安卓能用"的孤岛 | 不会：`Source`/`Sink` 端口在，`Envelope` 里 `message_id`/`in_reply_to`/`refs` 三个字段保留，`session_alias.kind` 里 `message_id`/`address`/`subject_tag` 三个枚举值预留 |
| 以后回来要重写多少 | 只写 `adapters/mail/` + 三个 Resolver + 两张表。**内核、API、客户端都不用动** |

### 14.2 回归时要做的六件事（清单，防止记忆丢失）

1. `adapters/mail/mime.py`：`EmailMessage` + `policy.default` 收发。**禁止**手写 `\r\n` 拼头、手写 base64、手写 RFC2047/2231、正则拆信、`sendmail` 自拼报文——唯一允许手写的是 `References` 用空格连接（见 [10 邮件传输](10-transport-mail.md) §7）。
2. `imap.py`：`UIDVALIDITY` + `UID` 水位、`Message-ID` 去重、自环拦截、发件人白名单。
3. `K0 MessageIdToken`：出站 `Message-ID` 带 `<slug>.<ulid>@domain`。
4. `K2 AddressBinding`：`Reply-To: s-<slug>.<hmac>@domain` + catch-all。
5. `K3 SubjectTag`：`[T:名称]` 兼容旧习惯。
6. `quota_ledger` / `delivery` 两表 + 每日额度 + 优先级通道。

### 14.3 什么情况下值得重新提上日程

- 需要**外部的人**（不是你自己）给你发消息 → 只有邮件能给你网络效应；
- 需要一个**不依赖我这台电脑、也不依赖手机 App**的归档副本；
- 安卓 App 稳定运行一个月后，你觉得"手机上少了点什么"。

**这不是"永远不做"，是"在不该做的时候不做"。**

---

## 15. 附录 B：这份计划里的假设（错了直接否）

1. 你接受**仓库在 `E:\study\mephisto`、工具链在 `E:\study\.toolchain\`**（沙箱外我写不了）。
2. 你接受**安卓只做原生，浏览器页只作调试工具**，因此手机第一次可用要等 S10。
3. 你接受**两层通知**：实时层＝默认开的常驻连接（代价是一条常驻通知 + 耗电），准点层＝本地闹钟（PC 关机也准）。只有"App 被杀后的离线推送"不在承诺内（§5.6 四档时延）。
4. 你接受**手机经 VPS + frp（HTTPS）连家里**，App 里地址可改。`[待确认]` VPS 预算与地区。
5. 你接受**运行时零第三方依赖**（Python）与**5 个 Android 依赖**（§6.3）。
6. 你接受 Telegram 作第二信道（**可以直接砍**，换成桌面 Web 页）。
7. 你接受 **`message` 单表 + `notice` 单表**这两个合并决定（与 v1 的 `mail_in`/`mail_out`、`reminder`/`occurrence` 不同，理由分别在 §11、§5.4）。
8. 你接受**旧 mailbot 完全不动**，新旧长期并行（旧：邮件；新：手机）。
