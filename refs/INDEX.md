# refs · 上游调研副本

> 这些文件是**只读副本**，原件在别处。改动请改原件，再重新复制。
> 复制时间：2026-10-25。

## 来源与用途

| 副本 | 原件 | 用途 |
|---|---|---|
| `chatlog/*.md` | `E:\study\research\chatlog\*.md` | 微信（WeFlow）/ QQ 四条路线的技术内核调研 |
| `wxread/{README,core-facts,decisions}.md` | `E:\study\wxread\` | 自持微信读取器的实际能力、实测事实 F1–F10、决策 D1–D6 |

**没有复制的东西**（体积或形态不适合）：

| 原路径 | 内容 | 怎么用 |
|---|---|---|
| `E:\study\research\chatlog\evidence\` | 19 MB / 75 个原始证据文件（日志、DLL 导出表、asar 解包、真机目录枚举） | 抽查时直接去原件目录 |
| `E:\study\research\chatlog\snippets\` | 7 个可跑取证脚本 | 同上 |
| `E:\study\wxread\tools\`、`_decrypted\` | 采集器实现与明文副本 | 直接跑，不要复制（明文副本应当留在原地并在用完后清理） |
| `E:\study\research\_repos\WeFlow` | WeFlow 源码快照（固定 commit） | 对照源码行号时用 |

## chatlog 副本的读法

| 文件 | 一句话 | 优先级 |
|---|---|---|
| `README.md` | 十条结论速览 + 未解问题清单 | **先读** |
| `00-baseline.md` | 环境、commit、本机能力边界（哪些结论是实测、哪些是源码推断） | 读，避免误判可信度 |
| `50-weflow-expiry.md` | 四层过期机制（写死日期、策略版本号、180s grant、在线令牌） | **决策必读**：它决定了 WeFlow 不能当主路径 |
| `90-risks.md` | 分平台风险：法律、吊销、隐私（聊天正文外发）、破坏性副作用 | **决策必读** |
| `80-field-mapping.md` | 微信/OneBot/官方 Bot 三平台字段对照 + 归一化最小字段集（11 必需 + 4 可选） | 写归一化前必读 |
| `60-qq-landscape.md` | QQ 四条路线能力矩阵 | 要做 QQ 时读 |
| `72-qq-local-recon.md` | 本机 QQ 库实测判定 | 想走本地库时读 |
| `20-weflow-datamodel.md` | 真实库布局、`localType` 复合值、zstd/hex/XML 编码 | 要解析微信消息时读 |
| `30-weflow-http-api.md` | 16 条路由、SSE、鉴权 | 只作参照（该服务已过期） |
| `40/41/70/71` | ABI、密钥提供者、NapCat、NTQQ 本地库细节 | 深挖时读 |
| `55-weflow-persistence.md` | "读取持久化"的四层分解 | 想自持读取时读 |
| `10-weflow-arch.md` | 进程/worker 结构、实时管道 | 一般不需要 |
| `qq-kernel-facts-report.md` | 子代理生成的外部调研（已并入正文） | 备查 |

## wxread 副本的读法

| 文件 | 一句话 |
|---|---|
| `README.md` | 能读真实群消息；三条命令；与 WeFlow 的唯一耦合是 passphrase 来源 |
| `core-facts.md` | F1–F10：分片按时间滚动、`sort_seq` 才是水位、压缩负载在 `message_content`、性能数字 |
| `decisions.md` | D1 语言=Python、D2 SQLite+手写 SQL、D3 全量入库、D4 先 LIKE 不上 FTS、D5 数据落地、D6 为 QQ 预留 `channel` |

**三条最容易写错的**（写代码前先记）：

1. 一个会话可以跨分片，必须 `glob message_*.db` 取并集，且编号不代表新旧（`message_0` 才是最新）。
2. `local_id` 有逆序，**不能当水位**；水位用 `sort_seq = create_time*1000+ms`。
3. 正文在 `message_content`（`WCDB_CT_message_content==4` 时是 zstd bytes），不是 `compress_content`（恒为空）。
