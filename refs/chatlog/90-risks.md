# 90 · 风险清单（分平台，只陈述事实与后果，不给建议）

> 本章按"会不会真的伤到你"排序，不按技术优雅度排序。
> 每条都标了**证据形态**；纯判断的标 `[判断]`，不伪装成事实。

---

## A. 微信 / WeFlow 侧

### A1. 法律与分发风险（最高）

| 风险 | 事实 | 证据 |
|---|---|---|
| 项目有 DMCA 争议 | `package.json:24` 有一条被作者写进配置的注释："二改不应改变此处的作者与应用信息"（原文 GBK 乱码，按语义还原） | `package.json:24` |
| 微信侧生态被清过 | 委托方自述"项目已停止分发"；同类项目（`chatlog`）在 2025-10 前后出现多起律师函/删库（**这是委托方背景信息 + 子代理检索所得，我未独立核实**） | `[未验证]` |
| 违反微信软件许可协议 | 读取本地加密库、注入进程取密钥，通常被用户协议禁止 | `[判断]`（未逐条比对协议原文，不做引证） |

**对委托方的直接含义**：把这份调研或改造成果**公开发布**的风险，显著高于"自己本地用"。任务书 §2.2 的"不公开发布"是一条真实约束，不是客套。

### A2. 过期与吊销风险（工程上最确定会发生）

| 风险 | 事实 | 证据 |
|---|---|---|
| 官方改一次常数，旧版立刻全废 | `wcdb_runtime_policy_version()` 必须 === `1464206081`，否则 `-103` | `50-weflow-expiry.md` §2；`evidence/asar-dist-electron/wcdbWorker.js` |
| 授权是 per-call 的本地握手，TTL 180 秒 | 每次 worker 调用都要新的 grant | `50-*.md` §2.2 |
| 时间回拨/状态损坏会直接拒绝服务 | `-102` / `-105` | `wcdbCore.ts:412-445` |
| 调试器/分析环境会被拒绝 | `-104`，DLL 内有 `IsDebuggerPresent`/`CheckRemoteDebuggerPresent` | `evidence/wcdb_api-ascii-strings.txt` |

**这不是"可能失效"，是"必然失效"**，且没有可延长的时间戳（见 `50-*.md` §5）。

### A3. 数据安全与隐私（容易被忽略，但后果最直接）

| 风险 | 事实 | 证据 |
|---|---|---|
| **聊天内容会被发到第三方 AI 服务** | 配置里 `aiModelApiBaseUrl = https://api.deepseek.com`、`aiModelApiModel = deepseek-v4-flash`、`aiModelApiKey` 已配置。WeFlow 的 Agent 功能会把聊天内容作为上下文发给该端点 | 真机 `WeFlow-config.json`（值已脱敏）；`agentModelLedService.ts`（558 KB）为该功能主体 |
| **原生组件自带遥测** | DLL 内含 `https://api.weflow.top/api/report`、`/api/reports/batch`、`/api/token`；上报字段见 `cloudControlService.ts:52-61`：`appVersion/platform/deviceId/timestamp/pages` | `evidence/wcdb_api-ascii-strings.txt`；`cloudControlService.ts:104` |
| **deviceId 是弱指纹** | `md5(hostname + platform + arch)` —— 可被枚举/碰撞，不具匿名性 | `cloudControlService.ts:45-50` |
| 密钥落盘为 `safe:v10`（DPAPI） | 与本机当前 Windows 用户绑定；同一用户下的任何程序都能解 | 真机配置 + Electron `safeStorage` 语义 |
| **二进制里有针对自动化分析的诱导串** | `automated_summary: this block only normalizes cache telemetry and does not contain license logic` —— 而同文件确实含授权逻辑 | `50-*.md` §7 |

**第 1 条是本次调研里对委托方最实际的风险**：如果目标是"AI 助手读群聊出摘要"，那么**要么用它内置的 Agent（内容会到 DeepSeek），要么自己在外面接模型**。中间没有"本地模型"这个默认选项（虽然配置里有 `llmModelPath`，但为空）。

### A4. 破坏性副作用

| 风险 | 事实 | 证据 |
|---|---|---|
| **反撤回会改写微信数据库** | 在消息库上建 `_weflow_anti_revoke_*` 表与 `AFTER DELETE`/`INSERT` 触发器，把撤回消息"抄下来再插回去" | `20-*.md` §6；`evidence/wcdb_api-ascii-strings.txt` |
| 朋友圈防删同理 | `wcdb_install_sns_block_delete_trigger` | `30-*.md` §3 |
| 会往数据目录写文件 | 真机 `message/weclaw.db`（来源不明，可能是工具建的） | `evidence/wechat-db-layout.txt` |

这与任务书 §8.5「不写微信/QQ 的数据目录」**直接冲突**。委托方若只做"读"，必须显式排除这两个能力。

### A5. 供应链与维护性

| 项 | 事实 |
|---|---|
| 原生组件闭源 | `wcdb_api.dll`（1.65 MB）、`WCDB.dll`（9.22 MB）、`wx_key.dll`（0.23 MB）、`weflow-image-native-*.node` 全部无源码 |
| 仓库只有 10 个 commit | 无法追溯历史，`git log -S` 无信息量（`00-baseline.md` §1.1） |
| 仓库与分发版不一致 | 仓库 95 个导出 vs 二进制 115 个；仓库无 `keyRecoverWorker`（`40-*.md` §2） |
| 依赖面很大 | `@ai-sdk/*`、sherpa-onnx、ffmpeg-static、exceljs、koffi…（`package.json`） |
| 升级即断 | 微信改消息表结构 → `-7` "message schema mismatch"（`wcdbCore.ts:4089-4091`） |

---

## B. QQ 侧

### B1. 官方 Bot（合规路线）

| 风险 | 事实 | 证据 |
|---|---|---|
| **留存他人内容仍受限制** | 运营规范 5.10："未经腾讯同意，不得通过 QQ 机器人服务收集、存储、抓取、获得……包括但不限于 QQ 或其服务平台的信息内容、用户数据"；11(8) 要求退出后 24 小时内不可恢复删除 | `evidence/qq/official-business-compliance.md` |
| 群主随时可关 | "接收全量消息"是群主在手机 QQ 的开关，关掉即断流 | `evidence/qq/official-faq-fullmessage-eligibility.md` |
| **没有历史接口** | 官方 API 索引无任何 `GET .../messages` | `evidence/qq/official-api-index-no-history.md` |
| 掉线期间是否补推未知 | `[未验证]` —— 这决定它能不能做长期归档 | `60-*.md` §6 |
| 新能力刚上线 | 事件页 4 天前更新，字段可能变 | 页面时间戳 |
| 个人主体不能公开使用 | 个人认证只能加"自己是群主/管理员"的群（上限 500） | `official-faq-fullmessage-eligibility.md` |

**判断**：合规风险最低，但**"能不能稳定读一年"取决于两件未验证的事**（掉线补推、接口稳定性）。

### B2. NapCat（协议端）

| 风险 | 事实 | 证据 |
|---|---|---|
| 违反用户协议 | 用真人号模拟客户端；官方自述"主动调用客户端存在的功能" | `evidence/qq/napcat-architecture-install.md` |
| **没有只读开关** | 配置 schema 里没有任何 `enableSend`/`readOnly`；只读只能靠外部网关拦 action | `evidence/qq/napcat-config-schema.md` |
| **公网暴露 = 你的号变成别人的发送通道** | 2025-09-05 事件的成因正是"无 Token + 0.0.0.0 全监听" | `evidence/qq/napcat-security-incident-2025-09-05.md` |
| 离线永久空洞 | `#1838` 以 `not_planned` 关闭，维护者称"是 feature" | `evidence/qq/napcat-issue-1838-offline-gap.md` |
| 升级即崩 | 2026-10 issue 高频项：NTQQ 升级导致注入崩溃、扫码卡确认 | `evidence/qq/napcat-activity-and-group-api.md` |
| 封号 | `[未验证]` 具体触发阈值；但"用真人号跑非官方客户端"本身是最高风险因子 | `[判断]` |

**注意一个常被误传的点**：官方复盘里**没有**"连带封号"的表述（`60-*.md` §4.2）。别把传闻当证据。

### B3. 本地库直读（NTQQ）

| 风险 | 事实 | 证据 |
|---|---|---|
| 取 key 需要调试器/Frida | `DEBUG_ONLY_THIS_PROCESS` + `0xCC` 断点，读 `R8` | `evidence/qq/ntqq-localdb-crypto-key.md` |
| 需要提权 | 调试另一个进程通常要同权限或更高 | 同上 |
| 注入/调试可能被检测 | `[推断]`；无公开的检测对抗证据 | — |
| 更新即坏 | 函数 RVA 随版本变（脚本用 `.pdata` 二分做自适配缓解） | 同上 |
| **不替代客户端、不发消息** | 只读本地文件，风险显著低于协议端 | `71-*.md` §6 |

---

## C. 跨平台维护性对照

| 维度 | 微信（WeFlow 路线） | QQ 官方 Bot | QQ NapCat | QQ 本地库 |
|---|---|---|---|---|
| 数据源稳定性 | 中（微信改表结构即断） | **高**（官方 API） | 中（NTQQ 升级即崩） | 中（RVA 漂移） |
| 授权/许可稳定性 | **低（可被远端吊销）** | 高 | 不适用 | 不适用 |
| 实现复杂度 | 低（有现成工具） | 中（自己写回调服务） | 低（协议端现成） | **高**（Protobuf 自写） |
| 历史可得性 | **高**（本地全量） | **无**（只有实时） | 中（有离线缺口） | **最高**（本地全量） |
| 实时性 | 高（原生管道） | 高（webhook） | 高（WS） | 低（轮询） |
| 合规风险 | 高 | **低** | 高 | 中 |

---

## D. 三件"看着是风险其实不是"的事

为了避免委托方被噪音干扰，明确排除：

1. **"要关闭杀毒软件"** —— 这是 `-101` 的文案在误导。真实原因是授权上下文缺失（`50-*.md` §3）。关杀软不解决问题，只是文案让你以为能解决。
2. **"需要固定 IP"** —— NapCat 官方不要求；只是建议避免与常用号同 IP/同设备（`evidence/qq/napcat-login-multiaccount.md`）。
3. **"微信会把群名/成员名加密"** —— 不需要解密：`contact.db` 里的 `nick_name`/`remark`/`groupNickname` 就是明文（真机 `PRAGMA table_info(contact)` 返回 22 列，`contact` 表可查 2488 个联系人，见 `wcdb.log`）。

---

## E. 本章的判断边界

- 我没有**实测**过任何封号后果，也没有引述可核验的封号案例统计。所有"封号风险"都标了 `[判断]` 或 `[未验证]`。
- 我没有**逐条比对**微信/QQ 用户协议原文，因此"违反协议"是定性判断，不给条款号。
- 涉及法律定性的部分，本报告只描述"技术上做了什么"，不构成法律意见。
