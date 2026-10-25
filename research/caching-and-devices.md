# DeepSeek 上下文缓存规则 & 多设备共享会话：机制、数字与架构取舍

> 调研对象：邮件驱动的常驻个人助手（每封信构造一次请求：系统提示 + 工作区文件 + 会话摘要 + 本封信）。
> 调研时间：2026-10-25。所有文档类来源均为当日抓取结果；DeepSeek 官方文档因本机沙箱无法直连 `api-docs.deepseek.com`（DNS 解析为非公网地址，抓取被拒），改用**官方文档的逐字镜像仓库** `thevibeworks/deepseek-docs`（镜像自带 `source:` 与 `fetched:` 字段，抓取日 2026-10-17）。这一点影响可信度评级，见 §1。
> 结论强度约定：`[已验证]` = 读到官方文档/原始材料原文；`[已验证-社区]` = 多位实践者在 issue/博文中给出可复现证据，但非官方口径；`[未证实]` = 没找到一手材料，或只能从机制推断。
> 本地已有设计文档（[`60-runtime.md`](../docs/60-runtime.md)、[`30-workspace-memory.md`](../docs/30-workspace-memory.md)）按"一手材料"对待；与之冲突处直接标出。

---

## 0. 结论先行

| # | 结论 | 依据 |
|---|---|---|
| 1 | **缓存全自动、无 API、不可手动打点**。DeepSeek 的 `cache_control` 类参数被**明确忽略**——区别于 Anthropic 的显式 cache breakpoint。 | [Anthropic 兼容文档](https://api-docs.deepseek.com/zh-cn/guides/anthropic_api)，抓取 2026-10-25：`tools[].cache_control`、`content[].cache_control` 均标注"忽略"；[kv_cache 指南](https://api-docs.deepseek.com/zh-cn/guides/kv_cache)："默认开启，用户无需修改代码" |
| 2 | **命中契约 = 从第 0 个 token 开始的前缀完全一致，且该前缀必须是服务端已"落盘"的完整前缀单元**。中间开始重复、或只匹配到半个单元，都不算命中。 | [kv_cache 指南](https://api-docs.deepseek.com/zh-cn/guides/kv_cache)（2026 版按 Sliding Window Attention 重写为"缓存前缀单元"）；[news0802](https://api-docs.deepseek.com/zh-cn/news/news0802)："只有当两个请求的前缀内容相同时（从第 0 个 token 开始相同），才算重复" |
| 3 | **最小存储粒度 64 tokens**；不足 64 tokens 的内容不会被缓存。命中计数同样按 64-token 块对齐，因此分歧点最多浪费 63 tokens。 | [news0802 注 1](https://api-docs.deepseek.com/zh-cn/news/news0802)（原文脚注："缓存系统以 64 tokens 为一个存储单元"） |
| 4 | **价格差 30–50 倍**（币种 CNY，每百万 tokens）：`deepseek-flash` 命中 0.02 元 vs 未命中 1 元（空闲时段），高峰时段翻倍。这是当前 API 市场里最大的一档输入价差。 | [模型 & 价格](https://api-docs.deepseek.com/zh-cn/quick_start/pricing)，抓取 2026-10-25 |
| 5 | **缓存是服务端硬盘缓存，与设备无关**。它不绑定进程、不绑定 session id、不绑定机器；绑定的是 `(账号, user_id, 模型, 请求前缀字节)`。→ **"换设备"本身不破坏缓存**，"换设备后重建出不同字节流"才破坏缓存。这是本文对架构决策最关键的一条。 | [kv_cache 指南](https://api-docs.deepseek.com/zh-cn/guides/kv_cache) + [创建对话补全](https://api-docs.deepseek.com/zh-cn/api/create-chat-completion)："user_id 可用于 KVCache 缓存隔离"；[news0802](https://api-docs.deepseek.com/zh-cn/news/news0802)："每个用户的缓存是独立的" |
| 6 | **主流 harness 的会话全部是本地文件，跨机默认不支持**；目前只有 Claude Code 给出官方跨机方案（`SessionStore` 适配器 → S3/Redis/Postgres）。 | [Claude Code: Persist sessions to external storage](https://code.claude.com/docs/en/agent-sdk/session-storage)，抓取 2026-10-25；[Work with sessions](https://code.claude.com/docs/en/agent-sdk/sessions)："Session files are local to the machine that created them" |
| 7 | **"缓存"与"可迁移"不在同一层，因此不冲突**；真正冲突的是"压缩/摘要"与"字节稳定"。迁移的代价是**一次**固定成本的冷启动，不是永久性劣化。 | 见 §5.1、§5.2 |
| 8 | **Android 不适合当常驻 Agent 主机**：Android 12+ 的 phantom process killer 会杀掉超过 32 个（全系统合计）子进程，且按 CPU 超限杀，`termux-wake-lock` 只能阻止深睡、代价是耗电。有"能跑"的真实记录，但没有长期无人值守的可靠性数据。 | 见 §4.3 |
| 9 | 本地 [`60-runtime.md`](../docs/60-runtime.md) §5.3 的"harness 会话只能当缓存、不能当账本"判断，本报告**确认正确**，并补一条修正：**不必为吃缓存而固定 `--session-id`**，因为命中只依赖请求字节前缀。 | 见 §5.1 |

---

## 1. 方法与局限

- 只读一手材料：DeepSeek 官方文档（经逐字镜像）、Claude Code / Codex / OpenHands / Aider 官方文档、Android 平台源码级文档、Cursor 官方论坛、GitHub issue。二手博客（如 `dshbase`）只用于"社区实测数字"，且单列标注。
- **DeepSeek 文档不是直连拿到的**：本机对 `api-docs.deepseek.com` 的抓取被沙箱拒绝（`resolves to a non-public IP address`）。改用镜像仓库，镜像每个文件头带 `source:` 原文链接与 `fetched:` 抓取日期，内容与原站一致度**未独立交叉验证**。凡涉及定价/机制的断言，都同时引了 `news0802` 或 API schema 页作为第二来源。
- **没有做任何实测**（没有 API key、也不该在此任务里发请求）。所有"命中率"数字都是引用他人自报或机制推演，不构成对我方场景的预测。
- 时间敏感：本文抓到的定价页已经是 **V4 世代**（`deepseek-flash` = V4.1-Flash-1017、`deepseek-v4-pro` = V4-Pro-0813），与 2025 年的 `deepseek-chat`/`deepseek-reasoner` 命名完全不同；切换期结论会过期，引用请带日期。
- 中文世界里"为什么明明没改却不命中"的系统性记录很少，本文只能用英文 issue 与本工作区的实测补齐（§3.3）。

---

## 2. 缓存机制：逐条回答

### 2.1 自动还是手动？在哪一层做？

| 问题 | 答案 | 来源 |
|---|---|---|
| 需要写代码开启吗 | 不需要。"对所有用户默认开启" | [kv_cache 指南](https://api-docs.deepseek.com/zh-cn/guides/kv_cache) |
| 有显式打点 API 吗（如 `cache_control`） | 没有，且 Anthropic 格式里的 `cache_control` 被**忽略**，不会报错也不会生效 | [Anthropic API 兼容性细节](https://api-docs.deepseek.com/zh-cn/guides/anthropic_api)，抓取 2026-10-25 |
| 在哪一层 | 请求被摊平成**一条 token 流**后做**前缀**匹配；不是对"任意片段"做 KV 复用，也不是对 message 对象做哈希 | [kv_cache 指南](https://api-docs.deepseek.com/zh-cn/guides/kv_cache) §"缓存落盘与命中规则" |
| 2026 年的机制变化 | 从"整条前缀连续匹配"变为**"缓存前缀单元"（cache prefix unit）完整匹配**。原因写在文档里：Sliding Window Attention 让前缀的存取与判别方式改变 | 同上 |

`[已验证]` 一条容易被忽略的推论：**"前缀"= 序列化后交给模型的那条 token 流**，所以 `system` 段、`tools[]` 段、`messages` 段都在这条流里，顺序即渲染顺序。社区实测把这条推论量化了：`[已验证-社区]`

```
[system tokens] [tools[] tokens] [msg1 user] [msg2 assistant] ... [msgN]
```

> "DeepSeek-style prompt caching flattens the request into one token stream. For us (`system` ≈ 14,592 tok, `tools[]` ≈ 15,099 tok)…… Everything after the divergence point misses once."
> — [hermes-agent issue #105236](https://github.com/NousResearch/hermes-agent/issues/105236)，2026-09-07

这条 issue 还给出了"用命中残留量反推断点位置"的方法，堪称本文最实用的一张表：

| 残留量 | 断点位置 | 现场特征 |
|---|---|---|
| `hit ≈ 0`（prompt 的 1–3%） | system prompt 被重建 | 日志里出现 "stale runtime identity; rebuilding" |
| `hit ≈ system tokens`（该案例 14,592） | **`tools[]` 段**（system 对、tools 变） | 无日志；对比 tools 哈希，下一轮恢复 |
| `hit ≈ system+tools`（该案例 ~30K） | 第一条 user 消息（注入块漂移） | 首条 user 行 `api_content` 为 NULL |
| `hit = 75–85%` | 对话中段分歧（改写/丢段） | 写穿、缺行 |
| `hit < 10%` → 下轮 97%+ | 构建期运行时状态（工具面/provider 加载） | 并发构建日志 |

### 2.2 最小粒度与缓存键

| 项目 | 结论 | 强度 |
|---|---|---|
| 存储单元 | **64 tokens**，不足 64 tokens 不缓存 | `[已验证]` [news0802 注1](https://api-docs.deepseek.com/zh-cn/news/news0802) |
| 是否包含 system prompt | 包含。它就是 token 流第 0 段 | `[已验证]`（例一：system + user 的公共前缀被识别为单元） |
| 是否包含 tools 定义 | **参与前缀**（机制上确定：tools 渲染在 messages 之前） | `[已验证-社区]`（上表 `hit ≈ system tokens` 的断点定位） |
| 是否包含历史 messages | 包含，且**必须逐轮追加**才继续命中 | `[已验证]` 例一 |
| 缓存键由什么决定 | 实践中 = `(账号, user_id, 模型/版本, 前缀字节序列)`。账号级隔离："每个用户的缓存是独立的，逻辑上相互不可见"；`user_id` 可用于"KVCache 缓存隔离" | `[已验证]`（隔离）+ `[未证实]`（键的精确组成：文档没有给出 key schema，也没有说 `temperature`/`top_p`/`thinking` 是否进键） |
| 模型版本切换会不会清缓存 | 未在缓存文档中说明，但 `system_fingerprint`/模型版本是后端配置标识，**应当视为会失效**（保守假设） | `[未证实]`（文档只在 API schema 里出现 `system_fingerprint` 字段） |

> **对多设备场景的直接后果**：如果手机端与电脑端用了不同的 `user_id`（很多 harness 会塞一个设备/客户端标识），两边的缓存会被**逻辑隔离**，命中率归零但不报错。这是"两台机器各跑各的"最隐蔽的坑。

### 2.3 命中条件：必须逐字节一致吗？

`[已验证]` 三层要求，缺一不可：

1. **从第 0 个 token 开始**就相同（中间开始重复无效）。
2. 重复长度必须**完整匹配一个已落盘的"缓存前缀单元"**（不是"差不多到那就行"）。
3. 单元必须已被写入硬盘（写入耗时"秒级"，首次请求不可能命中自己）。

所以"逐字节一致"的说法**方向正确但不够**：准确说法是"**token 化之后的前缀序列一致，且长度落在单元边界上**"。

| 变更 | 会不会未命中 | 依据 |
|---|---|---|
| 头部插一个 ISO 时间戳 `2026-10-25T14:03:11Z` | **会**，且从第 0 段之后全废。连"只改时分秒"也一样，因为 token 流不同 | `[已验证]` 机制（单元匹配要求完全相同）+ `[已验证-社区]`："A changed timestamp or reordered tool invalidates the cache"（[dshbase](https://dshbase.com/blog/deepseek-harness-cost/)，2026-08-14） |
| 头部插 `cwd` / 平台名 / 版本号 | **会**。真实案例：system prompt 内容一字未改，但"运行时身份"含 `(model, provider, cwd, platform)`，切换桌面端↔TUI 或换目录就被判为 stale 重建成新会话行 → 整条前缀分歧，**一次全 miss（20 万+ tokens）** | `[已验证-社区]` [issue #105236](https://github.com/NousResearch/hermes-agent/issues/105236) §2 |
| `tools[]` 顺序变了，或 description 里有动态计数（"Search {N} additional tools"） | **会**。重启后按注册表重新生成 tools，N 随插件加载数变化 → 字节不同 → 每次重启付一次全 miss | `[已验证-社区]` 同上 §5b（文中给了 6 vs 2 个 deferred tools 的对照证明） |
| JSON 序列化 key 顺序抖动 | **会**（推理，但机制上确定：序列化差异 → token 流差异） | `[未证实-官方]`：官方文档从未讨论客户端序列化 |
| 空白/换行差异 | **会**，同理 | `[未证实-官方]`，机制推断 |
| 会话摘要被重写（compaction） | **会**，且是最贵的一种：前缀在中段被替换，之后全废 | `[已验证]`（Claude Code 文档承认 compaction 后历史被 summary 取代）+ 机制推断 |
| `temperature` 变了 | **不会**。文档明确：缓存只匹配输入前缀，输出仍按 temperature 采样 | `[已验证]` [kv_cache 指南](https://api-docs.deepseek.com/zh-cn/guides/kv_cache) §"硬盘缓存与输出随机性" |

### 2.4 落盘时机：为什么"第一次不命中、第三次才命中"

`[已验证]` 文档给了三类落盘时机（这是 2026 版最值得读的一段）：

| 时机 | 规则 | 对邮件助手意味着什么 |
|---|---|---|
| **请求结束位置落盘** | 每次请求的"用户输入结束位置"和"模型输出结束位置"各产生一个前缀单元 | 第 N 封信的请求若 = 第 N−1 封信请求 + 新内容，则能命中到第 N−1 次的结束点（**这是最肥的一块**） |
| **公共前缀检测落盘** | 系统发现多次请求有公共前缀时，把该公共前缀单独落盘为一个单元 | 冷启动后第 2–3 次请求开始，`系统提示 + 工具 + 工作区文件` 这段头部会变成可命中的单元——**这与是否复用 session id 无关** |
| **按固定 token 间隔落盘** | 长输入/长输出按固定 token 间隔截取单元 | 避免"长前缀因迟迟不到结束位置而完全无法缓存" |

文档的例二（长文本问答）是理解代价的关键：三次请求共享 `<system> + <财报正文>`，只有最后一句问题不同。**前两次都不命中**，第二次之后系统才把公共前缀落盘，**第三次才命中**。

> 结论：**缓存有 1–2 次请求的"学习期"**。任何"每次都是新会话、且每次头部略有差异"的设计，会永远停在"第一次"的状态，命中率恒为 0——这解释了绝大多数"我没改为什么不命中"的抱怨。

### 2.5 TTL 与失效

| 项目 | 结论 | 强度 |
|---|---|---|
| 不用的缓存多久清 | "缓存不再使用后会自动被清空，时间一般为**几个小时到几天**" | `[已验证]` [news0802 注3](https://api-docs.deepseek.com/zh-cn/news/news0802) / [kv_cache 指南](https://api-docs.deepseek.com/zh-cn/guides/kv_cache) |
| 是否有保活机制 | 官方无。社区做法是定时用同一前缀发一次极小请求"保温"（Aider 有 `--cache-keepalive-pings`，每 5 分钟 ping，只为 Anthropic 的 5 分钟 TTL 设计） | `[已验证]` [Aider options](https://aider.chat/docs/config/options.html) |
| 什么操作会清 | 官方只给"长时间不用自动清空"；**没有**"换设备/换 IP/换 session 会清"这类说法，也**没有**"手动清"的 API | `[已验证]`（文档无此描述，属"查不到"而非"确认不会"） |
| 保活值得吗（算一下） | 40K tokens 前缀 ping 一次 = 0.04M × 0.02 元 = **0.0008 元**；每 5 分钟一次 = 288 次/天 = **0.23 元/天 ≈ 7 元/月**。相对于"周末断档后周一全量 miss"，量级上不划算也没必要——TTL 是"小时到天"，不是 5 分钟 | 自算，价格见 §2.6 |

### 2.6 计费：官方定价页数字（CNY / 百万 tokens）

`[已验证]` [模型 & 价格](https://api-docs.deepseek.com/zh-cn/quick_start/pricing)，抓取 2026-10-25：

| 模型 | 输入·缓存命中 | 输入·未命中 | 输出 | 命中:未命中 |
|---|---|---|---|---|
| `deepseek-flash`（V4.1-Flash-1017）· 空闲时段 | **0.02 元** | **1 元** | 4 元 | 1 : 50 |
| `deepseek-flash` · 高峰时段 | 0.04 元 | 2 元 | 8 元 | 1 : 50 |
| `deepseek-v4-pro`（V4-Pro-0813）· 空闲时段 | **0.15 元** | **4.5 元** | 13.5 元 | 1 : 30 |
| `deepseek-v4-pro` · 高峰时段 | 0.30 元 | 9.0 元 | 27 元 | 1 : 30 |

- 高峰/空闲定义：北京时间**周一至周五（非法定节假日）9:00–12:00、14:00–18:00** 为高峰，其余（含周末与法定节假日全天）为空闲，**空闲价 = 高峰价的一半**。→ 邮件助手完全可以靠"错峰批处理"直接把单价砍半。
- 两模型上下文 1M、最大输出 384K；并发限制 flash 2500 / v4-pro 500。
- 历史锚点：2025-08 的 [news0802](https://api-docs.deepseek.com/zh-cn/news/news0802) 说命中价"0.1 元/百万 tokens"，并称"最高可省 90% 费用（需针对缓存特性优化），即使不做优化也省 50% 以上"。**现在的 0.02 元让价差从 10x 变成 50x**，优化的边际收益比文档写的更大。
- 厂商自报延迟收益：128K 输入且大部分重复时，首 token 延迟 **13 秒 → 500 毫秒**（同上，2025 年数据，`[已验证-厂商自报]`）。

**把价格换成"每封信"的钱**（自算，按 flash 空闲价、输入 40K tokens、输出 1.5K tokens）：

| 命中率 | 每封信输入成本 | 每封信输出成本 | 每封合计 | 3000 封/月 |
|---|---|---|---|---|
| 0% | 0.0400 元 | 0.0060 元 | 0.0460 元 | **138 元** |
| 90%（36K 命中 / 4K 未命中） | 0.0047 元 | 0.0060 元 | 0.0107 元 | 32 元 |
| 99%（39.6K / 0.4K） | 0.0012 元 | 0.0060 元 | 0.0072 元 | 21.6 元 |

两点值得盯：

1. **命中率从 90% 提到 99%，只省 32 → 21.6 元/月**（绝对值小）。**真正的大头是 0% → 90%**（138 → 32 元）。所以工程上值得做的是"消灭结构性 miss"，而不是抠最后几个点。
2. **输入被缓存后，输出成为主要成本项**（0.006 元 > 0.0047 元）。下一阶段的优化重点从"prompt 组织"转向"少说话"：缩短回复、`thinking.reasoning_effort` 降到 `none`/`low`、限制 `max_tokens`。用 `deepseek-v4-pro` 时这个反转更早发生（输出 13.5 元/M）。

### 2.7 观测手段（不报错，但能测）

`[已验证]` 响应 `usage` 里有两个字段（2026 版还额外给了 OpenAI 形状的等价字段）：

| 字段 | 含义 | 备注 |
|---|---|---|
| `prompt_cache_hit_tokens` | 本次输入中命中缓存的 tokens | 计费按此 |
| `prompt_cache_miss_tokens` | 未命中的 tokens | 恒等式 `prompt_tokens == hit + miss` |
| `prompt_tokens_details.cached_tokens` | 与 `prompt_cache_hit_tokens` **相同值**（文档明说"与 prompt_cache_hit_tokens 相同"） | 2026 版新增的 OpenAI 兼容字段 |
| `completion_tokens_details.reasoning_tokens` | 思考模式下的 CoT token 数 | 用来判断思考开销 |

来源：[创建对话补全 API schema](https://api-docs.deepseek.com/zh-cn/api/create-chat-completion)，抓取 2026-10-25；流式下 `usage` 附在最后一个内容块（`stream_options.include_usage` 可让每块都带）。

工程注意（真实踩坑）：很多 harness **只读 OpenAI 的 `prompt_tokens_details.cached_tokens`，不读 DeepSeek 的原生字段**，导致面板上命中率恒为 0%：

> "direct DeepSeek API usage will still show 0 cache-hit tokens, because the cache-stat extractor only reads the OpenAI/OpenRouter-style nested shape and never reads DeepSeek's own top-level fields."
> — [hermes-agent issue #61871](https://github.com/NousResearch/hermes-agent/issues/61871)，2026-07-10（该仓库当时 grep `prompt_cache_hit_tokens` 零命中）

→ **可执行的观测规则**：落库时同时记 `hit/miss` 与"前缀版本号"，按版本看命中率；恒等式 `hit + miss == prompt_tokens` 必须成立，否则是你的解析层坏了（不是缓存坏了）。

### 2.8 支持哪些模型与端点

| 维度 | 结论 | 强度 |
|---|---|---|
| 模型 | 当前两代（`deepseek-flash`、`deepseek-v4-pro`）都在定价页上单列"缓存命中"价，即都支持 | `[已验证]` 定价页结构 |
| 旧模型名 `deepseek-chat` / `deepseek-reasoner` | 2026 文档已不出现。定价页只说明 `deepseek-v4-flash` / `deepseek-v4-flash-vision-exp` 仍可调用但已下线映射。`deepseek-chat` 现状**查不到明确说明** | `[未证实]` |
| OpenAI 格式 `/chat/completions` | 支持，`usage` 有完整 hit/miss 字段 | `[已验证]` |
| Anthropic 格式 `/anthropic/messages` | 接口可用，但 `cache_control` 被忽略；该端点 `usage` 是否回传 hit/miss **未在兼容性文档中列出** | `[未证实]` |
| Responses API | 支持工具与 system 插入，**是否回传 hit/miss 未证实** | `[未证实]` |
| FIM 补全（Beta） | 未提及缓存 | `[未证实]` |
| 思考模式 / 非思考模式 | 未提及差异；两者都会产生"模型输出结束位置"单元 | `[未证实]` |

---

## 3. 为了高命中率，prompt 该怎么组织

### 3.1 五条硬规则（按收益排序）

| # | 规则 | 为什么 | 怎么落地 |
|---|---|---|---|
| **H1** | **头部逐字节冻结**：宪法/`AGENTS.md`、工具定义、记忆索引、输出格式约定全部放最前，且**禁止**在其中出现时间戳、run id、cwd、设备名、随机排序的列表 | 前缀任何位置变化 → 之后全 miss；头部是最多个请求共享的部分，代价最高 | 头部内容做内容 hash 当版本号（`prefix_v7_a3f19c2e`），写进指标维度；时间戳只允许出现在尾部 |
| **H2** | **只追加，不改写**：会话日志 append-only；不做中途 summarize/rewrite/重排 | compaction 把旧轮次换成摘要 = 前缀中段被替换，之后全废；DeepSeek 例一证明"追加则一直命中" | 摘要只用于"重建"（迁移/冷启动）场景，且重建后**立刻固定**，不要每次对话都重新摘要 |
| **H3** | **工具定义序列化固定**：`tools[]` 顺序固定、JSON key 顺序固定、description 里**不放运行时计数/插件数量/日期** | 次贵的一段。真实案例：重启后 `tools[]` 因注册表状态变化重新生成 → 每次重启一次全 miss（案例里 tools 段 15,099 tokens） | 工具定义当"生成的静态资产"：编译期产出 + 落盘 + hash，运行时只读 |
| **H4** | **动态内容全部压到最后**：本封信、当前时间、本轮状态、用户临时输入 → 永远在最后一块 | 命中长度 = 最长可匹配单元边界；尾部才变的请求能吃到几乎全部前缀 | 请求布局见 §5.3 |
| **H5** | **`user_id` 固定且全局一致**（多设备尤其重要）；不要塞设备指纹 | `user_id` 参与缓存隔离；两台机器不同 `user_id` = 两套独立缓存 | 配置项写死，不放 local state |

### 3.2 次级规则（便宜且有效）

- **不要在同一会话里跑"多消费者"**：桌面/TUI/gateway 各自把持久化历史重放成不同字节（真实案例：三个消费者三种重放形状，导致同一历史产生三种字节流）→ `[已验证-社区]` [#105236 §6](https://github.com/NousResearch/hermes-agent/issues/105236)。做法：**单一 canonicalize 函数**给所有路径用。
- **后台任务不要"浅拷贝父对话再改"**：任何一个 in-place 修改都会写穿到父对话的持久化历史，之后磁盘 ≠ 线上字节流（同上 §3）。
- **工具输出不要每轮重发全文**：把中间结果留在工具侧，只把结论写进上下文（社区实测把这条列为"最省钱的杠杆"之一）→ [#dshbase](https://dshbase.com/blog/deepseek-harness-cost/)。
- **控制工具数量**：每个 schema 都住在前缀里；社区统计"4 个工具 + append-only 会话"能跑到 **99.93% 命中率**（Pi 自报），对应"~¥19 vs ¥900+ / 1B tokens"的输入账单差（`[已验证-社区自报]`，非官方 benchmark）。

### 3.3 "明明没改为什么不命中"——公开记录汇总

| 现象 | 真实原因 | 来源 |
|---|---|---|
| 换了 UI（桌面 ↔ TUI）或 `cd` 到别的目录后第一次请求全 miss | system prompt 内容没变，但"运行时身份"含 `cwd`、`platform`，被判 stale 后重建 | [#105236 §2](https://github.com/NousResearch/hermes-agent/issues/105236) |
| 每次重启后第一轮全 miss，之后正常 | `tools[]` 每次 init 都从注册表重新生成，description 内嵌"Search N additional tools" | [#105236 §5b](https://github.com/NousResearch/hermes-agent/issues/105236) |
| 面板上命中率恒为 0%，但账单确实便宜了 | 客户端只读 `prompt_tokens_details.cached_tokens`，没读 DeepSeek 原生字段 | [#61871](https://github.com/NousResearch/hermes-agent/issues/61871) |
| 命中率 75–85%，偶发掉一次 | 后台 review 分支浅拷贝父对话并 in-place 改写，写穿到父持久化历史 | [#105236 §3](https://github.com/NousResearch/hermes-agent/issues/105236) |
| 中等长度对话突然掉到 10% 以下，下一轮又回到 97% | 构建期运行时状态（provider 加载、并发构建）导致工具面不同 | [#105236 §1 表](https://github.com/NousResearch/hermes-agent/issues/105236) |
| 加了几个 MCP 工具后整体变贵 | 前缀变长 → 每轮重发的输入变多；且工具越多模型越容易多轮 → 每轮都重发 | [dshbase](https://dshbase.com/blog/deepseek-harness-cost/) `[社区分析]` |

**反向证据（说明"没改就一定能命中"不成立）**：官方明确写"缓存系统是尽力而为，不保证 100% 命中"（[news0802 注2](https://api-docs.deepseek.com/zh-cn/news/news0802)）。所以指标应该看"命中率是否接近上限"，而不是"是否 100%"。

---

## 4. 多设备 / 多机共享会话

### 4.1 各 harness 的会话存储与跨机能力

| Harness | 存储位置与格式 | 续接方式 | 能否两台机器接同一会话 |
|---|---|---|---|
| **Claude Code** | `~/.claude/projects/<encoded-cwd>/<session-uuid>.jsonl`（cwd 里非字母数字全换成 `-`，超 200 字符截断加 hash；`CLAUDE_CONFIG_DIR` 可改根） | `--continue`（最近一个）/`--resume <id>`/`--fork-session`；结果消息带 `session_id` | **可以，且有官方两条路**：① `SessionStore` 适配器（S3/Redis/Postgres，双写镜像）② 直接搬 `.jsonl` 到新机器 `~/.claude/projects/` 任意子目录下。**注意**：store 的 key 含 `projectKey`（cwd 编码），必须用匹配的 cwd 恢复；官方还给了第三条"别依赖 resume"的忠告 |
| **Codex CLI** | `~/.codex/sessions/YYYY/MM/DD/rollout-<时间戳>-<uuid>.jsonl`，一个 chat = 一个 jsonl | `codex resume`（选择器）/`codex resume --last`；resume 时"loads the session rollout file from `~/.codex/sessions/`" | **可以，靠手工拷贝**。有完整社区教程：`scp` 单个 rollout 文件到目标机同日期目录 → `/resume` 选中。注意"不要拷 `history.jsonl`，保持文件名与目录结构" |
| **Aider** | **仓库内文件**：`.aider.chat.history.md`、`.aider.input.history`、`.aider.tags.cache`、`.aider.llm.history`；默认会被写进 `.gitignore` | `--restore-chat-history`（默认 **False**） | **理论上天然可以**（同一 checkout 就有历史），但默认 gitignore 掉了；要靠 `--no-gitignore` + 手动提交才跨机。且历史是 Markdown 渲染稿，不是逐 token 的完整 transcript |
| **OpenHands** | `~/.openhands/conversations/<id>/conversation.json` | `openhands --resume <id>` / `--resume --last`（ACP/IDE 模式同） | 文档明说"会话历史存在本机"；跨机= 搬目录，无官方共享存储 |
| **Cursor** | 本地 SQLite（`state.vscdb` 等 workspaceStorage）+ 账户侧部分数据；**精确位置未证实** | 编辑器内历史列表 | **不能**。论坛证据：`Chat history not available across devices`（2026-05-21）、`Cloud Sync for Chat History` 仍是 **feature request**（14 回复 / 2256 浏览，最后活动 **2026-10-20**） |
| **dsh（本地）** | 本地持久化会话，`--session-id` 续接，**校验 cwd 一致**，id 不存在/属 subagent/跨 cwd 直接失败 | `--session-id` | 单机。跨机需自己搬（见 [`60-runtime.md`](../docs/60-runtime.md) §5.3 实测） |

来源：[Claude Code sessions](https://code.claude.com/docs/en/agent-sdk/sessions)、[Claude Code session-storage](https://code.claude.com/docs/en/agent-sdk/session-storage)（均抓取 2026-10-25）；[codex resume](https://mintlify.wiki/openai/codex/cli/resume) + [zonca 博客：Copy a single Codex session to another machine](https://zonca.dev/posts/2025-12-16-copy-codex-session)、[OpenHands: Resume Conversations](https://docs.openhands.dev/openhands/usage/cli/resume)、[Aider options](https://aider.chat/docs/config/options.html)、[Cursor 论坛](https://forum.cursor.com/t/is-cross-device-sync-possible/147030)。

### 4.2 "会话状态放仓库，任何机器 checkout 后接着干"——成熟做法与代价

| 做法 | 谁在用 | 真实代价 |
|---|---|---|
| 仓库内历史文件 + `--restore-chat-history` | Aider（历史文件在仓库根，但默认进 `.gitignore`） | ① 历史是给人看的 Markdown，重建上下文靠"把历史塞回 prompt"，不是逐字节复原 → **必然一次全 miss**；② 并发写同一文件 = 后写覆盖，git 冲突在 markdown 上基本手解 |
| 搬单个 jsonl 到目标机 | Codex（社区教程）、Claude Code（官方"Move the session file"） | ① 文件名/目录结构/时间戳必须对齐；② **体积**：jsonl 含全部 tool result，几百 MB 级别不罕见；③ 反向同步时**冲突无解**（append-only 文件在 git 里冲突形状极差）；④ 逐字节复原**未必**成立：不同 harness 版本重放时可能插入/剥离块（Claude Code 明确 `canonicalize` 会重排） |
| 官方 SessionStore（S3/Redis/PG） | Claude Code（唯一给出官方方案者） | ① 需要自己实现 `append`/`load` 并做去重（重试会重投，需按 `entry.uuid` 去重）；② `append` 并发会 race，官方要求"事务/CAS/每会话锁"；③ 存储内容仍是 Claude 自己的 entry 格式，换 provider 就作废 |
| **不共享 transcript，共享"应用状态"** | Claude Code 官方第三条建议："Don't rely on session resume. Capture the results you need (analysis output, decisions, file diffs) as application state and pass them into a fresh session's prompt. This is often more robust than shipping transcript files around." | ① 上下文要靠**摘要**重建，必然损失细节；② 重建 = 一次全 miss；③ 但状态小、可 diff、可人审、可跨 harness |

最后一行正是本工作区 [`60-runtime.md`](../docs/60-runtime.md) §5.3 的做法（`session.md` + `memory/` 为账本，harness 会话只当缓存），且被官方文档独立佐证为"often more robust"。

### 4.3 手机端（Android）跑常驻 Agent 的现实约束

| 约束 | 具体数字/机制 | 强度 |
|---|---|---|
| **phantom process killer**（Android 12 起，静默引入） | 全系统合计最多 **32 个** App 子进程，超出即杀；**另外**CPU 超限也会杀（检查周期 `POWER_CHECK_INTERVAL` 默认 **5 分钟**）。判定看**父 App 进程**的 oom adj，不看子进程自己 | `[已验证]` [AOSP 机制文档](https://raw.githubusercontent.com/agnostic-apollo/Android-Docs/master/en/docs/apps/processes/phantom-cached-and-empty-processes.md)（引 `PhantomProcessList.java`、`ActivityManagerConstants.java`） |
| 能否关掉 | 可以：`settings_enable_monitor_phantom_procs`（Android 12L beta 3 起引入），但**需要 adb/特权**；`device_config` 同步可能把设置重置回去 | `[已验证]` 同上 |
| 症状 | 回到 Termux 看到 `[Process completed (signal 9) - press Enter]`；logcat 里 `Killing PhantomProcessRecord ...: Trimming phantom processes` | `[已验证]` 同上（文档给了完整复现步骤：开 40 个 `sha256sum`，插拔充电器立刻触发） |
| 深睡 / Doze | `termux-wake-lock` 可阻止 Doze 深睡（代价是持续耗电），`termux-wake-unlock` 释放 | `[已验证]` [Termux Wiki: termux-wake-lock](https://wiki.termux.com/wiki/Termux-wake-lock)；**Doze 维护窗口的具体间隔未证实**（没抓到 AOSP/官方数字） |
| 真实"跑起来了"的记录 | Galaxy S26 Ultra / Android 16 / Termux + proot，50+ commits、6 个子 agent、"wake locks that prevent Android from killing a long session"、57 个 Termux API 中 24 个确认可用 | `[已验证-自述]` [claude-code-android](https://github.com/ferrumclaudepilgrim/claude-code-android/blob/v2.5.1/docs/story.md)（2026-03） |
| 该记录里的真实故障 | `/tmp` 不可写；**Node.js v24 在 Termux/ARM64 上挂起（无报错、无栈）**；`/data/data/com.termux/files/` 而非 `/usr/bin`；32 位 ARM 设备（很多廉价三星机）**不支持**（Claude Code 要 arm64）；两个"静默失败"（裸敲 `claude` 无提示） | `[已验证-自述]` 同上 |
| 长期无人值守可用性 | **未证实**。上面这份记录是"人在手机上交互式写代码 24 小时–数周"，不是"7×24 无人值守收发邮件"；没有找到 phone-only 常驻 agent 的连续运行统计 | `[未证实]` |

**工程结论**：手机做 daemon 的失败模式是**静默 SIGKILL**（不是崩溃、没有异常），而邮件助手的语义是"每封信都要处理"——两者叠加会产生**丢信**。可行的形态只有两种：

1. **常驻跑在 VPS/家用机上**（systemd/docker + 健康检查 + 重启），手机只是客户端（收推送、发指令）。这与"多设备共享会话"天然一致：**状态在服务端，设备无状态**。
2. **手机本地跑，但只做"呈现 + 本地提醒 + 采集"**（这一条与 [`60-runtime.md`](../docs/60-runtime.md) §"手机端"的现有判断一致）。

---

## 5. "缓存命中"与"会话可迁移"到底冲突吗

### 5.1 关键洞察：两者不在同一层，所以不冲突

| | 缓存 | 可迁移性 |
|---|---|---|
| 位置 | **DeepSeek 服务端硬盘**（分布式硬盘阵列），不属于任何进程/设备 | 本地文件 / 对象存储 / 数据库 |
| 键 | `(账号, user_id, 模型, 请求前缀字节)` | 会话 id / 文件路径 |
| 生命周期 | 小时到天，自动清 | 由你决定，可永久 |
| 谁能破坏它 | 只有"下一次请求的字节流不同"或"过期" | 删文件、丢盘、换格式 |

→ **换设备、换进程、换 IP 都不会命中失败**，只要新设备**重建出同样的字节前缀**。所以"手机和电脑处理同一条会话"在缓存层面是免费的。

**这修正了 [`60-runtime.md`](../docs/60-runtime.md) §5.3 的一句推论**：原文说"为每条会话固定一个 dsh session id，从而吃到前缀缓存"。实际上，**固定 session id 不是命中的必要条件**，只是"保证字节流 append-only"的一种最省事手段。反例：`dsh --profile headless` 每次新会话，只要渲染出的 `system + tools + workspace` 头部字节一致，第二次起就能命中"公共前缀单元"（§2.4）。**推论：缓存收益可以在"每次新会话"的架构下拿到**，这对"每封信触发一次 Agent"的设计是好消息。

### 5.2 真正冲突的两处

| 冲突 | 机制 | 代价（可量化） |
|---|---|---|
| **压缩 / 摘要 vs 字节稳定** | 摘要把前缀中段替换掉，之后全 miss。这是"可迁移"最常用的手段（重建上下文靠摘要），也是"长期会话"最常用的手段（compaction） | 一次 40K tokens 前缀的全 miss = 0.04 元（flash 空闲价）；40K→1M 级前缀 = 1 元/次。**频率**才是问题，不是单次 |
| **多写者 vs 前缀唯一** | 两台机同时推进同一会话 → 产生两个不同前缀 → 服务端两个单元 → 两边**交替 miss**，且状态分叉（谁最后写谁赢，另一方的工作丢失） | 命中率可能掉到 ~0，且是**静默**的（不报错，只是变贵、变蠢） |

其他"看起来冲突其实不冲突"的项：换设备 ✅、换进程 ✅、换 harness（只要渲染字节一致）✅、换 `temperature` ✅（文档明确不影响缓存）。

### 5.3 建议的请求布局：稳定前缀 + 动态尾部

一条会话的请求按"变更频率"分三段，字节冻结的严格程度依次放松：

| 段 | 内容 | 变更频率 | 要求 | 备注 |
|---|---|---|---|---|
| **A. 冻结段**（越长越好） | 宪法/`AGENTS.md`、工具定义（顺序固定）、输出格式约定、记忆**索引**（不是全文） | 随代码版本变（周/月） | 逐字节冻结；内容 hash 当版本号；**禁**时间戳/cwd/设备名/动态计数 | 目标：占输入的 50–70% |
| **B. 半稳定段** | 工作区文件快照（按固定顺序、固定换行）、既有会话摘要（**固定产物**，不是每次重新生成） | 天/会话 | 允许变，但"变就整段重算"，且**只在段边界变** | 用"文件清单 + 内容 hash"决定是否需要刷新整段 |
| **C. 动态段**（越短越好） | 本封信、当前时间、本轮状态、待办 | 每请求 | 永远在最后 | 只有这一段该变 |

对应的"命中率天花板"估算（自算，基于 §2.4 的单元机制）：

| 场景 | 可命中长度 | 命中率 |
|---|---|---|
| 同一天内连续 N 封信，A+B 不变，只换 C | 到"上一封信请求的结束位置" | **≈95–99%**（只有 C 与上一封的 C 不重合处 miss） |
| 冷启动后第 1 封 | 0（单元还没落盘） | 0% |
| 冷启动后第 2–3 封起 | A（已落盘为公共前缀单元） | 50–70% |
| 迁移/重建后第 1 封 | 0（B 变了 + 摘要重写） | 0%，一次 |
| 两台机并发写 | 双向交替失效 | 0–50% |

**取舍建议（按优先级）**：

1. **账本与缓存分离**，即现方案：`session.md` + `memory/` 是唯一真相，harness 会话/服务端缓存都是可丢弃的加速层。**不要**为缓存牺牲可迁移性——反过来才是对的。
2. **冻结 A 段并用 hash 版本化**。这是唯一"既提命中率又不伤可迁移性"的动作：A 段越稳定、越长，重建时越容易逐字节复原（因为 A 段本来就是从仓库文件渲染出来的）。
3. **B 段做成"可重建但稳定"**：会话摘要**写一次就冻住**（带 `generated_at` 与 `source_range`），下一次只在追加区间上"增量补摘要"，不要每次重写全文。这是把"压缩"从"破坏前缀"变成"追加"的唯一办法。
4. **C 段永远最后**，并接受"每封信都要付一次 C 的 miss"（2K tokens ≈ 0.002 元）。
5. **单写者租约**：会话目录放 `.lock`（或 git 分支 + 提交串行化），拿不到租约的设备只读。宁可"手机不能写"，不要"两台机同时写"。
6. **迁移预算化**：把"迁移后第一次请求全 miss"当作已知成本（40K 前缀 ≈ 0.04 元 flash），不要为省这 4 分钱引入复杂机制。

### 5.4 一个"够用就好"的判据

- 若命中率 ≥ 90%：优化空间只有 ~10 元/月量级，**停止优化缓存**，去优化输出长度与错峰（空闲时段直接半价，收益比"抠最后 9 个点"更大）。
- 若命中率 < 50%：几乎一定是 A/B 段里有"伪稳定"内容（时间戳、cwd、工具动态计数、每次重写的摘要）。按 §3.3 的断点定位表逐条排除。
- 若命中率在 0% 且 `prompt_cache_miss_tokens == prompt_tokens` 恒成立：先怀疑**解析层**（§2.7 的 #61871）与 **`user_id` 不一致**，再怀疑缓存本身。

---

## 6. 未证实 / 卡住的地方（诚实清单）

| # | 问题 | 卡在哪 |
|---|---|---|
| 1 | 缓存键的精确构成（`temperature`、`thinking`、`tools` 是否进键） | 官方文档从未给出 key schema，只说"前缀"与"用户隔离" |
| 2 | Anthropic 格式 `/anthropic/messages` 与 Responses API 是否回传 hit/miss | 兼容性文档只列字段支持情况，没有 usage 章节 |
| 3 | `deepseek-chat` / `deepseek-reasoner` 是否仍可用、是否支持缓存 | 2026 定价页只列两个新模型名；旧名状态无官方说明 |
| 4 | 是否存在"手动失效/查看缓存"的运维接口 | 文档只有"自动清空"，无 API |
| 5 | 手机端 7×24 无人值守跑 LLM Agent 的可靠性统计 | 只找到交互式使用的自述记录，没有连续运行/丢信率数据 |
| 6 | Doze 维护窗口的具体间隔与对 HTTPS 轮询的实际影响 | 未抓到 AOSP/官方数字（本可查 `developer.android.com` Doze 文档，本轮未取） |
| 7 | Cursor 聊天记录的精确落盘位置与云端比例 | 论坛只确认"不跨设备"，没有官方存储说明 |
| 8 | 缓存命中对 `deepseek-v4-pro` 的延迟收益是否与 flash 同量级 | 只有 2025 年旧的 13s→500ms 数据（V2/V3 时代） |

---

## 7. 来源清单（全部 2026-10-25 抓取）

**DeepSeek 官方文档**（经 `thevibeworks/deepseek-docs` 逐字镜像，镜像自述抓取日见括号）
1. 上下文硬盘缓存 — https://api-docs.deepseek.com/zh-cn/guides/kv_cache （镜像 2026-10-17）
2. 模型 & 价格 — https://api-docs.deepseek.com/zh-cn/quick_start/pricing （镜像 2026-10-17）
3. 创新采用硬盘缓存（含 64 tokens、TTL、0.1 元历史价） — https://api-docs.deepseek.com/zh-cn/news/news0802 （镜像 2026-08-23）
4. Chat Completions API（usage schema、`user_id` 与 KVCache 隔离） — https://api-docs.deepseek.com/zh-cn/api/create-chat-completion （镜像 2026-09-29）
5. 使用 Anthropic API（`cache_control` 被忽略） — https://api-docs.deepseek.com/zh-cn/guides/anthropic_api （镜像 2026-10-17）
6. Tool Calls（工具定义与对话中间插 tool/system 的端点差异） — https://api-docs.deepseek.com/zh-cn/guides/tool_calls （镜像 2026-10-17）

**社区实测 / 踩坑**
7. hermes-agent #105236：6 个 cache break 根因、前缀布局、断点残留量表 — https://github.com/NousResearch/hermes-agent/issues/105236
8. hermes-agent #61871：只读 `prompt_tokens_details.cached_tokens` 导致命中率恒 0 — https://github.com/NousResearch/hermes-agent/issues/61871
9. dshbase：harness 成本分析（99.93% 命中、¥19 vs ¥900、7x 差） — https://dshbase.com/blog/deepseek-harness-cost/ （2026-08-14）

**Harness 会话存储**
10. Claude Code — Persist sessions to external storage — https://code.claude.com/docs/en/agent-sdk/session-storage
11. Claude Code — Work with sessions（跨机三条路、compaction、`~/.claude/projects` 布局） — https://code.claude.com/docs/en/agent-sdk/sessions
12. Codex CLI — `codex resume` — https://mintlify.wiki/openai/codex/cli/resume
13. 复制单个 Codex 会话到另一台机器 — https://zonca.dev/posts/2025-12-16-copy-codex-session
14. OpenHands — Resume Conversations — https://docs.openhands.dev/openhands/usage/cli/resume
15. Aider — Options reference（`--restore-chat-history`、`--cache-keepalive-pings`、历史文件默认名） — https://aider.chat/docs/config/options.html
16. Cursor 论坛 — 跨设备同步仍为 feature request — https://forum.cursor.com/t/is-cross-device-sync-possible/147030

**Android**
17. Phantom / Cached / Empty Processes（32 进程上限、5 分钟 CPU 检查、`settings_enable_monitor_phantom_procs`） — https://raw.githubusercontent.com/agnostic-apollo/Android-Docs/master/en/docs/apps/processes/phantom-cached-and-empty-processes.md
18. Termux Wiki — `termux-wake-lock` — https://wiki.termux.com/wiki/Termux-wake-lock
19. claude-code-android（Android 16 实跑记录与故障清单） — https://github.com/ferrumclaudepilgrim/claude-code-android/blob/v2.5.1/docs/story.md

**本工作区**
20. [`docs/60-runtime.md`](../docs/60-runtime.md) §5.3：harness 会话只当缓存、不当账本；`dsh --session-id` 实测行为
21. [`docs/30-workspace-memory.md`](../docs/30-workspace-memory.md)：工作区文件与记忆索引的体积约束
