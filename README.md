# Mephisto

> 手机是入口，家里那台电脑是唯一执行节点。
> dsh 是执行器（不是产品形态）——Mephisto 借它的工具、DeepSeek 的契合度与高缓存命中，
> 把"发一句话 → 真的在电脑上干活 → 手机上看到结果"做成一条可靠链路。

---

## 现在是什么状态

| 部分 | 状态 | 标签 |
|---|---|---|
| 服务端（Python 3.14，**零第三方依赖**） | 可用：HTTP API + SSE、队列、提醒、MCP 工具 | `step-0` … `step-6` |
| 安卓客户端（Kotlin/Compose） | 可用：会话列表、对话、发送/打断、提醒与闹钟 | `step-7` … `step-10` |
| 测试 | 服务端 **201** 用例、安卓 **103** 用例（JVM） | — |
| 端到端完整测试 | ❌ 未走完 | — |
| 独立网络（移动数据） | ❌ 未复验 | — |

对外入口：`https://mephisto.xcfx.com`（Cloudflare Tunnel → 本机 `127.0.0.1:8787`）。

---

## 仓库

| 仓库 | 内容 |
|---|---|
| 本仓库 | 服务端：`mephisto/` + `tests/` + `docs/` + `contracts/` |
| [Mephisto-Android](https://github.com/miaki03/Mephisto-Android) | 客户端：Compose App + `tools/` 构建脚本 |

两个仓库按步号对齐（服务端 `step-0…6`，客户端 `step-7…10`），一步一个 annotated tag。

---

## 30 秒上手

```powershell
# 体检 + 测试
python -m mephisto doctor
python tests\run.py

# 起服务（会同时起 worker 与调度器）
$env:DSH_HOME = (Resolve-Path state\dsh-home).Path
python -m mephisto serve --port 8787

# 发一个令牌给手机（只显示一次）
python -m mephisto device add --name 手机

# 建一条提醒
python -m mephisto remind "3分钟后 喝水"
```

客户端：`cd E:\study\Mephisto-Android; tools\build.ps1 -Task installDebug -Install`。

---

## 文档怎么读

**先读这两份，能覆盖 90% 的日常：**

| 文档 | 用途 |
|---|---|
| [docs/INDEX.md](docs/INDEX.md) | **模块与函数索引**：改东西前先在这里定位，不用通读代码 |
| [docs/STATE.md](docs/STATE.md) | 进度台账：现在在哪一步、环境事实、已定决策 |

**按角色选读：**

| 你要做什么 | 读 |
|---|---|
| 继续开发 / 规划下一步 | [docs/AGENT-BRIEF.md](docs/AGENT-BRIEF.md)（任务书）→ [docs/99-implementation-plan.md](docs/99-implementation-plan.md) |
| 设计客户端 UI | [docs/CLIENT-CONTRACT.md](docs/CLIENT-CONTRACT.md)（职责与逻辑边界） |
| 联调 / 测真机 | [docs/TEST-PLAN.md](docs/TEST-PLAN.md)（分级测试与配合清单） |
| 理解某块设计的原因 | `docs/00–90-*.md`（架构、传输、会话、提醒、运行器、安卓…） |

---

## 结构

```
mephisto/
├─ domain/        纯规则，零 IO（信封、会话、通知、提醒、值对象）
├─ app/           编排（一轮对话、守护进程、调度器、时间解析）
├─ store/         SQLite 读写（13 张表，约束写在 DDL）
├─ adapters/      http（17 端点 + SSE） / runner（dsh 子进程） / mcp（stdio）
├─ ports.py       Source / Sink / Runner 三条副作用边界
├─ blobs.py       内容寻址附件 + 分片拼装
├─ cli.py         doctor / db / device / run / serve / remind / mcp
└─ config.py      根目录从 pyproject.toml 向上查找；路径全部相对
```

铁律：**状态在文件/数据库里，不在框架内**；幂等靠数据库唯一约束，不靠"记得判重"；
判成功看退出码，不看事件流里有没有 `final`。

---

## 已知限制（写出来，免得当成 bug）

- 回复是**整段**到达（服务端事件是提交级的，没有逐 token 流）
- 同一会话不能并行两轮——第二条会排队，等第一条结束后合并执行
- 客户端只有**打开过的**会话才有"最近一条预览"（预览来自本地缓存）
- 没有消息分页（接口只有 `since_seq` 升序，没有 `before`）
- 附件上传客户端未实现（服务端接口已就绪）
- 邮件通道搁置（`step-14` 计划，未排期）
