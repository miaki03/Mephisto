# 80 · 路线：从现在的 mailbot 走到下一阶段

> 原则第一条：**不重写。** 第一阶段有 333 个用例和两周的真实运行日志，那是资产。
> 本文只列"要改的三件事"和"按什么顺序改"。

---

## 1. 现状盘点

| 已经能跑 | 证据 |
|---|---|
| 收信（IMAP 轮询）、按线程归档、每封信建任务目录 | `logs/mail-in-*.jsonl` 从 2026-10-09 起连续 |
| 拉起 DSH 干活、复用会话、续接失败降级 | `logs/session-*.jsonl`、`session.degrade` |
| `manifest.json` 交付（正文/附件/留本地/提醒/中途发信） | `attachments.py`、`actuator.py` |
| 斜杠指令（`/帮助` `/线` `/清空` `/提醒` `/投递` `/停止`） | `commands.py` |
| 提醒调度（10s 扫描、错过补发） | `reminders.json`、`logs/remind-*.jsonl` |
| 安全边界（白名单、Bearer、越权路径拦截、写边界） | `state/allowlist.txt`、`tests/test_router_actuator.py` |
| 一条命令安装/迁移 | `tools/bootstrap.py` |

**缺的**：共享工作区与长期记忆、聊天记录、路由的自动兜底、提醒的幂等与补触发、多通道。

## 2. 三件要改的事

### 2.1 工作区粒度：任务 → 会话

现在 agent 只能写 `jobs/<taskId>/`，导致 `context.md` 在边界外、记忆无处安放（`AGENTS.md` 第 112 行自认"写不了就算了"）。

**改成**：写边界 = 整个 `workspace/`，但共享区只允许追加 + 每夜 git 快照（`30-workspace-memory.md` §2.4）。

这是**唯一一处需要动沙箱配置**的改动，也是收益最大的一处——它是"记忆"和"跨会话复用"的地基。

### 2.2 路由主键：标签 → ID/地址

现在靠 `[T:名称]` 标签 + `aliases.json`。问题是你自己说的：让用户敲标签用不起来。

**改成**：K0（Message-ID 自带会话号）→ K1（别名表）→ K2（`Reply-To` 会话地址）→ K3（标签，保留兼容）→ K4（分类器，兜底落杂谈）。

**这条改动的兼容性很好**：`threadId` 仍是主键，`aliases.json` 保留，标签不删。只是**加了两条更省事的路**，用户不用学新东西。

### 2.3 微信源：WeFlow 运行时 → 自持 `wxread`

`README.md` §8 写的第二阶段是"WeFlow 事件源"。**这个应用的运行时已经停摆**（不是技术不行，是它的授权里写死截止 `2026-10-16 00:00`，过期即 `-101`，且远端可吊销 —— `refs/chatlog/50-weflow-expiry.md`）。另外两条不能接受的点：内置 Agent 会把聊天正文发到第三方端点并带遥测、反撤回会改写微信数据库（`refs/chatlog/90-risks.md` A3/A4）。

**但技术来源不变**：`wxread` 就是从 WeFlow 提取出来的数据层（取密钥、SQLCipher 页解密、分片枚举、内容解码），已在真机读到群消息。所以这不是"换路线"，是**"沿用技术、换掉运行时"**。

契约不变：仍是"加一个 `kind: wechat` 入站通道"，只是事件来源从"调 WeFlow 的 API"变成"读自己的 `chatlog.sqlite3`"。

## 3. 分期

> **排期已由 [99 实施计划](99-implementation-plan.md) v2 取代**（邮件搁置、安卓优先，编号改为 S0–S13）。
> 下面 P-1…P5 的内容仍然有效——每条"做什么/验收"都被 v2 吸收进了对应步骤——但**顺序与编号不要再用**。

### P-1 · Runner 迁移到 Python SDK（约 1 周，可与 P0 并行）

为什么排在最前：它决定"部署是什么形状"，越晚改成本越高（状态越多、profile 资产越多）。

| 做什么 | 验收 |
|---|---|
| 干净 venv 里 `uv add deepseek-harness-sdk`，**不装 Node** | `dsh --version` 有输出 |
| profile 从 `headless` 模板换成 `sdk` 模板，搬入原有 patch | `--dump-config` 能看到 `sandbox-policy`/`fs-sandbox`/`agent-default-model` |
| `DSH_HOME` 指向部署自有目录 | 会话文件落在部署目录，`~/.dsh` 无新增 |
| Runner 换成 `DeepSeekHarness`（常驻 + `resume_session`） | 一封信跑通；第二封续接并记得上一轮 |
| **决定不要薄插件** | 没有 pnpm、没有 `link:E:/...` 绝对路径软链；交付只靠 `manifest.json` |

**这一步的收益不只是"少装 Node"**：profile 从"机器状态"变成"仓库里的文件"，版本被 `uv.lock` 钉死，换机器从"照清单装"变成"`uv sync`"。

详见 [`65-distribution.md`](65-distribution.md)。

### P0 · 路由（1–2 周）

| 做什么 | 验收 |
|---|---|
| 出站 Message-ID 改为 `<s<slug>.<ulid>@domain>`；入站加 K0 解析 | 用网页版改主题回信，仍落原会话 |
| 出站加 `Reply-To: s-<slug>.<hmac>@domain`，catch-all 能收 | 从通讯录新建一封信发到该地址，落对会话 |
| `route_decision` 落库（记录被否决的候选与置信度） | 一周后能算出命中分布 |
| 分类器接上，默认落杂谈 | 分类器超时/报错时不丢信 |

**为什么排第一**：改动小、风险低、直接解决"敲标签太麻烦"，而且它是后面所有"会话有记忆"的前提（没有稳定会话就没有稳定记忆）。

### P1 · 工作区与记忆（2–3 周）

| 做什么 | 验收 |
|---|---|
| 写边界上移到 `workspace/`；共享区只追加 | agent 能写 `memory/inbox/`，越权删除被拒 |
| `session.md` 骨架 + 每次运行结束追加 | 新会话开起来能读到上一条会话的待办 |
| `memory/` 目录 + `MEMORY.md` 索引 + 每晚整理任务 | 索引超 200 行时报错而不是截断 |
| git 每小时快照 | `git log` 能看到一条会话的演化 |

### P2 · 微信聊天记录（3–4 周，可与 P1 并行）

| 做什么 | 验收 |
|---|---|
| `wxread` → `chatlog.sqlite3` 增量采集（`uid` 幂等、6h 回看） | 连续跑 24h 不重复、不漏 |
| 清洗（连发合并、噪声折叠、接龙折叠） | 一个接龙只产出一条合成记录 |
| 防抖 + 摘要 + 夜间汇总 | 单群一天 ≤4 封，且不占 `reply` 额度 |
| `chatlog` 技能（`sync`/`digest`/`search`）+ `SKILL.md` | 手动发信"总结一下 X 群"能出结果 |

### P3 · 提醒升级（1 周）

| 做什么 | 验收 |
|---|---|
| `reminders.json` → `reminder` + `occurrence` 两表 | 同一提醒重复调度只发一次 |
| 补触发与宽限（默认 2h）+ "错过的提醒"汇总 | 关机 3 小时后开机，行为符合预期 |
| sink 抽象（mail / 将来的 alarm） | 取消能连带删外部闹钟 |
| **提醒邮件带 `invite.ics` 附件** | 手机点一下就能进系统日历并带闹钟（`90-android.md` §4.1） |

### P4 · QQ（视 P2 效果再定）

只做官方 Bot 通道，且**只读归档**。上线第一周必须实测"掉线 1h/6h 后是否补推"——这条决定它能否做长期归档（`refs/chatlog/60-qq-landscape.md` §6）。

### P5 · 为安卓做"不返工"的预留（可以现在做，成本极低）

不是做 App，是**把 App 会用到的东西先放进数据模型**（详见 `90-android.md` §9）：

| 做什么 | 验收 |
|---|---|
| `delivery.delivered_at`/`seen_at`/`e2ee`/`client_msg_id` 进 schema（**已在本仓库 `contracts/` 里加了**） | 三项都可空，且 `null` 语义是"未知" |
| `device` / `device_token` / `device_cursor` / `sink_ref` 四张表 | 邮件本身也作为 `kind='mail'` 的设备存在 |
| HTTP API 按 `/api/v1/...` 版本化（现有 `apiserver.py` 已起步） | 令牌可吊销、每设备独立游标 |

### A1–A5 · 安卓客户端（远期，见 `90-android.md` §5）

A1 只读 → A2 双向 → A3 闹钟/日历 → A4 审批 → A5 推送。**A3 可以早做**，因为它只依赖 `sink_ref` 协议，且 `.ics` 已经覆盖了大部分需求。

> 注意 A4（审批）不是体验优化：现在 `approval: never` 等于把"需要人确认"的能力整类砍掉了。App 的弹窗是把这类能力还回来的唯一途径。

## 4. 不做

| 不做 | 理由 |
|---|---|
| 重写采集层 | 1000+ 行已实测代码 |
| 引入 ORM / Web 框架 / 任务队列 | 19 个纯标准库模块已经证明够用 |
| 用 `dsh-schedule` 做提醒 | 它要求 Web Host，会锁死分发形态（`70-runner-and-cache.md` §2） |
| 一会儿话一邮箱 | 限流按域、DMARC 要对齐、新域要预热（`10-transport-mail.md` §3） |
| 多设备共享 harness 会话 | 会话是本地文件且绑 cwd，做不到，也不需要 |
| 第一阶段就做拟人化人格 | 稳定角色 > 人设（`30-workspace-memory.md` §1） |

## 5. 风险与对策

| 风险 | 概率 | 对策 |
|---|---|---|
| 改写边界后，被注入的内容删掉共享记忆 | 中 | `memory/` 只追加 + 每小时 git 快照（`30` §2） |
| 微信升级导致解密参数失效 | 中 | 参数集中在 `wxsource.decrypt_page()`，改一处即可；仍有 WeFlow 配置文件里的 passphrase 作为来源 |
| 分类器误并导致上下文污染 | 中 | 默认落杂谈；`session move/merge` 可修；`route_decision` 可复盘 |
| 邮件被判垃圾（最隐蔽） | 中 | 固定 From + SPF/DKIM/DMARC + 每周"心跳"检查（`50-reminders.md` §8） |
| DSH 升级改前缀 | 高 | 钉版本 + 降级当常态 + 业务状态不落 harness（`70` §6） |
| 提醒静默失效 | 中 | `occurrence` 唯一索引 + 心跳 + "错过的提醒"汇总 |

## 6. 一条时间尺度上的判断

这套东西会跑很多年。**能跑三年的系统和能跑三天的系统，差别不在功能，在"出错时能不能发现、坏了能不能修、换环境能不能搬"。**

所以本文档集里一半的篇幅在讲失败模式、降级路径、可复盘性——这不是过度设计，是这类系统的真正难点。
