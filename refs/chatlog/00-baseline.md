# 00 · 基线（环境 / 版本 / 源码快照 / 验证方式）

> 本文是全部后续文档的前提。任何引用源码行号的结论，都对应下面这个固定 commit。

## 1. 源码快照

| 项 | 值 | 证据 |
|---|---|---|
| 主仓库 | `https://github.com/hicccc77/WeFlow` | `evidence/github-repo-meta.json` |
| 克隆 commit | `fb58afa9876c6b334887a33471dd5f0077eedec4` | `evidence/00-baseline.txt` |
| commit 时间 | `2026-10-20 16:05:46 +0900` | 同上 |
| commit 标题 | `docs: remove the Alpha announcement` | 同上 |
| 分支 | `main` | 同上 |
| **仓库总 commit 数** | **10** | 同上 |
| `git status --porcelain` | 空（工作区干净） | 同上 |
| `package.json` name / version | `weflow` / `5.0.0` | `package.json:2-3` |
| 追踪文件数 / 工作区体积 | 554 个 / 47.4 MB | `evidence/00-baseline.txt` |

克隆位置（本机）：`E:\study\research\_repos\WeFlow`。**只读使用，未修改任何文件**，验收项 7 的 `git status` 为空即由此证据支撑。

### 1.1 一个必须先讲清的事实：仓库历史被压平了

`git rev-list --count HEAD` = 10，而首个 commit 名叫 `init`（`git log --oneline`），初始提交即包含全部业务代码。

结论（`[确认]`，证据 `evidence/00-baseline.txt`）：

- 该仓库**没有可追溯的开发历史**。任务书里"最近提交 2026-10-22"与实测 `2026-10-20` 不一致，以实测为准。
- 因此**无法用 `git log` 追溯"原生组件是哪次提交被移除的"**，只能从代码与 docs 的现状判断（见 §3）。
- `git log -S"-101"` 与 `git log -S"防回拨"` 都只命中 `init` —— 说明这些错误码在首个快照里就已存在，不存在"后期加入的过期机制"这条时间线。

> 这条约束会反复影响第 5 章的判断：凡是"某个机制是什么时候引入/被谁改掉"的问题，本仓库都答不了。

## 2. 技术栈（来自 `package.json`，非推断）

| 层 | 实现 | 证据 |
|---|---|---|
| 运行时 | Electron `^43.5.0` | `package.json:110` |
| 主进程入口 | `dist-electron/main.js`（源码 `electron/main.ts`） | `package.json:5` |
| 构建 | Vite `^8.1.3` + `vite-plugin-electron` | `package.json:112,116` |
| UI | React `^19.2.3` + `@heroui/react` `^3.2.2` | `package.json:56,62` |
| FFI | **koffi `^3.1.0`** | `package.json:75` |
| 解压 | **fzstd `^0.1.1`**（zstd 的纯 JS 实现） | `package.json:70` |
| 语音 | `silk-wasm ^3.7.1` + `sherpa-onnx-node ^1.13.3` | `package.json:82,79` |
| 视频解码 | 自带 `electron/assets/wasm/wasm_video_decode.wasm`（3.6 MB） | `evidence/00-baseline.txt` |
| 导出 | `exceljs`、`jszip`、`dom-to-image-more`、`html2canvas` | `package.json:69,74,64,71` |
| 分词 | `jieba-wasm ^2.2.0` | `package.json:72` |

`package.json:24` 有 `"//": "二改不应改变此处的作者与应用信息"`（原文为 GBK 乱码，按语义还原）——作者明确表态"二次修改不要改作者信息"，这是分发约束而非技术约束。

## 3. 本机实跑能力边界（必须先读）

这台机器上**没有微信客户端、没有微信账号、没有 QQ 客户端、没有 QQ 账号**。因此：

| 想验证的事 | 本机能否实跑 | 处理方式 |
|---|---|---|
| 仓库结构、代码逻辑、ABI 签名、路由表 | ✅ 能（读源码 + 静态提取） | 全文按 `文件:行号` 给证 |
| koffi 的加载/绑定/出参机制 | ✅ 能（用 kernel32 等价签名实跑） | `evidence/weflow-abi-probe.txt` |
| 真机拉起 WeFlow → 读微信库 → 打印消息 | ❌ 不能 | 写清操作步骤，标 `[未验证]` |
| HTTP API 的**真实响应体** | ❌ 不能（服务需先 `wcdb_open_account` 成功） | 以源码字段名 + 官方 docs 为准，标 `[确认-源码]` |
| QQ NapCat 抓真实 `message.group` 事件 | ❌ 不能 | 以官方文档 + issue 原文为准，标 `[确认-文档]` 或 `[未验证]` |

**"`[确认]` 不等于"我跑过"。** 本次的 `[确认]` 分两种，正文里逐条写明来源形态：

- `[确认-源码]`：我在固定 commit 上读到了这段代码，行号可点。
- `[确认-实测]`：我在这台机器上跑出了这个输出，evidence 文件可查。
- `[确认-文档]`：我 fetch 到了官方文档原文，URL + 访问日期可查。

## 4. 本机环境

| 项 | 值 |
|---|---|
| OS | Windows 10.0.26100（Windows 11 内核版本段） |
| arch | AMD64 |
| Node | v24.20.0 |
| 网络 | 需经本地代理 `127.0.0.1:7890`；Git 必须 `-c http.sslBackend=openssl`（schannel 走代理隧道报 `SEC_E_NO_CREDENTIALS`） |
| 工作目录 | `E:\study` |

采集时间：`2026-10-25 11:53:48 +08:00`。

## 5. 我实际跑过的命令（可复现清单）

```powershell
# 1) 克隆（固定 commit）
git -c http.sslBackend=openssl -c http.proxy=http://127.0.0.1:7890 `
    clone https://github.com/hicccc77/WeFlow.git E:\study\research\_repos\WeFlow

# 2) 基线快照 → evidence/00-baseline.txt
#    （git rev-parse / git status / 文件体积 / 原生二进制清点）

# 3) ABI 机制实跑 → evidence/weflow-abi-probe.txt
$env:NODE_PATH='E:\study\research\_repos\_tools\node_modules'
node E:\study\research\chatlog\snippets\weflow-abi-probe.js
```

第 3 条的输出（`evidence/weflow-abi-probe.txt`）：

```
OK   int32 no-arg (wcdb_init 同形) -> 8604
OK   void* (const char*) (wcdb_open_account 第1/2参同形) -> 140699745976320
OK   int32 (_Out_ int64*) rc=1 out[0]=126437556710 typeof=number -> written
OK    signature-parse: Cannot find function 'wcdb_open_account_mirror' in shared library
koffi version = 3.3.4
node v24.20.0 / win32 x64
```

## 6. 本机跑不了的东西，以及卡在哪

| 卡点 | 具体原因 | 需要什么才能过 |
|---|---|---|
| WeFlow 端到端 | 无微信客户端 → 无 `db_storage/session/session.db`；无 key provider → `wcdb_open_account` 必失败；仓库不含 WCDB.dll | 一台装有已登录微信的 Windows + 一个可用的 WCDB 第三方实现 + key provider |
| 仓库自带 ABI 契约测试 `scripts/check-wcdb-optional-exports.cjs` | 依赖 esbuild，而 esbuild 以 `spawn` + 管道 stdio 启动内部服务，在本沙箱下报 `Error: spawn EPERM`（改用 `esbuild-wasm` 同样 EPERM） | 放开子进程管道限制，或授予完整沙箱权限 |
| NapCat 实测 | 无 QQ 账号（不允许使用委托方真实账号，且本机无测试小号） | 一个可登录的测试小号 + NTQQ 客户端 |

> 上述三条并非"没做"，而是环境物理上不允许；对应章节会把"该跑但没跑成"的每一个点单独标 `[未验证]`，并写清补救路径。

## 7. 证据文件索引

| 文件 | 内容 |
|---|---|
| `evidence/00-baseline.txt` | commit / 版本 / 文件数 / 二进制清单 / 关键文件体积 |
| `evidence/github-repo-meta.json` | GitHub API 返回的仓库元数据（star / pushed_at / default_branch） |
| `evidence/github-releases.json` | Releases API 返回 `[]`（无公开产物） |
| `evidence/weflow-abi-probe.txt` | koffi 绑定 `_Out_ int64*` 的实跑输出 |
| `evidence/weflow-ffi-binding-sites.txt` | `wcdbCore.ts` 中全部 `lib.func(...)` 签名抽取 |
