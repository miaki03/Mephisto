# 80 · 统一数据模型对照（描述性）

> 本章只做**字段对照**，不设计表结构、不给实现。
> 三列并列：微信（WeFlow/WCDB）、QQ 协议端（OneBot 11）、QQ 官方 Bot（`GROUP_MESSAGE_CREATE`）。
> 加第三列的原因见 `60-qq-landscape.md`：官方 Bot 在 2026-10 已能读全量群消息，它现在是真选项，不能只对照 OneBot。

---

## 1. 主对照表

| 语义 | 微信（WeFlow） | QQ（OneBot 11） | QQ 官方 Bot（全量模式） |
|---|---|---|---|
| **消息唯一 ID** | `serverId`（int64，**> 2^53，必须按字符串**）+ `localId`（库内自增）；去重键 `messageKey`（`apiMessageMapping.ts:36-73`） | `message_id`（int32）+ `real_id`；标准**未规定**唯一性范围 | `id`（字符串，形如 `ROBOT1.0_xxxx`） |
| **会话 ID** | `talker`：私聊 `wxid_*`、群 `*@chatroom`、公众号 `gh_*` | `group_id`（群）/ `user_id`（私聊） | `group_openid`（**不透明**，不是群号）；单聊用 `user_openid` |
| **发送者 ID** | `senderUsername`（wxid） | `sender.user_id`（QQ 号） | `author.id` + `author.member_openid`（openid，**拿不到 QQ 号**） |
| **发送者昵称** | 需另查 `getContact` → `displayName` / `nickname`（不在消息行里） | `sender.nickname`（**在事件里**） | `author.username`（在事件里） |
| **群昵称** | `groupNickname`（需查群成员，源数据缺失时为空） | `sender.card`（**在事件里**，空串=未设） | ❌ 无独立字段 |
| **群内角色** | `isOwner`（群成员接口）；无 admin/member 三档 | `sender.role`：`owner`/`admin`/`member` | `author.member_role`：`owner`/`admin`/`member` |
| **时间戳** | `createTime`：**秒**（int32，`normalizeTimestamp` 把 >1e12 当毫秒除 1000） | `time`：**秒**（int） | `timestamp`：**RFC3339 字符串带时区**（如 `2026-07-21T08:00:00+08:00`） |
| **消息类型** | `localType`（**含复合值**，见 `20-*.md` §3） | `message_type`（group/private）+ `sub_type` + segment `type` 数组 | `message_type`（0=文本/3=卡片/101=并行/102=聊天记录/103=引用）+ `msg_elements[]` |
| **文本内容** | `content`（可能来自 zstd 解压 / hex / base64 / XML 抽取） | `raw_message`（CQ 码）或 `message[].data.text` | `content`（@ 被替换成 `<@member_openid>` / `<@all>`） |
| **引用/回复** | `replyToMessageId` + `quote{platformMessageId,sender,accountName,content,type}`；底层是 appmsg type 57 | `reply` segment：`{type:'reply', data:{id}}` | `msg_elements[]` 里 `message_type=103` + `message_scene.ext` 的 `ref_msg_idx` |
| **@ 提及** | ❌ **无结构化字段**（只能从 `rawContent` 的 XML 里自己抠） | `at` segment：`{type:'at', data:{qq}}` | `mentions[]`（`User[]`）+ `content` 里 `<@openid>` |
| **撤回** | 新增一条 `localType` 10000/10002 系统消息，内容含"撤回"；靠文本匹配识别（`messagePushService.ts:592-615`） | `notice` 事件（`post_type=notice`，群撤回为 `group_recall`） | ❌ 文档未给撤回事件 → `[未验证]` |
| **媒体文件** | `mediaUrl`（HTTP，需 `media=1` 先落盘）+ `mediaLocalPath`；原图需 xor/aes 解密 | `image.url`（**非标准字段，NapCat 自加**）+ `get_image(file)` 取本地路径；`get_record(file,out_format)` 取语音 | `attachments[].url`（带 `rkey`，**有有效期**）+ `content_type` / `size` / `voice_wav_url` / `asr_refer_text` |
| **合并转发** | appmsg 里 `chatRecordTitle` / `chatRecordList` | `forward` segment（+ `parse_mult_msg` 控制是否展开） | `msg_elements[]` 里 `message_type=102`，内层递归 `msg_elements` |
| **卡片/小程序/链接** | `localType=49` → `linkTitle`/`linkUrl`/`appMsgKind`/`fileExt`… | `json` segment / `mface` segment | `ark_data{prompt,ark_type,ark_name,fields}` |
| **消息方向** | `isSend`（1=我发的，0=别人，`null`=未知） | `self_id === user_id` 判定 | `author.bot` / 与 bot 自身 id 比对 |

---

## 2. 三个判断

### 2.1 哪些字段一边有一边没有

**微信有、QQ 两边都没有：**

| 字段 | 为什么重要 |
|---|---|
| `rawContent`（原始 XML / 压缩原文） | 出了解析 bug 还能回溯；QQ 侧拿不到原始结构 |
| `localId` | **稳定的本地游标**；QQ 的 `message_id` 是 int32，`real_id` 语义不明 |
| `quote` 快照（被引消息的完整内容副本） | 一句话就能知道"在回复什么"，不用二次查询 |
| 朋友圈（`sns.db`） | QQ 侧根本没有等价物 |
| 群昵称历史/备注体系 | 微信有 `remark`/`alias`/`groupNickname` 三套名字 |

**QQ 有、微信没有：**

| 字段 | 为什么重要 |
|---|---|
| `sender.card` / `sender.role` / `sender.level`、`anonymous` | **群昵称与角色直接躺在事件里**，微信要额外 join |
| `mentions[]`（结构化 @） | 微信侧只能自己抠 XML |
| `attachments[].asr_refer_text`（**语音的 ASR 参考文本**） | 微信要本地跑 `sherpa-onnx` 转写；QQ 官方直接给文本 |
| `attachments[].voice_wav_url`（已转好的 WAV） | 省掉 silk 转码 |
| RFC3339 带时区的时间戳 | 无歧义 |
| `message_scene.ext`（`msg_idx` / `auth_token`） | 支持"引用"与"鉴权下载" |

**两边都没有的：**

- **结构化的 @ 提及**：微信没有（只有 QQ 有）。
- **跨平台稳定的"消息序号"**：微信有 `sortSeq`，QQ 有 `message_seq`，官方 Bot 完全没有 —— 官方 Bot 只保证"实时推送顺序"。

### 2.2 时间戳精度与时区是否一致

| 平台 | 精度 | 时区 | 边界处理要点 |
|---|---|---|---|
| 微信 | **秒**（int32，上限 2038） | epoch，无时区 | `> 1e12` 会被当毫秒自动除 1000（`wcdbCore.ts:1583-1591`）；同秒多条靠 `sortSeq` |
| OneBot 11 | **秒**（int） | epoch，无时区 | 官方文档未规定"同一秒"的排序，靠 `message_seq` |
| 官方 Bot | **秒级但用 RFC3339 字符串** | **显式带偏移**（`+08:00`） | 解析后必须归一化到 epoch 秒，否则与另两边不可比 |

**结论：精度都是秒，但表示形式三种（int 秒 / int 秒 / RFC3339 字符串）。** 做增量拉取时：

- 微信与 OneBot 的"水位线"可以直接用秒，但**同一秒内的消息会重复**，必须用 ID 去重；
- 官方 Bot 的时间戳是字符串，**必须先 parse 再比**，且要注意它带时区（不要用"本地时间"直接比）；
- **三边都没有毫秒**，所以"同一秒内的严格顺序"只能靠平台各自的序号（微信 `sortSeq`、QQ `message_seq`、官方 Bot 无）。

### 2.3 若要写一个统一的归一化格式，最少需要哪些字段

只列字段，不给实现。共 **11 个必需 + 4 个可选**：

**必需（缺一不可）**

| # | 字段 | 类型 | 说明 |
|---|---|---|---|
| 1 | `platform` | enum | `wechat` / `qq` / `qqbot` —— 没有它，ID 会撞 |
| 2 | `messageId` | **string** | 统一字符串化（微信 serverId 超精度、官方 Bot 本来就是字符串） |
| 3 | `sessionId` | string | 平台原样（`xxx@chatroom` / `group_id` / `group_openid`） |
| 4 | `sessionType` | enum | `group` / `private` |
| 5 | `senderId` | string | wxid / QQ号 / openid |
| 6 | `timestampSec` | number | **统一到 epoch 秒**（官方 Bot 的 RFC3339 要 parse） |
| 7 | `type` | enum | 归一化类型：`text`/`image`/`voice`/`video`/`file`/`sticker`/`link`/`card`/`forward`/`reply`/`system`/`unknown` |
| 8 | `text` | string | 可直接喂 LLM 的文本（图片转 `[图片]`） |
| 9 | `isSelf` | boolean | 是否本人发出 |
| 10 | `senderName` | string | 展示名（优先群昵称，回退昵称） |
| 11 | `raw` | object | **原始事件/行**，永不丢弃 —— 出了解析 bug 只能靠它回溯 |

**可选（有则更好）**

| # | 字段 | 说明 |
|---|---|---|
| 12 | `quoteMessageId` | 引用目标的消息 ID（三边都能拿到） |
| 13 | `mentions` | `string[]` 被 @ 的 ID（微信侧拿不到 → 空数组） |
| 14 | `media[]` | `{kind,url,localPath,size}`（URL 有有效期，必须尽快落地） |
| 15 | `seq` | 平台内序号（微信 `sortSeq` / QQ `message_seq`），用于同秒排序 |

> 最小可用集其实是 **1/2/3/5/6/7/8/9**（8 个）—— 够做"按群按时间取文本做摘要"。
> 加上 10/11 是为了"可解释、可回溯"；加上 12 是为了"摘要里能说清在回复什么"。

**没有放进去的（有意）**：`localId`、`rawContent`、`WCDB_CT_*`、`font`、`area`/`sex`/`age` 等 —— 平台特有，放归一化层只会制造空值。

---

## 3. 一张"翻译风险"表（写归一化时最容易错的地方）

| 陷阱 | 表现 | 规避要点 |
|---|---|---|
| 微信 `localType` 复合值 | `244813135921` 直接比 `49` 会漏 | 先 `& 0xFFFFFFFF`（见 `20-*.md` §3.2） |
| 微信 `serverId` 精度 | JS `Number` 把 `6116895530414915131` 变成 `6116895530414915000` | 全程字符串 |
| 微信 `isSend` 三态 | `null` 不是 `false` | 保留三态或单独标"未知" |
| 微信 `content` 可能是 zstd/hex/base64 | 直接当文本会得到乱码 | 按 magic `0xFD2FB528` 判 zstd |
| 微信时间戳毫秒混入 | `> 1e12` 需除 1000 | 统一归一化函数 |
| QQ `message` 可能是 array 或 string | 取决于 `messagePostFormat` | 两边都要处理 |
| QQ 空历史**抛错** | 当空数组处理会吞异常 | try/catch |
| 官方 Bot 时间戳是 RFC3339 | 直接数字比较会错 | parse 后比 |
| 官方 Bot `group_openid` ≠ 群号 | 想按群号过滤会失败 | 以 openid 为准，另存映射 |
| 官方 Bot @ 是 `<@openid>` | 文本里残留尖括号标记 | 用 `mentions[]` 还原成昵称 |
| 三边撤回语义不同 | 微信是"新增系统消息"，QQ 是"notice 事件" | 归一化时区分"删除通知"与"系统消息" |

---

## 4. 本章未解问题

1. 官方 Bot 是否有**撤回事件**（文档未见）。
2. 官方 Bot 的 `id` 与 `msg_idx` 的稳定性（重投递是否变）。
3. OneBot 的 `message_id` 重启后是否重排。
4. 微信 `sortSeq` 的生成规则（是否严格单调）。
5. 官方 Bot 全量模式下"掉线期间"的消息服务端是否补推 —— **这一条决定它能不能做长期归档**，也是本章最重要的未知。
