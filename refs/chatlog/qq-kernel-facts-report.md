# QQ 非官方协议端事实核查（NapCat / Lagrange.Core / OneBot 11）

核查日期 2026-10-25。标注：`[确认]` 有原文/源码证据；`[推断]` 由证据推导；`[未验证]` 未取得证据。

## 0. 三句话结论

1. `[确认]` NapCat 不是纯协议实现，而是**驱动官方 NTQQ 客户端**、调用其 Node 模块的"协议端"；Lagrange.Core 是 **Pure C# 重写 NTQQ 协议**，必须外挂 SignServer。二者风险面完全不同。
2. `[确认]` `get_group_msg_history` **不在 OneBot 11 标准内**（go-cqhttp 扩展）；且 NapCat 明确**不补离线消息**（#1838 以 not_planned 关闭）。
3. `[确认]` NapCat **没有任何只读/禁发配置项**，只能靠部署侧自律（网关白名单 / 不暴露写接口）。

---

## 1. NapCat 架构与安装/登录

### 1.1 架构 `[确认]`

官方自述（https://napneko.github.io/guide/napcat）逐字：

> 基于 TypeScript 构建的 Bot 框架，通过相应的启动器或者框架，主动调用 QQ Node 模块提供给客户端的接口，实现 Bot 的功能。

> 1. **协议端相比** 并非完全自己实现 QQ SendMsg 这种功能，而是主动调用客户端存在的功能。
> 2. **Hook 端相比** NapCat 可以完全自身引导 QQ 程序的启动，而非借助客户端启动流程，所以 API 所有权几乎完全拥有，不需要 Hook。
> 3. **Electron IPC Hook 端对比** NapCat 本身并不依赖 Electron 与注入框架，调用来源 IPC 更下层封装的 Node 模块，也许甚至脱离 QQ 主程序运行。

- `[确认]` 与 Lagrange 的本质差异：Lagrange 自建协议栈（需签名、需模拟设备），NapCat 复用**真实客户端**（不需签名，但必须跑 NTQQ 本体，且版本强耦合）。
- `[确认]` 两种加载形态并存：**Shell 独立启动器**（自带引导 QQ，含 `launcher.bat` / `NapCatWinBootMain.exe`+DLL 注入 / Docker / AppImage）与 **Framework = LiteLoaderQQNT 插件**。
- `[确认]` README 致谢："Lagrange 对本项目的大力支持 参考部分代码 已获授权" —— 两者非纯竞品关系。

### 1.2 安装形态 `[确认]`（入口 https://napneko.github.io/guide/install ，细分 /guide/boot/Shell、/guide/boot/Framework）

| 形态 | 是否需要先装 QQ | 备注（原文） |
|---|---|---|
| Windows 手动 | **需要** | "安装 QQ，下载并解压 NapCat.Shell.zip"，双击 `launcher.bat` |
| Windows 一键 | **不需要** | "无需预先安装 QQ"，跑 `NapCatInstaller.exe` |
| Linux 一键 | 脚本自装 | `napcat.sh --docker n --cli y`，装到 `~/Napcat` |
| Docker | 不需要（镜像自带） | `mlikiowa/napcat-docker:latest`，`MODE: ws` + `WEBUI_TOKEN`，端口示例只绑 `127.0.0.1` |
| AppImage / macOS / Termux | 自带或需装 QQ | AppImage "包内包含 QQ 和 NapCat" |
| Framework | **需要** QQ + LiteLoaderQQNT | "适用于已有 LiteLoaderQQNT 环境的桌面 QQ" |

NTQQ 版本约束 `[确认]`（config/advanced.md「当前支持版本」表，PacketBackend）：Windows amd64 **28418–36580**、Linux amd64/arm64 **28498–36580**、macOS **29456**。

### 1.3 登录 `[确认]`

- 扫码为主：WebUI → QQ 登录 → `QRCode`；WebUI token 随机生成并"发送至登录账号本身的私聊"（other/security.md）。
- 快速登录：`launcher.bat 123456789`、AppImage `-q 123456789`。
- 密码登录 `[未验证]`：NapCat 文档只写 QRCode，未见密码登录流程（/guide/login 与 src/guide/login.md 均 404）。
- 掉线：有专门事件 `notice_type = 'bot_offline'`（含 `tag`、`message`），可外部监控。
- IP `[确认]`：**不要求固定 IP**，但官方建议"避免在同一 IP/设备上同时登录 Bot 账号与常用账号"，"频繁掉线可通过更换账号或设备改善"，"网络环境复杂导致登录困难时…更换 IP 或重新拨号，或配置 SOCKS5 代理"。
- 设备指纹 `[推断]`：无独立指纹配置项；官方唯一相关手段是 `o3Hook = 0`（关闭包拦截）与换设备。指纹由 NTQQ 客户端自身维护，同一数据目录即同一设备身份。
- 多账号 `[确认]`：配置按账号分文件（`onebot11_<QQ>.json`、`napcat_<QQ>.json`）；官方桌面管理工具特性写明"多账户管理: 支持同时登录和管理多个 QQ 账号"。Docker 单容器单账号 `[推断]`。

---

## 2. `message.group` 字段表

来源：NapCat 事件文档（src/onebot/event.md）+ Mintlify 自动生成事件页（生成自 commit `4ed4dfd`）+ 源码 `packages/napcat-onebot/index.ts`、`config/config.ts`。

| 字段 | 类型 | 依据 |
|---|---|---|
| `time` | int64 | `[确认]` 标准+实现 |
| `self_id` | int64 | `[确认]` |
| `post_type` | `"message"` | `[确认]` |
| `message_type` | `"group"` | `[确认]` |
| `sub_type` | `normal`/`anonymous`/`notice` | `[确认]` |
| `message_id` | int32 | `[确认]` |
| `message_seq` | number | `[确认]`（事件页 + `get_group_msg_history` 入参） |
| `real_id` | number | `[确认]`（事件页示例；标准无） |
| `group_id` | int64 | `[确认]` |
| `group_name` | string，可选 | `[未验证]` 是否每版都下发 |
| `user_id` | int64 | `[确认]` |
| `anonymous` | object\|null | `[确认]`（event.md 类定义；Mintlify 页遗漏） |
| `message` | array 或 CQ 字符串 | `[确认]`，由 `messagePostFormat` 决定 |
| `raw_message` | string（CQ 码） | `[确认]` |
| `font` | int32（"usually 14"） | `[确认]` |
| `sender` | object | `[确认]` |
| `sender.user_id/nickname/card/role(owner\|admin\|member)` | | `[确认]` |
| `sender.sex/age/area/level/title` | | `[确认]`（event.md 给 title/level；Mintlify 给 sex/age/area） |

**segment 结构** `[确认]`：`{ "type": ..., "data": { ... } }`；类型含 `text/at/reply/face/mface/dice/rps/poke/image/record/video/file/json/music/forward`。
**格式开关** `[确认]`：适配器级 `messagePostFormat`，源码 `config.messagePostFormat === 'string' ? stringMsg : arrayMsg`，schema 默认 `'array'`；文档站写 `"array"// 消息上报格式 string/array`。

### 脱敏 JSON 骨架（`message.group`，array 模式）

```json
{
  "time": 1730000000,                    // 文档确认字段
  "self_id": 100000001,
  "post_type": "message",
  "message_type": "group",
  "sub_type": "normal",
  "message_id": 345678,                  // 实现生成的短 ID
  "message_seq": 90123,
  "real_id": 345678,
  "group_id": 200000001,
  "group_name": "示例群",
  "user_id": 300000001,
  "anonymous": null,
  "message": [
    { "type": "reply", "data": { "id": "111111" } },
    { "type": "at",    "data": { "qq": "100000001" } },
    { "type": "text",  "data": { "text": " 你好" } },
    { "type": "image", "data": { "file": "ABCD.jpg", "url": "https://multimedia.nt.qq.com.cn/...", "file_size": "123456" } }
  ],
  "raw_message": "[CQ:reply,id=111111][CQ:at,qq=100000001] 你好[CQ:image,file=ABCD.jpg]",
  "font": 14,
  "sender": { "user_id": 300000001, "nickname": "张三", "card": "群名片",
              "role": "member", "sex": "unknown", "age": 0, "area": "", "level": "1", "title": "" }
}
```
说明：字段名与结构均来自文档；**具体数值为示例**，`url`/`file` 值不可当作真实格式规范。

---

## 3. `get_group_msg_history` 边界

源码为准（`packages/napcat-onebot/action/go-cqhttp/GetGroupMsgHistory.ts`）：

| 参数 | 类型/默认 | 说明 |
|---|---|---|
| `group_id` | string，必填 | 群号 |
| `message_seq` | string，可选 | "起始消息序号"；空或 `'0'` 视为不传 |
| `count` | number，默认 **20** | 获取数量 |
| `reverse_order` / `reverseOrder` | bool，默认 false | 反向排序（后者是旧版兼容别名） |
| `disable_get_url` | bool，默认 false | 不取 URL |
| `parse_mult_msg` | bool，默认 **true** | 解析合并转发 |
| `quick_reply` | bool，默认 false | 快速回复 |

- `[确认]` **可翻页**：传 `message_seq` → `core.apis.MsgApi.getMsgHistory(peer, startMsgId, count, reverse)`；不传 → `getAioFirstViewLatestMsgs(peer, count)`（最近 N 条）。
- `[确认]` `message_seq` 先经 `MessageUnique.getMsgIdAndPeerByShortId` 映射为内部 msgId，映射不到则原样当 msgId → 应传**事件里的 message_seq / message_id 短 ID**。
- `[确认]` 返回 `{ status, retcode, data: { messages: [...] } }`，元素为完整消息事件对象。
- `[确认]` 空结果**抛错**：`throw new Error('消息${seq}不存在')`，不返回空数组。
- `[未验证]` `count` 上限：action 层无 clamp，直接透传 core；未找到文档化上限。
- `[未验证]` 最远回溯范围：无"天数/条数"参数，取决于本地 NTQQ 消息库与服务器漫游窗口；超出即空 → 抛错。
- `[确认]` 官方文档 `src/onebot/api.md` 只写了 `group_id, count`，**不完整**，引用须以源码为准。

---

## 4. 离线缺口（issue #1838）

标题 `[确认]`：**「[BUG] get_group_msg_history 不会返回离线时的消息」**
作者 givegrandpaclimb；创建 2026-05-12T07:37:46Z；关闭 2026-05-22T14:31:04Z；关闭者 MliKiowa（MEMBER）；`state_reason: not_planned`；4 条评论。环境 Windows 11 25H2 / QQNT 9.9.26-44498 / NapCat 4.17.6。

正文摘录 `[确认]`：
> 调用 get_group_msg_history 不会返回离线时没有获取到的消息
> bot离线，群里聊了很多句后，调用 get_group_msg_history 只能获取最近的几条和离线之前的消息
> 期望：调用 get_group_msg_history 应该返回所有历史记录（就像人们在群里手动往上翻一样）

维护者回复 `[确认]`：
> **MliKiowa**：是的是 feature，不打算实现非离线消息推送，这会出现大量消息涌入问题
> 用户追问"能不能允许手动拉取…指定数量或时间段范围"
> **MliKiowa**：可以，晚点我会检查的（2026-05-23）

源码侧旁证 `[确认]`（`packages/napcat-onebot/index.ts`，`onRecvMsg`）：
```ts
if (this.bootTime > parseInt(m.msgTime)) {
  this.context.logger.logDebug(`消息时间${m.msgTime}早于启动时间${this.bootTime}，忽略上报`);
  continue;
}
```
→ 即使 NTQQ 登录后拉回了离线消息，**早于 bootTime 的消息也被直接丢弃不上报**，机制上与 #1838 完全吻合。

`[未验证]` 2026-05-23 之后是否已实现"手动拉取离线区间"：近期 issue 抽样未见相关条目，未在更新日志定位到实现。

**结论**：依赖"离线补齐"的采集方案在 NapCat 上**不成立**；必须常驻在线 + 本地持久化（并接受掉线窗口永久缺失）。

---

## 5. 安全事件复盘（2025-09-05）

原文 https://napneko.github.io/develop/security （标题「9月5日 OneBot 安全问题复盘」，471 字，最后编辑 6 个月前）`[确认]`：

> 2025年9月5日，OneBot 生态内爆发了一场严重的安全事件，导致多个使用 OneBot 协议的机器人平台攻击，发送违法信息，甚至引发了大规模的网络攻击。

> 事件起因被锁定在 OneBot 协议无Token问题上，大量用户采用一键包和一键脚本，暴露公网环境，导致攻击者通过扫描工具发现了大量暴露的 OneBot 机器人实例。

问题整理（原文五条）：不修改 Token → 空 Token 可访问；受影响主要是 OneBot Http Server/WebSocket Server，攻击者发恶意请求控制机器人发违法信息；NapCat **未提供随机 OneBot Token 默认填充**；**多数服务以 0.0.0.0 IPV4 全监听**导致公网暴露。

官方意见（原文）：随机默认 OneBot Token、可禁止局域网外访问、登录速率配置、登录随机化 WebUI 密钥、采用账号消息通知用户 Token 更新、强制要求用户登录后修改密码。

⚠ 措辞校正 `[确认]`：原文**没有**"监听 127.0.0.1"这句；原文是把 0.0.0.0 全监听**作为问题**列出，改进意见写作"可禁止局域网外访问"。也**未**提及"连带封号"。**不要**把复盘改写成"官方要求监听 127.0.0.1"。

补充 `[确认]`（other/security.md）：v4.8.106 以下 WebUI 默认弱密码 `napcat`；v4.8.106+ 取消固定默认密码，随机 Token 打印在启动日志并私聊发给机器人号自身。

---

## 6. 只读配置结论：**没有**

`[确认]` 源码 `packages/napcat-onebot/config/config.ts` 的 `OneBotConfigSchema` 顶层只有：`network`、`musicSignUrl`、`enableLocalFile2Url`、`parseMultMsg`、`imageDownloadProxy`、`timeout`。
适配器级只有：`name/enable/port/host/url/messagePostFormat/token/debug/reportSelfMessage/enableCors/enableWebsocket/enableForcePushEvent/heartInterval/reconnectInterval/verifyCertificate`。

- **不存在** `enableSend` / `disableSend` / `readOnly` 一类开关；没有任何项能限制 `send_group_msg`/`send_msg`。
- `reportSelfMessage` 默认 `false` 仅影响**自己发的消息是否上报**，不限制发送。
- 另注：`host` 源码默认 `127.0.0.1`，而文档 config/basic.md 写"默认地址为 0.0.0.0" → **文档与源码不一致**，以源码为准。

结论：只读必须在**外部**做（独立网关只放行白名单 action、或干脆只订阅事件不暴露 HTTP/WS 写通道）。NapCat 自身「自律」不成立。

---

## 7. Lagrange.Core 对比

`[确认]` 仓库：`LagrangeDev/Lagrange.Core`，C#，**2995 star / 351 fork / 42 open issues**，`pushed_at 2026-09-07`（距核查约 7 周），未归档，无 SPDX 许可。
`[确认]` README：当前分支为 **V2**，"V1 that has sunset"；V2 对外服务出口改为 **Milky 协议**（`Lagrange.Milky`），OneBot 11 出口属 V1。
`[确认]` 近三次提交（2026-08-20 / 09-01 / 09-07）全部在 Milky、Proto 模块 → OneBot11 已非重点。

| 维度 | NapCat | Lagrange.Core |
|---|---|---|
| 实现路线 | 驱动**真实 NTQQ 客户端**，调其 Node 模块 `[确认]` | **Pure C#** 重写 NTQQ 协议 `[确认]` |
| 是否需签名服务 | **不需要** `[确认]` | V1 Lagrange.OneBot **必须**配 `SignServerUrl`（默认 `https://sign.lagrangecore.org/api/sign`）`[确认]` |
| 签名服务可用性 | — | 官方明示"**本项目不主动提供 SignServer**，请前往 README 加入 Telegram 群聊获取信息"；且"不可与 Android 协议混用（如 unidbg-fetch-qsign）" `[确认]`；V2 是否仍需签名 `[未验证]` |
| 登录 | 扫码 / 快速登录；密码登录 `[未验证]` | 扫码为主；`Account.Password` 官方标注"**不再支持**" `[确认]` |
| 出口协议 | OneBot 11（+ 扩展） | V2 = Milky；OneBot 11 = V1（sunset） `[确认]` |
| 活跃度 | 11459 star，push 2026-10-22，open issue 7 `[确认]` | 2995 star，push 2026-09-07，open issue 42 `[确认]` |
| 文档站 | napneko.github.io 正常 | **https://lagrangedev.github.io/Lagrange.Core/ → 404**（已迁至 Lagrange.Doc/v2）`[确认]` |
| 版本耦合风险 | 高（NTQQ build 区间约束，注入崩溃类 issue 高频）`[确认]` | 低（不依赖客户端），但受签名服务与协议变更影响 |
| 合规/风控 | 共享真实客户端风控 | 协议端风控、需模拟设备 |

**是否成立为备选** `[推断]`：作为「NapCat 挂了顶上」的**协议层备选**，Lagrange 的价值恰恰在于"不用 NTQQ 客户端"，能绕开注入崩溃与版本升级；但代价是 (a) 签名服务无官方公开承诺，(b) OneBot 11 已不在 V2 主线，需要 Milky→OneBot 适配层，(c) 历史消息接口在 Milky 侧为 `/get_history_messages`（`start_message_seq` 尚未实现，见 Lagrange.Milky Feature List `[确认]`）。**结论：可作为"协议级降级方案"，不能作为"零改造替换"。**

---

## 8. OneBot 11 标准边界

`[确认]` 事件基类：`time`(int64) / `self_id`(int64) / `post_type` ∈ {`message`,`notice`,`request`,`meta_event`}；配置项 `event.message_format`（默认 `string`，可选 `array`）。

`[确认]` `message.group` 标准字段：`time, self_id, post_type, message_type, sub_type(normal|anonymous|notice), message_id, group_id, user_id, anonymous{id,name,flag}, message, raw_message, font, sender{user_id,nickname,card,sex,age,area,level,role,title}`。
`[确认]` `message.private`：`sub_type(friend|group|other)`，sender 仅 `user_id/nickname/sex/age`。

标准边界：

- `[确认]` **无 `get_group_msg_history`**（公开 API 目录逐条核对，也无 hidden 对应项）→ 属 go-cqhttp 扩展。
- `[确认]` **无私聊历史接口**；标准取单条消息只有 `get_msg`（按 message_id）。
- `[确认]` `message_id` 类型 `int32`；标准**未规定**唯一性范围（全局/会话内）→ 唯一性为实现约定，标准层 `[未验证]`。
- `[确认]` 媒体：`get_image(file)` → `{file: 本地路径}`；`get_record(file,out_format)` → 本地路径；**标准无 `get_file`**（NapCat 的 `get_file` 是扩展）。
- `[确认]` 标准**未约定图片 URL 有效期**，且 segment 里 `url` 字段本身不是标准定义（NapCat 自行附加）→ 有效期 `[未验证]`，需按腾讯 CDN 实测。
- `[确认]` 标准只有**调用方侧**限速约定（`_rate_limited` 后缀 + `api.rate_limit_interval` 默认 500ms）与 `_async`，无服务端频率承诺。

---

## 9. Evidence 清单（E:\study\research\chatlog\evidence\qq\）

| 文件 | 内容 |
|---|---|
| `napcat-architecture-install.md` | 架构自述原文、Shell/Framework/Windows/Linux/Docker 安装、NTQQ 版本区间 |
| `napcat-login-multiaccount.md` | 扫码/快速登录、bot_offline、IP 与风控、多账号配置文件 |
| `napcat-message-group-format.md` | message.group 字段、segment、messagePostFormat、示例 JSON |
| `napcat-get-group-msg-history.md` | PayloadSchema 与 _handle 源码逐字 |
| `napcat-issue-1838-offline-gap.md` | issue 全文 + 4 条评论原文 |
| `napcat-security-incident-2025-09-05.md` | 复盘原文 + 措辞校正 |
| `napcat-config-schema.md` | OneBotConfigSchema 全文、禁发项核查、host 默认值冲突 |
| `napcat-onebot-event.md` | event.md 事件体系全文（含匿名、灰条、poke 等） |
| `napcat-activity-and-group-api.md` | star/commit/issue 抽样、群接口与频率限制结论 |
| `lagrange-repo-and-architecture.md` | 仓库元数据、最近提交、V1 sunset/V2 Milky |
| `lagrange-signserver.md` | SignServer 原文、默认配置、密码"不再支持" |
| `onebot11-events-and-api.md` | 标准事件/消息字段、公开 API 全清单、媒体接口 |

## 10. 未解问题 / fetch 失败记录

1. `https://lagrangedev.github.io/Lagrange.Core/` → **404（站点不存在）**，已改用 `Lagrange.Doc/v2`。
2. `https://napneko.github.io/guide/login` 与 `src/guide/login.md` → **404**，密码登录流程无官方文档。
3. `https://github.com/NapNeko/NapCatQQ/issues/1838` HTML 为 JS 渲染，改用 GitHub REST API 取正文与评论（内容一致）。
4. `get_group_msg_history` 的 `count` 上限、最远回溯范围：无文档、core 层未定位截断逻辑 → `[未验证]`，建议实测（`count` 200/1000/5000 各跑一次）。
5. V2 Lagrange 是否仍需 SignServer：v2 文档 «Lagrange.Core» 章节标注"未完工" → `[未验证]`。
6. 图片 URL 有效期、`get_group_list`/`get_group_member_list` 频率限制 → 官方文档均无规定，需实测。
7. 2026-05-23 之后是否已加入"按区间手动拉离线历史" → `[未验证]`。
