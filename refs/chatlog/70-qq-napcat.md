# 70 · NapCat 专项：登录、收消息、历史边界、风险

> **本章有一条必须先声明的前提：我没有真的跑起来。**
> 原因：本机无 QQ 客户端、无测试小号；任务书 §8.3 禁止使用委托方真实账号，我也不应凭空注册。
> 因此：**没有任何一条 `message.group` 真实抓包**。下面所有 schema 都来自官方文档与源码，逐条标 `[确认-文档]`；需要抓包才能确认的，标 `[未验证]` 并写明"该跑但没跑"。
> 证据目录：`evidence/qq/napcat-*.md`、`evidence/qq/onebot11-*.md`。

---

## 1. 它是什么（架构认知）

官方自述（`evidence/qq/napcat-architecture-install.md`）：

> "并非完全自己实现 QQ SendMsg 这种功能，而是主动调用客户端存在的功能"
> "可以完全自身引导 QQ 程序的启动……不需要 Hook"
> "不依赖 Electron 与注入框架"

要点：

- **NapCat 不是协议重写**，它是"驱动真实 NTQQ 客户端"的外壳 —— 把客户端当成一个可编程的对象来调用。这与 Lagrange.Core（Pure C# 重写协议）是**根本不同的两条技术路线**。
- 加载形态有两种并存：
  - **Shell 独立启动器**：`launcher.bat` / `NapCatWinBootMain.exe` + DLL 注入 / Docker / AppImage；
  - **Framework 插件**：作为 LiteLoaderQQNT 的插件加载。
- 依赖：Windows 手动版与 Framework 版**需先装 QQ**；Windows 一键版与 Docker **不需要**（自带运行时）。

`[推断]` "不需要 Hook" 这句是相对于"hook 微信内存"那类工具说的（它在客户端内部，不需要外部注入读内存）；但 Docker/一键版仍然要把自己的模块塞进 QQ 进程。这个词的边界要以官方为准，不要当成"零注入"。

**NTQQ 版本约束**（官方给的兼容区间）：Windows build 28418–36580、Linux 28498–36580、macOS 29456。超出区间大概率注入失败。

---

## 2. 登录

| 项 | 结论 | 出处 |
|---|---|---|
| 主要方式 | **扫码**；也支持"快速登录"传 QQ 号（`launcher.bat <QQ>`、AppImage `-q <QQ>`） | `napcat-login-multiaccount.md` |
| 密码登录 | 官方无文档 → `[未验证]` | 同上 |
| 掉线感知 | 有 `notice_type = 'bot_offline'` 事件 | `napcat-onebot-event.md` |
| 掉线重登 | 支持；但**离线期间的消息补不回来**（见 §4） | `napcat-issue-1838-offline-gap.md` |
| 固定 IP | **不要求**；官方建议避免与常用号同 IP/同设备，必要时换 IP 或挂 SOCKS5 | `napcat-login-multiaccount.md` |
| 设备指纹 | **无独立配置项**；只有 `o3Hook=0` 这类开关。`[推断]` 指纹由 NTQQ 数据目录决定（换目录=换设备） | 同上 |
| 多账号 | **支持**：配置按 QQ 分文件 `onebot11_<QQ>.json` / `napcat_<QQ>.json`；官方桌面工具声明支持多账号 | 同上 |

多账号的实际含义：每个号一个 NTQQ 数据目录 + 一套配置 + 一条 WS 连接。**资源占用 ≈ 号数 × 一个 QQ 客户端**。

---

## 3. 收消息：`message.group` 的字段结构

### 3.1 传输格式：array 还是 CQ 码

**两者都支持，由每个适配器的配置项 `messagePostFormat` 决定，源码默认 `array`。**
源码原文（`evidence/qq/napcat-message-group-format.md` 引）：

```js
config.messagePostFormat === 'string' ? stringMsg : arrayMsg
```

- `array`：`message` 是 segment 数组，`[{type:'text', data:{text:'...'}}, ...]`
- `string`：`message` 是 CQ 码字符串，`[CQ:image,file=xxx.jpg]`
- 无论哪种，`raw_message` 都是 CQ 码形式（兼容旧客户端）。

### 3.2 事件字段

`[确认-文档]` 的字段（`napcat-message-group-format.md` + `napcat-onebot-event.md`）：

| 字段 | 类型 | 说明 |
|---|---|---|
| `post_type` | string | `message` / `notice` / `request` / `meta_event` |
| `message_type` | string | `group` / `private` |
| `sub_type` | string | 群：`normal` / `anonymous` / `notice`；私聊：`friend` / `group` / `other` |
| `time` | number | **秒**级 Unix 时间戳 |
| `self_id` | number | 机器人自己的 QQ 号 |
| `user_id` | number | 发送者 QQ 号 |
| `group_id` | number | 群号 |
| `message_id` | number | 消息 ID（**int32**） |
| `message_seq` | number | 群消息序号（**官方 vitepress 的类定义漏了这个字段，但实际事件里有**） |
| `real_id` | number | 真实消息 ID（与 `message_id` 可能不同） |
| `message` | array \| string | segment 数组或 CQ 码 |
| `raw_message` | string | CQ 码原文 |
| `font` | number | 字体 |
| `sender` | object | 见下 |
| `anonymous` | object \| null | 匿名身份（`id`/`name`/`flag`） |

`sender` 对象：

| 字段 | 说明 |
|---|---|
| `user_id` | QQ 号 |
| `nickname` | 昵称 |
| `card` | **群名片**（群昵称），空字符串表示未设 |
| `role` | `owner` / `admin` / `member` |
| `title` | 群头衔 |
| `level` | 群等级 |
| `sex` / `age` / `area` | 资料字段（可能为空） |

### 3.3 segment 类型全表

`[确认-文档]`：`text` / `image` / `face` / `at` / `reply` / `record` / `video` / `file` / `json` / `mface` / `forward` / `dice` / `rps` / `poke` / `markdown`（后者视版本）。

### 3.4 事件 JSON 骨架（**不是抓包，是按文档字段拼装的**）

```jsonc
{
  "post_type": "message",
  "message_type": "group",
  "sub_type": "normal",
  "time": 1760000123,                  // 秒
  "self_id": 1000000000,
  "user_id": 2000000001,
  "group_id": 3000000002,
  "message_id": 123456789,
  "message_seq": 98765,
  "real_id": 123456789,
  "message": [
    { "type": "reply", "data": { "id": "123456700" } },
    { "type": "at",    "data": { "qq": "1000000000" } },
    { "type": "text",  "data": { "text": " 看一下这个" } },
    { "type": "image", "data": { "file": "abc.jpg", "url": "https://...", "summary": "[图片]" } }
  ],
  "raw_message": "[CQ:reply,id=123456700][CQ:at,qq=1000000000] 看一下这个[CQ:image,file=abc.jpg]",
  "font": 0,
  "sender": {
    "user_id": 2000000001, "nickname": "张三", "card": "产品-张三",
    "role": "member", "title": "", "level": "1", "sex": "unknown", "age": 0, "area": ""
  },
  "anonymous": null
}
```

> **`[未验证]`：以上字段的组合与真实事件是否完全一致（尤其 `message_seq`/`real_id` 是否总出现、`url` 是否有值）——需要一次真实抓包。** 抓包脚本已就绪：`snippets/onebot-listen.js`，连上 WS 后原样打印 10 条事件。

---

## 4. 拉历史：`get_group_msg_history` 的真实边界

### 4.1 参数（源码 `PayloadSchema`，比官方 api.md 完整）

| 参数 | 必填 | 默认 | 说明 |
|---|---|---|---|
| `group_id` | 是 | — | 群号 |
| `message_seq` | 否 | — | 翻页锚点；**传了走"从该序号往前后翻"，不传走"取最新一屏"** |
| `count` | 否 | **20** | 条数 |
| `reverse_order` / `reverseOrder` | 否 | — | 反向排序（旧别名都接受） |
| `disable_get_url` | 否 | — | 不解析媒体 URL（提速） |
| `parse_mult_msg` | 否 | `true` | 是否展开合并转发 |
| `quick_reply` | 否 | — | 快捷回复相关 |

- 返回：`{ data: { messages: [...] } }`。
- **空结果是抛错而不是返回空数组**（错误文案形如"消息X不存在"）—— 客户端必须 try/catch。
- `[未验证]`：`count` 的上限（action 层没有 clamp）、最远能回溯多久。

### 4.2 离线缺口（本项目最关键的一条负面证据）

GitHub `NapNeko/NapCatQQ#1838`，标题《get_group_msg_history 不会返回离线时的消息》：

- 创建 2026-05-12，关闭 2026-05-22，`state_reason = not_planned`，4 条评论。
- 报告者（QQNT 9.9.26 / NapCat 4.17.6）：*"bot 离线，群里聊了很多句后，调用 get_group_msg_history 只能获取最近的几条和离线之前的消息"*。
- **维护者 MliKiowa 的关闭理由（原文）**：

> "是的是 feature，不打算实现非离线消息推送，这会出现大量消息涌入问题"

- 报告者追问能否"手动按数量/时间段拉取离线期间的历史"，维护者回："可以，晚点我会检查的"（2026-05-23）—— **当时未实现**，之后是否补上 `[未验证]`。
- 源码旁证：`onRecvMsg` 里有 `if (bootTime > msgTime) { 忽略上报; continue }` —— **早于进程启动时间的消息直接被丢弃**，机制与现象吻合。

**结论：靠"掉线后补拉"做长期归档，在 NapCat 上不成立。** 只能"常驻在线 + 自己持久化"。这是设计决策，不是待修 bug。

### 4.3 群/成员信息

- `get_group_list` / `get_group_member_list`：返回结构与字段见 `napcat-activity-and-group-api.md`。
- **频率限制：官方文档没有规定**。所以只能写"未规定"，不能写"有限制"。`[未验证]`

---

## 5. 只读：能不能在配置层关掉发送

**不能。**（`evidence/qq/napcat-config-schema.md`）

- `OneBotConfigSchema` 顶层只有：`network` / `musicSignUrl` / `enableLocalFile2Url` / `parseMultMsg` / `imageDownloadProxy` / `timeout`。
- 适配器级（http / ws / ws-reverse）**没有** `enableSend` / `disableSend` / `readOnly` 一类开关。
- `reportSelfMessage` 只控制"自己发的消息要不要上报"，与"能不能发"无关。

**唯一可行的只读做法**：在 NapCat 外面套一层网关，把 `send_*` 类 action 全部拦掉（白名单放行 `get_*`）。

> 这一点直接决定风险模型：**只读不是配置属性，是架构约束**。一旦 WS 暴露出去，任何人都能用你的号发消息。

---

## 6. 2025-09 安全事件（按原文，不加料）

- 出处：`https://napneko.github.io/develop/security`，原文日期 **2025 年 9 月 5 日**。
- 经过：OneBot 协议**不带 Token** + 一键包被直接暴露到公网 → 被扫描 → 被调用发送接口。
- 官方自己列的问题：`NapCat 未提供随机 OneBot Token 默认填充`、`多数服务以 0.0.0.0 IPV4 全监听`。
- 改进意见原文是"**可禁止局域网外访问**"。
- **原文没有"必须监听 127.0.0.1"这句，也没有"连带封号"的表述。**

证据：`evidence/qq/napcat-security-incident-2025-09-05.md`。

工程结论（这是推断，不是引文）：**任何 OneBot 服务都只应监听 `127.0.0.1` 并强制 token**。理由是原文列出的两个缺陷恰好都能被这两条堵死。任务书 §8.4 的要求与此一致。

> 「只读并不能免疫这类风险」——这一点任务书说对了：攻击者用的是**你的发送通道**，你的采集端读不读消息与他无关。

---

## 7. 活跃度与稳定性观察

| 指标 | 值 | 日期 |
|---|---|---|
| star / fork | 11459 / 837 | 2026-10-25 |
| open issue | 7 | 同上 |
| 最近 push | 2026-10-22 | 同上 |

2026-10 抽样 12 条 issue 的高频类型：

1. **NTQQ 版本升级导致注入崩溃**（栈里出现 `QQNT.dll+0x26b6e3a`）、"扫码后卡在等待确认" —— **占比最高**；
2. 发送类 API 报错（`1200` / `EventChecker Failed` / `Timeout`）；
3. 单接口缺陷（如 `do_group_album_comment`）；
4. dependabot 依赖升级。

**这三类就是"能不能稳定读一年"的答案**：拦路虎是 NTQQ 升级，不是 QQ 风控。

---

## 8. 复现清单（我该跑没跑的部分）

| 步骤 | 命令/操作 | 状态 |
|---|---|---|
| 装 NapCat（Windows 一键版） | 官方 release 解压 → `launcher.bat` | 未做（无测试号） |
| 扫码登录 | 手机 QQ 扫 | 未做 |
| 抓 10 条 `message.group` | `node snippets/onebot-listen.js ws://127.0.0.1:3001 <token>` | **脚本已就绪，未跑** |
| 测历史边界 | 改 `count` 从 20 递增到报错，记录上限 | 未做 |
| 测离线缺口 | 断线 1h/6h/24h 后 `get_group_msg_history` 对比条数 | 未做（这是验收标准里最难的一项） |
| 测只读 | 找配置项 → 确认不存在 | `[确认-文档]` |
| 测多账号 | 两个号同时挂 | 未做 |

## 9. 本章未解问题

1. `message.group` 真实事件的字段完整性（尤其 `message_seq`/`real_id`）。
2. `get_group_msg_history` 的 `count` 上限与最远回溯深度。
3. **离线缺口的量化**：离线 1h/6h/24h 各丢多少条（需要实测，任务书 D.3 要求）。
4. 2026-05-23 之后是否已实现"按区间手动拉离线历史"。
5. 图片 URL 的有效期。
6. `get_group_list` / `get_group_member_list` 的实际频率限制（官方未规定）。
7. 发送接口被扫时的具体攻击面（`send_msg` 之外还有哪些危险 action）。
