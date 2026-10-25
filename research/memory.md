# 生产级 LLM Agent 的长期记忆管理：架构研究与选型建议

> 调研对象：单用户、单机、文件系统共享的个人助理 Agent（下文简称"我方场景"）。
> 调研时间：2026-10-25。所有文档类来源均为当日抓取的线上版本（版本号/日期在表格中标注），论文类来源标注 arXiv 提交或修订日期。
> 结论强度约定：`[已验证]` = 直接读到原始文档/论文原文；`[未验证]` = 未找到一手材料，或仅从二手线索推断。

---

## 0. 结论先行

| # | 结论 | 依据 |
|---|---|---|
| 1 | **2026 年的行业共识已经从"向量库+图谱"部分回摆到"文件系统 + 索引 + 按需 grep"**。Letta（MemGPT 的商业继任者）在 2026 年把核心记忆整体迁移到 `MemFS`——一个 git 版本化的 Markdown 记忆仓库，默认**没有**语义/向量索引，靠文件搜索和 read 工具找记忆。 | [Letta MemFS 文档](https://docs.letta.com/concepts/memfs)，抓取 2026-10-25 |
| 2 | **"每轮抽取 + UPDATE/DELETE"这个曾经的主流写法正在被放弃**。mem0 在 2026-04 的新算法把抽取改成 **single-pass ADD-only**，不再覆盖/删除，冲突交给检索期排序 + 时间信息处理。 | [mem0 README"New Memory Algorithm (April 2026)"](https://github.com/mem0ai/mem0)，抓取 2026-10-25；[v2→v3 迁移文档](https://docs.mem0.ai/migration/platform-v2-to-v3) |
| 3 | **真正难的不是存储，是"写入时机 + 索引卫生 + 冲突语义"**。所有成熟系统都在工程上加了同样的三件东西：写入门槛（不是每轮都写）、后台整理（dreaming / consolidation / audit）、以及索引体积的硬上限。 | 见 §3、§4.3 |
| 4 | **公开 benchmark 数字基本不能用于选型**。Zep 自己的论文里，最朴素的 full-context 基线在 DMR 上拿到 98.0%，比 Zep 的 98.2% 只差 0.2 个点；论文作者自己写"DMR 不足以评估记忆系统"。 | [Zep 论文 §4.2 表 1](https://arxiv.org/html/2501.13956v1)，2025-01-20 |
| 5 | **既有设计（文件 + 索引 + grep + 只追加 + 人格只读）方向正确，不需要图数据库**；本报告补 4 处：索引超限硬失败、记忆体检命令、每条事实的机器可读新鲜度、先建 20–50 条评估集。 | §6.0、§6.5；[`docs/30-workspace-memory.md`](../docs/30-workspace-memory.md) |

---

## 1. 方法说明与局限

- 只读一手材料：论文 arXiv 原文/HTML、各项目官方文档与 README、标准站点（agents.md、sqlite.org）。未采信二手博客的性能数字。
- **同时把本工作区已有的设计文档与实测记录当作一手材料**（[`docs/30-workspace-memory.md`](../docs/30-workspace-memory.md)、[`refs/wxread/decisions.md`](../refs/wxread/decisions.md)），凡与外部证据冲突处已在 §6.0 标出。
- **没有跑任何 benchmark**，本文所有性能数字都是"引用他人自报"，不构成对我方场景的预测。
- 文档类材料变动很快（本文抓到的最典型例子：mem0 的 README 与迁移文档对同一套新算法给出两组不同分数，见 §2.2）。引用时请以日期为准。
- 中文检索部分缺少权威一手材料（SQLite 官方文档只描述 tokenizer 机制，不讨论 CJK 后果），相关结论标注为推断或"未验证"。

---

## 2. 主流架构逐个解剖

### 2.1 MemGPT → Letta：从"OS 分层"到"git 仓库"

**它要解决什么**：上下文窗口装不下长期对话历史；且"摘要压缩"是有损的。MemGPT 的类比是把 LLM 当 OS，把上下文当 RAM，把外部存储当磁盘，让模型自己通过工具在两层之间搬运数据（virtual context management），并用"中断"控制与用户的控制流交接。`[已验证]`（[MemGPT 论文](https://arxiv.org/abs/2310.08560)，v1 2023-10-12 / v2 2024-02-12）

**经典时期的机制**（MemGPT 论文与 Letta v1 文档）：
- 主上下文 = system instructions + working context（可编辑的 core memory）+ FIFO 消息队列；外部上下文 = recall storage（历史消息，可搜索）+ archival storage（任意长文本，向量检索）。`[已验证-术语]`
- core memory 以 **memory blocks** 形式存在：带 label、带字符/token 上限、可 attach 给多个 agent 共享；典型两块是 `persona`（我是谁）与 `human`（用户是谁）。`[已验证]`（[Letta memory blocks 文档](https://docs.letta.com/v1-sdk/memory/memory-blocks)，抓取 2026-10-25）
- **sleep-time compute**：后台 agent 在空闲时重写记忆块，把"整理记忆"从热路径挪走。

**2026 年的实际形态（关键变化）**：Letta 现在的记忆是 **MemFS**，一个属于 agent 的 git 仓库，投影到 agent 运行的那台机器上作为普通目录，agent 用**普通文件工具**读写：`[已验证]`（[MemFS 文档](https://docs.letta.com/concepts/memfs)）

```
$MEMORY_DIR/
├── MEMORY.md          # 索引
├── persona.md         # 根目录文件 → 每轮进 system prompt
├── human.md
├── reference/         # 有自己 MEMORY.md 的子目录 → 不进 context，按需读
│   ├── MEMORY.md
│   └── project-notes.md
└── skills/
    └── my-skill/SKILL.md
```

规则非常明确，值得直接抄：
- 除 `MEMORY.md` 外，每个记忆是带 YAML frontmatter（`name` / `description`）的 Markdown。
- **根目录文件每一轮都进 system prompt**；**带自己 `MEMORY.md` 的子目录默认不进 context**，目录名和文件名本身充当"路标"。
- **默认没有语义/向量索引**；要找记忆就用文件搜索 + read。想要关键词/向量/混合检索，需另装 `memfs-search` mod（关键词无依赖，语义模式依赖外部 QMD 索引）。
- 每次编辑都 commit（版本历史 + 冲突解决 + "已保存 vs 未提交"的清晰边界）；后台记忆子 agent（dreaming、memory doctor）用 **git worktree** 并发改记忆而不阻塞主 agent。
- `[已验证]` "dreaming" 就是 sleep-time compute 的现名：后台 subagent 复查最近会话、合并教训、更新记忆；触发条件可配（每 N 步，或上下文被压缩时）；可选"agent 先自查再应用"（多花 token，不打扰用户）。`/init` 初始化、`/remember` 显式写入、`/doctor` **审计"摆放位置、重复、system prompt token 占用"**。见 [Memory & dreaming 文档](https://docs.letta.com/configuration/memory)。

**代价**：
- 检索退化为"模型自己会不会找"。Anthropic 明确指出这条路的代价："运行时探索比直接取预算好的数据慢，且需要精心设计工具与启发式，否则 agent 会浪费 context 走死路"（[context engineering 文章](https://www.anthropic.com/research/effective-context-engineering-for-ai-agents)，抓取 2026-10-25）。
- 靠 git 做并发与回滚，意味着记忆目录最好在单机单写者场景；多写者需要额外协调（Letta 用 worktree 解决，我方场景用不到）。
- 云端版本还有仓库体积上限（Free 5 MiB / Pro 50 MiB 每个记忆仓库）。我方本机不受限，但**体积上限的存在本身就是个设计信号：记忆不该存大文件**。

---

### 2.2 mem0：从"抽取-更新-删除"到"单遍追加"

**它要解决什么**：多轮/多会话对话里的"该记什么"，并显著降低 token 成本。`[已验证]`

**2025 年的设计（论文，v1）**：动态地 extract → consolidate → retrieve；图记忆变体把会话元素间的关系建成图。自报在 LOCOMO 上 LLM-as-a-Judge 相对 OpenAI 记忆高 26%，p95 延迟低 91%，token 省 90%+，图版本比基础版整体高约 2%。`[已验证-自报]`（[arXiv:2504.19413](https://arxiv.org/abs/2504.19413)，2025-04-28）

**2026 年的设计（重大反转）** `[已验证]`（[迁移文档](https://docs.mem0.ai/migration/platform-v2-to-v3)，[Add Memory 文档](https://docs.mem0.ai/core-concepts/memory-operations/add)）：

| 维度 | 旧（v2 及以前） | 新（2026-04 起） |
|---|---|---|
| 抽取 | 两次 LLM pass（extract + merge） | **一次 LLM pass，ADD-only** |
| 记忆变更 | ADD / UPDATE / DELETE | **只有 ADD，不覆盖不删除** |
| agent 自己说过的事实 | 常被丢弃 | 一等公民，与用户陈述同权 |
| 图记忆 | 需外部图数据库 + 手工配置 | 内置：抽实体、跨记忆连边，只用于检索打分 |
| 检索 | 纯向量 | **语义 + BM25 关键词 + 实体匹配 多信号融合**，另有时间感知排序 |

官方给出的理由（值得记住，因为它直接反驳了"合并/去重越多越好"的直觉）：当事实变化时（"从纽约搬到旧金山"），**两个事实都保留 + 带时间上下文**，于是能区分"曾经住纽约"和"现在住旧金山"，"看似矛盾实则互补的事实不再丢失"；记忆条数会**持续增长而不是收敛**，相关性交给检索期排序解决；如果应用依赖 UPDATE/DELETE 控制条数，改用 **expiration_date**（显式过期，过期后 search/get_all 默认隐藏）。`[已验证]`

**代价与坑**：
- **记忆条数单调增长**，没有"衰减"就只有"过期"这一个粗粒度阀门。对单用户长期使用的个人助理，这迟早要一个整理器（见 §3.2）。`[推断]`
- 抽取质量完全押在一次 LLM pass 上，且默认依赖历史消息自动回填上下文做指代消解（"he"→"Biscuit"）——离线/无历史时抽取会退化。`[已验证]`
- **自报数字不稳定**：README 表格写 LoCoMo 92.5 / LongMemEval 94.4，同日抓到的迁移文档写 91.6 / 93.4，且 README 注明"分数反映托管平台，含开源 SDK 没有的专有优化"。这说明①数字随版本漂移；②开源复现不会一致。`[已验证]`
- 全部是"托管服务 + SDK"形态；我方要单机离线，只能借鉴机制，不能直接用。

---

### 2.3 Zep / Graphiti：时序知识图谱 + 双时间轴

**它要解决什么**：RAG 面向静态语料；企业记忆需要"结构化业务数据 + 会话数据"持续融合，并且**保留历史**（"当时为真"vs"现在为真"）。`[已验证]`

**真实机制**（[Zep 论文](https://arxiv.org/html/2501.13956v1)，2025-01-20，§2–§3）：
- 三层子图：**episode 子图**（原始消息/文本/JSON，非有损的 ground truth）→ **语义实体子图**（实体 + 事实边，多实体事实用超边）→ **community 子图**（标签传播做社区发现，社区名做检索入口）。
- **双时间轴（bi-temporal）**：`t` 轴（事件在世界上发生的时间）与 `t'` 轴（数据被系统摄入的时间）。每条事实边存四个时间戳：`t'_created`、`t'_expired`（系统侧）、`t_valid`、`t_invalid`（世界侧）。
- **边失效而非删除**：新边进来后，LLM 把它与语义相关的已有边比对找矛盾；发现**时间区间重叠的矛盾**时，把旧边的 `t_invalid` 设为新边的 `t_valid`；冲突裁决一律偏向新信息（按 `t'` 轴）。历史完整保留。
- **检索**：`f(α)=χ(ρ(φ(α)))` —— 召回（cosine 向量 + BM25 + 图上 BFS 多跳）→ 重排（RRF / MMR / 提及频率 / 图距离 / cross-encoder）→ 拼装成带日期区间的文本块（`FACT (Date range: from - to)`）。
- 工程细节：实体解析前先做 embedding + 全文双路候选召回，再用 LLM 判重；Cypher 用预定义模板而非 LLM 生成，以"保证 schema 一致、降低幻觉"。

**代价**：
- 每次写入要跑多个 LLM 调用（实体抽取、实体消解、事实抽取、事实消解、时间抽取），**写入成本远高于读取**；论文里 ingestion 并发默认被限流就是为了躲 429（[Graphiti README](https://github.com/getzep/graphiti)）。
- 必须运维图数据库（Neo4j / FalkorDB / Neptune）；Graphiti README 明确说参考实现"无认证、无授权、无 prompt injection 防护，不要暴露到不可信网络"。
- 强依赖结构化输出能力，"小模型/本地模型经常输出不合 schema 的 JSON，表现为抽取失败"（同上 README）。**我方若用本地小模型，这条路风险很高。**

**benchmark 真实性**（这条最重要）：
- DMR：MemGPT 93.4% → full-conversation 基线 **94.4%（gpt-4-turbo）/ 98.0%（gpt-4o-mini）** → Zep 94.8% / 98.2%。**Zep 只赢朴素全量上下文 0.2 个点**，且论文自己写"每个会话仅 60 条消息，轻松装进现有上下文窗口"，"这些问题只考单轮事实检索，且很多问法含糊"。`[已验证]`
- LongMemEval：Zep 相对 full-context 提 15.2%（gpt-4o-mini）/ 18.5%（gpt-4o），上下文 token 从 115k 降到 1.6k，延迟从 ~30s 降到 ~3s。**这是 Zep 真正的卖点：不是更准，是同样准的情况下便宜 70 倍。**`[已验证]`

---

### 2.4 A-MEM：Zettelkasten 式"记忆演化"

**机制**：新记忆进来时生成结构化笔记（上下文描述、关键词、标签），然后 LLM 分析历史记忆、建立链接；**新记忆还能反向更新旧记忆的属性与上下文表示**（memory evolution）。`[已验证]`（[arXiv:2502.12110](https://arxiv.org/abs/2502.12110)，v1 2025-02-17，v11 2025-10-08，NeurIPS 2025）

**代价**：写入时要做"找相关历史 + 建链 + 可能改写旧笔记"，是**写入期 O(历史) 的 LLM 开销**，且改写旧记忆会让审计变难（"这条记忆是谁在什么时候改的？"）。学术上有效，工程上对单机个人助理属于过度设计。`[推断]`

---

### 2.5 LangMem：把"热路径 vs 后台"讲得最清楚的一套 API

**机制**：提供两套并列能力 `[已验证]`（[LangMem README](https://github.com/langchain-ai/langmem)，抓取 2026-10-25）：
- **hot path**：`create_manage_memory_tool` / `create_search_memory_tool`，agent 在对话中自己决定何时写、何时搜；
- **background memory manager**：自动抽取、合并、更新；
- 另有 prompt 优化（把行为教训写回 system prompt）——这是罕见的"记忆 = 人格/指令"分支，见 §3.5。
- 后端抽象在 LangGraph 的 `BaseStore`（namespace + 向量索引），生产要用 Postgres 版。

**价值**：它把"写入时机"这件事显式拆成两条路径并给出统一抽象，是所有系统里最值得抄的**接口设计**。代价是绑 LangGraph 生态。

---

### 2.6 Cognee：ECL 管线 + "一条 Postgres 跑全栈"

**机制**：`remember` / `recall` / `improve` / `forget` 四个动词；文本变成实体-关系-可检索块，代码变成符号依赖图；会话内教训通过 `improve` 蒸馏进长期图。检索按策略路由（图 / 向量 / 代码）。`[已验证]`（[Cognee README](https://github.com/topoteretes/cognee)，v1.6.1，2026-09-24）

**与我方场景有关的两点**：
- **它明确有"单用户 / 本地"姿势**：`ENABLE_BACKEND_ACCESS_CONTROL=false` 是 "single-user/local posture"，不开就是多租户、每个请求都要认证用户。这个开关的存在说明"单用户"是个必须显式声明的一等场景。`[已验证]`
- **它把"记忆交换格式"做成了产品**：支持从 Mem0 / Letta / Zep / Graphiti 导入（COGX 格式）。`[已验证]` —— 我方若自己做记忆，**尽早定一个纯文本、可 diff 的导出格式**是低成本高回报。

**代价**：抽象层很厚（管线、图库、向量库、MCP server、UI），自报 BEAM 分数还附带"两档使用不同会话/不同模型/不同检索流程、不可直接比较"的免责声明。个人助理直接用它，维护成本大于收益。`[推断]`

---

### 2.7 MemoryOS：OS 式三级存储

**机制**：短/中/长三级存储单元；短→中用**对话链 FIFO**，中→长用**分段页组织**；四个模块 = 存储、更新、检索、生成；检索用分段页 + 热度。自报 LoCoMo 上 GPT-4o-mini 平均 F1 +49.11%、BLEU-1 +46.18%。`[已验证-自报]`（[arXiv:2506.06326](https://arxiv.org/abs/2506.06326)，2025-05-30）

**评价**：这是"分页 + FIFO + 热度"这套 OS 隐喻做得最完整的学术实现。但对单用户个人助理，**三级存储的分界线在哪里、什么时候晋级，是没有客观标准的超参**；实际运维中很容易变成"热点条目永远留在中层、冷条目永远不晋级"。`[推断]`

---

### 2.8 横向对比

| 系统 | 记忆载体 | 写入时机 | 冲突处理 | 检索 | 致命代价（我方视角） |
|---|---|---|---|---|---|
| MemGPT/Letta | **git 仓库里的 Markdown 文件**（2026） | 显式 `/remember` + 后台 dreaming | 覆盖 + git 历史 | 文件搜索，可选关键词/向量 mod | 靠模型会不会找；索引会腐化（有 `/doctor` 兜） |
| mem0 | 向量库 + 内置实体图 | 每次 `add` 一次 LLM pass，**只追加** | **不解决**，交给检索期 + `expiration_date` | 语义+BM25+实体 多信号融合 | 条数单调增长；托管服务，不能离线自持 |
| Zep/Graphiti | 时序 KG（Neo4j/FalkorDB） | 每条 episode 多次 LLM 调用 | **边失效不删除**（4 时间戳） | 向量+BM25+BFS 多路召回 + 重排 | 写入贵、要图库、依赖结构化输出 |
| A-MEM | 笔记 + 链接网络 | 每次写入建链 + 改写旧笔记 | 演化式改写 | 链接遍历 + 向量 | 写入开销随历史线性增长，审计困难 |
| LangMem | 任意 store（LangGraph） | **热路径 or 后台，显式二选一** | 交给 manager prompt | 向量 | 绑 LangGraph |
| Cognee | 图 + 向量 + 会话 | 管线（remember/improve） | 图更新 | 策略路由 | 抽象过厚，个人场景过度 |
| MemoryOS | 三级分页 | FIFO + 分段页晋级 | 热度更新 | 分页 + 热度 | 分界超参无客观标准 |

---

## 3. 文件式记忆在编码类 Agent 里的成熟做法

这一节是本文对我方场景最直接有用的部分。

### 3.1 指令文件的加载语义：CLAUDE.md / AGENTS.md 的真实差异

`[已验证]`（[Claude Code memory 文档](https://code.claude.com/docs/en/memory)，抓取 2026-10-25；[agents.md](https://agents.md)，抓取 2026-10-25）

| 维度 | CLAUDE.md（Claude Code） | AGENTS.md（开放格式） |
|---|---|---|
| 作用域层级 | 组织策略 → 用户 `~/.claude/CLAUDE.md` → 项目 `./CLAUDE.md` 或 `./.claude/CLAUDE.md` → 本地 `CLAUDE.local.md` | 仓库根 + **子目录嵌套**（OpenAI 主仓在写此文时已有 88 个 AGENTS.md） |
| **多文件冲突语义** | **全部拼接，不互相覆盖**；从根到当前工作目录排序，越近的越晚读到 | **"离被编辑文件最近的那个赢"**；"用户聊天里的显式指令压倒一切" |
| 实际后果 | 文档自己承认："如果两条指令矛盾，Claude 可能任意选一条" | 声称有确定的优先级 |
| 子目录文件 | 启动时**不加载**，直到 Claude 读/写/编辑了该子目录里的文件才加载 | 声称"自动读最近的" |
| 注入位置 | **作为 user message 放在 system prompt 之后**，不是 system prompt 本身 → "没有严格遵循的保证" | — |

**这是第一个大坑**：两个"事实标准"对"优先级"的承诺不一致。跨工具复用同一份记忆文件时，**不要依赖任何优先级语义**——要么保证全局无矛盾，要么在文件里显式写"本节优先级最高"。

其它已验证的实用细节：
- 大小建议：**每个 CLAUDE.md 目标 < 200 行**（"更长会占更多 context 并降低遵循度"）。超过 4 MiB 的文件**整份跳过不加载**。
- `@path` 导入：递归最多 **4 跳**；导入**不会**降低 context 成本（导入文件和导入者一起进 context）——只有"路径作用域规则"和"skills"才真正省 context。
- 块级 HTML 注释 `<!-- -->` 在注入前被剥离 → 可以给人类留维护笔记而不花 token。
- 压缩（`/compact`）后**项目根 CLAUDE.md 会被重新从磁盘读取并重注入**；嵌套的和 path 作用域的要等再次命中才回来。

### 3.2 自动记忆与"一行式索引"（最值得抄的一处）

Claude Code 的 auto memory 是一套完整、可直接抄的**文件式自动记忆**设计 `[已验证]`：

```
~/.claude/projects/<project>/memory/
├── MEMORY.md           # 索引，一条记忆一行，每轮加载
├── user_role.md        # 一条记忆一个文件
├── feedback_testing.md
└── ...
```

| 规则 | 具体值 | 为什么重要 |
|---|---|---|
| 索引加载上限 | **前 200 行 或 25KB，先到者为准** | 索引体积有硬预算 = 腐化有上限 |
| 超限行为 | 写入仍成功，但**返回错误要求重写索引**，因为下次加载时超限部分会被丢弃 | 把"索引腐化"变成**显式失败**而不是静默降级 |
| 接近上限 | 主动提醒"一条一行、细节移到 topic 文件、合并或删除过期条目" | 有维护提示 |
| topic 文件 | **启动时不加载**，靠标准文件工具按需读 | 渐进披露 |
| frontmatter `type` | `user` / `feedback` / `project` / `reference` 四类 | 分类决定"该不该写"，不是装饰 |
| `modified` 字段 | 带 frontmatter 的写入会被自动打上 ISO 8601 时间戳 | 记忆自己携带新鲜度 |
| 不写什么 | "能从代码库推导的（架构、路径、调试修复）不写"；"CLAUDE.md 已写的不写" | **写入门槛**的具体化 |
| 不是每轮都写 | "Claude 不会每个会话都保存；它按'未来是否还有用'决定" | 见 §4.1 |
| 保留策略 | 会话 transcript 会按 `cleanupPeriodDays` 清理，**memory 目录被排除在清理之外** | 记忆与会话日志解耦 |

配套的**索引审计**（两套系统都有，说明这是刚需）：
- Claude Code：`/doctor prompt-audit` 检查"为旧模型写的指令、引用了不存在的文件或命令、文件之间互相矛盾"，出报告+建议改动，不确认不改；`/doctor` 还会指出"本地 CLAUDE.md 与签入版本重复"、"Claude 能从代码库推导出的内容"，并提议迁到 skills / 嵌套文件。`[已验证]`
- Letta：`/doctor` 审计"摆放位置、重复、system prompt token 占用"。`[已验证]`

### 3.3 progressive disclosure 的三级结构

Anthropic 把渐进披露讲成了可操作的规范 `[已验证]`（[Equipping agents with Agent Skills](https://www.anthropic.com/research/equipping-agents-for-the-real-world-with-agent-skills)，抓取 2026-10-25，正文含 2025-12-18 更新）：

| 层级 | 内容 | 何时进 context | 成本量级 |
|---|---|---|---|
| 1 | `name` + `description`（frontmatter） | **启动时全部预加载**进 system prompt | 每 skill 数十 token |
| 2 | `SKILL.md` 正文 | 模型判断相关时用工具读取 | 通常 < 5k token |
| 3+ | 目录内的 `reference.md` / `forms.md` / 脚本 | 模型自己决定去读 | "理论无上限" |

关键论断（可直接引用在架构文档里）：
- "Skill 的 body 只在被使用时加载，所以**长参考材料在被需要之前几乎不花成本**。"（[Claude Code skills 文档](https://code.claude.com/docs/en/skills)）
- "用文件系统 + 代码执行的 agent，不需要把 skill 全文读进 context……因此**能打包进一个 skill 的 context 量实际上是无限的**。"（Anthropic 同上）
- 反面：`CLAUDE.md` → 事实；`skill` → 过程。"如果某条内容是多步流程、或只在某部分代码库有用，就移到 skill 或路径作用域规则里。"

**这条对我方场景直接成立**：把"用户是谁/怎么说话"留在常驻层，把"这个项目怎么部署/上次那个报错怎么修的"做成按需读的文件。

### 3.4 `.cursor/rules`：把"什么时候加载"写成 frontmatter 矩阵

`[已验证]`（[Cursor Rules 文档](https://cursor.com/docs/rules)，抓取 2026-10-25）

| `alwaysApply` | `description` | `globs` | 行为 |
|---|---|---|---|
| `true` | — | — | 总是包含（忽略 globs 和 description） |
| `false` | — | 有 | 当上下文里有匹配文件时自动挂载 |
| `false` | 有 | 无 | Agent 读 description，自行判断相关性后拉入 |
| `false` | 无 | 无 | 只有 `@` 提及时才包含 |

工程细节坑：`.cursor/rules/` 里**纯 `.md` 文件会被忽略**（因为没 frontmatter 表达 `description`/`globs`/`alwaysApply`）；想用纯 markdown 就用 `AGENTS.md`。Cursor 同时支持 project / user / team 三种规则作用域。

**可抄的点**：用四个字段（是否常驻、描述、路径模式、是否只能手动触发）就能表达"加载策略"的全部组合，且**默认不是常驻**——这是控制上下文预算最省事的方法。

### 3.5 memory bank / NOTES.md：最土但最有效

Anthropic 的表述 `[已验证]`："结构化笔记 = agent 把笔记写到 context 之外的持久存储里，之后再拉回来……像 Claude Code 建 todo list，或者你的自定义 agent 维护一个 `NOTES.md`"；Claude 玩 Pokémon 的例子说明：**没有给任何记忆结构提示**，agent 自己演化出地图、成就、战术笔记，并在上下文重置后读自己的笔记继续多小时的训练。这就是"给文件工具 + 一个可写的工作笔记文件"的最小可行方案。

### 3.6 文件式记忆的四个坑（按危害排序）

1. **上下文预算被常驻层吃掉**。所有系统都有硬限额：CLAUDE.md < 200 行、auto memory 索引 200 行/25KB、记忆仓库 5/50 MiB。Anthropic 的原则："找**最小的、高信号的 token 集合**"；且 context rot 是实测现象——token 越多，从 context 里准确回忆的能力越低（引 [trychroma context-rot](https://research.trychroma.com/context-rot)）。
2. **索引腐化（silent rot）**。索引行数只增不减 → 触及上限 → **超出部分在下次加载时被静默丢弃**。Claude Code 的对策是"写超限就报错要求重写索引"；这是**唯一正确的处理方式**：宁可让写入失败，也不要让加载静默降级。
3. **冲突覆盖**。三种语义在不同实现里都出现过：拼接（Claude Code，可能任意选一条）、就近覆盖（AGENTS.md）、时间戳失效（Zep）。**同一份记忆被多个工具读时，"就近覆盖"是最危险的**，因为它让上层文件凭空失效。
4. **检索的假阳性/假阴性**。grep 对**精确词**极强（代码标识符、项目名、人名），对**同义词/改写/跨语言**几乎无效。索引行是唯一的"语义入口"，所以**索引行必须写成一句完整的、包含用户可能用的词的句子**，而不是一个名词短语。`[推断，但有 Anthropic "名称/路径/时间戳都是信号"的支撑]`

---

## 4. 关键机制的具体做法

### 4.1 写入时机：四种策略，都用过

| 策略 | 谁在用 | 优点 | 代价 |
|---|---|---|---|
| **每轮写**（hot path，模型自己决定） | LangMem `create_manage_memory_tool`；mem0 每次 `add` | 不丢信息，无需额外调度 | 每轮多一次 LLM 调用；易产生大量低价值条目；打断主任务 |
| **显式命令写** | Letta `/remember`；Claude Code "把这条加到 CLAUDE.md" | 质量最高、可控 | 依赖用户/模型主动；会漏 |
| **会话结束/压缩时写** | Claude Code（压缩后重读根 CLAUDE.md）；Letta dreaming 可配"上下文被压缩时"触发 | 成本摊薄，天然有时机 | 会话崩了就丢；压缩点不固定 |
| **后台定期整理**（sleep-time / dreaming / consolidation） | Letta dreaming（每 N 步或压缩时，可配"先自查再应用"）；LangMem background manager；MemoryOS 晋级 | 不阻塞主流程；可做全局去重与重组 | 需要额外调度与模型调用；**整理器自己会引入错误**，所以 Letta 提供"应用前复查"开关 |

**已验证的取舍表述**（Anthropic）：compaction 是"第一个杠杆"，但"过度激进的压缩会丢掉当时看不出重要性的细节"；建议"先把 compaction prompt 调到**最大化召回**，再迭代提升精度"。

**我方建议**：显式命令（一等公民）+ 会话结束/压缩点（默认）+ 后台整理（可关）。**不要每轮写**。

### 4.2 合并 / 去重 / 冲突解决

三条路线，本报告认为**应当组合使用，且"解决"的责任应尽量后移到读取期**：

| 路线 | 机制 | 代表 | 适用 |
|---|---|---|---|
| A. 覆盖/更新 | 抽取时与已有条目比对，决定 ADD/UPDATE/DELETE | mem0 v2（**已废弃**） | 条目少、事实稳定 |
| B. 追加 + 时间有效性 | 只追加；每条事实带"何时开始为真/何时失效"；矛盾时**失效而非删除** | **Zep（4 时间戳）**；mem0 v3（只追加 + 检索期排序） | 事实会变（住址、偏好、项目状态） |
| C. 后台重组 | 定期由 LLM 合并重复、拆大文件、重排层次；重组前先备份 | Letta（重组前备份仓库）、Claude Code `/doctor` | 条目多、索引开始腐化 |

已验证的实施细节：
- Zep 的失效操作非常精确：新边与"语义相关且时间重叠且矛盾"的旧边比对，把旧边 `t_invalid` 设为新边 `t_valid`；冲突裁决**偏向新信息**（按摄入顺序）。`[已验证]`
- mem0 的反面教训也很有信息量：旧算法会在抽取时 UPDATE/DELETE，新算法判断这会**丢掉"看似矛盾实则互补"的事实**，因此改成只追加。`[已验证]`
- Claude Code 给的"人工兜底"约定：`AGENTS.md` 式的"用户聊天里的显式指令压倒一切"是最终的仲裁规则——**任何自动冲突解决都应该让位于用户当场的显式指令**。`[已验证]`

**我方建议**：文件里每条记忆一个"状态行"，形如 `- [fact] 用户常驻城市=杭州（since 2026-03，superseded: 上海 2024-2026）`。不删除旧值，只标 superseded。读取时常驻层只显示 current，全文 grep 仍能看到历史。

### 4.3 遗忘 / 衰减

**学界最经典的两个机制**：
- **Ebbinghaus 遗忘曲线**：MemoryBank 按"经过时间 + 记忆相对重要性"决定遗忘与强化（每次召回后强化）。`[已验证]`（[arXiv:2305.10250](https://arxiv.org/abs/2305.10250)，2023-05）
- **重要性 × 新鲜度 × 相关性**联合打分：Generative Agents 的 memory stream 检索"combines relevance, recency, and importance"。`[已验证]`（[arXiv:2304.03442](https://arxiv.org/abs/2304.03442)，2023-08）

**工程界的实际做法要朴素得多**：
- mem0：**没有衰减，只有显式 `expiration_date`**；过期条目默认从 search/get_all 隐藏，按 id 仍可取回。`[已验证]`
- Claude Code / Letta：**没有自动遗忘**；靠"写入门槛 + 索引上限 + 审计命令 + 人工删除"。`[已验证]`
- Zep：**从不删除**，只失效（可查历史）。`[已验证]`

**判断**：衰减曲线在"记忆条目是海量、噪声多"的场景（对话流、陪伴类）有价值；在**单用户、写入受控、条目是人工确认过的教训**的场景里，衰减是负收益——它会把一年前定下的稳定偏好悄悄磨掉。**我方场景应选"不衰减 + 失效标记 + 定期人工审计"。** `[推断]`

### 4.4 检索：grep / 向量 / 混合，以及中文的坑

| 方案 | 强项 | 弱项 |
|---|---|---|
| grep / ripgrep | 精确词、标识符、路径、零依赖、可解释、毫秒级 | 同义改写、跨语言、模糊语义一概不行；结果随目录增长而噪声上升 |
| 向量 | 语义近似、跨表述 | 需要 embedding 模型与索引维护；精确词（错误码、人名）反而容易漏；索引会与文件内容不一致（"stale index"） |
| 混合（向量 + BM25 + 实体/图） | 目前各家一致选择 | 工程复杂度、需要调融合权重、需要重排 |

已验证的立场：
- **Anthropic 明确选了"混合"**：CLAUDE.md"天真地直接塞进上下文"，而 glob/grep 用于 just-in-time 取文件，从而"绕过了 stale index 和复杂语法树的问题"。这是主动放弃向量索引的理由，不是简化。`[已验证]`（context engineering 文章）
- **Letta MemFS 默认无向量索引**，要装 mod 才有；关键词模式零依赖，语义/混合依赖外部 QMD。`[已验证]`
- **mem0 v3 与 Zep 都做多信号融合**（语义 + BM25 + 实体；语义 + BM25 + BFS）。`[已验证]`

**中文的坑（两层，必须分开看）**：

1. **词法层（tokenizer）**——这一层在本工作区已有实测，不必再猜：
   - SQLite FTS5 默认 tokenizer 是 `unicode61`（"基于 Unicode 6.1"）；另有 `trigram` tokenizer，"把每三个连续字符当作一个 token，从而支持一般化的子串匹配"（[SQLite FTS5 文档](https://sqlite.org/fts5.html)，抓取 2026-10-25）。
   - **[本工作区实测]** "FTS5 的 `trigram` 分词器**匹配不了 2 字中文**（实测 `公告` 命中 0 条）；`unicode61` 对无空格中文把整段当一个 token，同样不行"；同时"89k 行全表 `LIKE` = **32 ms**，够用"。见 [`refs/wxread/decisions.md` D4](../refs/wxread/decisions.md)。这与 SQLite 文档的机制完全一致（trigram 需要 ≥3 个字符才能构成一个 token，2 字查询无从匹配）。
   - 社区侧同现象：`basic-memory` issue #1294 标题即 "CJK full-text search broken — hardcoded unicode61 tokenizer doesn't segment CJK"；`NousResearch/hermes-agent` issue #14829 标题即 "FTS5 unicode61 tokenizer silently drops CJK characters"。（仅读到标题，正文 `[未验证]`。）
   - 可行修法（按工程量排序）：① **不用 FTS5，直接子串扫描**（`LIKE` / `ripgrep`）——对本场景 10³–10⁴ 条记忆是微秒到毫秒级，最简单且已被实测证明够用；② 写入期用 `jieba` 预分词 + 空格分隔，再喂 `unicode61`（WeFlow 之所以要带 `jieba-wasm` 就是这个原因，见 D4）；③ `trigram` 只有在查询词 ≥3 字符时才可用，**不能作为中文主检索**。
2. **语义层**：中文的"同义改写"比英文更依赖上下文，grep 更难命中。工程解法不是上向量，而是**一张别名/同义词字典**（把"报销/报账/费用"指到同一 slug），本工作区已在 [`docs/30-workspace-memory.md` §9](../docs/30-workspace-memory.md) 采用。中文 embedding 的召回质量 `[未验证]`——本文没有找到可靠的对比评测。

**我方建议**：**文件名 + 索引行承载语义 + 子串扫描承载匹配 + 别名字典补召回**；只有在条目过万、且**召回失败率被真实统计出来**之后，才加分词 FTS 或向量 rerank。不要一上来就上向量库。

### 4.5 记忆与人格（persona）分离

这是所有成熟系统都做了、但很少被明确讲出来的一条。

| 系统 | 分离方式 | 证据 |
|---|---|---|
| MemGPT/Letta | 早期：core memory 的 `persona` 块 vs `human` 块（两个不同 label，可分别设字符上限） | [memory blocks 文档](https://docs.letta.com/v1-sdk/memory/memory-blocks) |
| Letta 2026 | `persona.md`（"我是谁、我看重什么、我怎么和人合作"）与 `human.md`、根 `MEMORY.md` 并列，都是"每轮进 system prompt"的根文件；路径即语义（"选一个 label 就是选它住在哪、以及它是否常驻 context"） | [MemFS 文档](https://docs.letta.com/concepts/memfs) |
| Claude Code | 不写人格；auto memory 用 `type: user` 记录"用户的角色、专业水平、工作偏好"，`type: feedback` 记录"用户给的纠正"——**把"用户画像"和"纠正记录"分成两类** | [memory 文档](https://code.claude.com/docs/en/memory) |
| LangMem | 唯一把"人格修改"做成产品能力的：prompt optimizer 可以把教训**写回 system prompt**（行为改变），而不仅是存事实 | [LangMem README](https://github.com/langchain-ai/langmem) |

**为什么必须分离**（三条已验证的支撑）：
1. **遵循度不同**：CLAUDE.md 是被当作 user message 注入的，"没有严格遵循的保证"；所以"必须遵守的规则"不该和"关于用户的事实"混在一个文件里，否则两者一起被弱化。要强制就得用 hook / 权限规则。`[已验证]`
2. **更新频率不同**：人格/口吻几个月不变；用户事实每周变。混在一起会导致"改一条偏好顺手改坏了人格"。
3. **审计需求不同**：人格需要人工审阅（它就是产品定义），事实需要自动整理。

**我方建议**：物理分文件 —— `persona.md`（agent 是谁，人工维护，只读）、`user.md`（用户画像，低频变）、`rules.md`（硬约束，最短）、`memory/*.md`（事实与教训，可自动写）。前三个由人改，最后一个允许 agent 写。

---

## 5. 可直接抄的现成结果（按性价比排序）

### 5.1 值得直接抄

| # | 结果 | 抄什么 | 来源 |
|---|---|---|---|
| 1 | **MemFS 目录结构 + 路径即策略** | 根文件常驻、子目录靠自己的 `MEMORY.md` 做闸门；`persona.md` / `human.md` / `reference/` / `skills/` 的命名与层次 | [Letta MemFS](https://docs.letta.com/concepts/memfs) |
| 2 | **一行式索引 + 硬上限 + 超限报错** | `MEMORY.md` 一条一行；"前 200 行或 25KB"；**超限时写入报错要求重写索引**；接近上限时提醒合并/删除 | [Claude Code memory](https://code.claude.com/docs/en/memory) |
| 3 | **frontmatter schema** | `name` + `description`（索引/触发用）+ `type`（`user`/`feedback`/`project`/`reference`）+ `modified`（ISO 8601，自动打）+ `paths`（作用域 glob） | 同上；[Cursor rules](https://cursor.com/docs/rules) |
| 4 | **三级渐进披露** | 层 1 只放 `name`+`description`；层 2 是正文；层 3 是目录内参考文件与脚本 | [Anthropic Agent Skills](https://www.anthropic.com/research/equipping-agents-for-the-real-world-with-agent-skills) |
| 5 | **四个加载开关** | `alwaysApply` / `description` / `globs` / 仅手动 —— 四字段矩阵覆盖所有加载策略，且默认非常驻 | [Cursor rules](https://cursor.com/docs/rules) |
| 6 | **双时间戳的事实条目** | 每条事实带 `valid_from` / `invalid_from`（世界时间）+ `created_at`（写入时间）；矛盾时**失效不删**，冲突偏向新信息 | [Zep 论文 §2.2.3](https://arxiv.org/html/2501.13956v1) |
| 7 | **审计命令** | 一个 `/doctor`：查重复、查死链（引用了不存在的文件/命令）、查"agent 能自己推导因而不该写"的内容、查常驻层 token 占用、查互相矛盾 | Claude Code `/doctor prompt-audit` + Letta `/doctor` |
| 8 | **整理前先备份、整理可"先自查再应用"** | 重组前备份仓库；后台整理器给出的改动可选"由另一个会话复查后再应用" | [Letta Memory & dreaming](https://docs.letta.com/configuration/memory) |
| 9 | **记忆交换格式（可 diff 的纯文本导出）** | 别自定义二进制格式；Cognee 专门做了 COGX 来从 Mem0/Letta/Zep/Graphiti 导入，说明这是真实需求 | [Cognee README](https://github.com/topoteretes/cognee) |
| 10 | **评估方法学** | LongMemEval 的五能力切分（信息抽取 / 多会话推理 / 时间推理 / 知识更新 / **拒答**）+ "indexing → retrieval → reading"三阶段拆解框架；LOCOMO 的规模（300 轮/9K token/35 会话） | [LongMemEval](https://arxiv.org/abs/2410.10813)（ICLR 2025）；[LoCoMo](https://arxiv.org/abs/2402.17753) |

**特别推荐抄"拒答"这一项**：LongMemEval 把"信息不在记忆里时应拒绝编造"作为五项核心能力之一。个人助理最常见的失败不是"记不住"，而是"半记错 + 自信编造"。任何自建评估都必须含这一项。

### 5.2 过度设计（我方场景明确不做）

| 做法 | 为什么不做 |
|---|---|
| 时序知识图谱 / 图数据库（Zep、Graphiti） | 单用户记忆是"事实清单"不是"实体关系网"；要图库、要多次 LLM 抽取、依赖结构化输出。论文自己的数据表明收益主要在**降 token/降延迟**，而这些在本地文件场景本来就不存在 |
| bi-temporal 四时间戳 | 只需要两个（`valid_from`、`superseded_at`）；`t'` 轴（摄入时间）在 git 提交历史里天然存在 |
| A-MEM 式"记忆演化"（新记忆改写旧记忆） | 写入开销随历史线性增长；破坏审计；对个人助理无可证明收益 |
| MemoryOS 三级存储 + FIFO 晋级 | 分界线无客观标准；实际会退化成"热点永远在中层" |
| 每轮 LLM 抽取（mem0 hot path 全自动） | 每轮一次调用，长期产生大量低价值条目；且 mem0 自己已经把它从"多 pass 更新"简化到"单 pass 追加" |
| 社区/主题摘要层（GraphRAG、Zep community） | 语料规模不够，摘要层维护成本 > 收益 |
| 自动衰减曲线（Ebbinghaus） | 会把稳定偏好磨掉；用"失效标记 + 人工审计"替代 |
| 中文分词 + FTS5 自建索引（在条目 < 1000 时） | ripgrep 子串扫描已够；真需要再加（`trigram` 优先，见 §4.4） |

---

## 6. 对我方场景的推荐组合

**前提约束**：单用户、单机、文件系统共享、记忆条目量级 10²–10⁴、无多租户、可接受毫秒级检索、希望记忆能被人和别的工具直接读写。

> **重要**：本工作区已有成文设计 [`docs/30-workspace-memory.md`](../docs/30-workspace-memory.md)（目录、分层、写入时机、预算、夜间整理、grep 检索、人格边界）。本节不是另起一套，而是**用外部一手证据去校验那套设计**，只列出"被证实 / 需补 / 需改"的差异。§6.1–6.3 保留一份"若从零开始"的备选布局供对照。

### 6.0 与既有设计的对照（结论：主体成立，补充建议见 §6.5）

| 既有设计的决定 | 外部一手证据 | 判定 |
|---|---|---|
| `persona/` 只读，只有用户能改；拟人主动放弃 | Letta 用 `persona.md`/`human.md` 作为"每轮进 system prompt 的根文件"，且 `name`/`description` 与 memory 分层；Claude Code 明确 CLAUDE.md 是"context 不是强制配置"，要强制得用 hook/权限 | **成立**，且"人格只读"比 Letta 更严——这是对的 |
| `memory/` 只追加，冲突并列 + 标〔已失效〕 | Zep 的边失效机制（矛盾时 `t_invalid = 新边 t_valid`，不删）是同一思想的强化版 | **成立**，只需两个时间点，不需要四时间戳 |
| `MEMORY.md` ≤200 行，每行"标题 + 一句话" | Claude Code auto memory 索引硬上限"前 200 行或 25KB"，且**超限写入直接报错要求重写索引** | **成立但缺失败模式**，见 §6.5-1 |
| 检索用 `grep` + `aliases.yaml`，不上向量 | Anthropic 的 just-in-time + glob/grep 立场；Letta MemFS 默认无向量索引；本工作区 89k 行 `LIKE` = 32 ms 实测 | **强支持**，是目前最省的选择 |
| 夜间整理**不调大模型**、全规则化 | mem0 v3 相反（只追加、相关性交给检索期）；Letta dreaming 相反（用 subagent + worktree） | **成立**：单机每日跑，成本必须近零且可审计；但 0.85 阈值仍需真实 `inbox/` 数据校准（文档自己也标了） |
| `inbox/` 作为候选缓冲（Agent 只能说"值得记"） | LangMem 的 hot-path 工具让 agent 直接写，代价是噪声；mem0 每轮 `add` 同理 | **成立**，缓冲层是写入质量的关键闸门 |
| 每轮固定开销 ≤ 8k token，降级顺序写进代码 | Anthropic"最小的高信号 token 集合" + context rot（token 越多回忆越差） | **成立** |

### 6.1 备选布局（仅作对照，若从零开始）

```
<workspace>/mephisto/memory/
├── MEMORY.md              # 索引：一条一行，硬上限 200 行 / 25 KB
├── persona.md             # agent 是谁（人工维护，只读）
├── user.md                # 用户画像（低频，人工+整理器）
├── rules.md               # 硬约束（最短，最先读）
├── projects/
│   ├── MEMORY.md          # 该目录的闸门索引 → 默认不加载
│   └── <slug>.md          # 每个项目一份：目标、约定、坑、上次进度
├── lessons/
│   ├── MEMORY.md
│   └── <topic>.md         # 教训/feedback，type: feedback
└── archive/
    └── <yyyy-mm>.md       # 失效条目归档（不删，只移）
```

- 根下 4 个文件常驻；`projects/` `lessons/` `archive/` 靠各自的 `MEMORY.md` 做闸门。
- 每个 `.md` 带 frontmatter：`name` / `description`（一句话，含用户可能用的词）/ `type` / `modified`。
- **索引行写成完整句**：`- projects/mephisto.md — 个人助理 agent 的架构决策、模块边界与部署方式`（而不是 `- mephisto`）。

### 6.2 六个核心机制的取舍

| 机制 | 做法 | 代价 |
|---|---|---|
| 常驻层预算 | 4 个根文件合计硬上限（建议 6k token，约 200 行）；超限报错而非静默截断 | 需要写一个检查器；超限时得有人改 |
| 写入时机 | ① 用户显式"记住"→ 立即写；② 会话结束/压缩点 → 整理器写；③ 会话中 agent 自评"这条未来还有用且代码/环境推不出来"才写（默认关） | ②要调度；③容易滥写，要配额 |
| 冲突 | 只追加 + `valid_from` / `superseded_at`；矛盾偏向新信息；旧值移 `archive/` 不删 | 文件会变长，需要归档动作 |
| 遗忘 | **不衰减**；只做过期（`expired_at`）与归档；每季度人工审计一次 | 需要人参与 |
| 检索 | 层 1：常驻 4 文件；层 2：`ripgrep` 文件名 + 索引行 + 正文；层 3：条目 > 1000 且实测 recall 不足时，加 `trigram` FTS5 或向量 rerank | 层 2 依赖索引行质量 |
| persona 分离 | `persona.md` / `rules.md` 由人改，训练/整理器**不允许**写；`user.md` 与 `memory/*` 允许整理器写 | 少一点"自适应"魔法 |

### 6.3 明确不做

- 不做图数据库、不做四时间戳、不做每轮抽取、不做自动衰减、不做记忆演化。
- 不做多租户/鉴权（Cognee 的 `ENABLE_BACKEND_ACCESS_CONTROL=false` 就是这个立场）。

### 6.4 分阶段

| 阶段 | 内容 | 验收 |
|---|---|---|
| P0（1 天） | 目录 + frontmatter + `MEMORY.md` 索引 + 加载器（常驻 4 文件 + 上限检查 + 超限报错） | 能启动、能 grep、超限会失败 |
| P1（2–3 天） | 写入路径（显式 / 会话末）+ 冲突状态行 + 归档 | 造 20 条冲突事实，检索能区分 current / superseded |
| P2（1 周内） | `/doctor` 式审计（重复、死链、可推导内容、常驻 token 占用、互相矛盾）+ 后台整理器（可关） | 审计能稳定报出人为植入的 4 类问题 |
| P3（按需） | 自建评估集（LongMemEval 五能力 + 拒答，20–50 条）+ 检索升级（trigram / 向量 rerank） | 先有 baseline，再决定要不要升级检索 |

### 6.5 对既有设计的六条具体补充（按价值排序）

| # | 建议 | 依据 | 代价 |
|---|---|---|---|
| 1 | **索引超限必须硬失败**：`MEMORY.md` 超过 200 行/预算时，写入要报错并要求重写索引，而不是写入成功、下次加载静默丢尾部 | Claude Code 的原话是这个行为："写入仍成功，但返回错误要求重写索引，因为超过上限的一切会在下次加载时被丢弃" | 一次检查函数 + 一个错误路径；收益是把"索引腐化"从静默故障变成显式故障 |
| 2 | **加一个"记忆体检"命令**（对应 `/doctor`）：查重复条目、死链（引用了不存在的 `people/x.md`）、"agent 自己能推导因而不该写"的内容、常驻层 token 占用、同类条目互相矛盾 | Claude Code `/doctor prompt-audit` 与 Letta `/doctor` 都在做这件事，两套独立产品收敛到同一功能 | 一次性写检查脚本；建议每周人工跑一次 |
| 3 | **每条事实带机器可读的新鲜度**：行尾 `（2026-10-24）` 之外，再在文件 frontmatter 记 `modified`（ISO 8601），由写入器自动打；常驻层只渲染 current 值，`superseded` 只出现在全文里 | Claude Code 对带 frontmatter 的记忆写入自动打 `modified`；Zep 把 `t_valid`/`t_invalid` 拼进检索上下文（`FACT (Date range: from - to)`） | 几乎为零 |
| 4 | **先建 20–50 条自有评估集，再谈检索升级**：按 LongMemEval 的五能力（信息抽取 / 多会话推理 / 时间推理 / 知识更新 / **拒答**）各造几条，用真实历史会话提问 | [LongMemEval](https://arxiv.org/abs/2410.10813)（ICLR 2025）把"信息不在记忆里时拒绝编造"列为五项核心能力之一 | 半天工作量；避免"凭感觉上向量库" |
| 5 | **定一个纯文本、可 diff 的导出格式**（`memory/` 整体就是，但要把字段规范写死：`slug / type / source / valid_from / superseded_at`） | Cognee 专门做 COGX 从 Mem0/Letta/Zep/Graphiti 导入，说明"换实现不丢记忆"是真实需求 | 一页规范 |
| 6 | **`AGENTS.md` 预算参考值**：外部实践是"每个指令文件 < 200 行"（Claude Code）、"导入最多 4 跳且导入不省 context"、"根文件进 system prompt"（Letta）。1.5k token ≈ 200–300 行中文，量级合理；但**真正"永不裁剪"的应该只有 `persona/` 与 `rules`**，工具约定可以降级 | 同上两处文档 | 无 |

**评估先行于检索升级**：没有 20 条以上的自有用例，任何"加向量库"的决定都是猜。这是本文最想强调的一条工程建议，也和既有文档"别在没数据时重开"的判断一致。



---

## 7. 未验证 / 需要后续确认

| # | 事项 | 状态 |
|---|---|---|
| 1 | 中文在 `unicode61`/`trigram` 下的失效模式 | **已由本工作区实测覆盖**（[`refs/wxread/decisions.md` D4](../refs/wxread/decisions.md)：trigram 匹配不了 2 字中文、unicode61 把整段无空格中文当一个 token、89k 行 `LIKE` = 32 ms）。外部一手文档只说机制、不谈 CJK 后果；社区 issue 仅见标题 `[未验证]` |
| 2 | jieba 分词在本场景（10³ 条短记忆）的实际收益 | `[未验证]` —— 无对比评测；D4 给出的升级路径是"真需要时"再做 |
| 3 | 中文 embedding 模型在"个人记忆检索"上的召回质量 | `[未验证]` —— 无一手评测 |
| 4 | A-MEM 的"记忆演化"在长期（> 6 个月）使用后是否会导致记忆漂移 | `[未验证]` —— 论文未做长周期实验 |
| 5 | MemoryOS 三级晋级阈值的敏感性 | `[未验证]` —— 本次未读全文，未见到消融细节 |
| 6 | Cursor 是否提供"自动记忆"（非 rules）能力 | `[未验证]` —— 本次抓取的 docs 页面在 AGENTS.md 一节被截断，未见相关章节 |
| 7 | mem0 两套分数（92.5/94.4 vs 91.6/93.4）哪个是最终口径 | `[未验证]` —— 同日抓取的 README 与迁移文档不一致，未找到勘误 |
| 8 | Anthropic 托管记忆工具（memory tool，`/memories` 目录）的限额与淘汰策略 | `[未验证]` —— 只在 context engineering 文章里看到"基于文件系统的 memory tool 公测"，未找到限额文档 |
| 9 | `docs/30` 里的 0.85 相似度阈值、90 天归档是否合适 | `[未验证]` —— 文档自己已标"待定"，需真实 `inbox/` 数据校准 |

---

## 8. 来源清单

| 来源 | URL | 日期 |
|---|---|---|
| **[本工作区]** 工作区与记忆设计 | [`docs/30-workspace-memory.md`](../docs/30-workspace-memory.md) | 2026-10 |
| **[本工作区]** 中文检索实测（D4） | [`refs/wxread/decisions.md`](../refs/wxread/decisions.md) | 2026-10 |
| MemGPT 论文 | https://arxiv.org/abs/2310.08560 | v1 2023-10-12 / v2 2024-02-12 |
| Generative Agents 论文 | https://arxiv.org/abs/2304.03442 | v1 2023-04-07 / v2 2023-08-06 |
| MemoryBank 论文 | https://arxiv.org/abs/2305.10250 | 2023-05-17 |
| LoCoMo 论文 | https://arxiv.org/abs/2402.17753 | 2024-02-27 |
| LongMemEval 论文 | https://arxiv.org/abs/2410.10813 | v1 2024-10-14 / v2 2025-03-04（ICLR 2025） |
| Zep 论文 | https://arxiv.org/abs/2501.13956 | 2025-01-20 |
| Zep 论文 HTML（§2.2.3 双时间轴、§4 实验） | https://arxiv.org/html/2501.13956v1 | 2025-01-20 |
| A-MEM 论文 | https://arxiv.org/abs/2502.12110 | v1 2025-02-17 / v11 2025-10-08（NeurIPS 2025） |
| mem0 论文 | https://arxiv.org/abs/2504.19413 | 2025-04-28 |
| MemoryOS 论文 | https://arxiv.org/abs/2506.06326 | 2025-05-30 |
| Letta · MemFS | https://docs.letta.com/concepts/memfs | 抓取 2026-10-25 |
| Letta · Memory & dreaming | https://docs.letta.com/configuration/memory | 抓取 2026-10-25 |
| Letta · memory blocks（v1 SDK） | https://docs.letta.com/v1-sdk/memory/memory-blocks | 抓取 2026-10-25 |
| Graphiti README | https://github.com/getzep/graphiti | 抓取 2026-10-25 |
| mem0 README（April 2026 算法） | https://github.com/mem0ai/mem0 | 抓取 2026-10-25 |
| mem0 · Add Memory | https://docs.mem0.ai/core-concepts/memory-operations/add | 抓取 2026-10-25 |
| mem0 · v2→v3 迁移 | https://docs.mem0.ai/migration/platform-v2-to-v3 | 抓取 2026-10-25 |
| LangMem README | https://github.com/langchain-ai/langmem | 抓取 2026-10-25 |
| Cognee README | https://github.com/topoteretes/cognee | v1.6.1，2026-09-24（抓取 2026-10-25） |
| Claude Code · 记忆与 CLAUDE.md/auto memory | https://code.claude.com/docs/en/memory | 抓取 2026-10-25 |
| Claude Code · Skills | https://code.claude.com/docs/en/skills | 抓取 2026-10-25 |
| Anthropic · Effective context engineering | https://www.anthropic.com/research/effective-context-engineering-for-ai-agents | 抓取 2026-10-25 |
| Anthropic · Equipping agents with Agent Skills | https://www.anthropic.com/research/equipping-agents-for-the-real-world-with-agent-skills | 抓取 2026-10-25（正文含 2025-12-18 更新） |
| AGENTS.md 标准 | https://agents.md | 抓取 2026-10-25 |
| Cursor · Rules | https://cursor.com/docs/rules | 抓取 2026-10-25 |
| SQLite FTS5（tokenizer / trigram） | https://sqlite.org/fts5.html | 抓取 2026-10-25 |
| Chroma context-rot（经 Anthropic 引用） | https://research.trychroma.com/context-rot | 未直接抓取 `[未验证]` |
| basic-memory issue #1294（CJK） | https://github.com/basicmachines-co/basic-memory/issues/1294 | 仅见标题 `[未验证]` |
| hermes-agent issue #14829（CJK） | https://github.com/NousResearch/hermes-agent/issues/14829 | 仅见标题 `[未验证]` |
