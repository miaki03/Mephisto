# 41 · 密钥从哪来：协议、`internalDbKeyHex` 的真实语义、前置条件

> 本章有两个版本的事实，**必须分开看**：
> - **仓库版（5.0.0，净化版）**：不内置任何密钥能力，把「取密钥」外包给你自己写的可执行文件。
> - **官方分发包（能真跑的那个）**：内置 `wx_key.dll`，用**内存扫描**直接取；并有 Frida 备用路径。
>
> 委托方手上如果要"能跑的 WeFlow"，用的是后者。仓库文档描述的是前者 —— 两者协议完全不同。

---

## 1. 仓库版协议（`electron/services/keyProviderService.ts`，136 行）

### 1.1 调用方式

`spawn(providerPath, [], { stdio: ['pipe','pipe','pipe'], windowsHide: true })`（`keyProviderService.ts:48`）

- 向 stdin 写**一行 JSON**（`keyProviderService.ts:101`）：`JSON.stringify(request) + '\n'`
- 从 stdout 按行读 **NDJSON**：

```jsonc
{"type":"progress","message":"...","level":0}   // 可多次
{"type":"result","success":true, ...}           // 恰好一次，作为终态
```

- 解析循环见 `keyProviderService.ts:63-84`；只认 `type` 为 `progress` / `result` 的行，其它行**静默丢弃**（`JSON.parse` 失败也跳过）。
- 终态判定：收到 `type==='result'` 立即 resolve；若进程 `close` 时仍未收到，则用退出码报错（`keyProviderService.ts:91-98`）。
- **超时**：`invoke()` 默认 `timeoutMs = 120_000`；`autoGetDbKey` 默认 `60_000` 且 `Math.max(timeoutMs, 1000)`。
- 未配置路径：直接返回 `{success:false, error:'未配置外部密钥获取实现，请在设置中指定可执行文件路径'}`，**不做任何降级**（`keyProviderService.ts:18, 38-41`）。

### 1.2 三个动作的完整字段（从类型定义直取，非猜测）

`keyProviderService.ts:20-23`：

```ts
type ProviderRequest =
  | { action: 'get_db_key';          dbPath?: string; accountId?: string; internalDbKeyHex?: string }
  | { action: 'get_image_key';       accountDir?: string; accountId?: string }
  | { action: 'scan_image_key_memory'; accountDir?: string }
```

返回类型（`keyProviderService.ts:15-16`）：

```ts
type DbKeyResult    = { success: boolean; key?: string; accountId?: string; error?: string; logs?: string[] }
type ImageKeyResult = { success: boolean; xorKey?: number; aesKey?: string; verified?: boolean; error?: string }
```

| 动作 | 入参 | 期望出参 | 语义 |
|---|---|---|---|
| `get_db_key` | `dbPath`（数据根目录）、`accountId`、`internalDbKeyHex`（可选） | `key`（数据库密钥）、`accountId`（可回填）、`logs` | 取 SQLCipher 的 DB key |
| `get_image_key` | `accountDir`、`accountId` | `xorKey`（数值）、`aesKey`（字符串）、`verified` | 取图片解密双密钥 |
| `scan_image_key_memory` | `accountDir` | 同上 | 从内存扫描图片密钥（当落盘密钥不可用时） |

注意 `xorKey` 是 **number**、`aesKey` 是 **string** —— 与 `nativeImageDecrypt.ts` 的 `decryptDatNative(inputPath, xorKey: number, aesKey?: string)` 对得上。

### 1.3 `internalDbKeyHex` 到底是什么意思

**结论：它是"内部密钥"的十六进制串，可选；给了才能走"派生"而不是"扫描"。**

证据链：

1. 仓库侧：**这个参数从头到尾没被前端填过。** 我 grep 了全部调用点（`evidence`：`main.ts:5398`、`preload.ts:201`、`SettingsPage.tsx:1441`、`WelcomePage.tsx:356`），前端只传 `(dbPath, accountId)` 两个参数，第三位永远 `undefined`。`[确认-源码]`
2. 官方侧：它在 `keyRecoverWorker.js` 里**真的被用了**，而且决定调哪个原生函数：

```js
c = o.func('bool RecoverDbKeyEx(uint32 targetPid, const char* dbPath, const char* internalDbKeyHex, _Out_ char *keyBuffer, int bufferSize)')
l = o.func('bool RecoverDbKey(uint32 targetPid, const char* dbPath, _Out_ char *keyBuffer, int bufferSize)')
...
let d = String(i.internalDbKeyHex || '').trim()
let f = c && d ? c(i.pid, i.dbPath, d, u, u.length)   // 有 internal key → 走 Ex
              : l(i.pid, i.dbPath, u, u.length);      // 没有 → 走普通扫描
```

（`/dist-electron/keyRecoverWorker.js`，`[确认-源码]`）

3. 语义推断：`RecoverDbKey` 是"在微信进程内存里搜一个形状正确的 32 字节密钥"；`RecoverDbKeyEx` 额外接收一个**已知的内部密钥**，用它做**校验/派生**，从而把"搜到的候选"收敛到唯一解。所以 `internalDbKeyHex` 是**降低误报的辅助输入**，不是主密钥。

> `[未验证]`：`internalDbKeyHex` 的精确长度/来源（是否就是微信 4.x 的 "internal key"，如何取得）。仓库里没有任何生成逻辑，官方构建也不生成它（前端从不填），说明它只在特定调试/恢复场景由人工提供。

### 1.4 返回值的强约束

官方 worker 对返回的 key 做了格式校验（`keyRecoverWorker.js`）：

```js
if (/^[0-9a-fA-F]{64}$/.test(e)) { n({ success:true, key: e.toLowerCase() }); return }
n({ success:false, error: '密钥格式无效' })
```

**64 个 hex 字符 = 32 字节。** 这是 WeChat 4.x 的 DB key 长度。任何返回其它长度的实现都会被判无效。

---

## 2. 官方分发包：内置 `wx_key.dll`（与仓库版完全不同）

### 2.1 它真的在包里

```text
E:\study\WeFlow\resources\resources\key\win32\x64\wx_key.dll   235,008 字节
```

`main.js` 里的查找顺序（`/dist-electron/main.js` 偏移 1551231 起）：

```js
if (process.env.WX_KEY_DLL_PATH) paths.push(process.env.WX_KEY_DLL_PATH)
paths.push(join(process.resourcesPath, 'resources','key','win32', arch, 'wx_key.dll'))
paths.push(join(process.resourcesPath, 'resources','key','win32','x64','wx_key.dll'))
paths.push(join(process.resourcesPath, 'resources','key','win32','wx_key.dll'))
paths.push(join(process.resourcesPath, 'resources','wx_key.dll'))
paths.push(join(process.resourcesPath, 'wx_key.dll'))
// 开发态还会试 cwd 下的同名路径
```

**`WX_KEY_DLL_PATH` 环境变量可以覆盖** —— 这是一个可替换点（委托方若想自己实现密钥获取，最干净的接口就是这个环境变量 + 同名导出）。

### 2.2 8 个导出（`evidence/wx_key-dll-exports.txt`）

| 序号 | 符号 | 用途（由调用点/命名推断） |
|---|---|---|
| 1 | `InitializeHook` | 初始化对微信进程的 hook/扫描上下文 |
| 2 | `PollKeyData` | 轮询取密钥数据（异步收集） |
| 3 | `RecoverDbKey` | `bool(uint32 pid, const char* dbPath, _Out_ char* buf, int size)` |
| 4 | `RecoverDbKeyEx` | 加 `internalDbKeyHex` 的变体 |
| 5 | `GetImageKey` | 取图片 XOR/AES 密钥 |
| 6 | `CleanupHook` | 释放 |
| 7 | `GetStatusMessage` | 状态文案（进度提示） |
| 8 | `GetLastErrorMsg` | `const char* GetLastErrorMsg()` —— 错误文案 |

调用签名（`keyRecoverWorker.js` 原文）：

```js
o.func('const char* GetLastErrorMsg()')
o.func('bool RecoverDbKeyEx(uint32 targetPid, const char* dbPath, const char* internalDbKeyHex, _Out_ char *keyBuffer, int bufferSize)')
o.func('bool RecoverDbKey(uint32 targetPid, const char* dbPath, _Out_ char *keyBuffer, int bufferSize)')
const u = Buffer.alloc(128)   // keyBuffer
```

### 2.3 取密钥的运行形态

`keyRecoverWorker.js` 是一个**独立 worker**（`vite.config` 会把它打成单独 entry；asar 里 `/dist-electron/keyRecoverWorker.js` 只有 2.4 KB）。它的通信协议与仓库版**结构相同但字段不同**：

```jsonc
// 收到的任务
{ "pid": 1234, "dbPath": "...", "dllPath": "...", "internalDbKeyHex": "" }
// 发出的进度
{ "type":"progress", "data": { "message": "正在执行内存扫描..." } }
// 发出的结果
{ "type":"result",   "data": { "success":true, "key":"<64hex>" } }
```

（源码里那些 `姝ｅ湪...` 是 **UTF-8 被当 GBK 解**的乱码，还原后是"正在加载扫描模块…""正在执行内存扫描…"。）

- 支持两种宿主通信：`process.send`（worker_thread/child_process IPC）或 **stdin/stdout NDJSON**（fallback）—— 后者正好和仓库版的外部可执行文件协议兼容。
- 有 `WEFLOW_KEYFLOW_LOG` 环境变量可以把每一步（`dll:load-begin` / `recover-call-begin` / `recover-call-end`）写到日志文件。**这是排查取密钥失败最好用的开关。**

### 2.4 Frida 备用路径（macOS / 部分 Windows 场景）

`main.js` 里有一条完全不同的实现：

- `ensureFridaAvailable()` —— 检查/自动下载 Frida。
- `getDbKeyByHelperElevated()` —— **请求管理员授权并执行 helper**（提示文案："正在请求管理员授权并执行 helper..."）。
- `WF_ERR::` 前缀的错误协议：`WF_ERR::<code>::<detail>`，其中 `-128` 或含 `User canceled` 视为用户取消（`main.js` 中 `S.startsWith('WF_ERR::')` 那段）。
- 会先找微信 PID：`Get-CimInstance` 式枚举（源码里解析 `ProcessId`/`CommandLine`，筛 `weixin.exe`，取 CommandLine 最短的那个 —— 因为 Electron 化的微信会有多个同名进程）。

`[推断]` Windows 上主路径是 `wx_key.dll`，Frida 是兜底；macOS 上只有 Frida 路径。

---

## 3. 密钥怎么存

真机配置文件 `%APPDATA%\WeFlow\WeFlow-config.json`（我读到的键，值已脱敏）：

```json
{
  "dbPath": "F:\\wxcache\\xwechat_files",
  "decryptKey": "safe:v10...(133 chars)",
  "myWxid": "wxid_gg7tf5htsvdo32_13d0",
  "imageXorKey": "safe:v10...(49 chars)",
  "imageAesKey": "safe:v10...(69 chars)",
  "wxidConfigs": {
    "wxid_gg7tf5htsvdo32_13d0": { "decryptKey": "safe:v10...", "imageXorKey": "...", "imageAesKey": "...", "updatedAt": 1786263793677 }
  }
}
```

- `safe:v10` 前缀是 **Electron `safeStorage`（Windows 上走 DPAPI）** 的密文封装。`base64("djEw")` = `v10`，与 Chromium 的 OSCrypt v10 格式一致。
- 也就是说：**密钥落盘后与本机当前 Windows 用户绑定**。换机器/换用户 → 解不开 → 必须重新取。
- `wxidConfigs` 支持**多账号**分别存密钥。

`[未验证]`：`safeStorage` 的具体可用性判断（`isEncryptionAvailable()` 为 false 时是否明文落盘）。

---

## 4. 复现条件（要准备什么）

| 条件 | 说明 | 证据 |
|---|---|---|
| Windows x64 | `wx_key.dll` 只给了 `win32/x64`；`arm64` 有分支但包里没有该架构的 dll | 目录清单 |
| 微信正在运行且已登录 | `RecoverDbKey(uint32 targetPid, ...)` 需要 PID；扫描的是进程内存 | 签名 |
| 同权限级别 | 若微信以管理员运行，取密钥进程也要管理员（`getDbKeyByHelperElevated` 就是干这个的） | 提示文案 |
| 杀软放行 | `-101` 文案虽在误导，但内存扫描**确实**会被杀软拦；这是真实约束 | `50-*.md` |
| DB key 长度必须 64 hex | 否则 worker 判"密钥格式无效" | `keyRecoverWorker.js` |
| 图片密钥可独立获取 | `GetImageKey` 是单独导出，可与 DB key 分开取 | 导出表 |

**不需要**的：不需要知道微信版本号（脚本不校验）；不需要固定 IP。

**会变的**：`wx_key.dll` 里针对微信内部结构的偏移/特征。微信大版本更新后可能失效 —— 这是这类工具最常见的"更新即坏"点。`[推断]`（无多版本样本可比对）。

---

## 5. 社区主流做法（只讲原理，不复制代码）

| 路线 | 原理 | 依赖 | 备注 |
|---|---|---|---|
| **内存扫描**（WeFlow 的 `wx_key.dll`、多数 `wxdump` 系工具） | 在微信进程堆里搜"32 字节、高熵、能与 `session.db` 首页 HMAC 对上"的候选 | 读进程内存权限（`OpenProcess`/`ReadProcessMemory`） | 4.x 早期可行 |
| **断点/hook 取 key**（Frida、调试器） | 在 SQLite 打开加密库的那一刻截获 `sqlite3_key` 调用参数 | Frida 或调试权限 | 微信 4.1.10+ 内存里不再常驻密钥后，这是**唯一稳的路**（QQ 侧同理，见 `71-qq-localdb.md`） |
| **派生/已知内部密钥** | 由 `internalDbKeyHex` 之类的"内部密钥"派生 DB key | 需先知道内部密钥 | 即 `RecoverDbKeyEx` 路径，闭源且缺少文档 |

`[推断]`：微信 4.1.10+ 收紧后，"纯内存扫描"这条路的成功率显著下降，社区普遍转向 hook。这与 §2.4 里 Frida 路径的存在互为印证。

---

## 6. 对委托方的三条硬约束（陈述事实，不给方案）

1. **仓库版不能直接用**：它只提供"外包协议"，不含任何实现；单靠仓库 clone 无法取到 key，也就无法打开任何库。
2. **官方版的取密钥能力与授权绑定**：`wx_key.dll` 与 `wcdb_api.dll` 一样受 `-101`/授权上下文影响（两者在同一宿主进程里被加载）。
3. **`WX_KEY_DLL_PATH` 是唯一干净的替换点**：想自己实现取密钥，实现 `RecoverDbKey` / `GetImageKey` / `GetLastErrorMsg` 三个导出即可顶上，不必改 WeFlow 代码。

---

## 7. 本章未解问题

1. `internalDbKeyHex` 的精确长度与来源（谁能提供、怎么算）。
2. `InitializeHook` / `PollKeyData` 的签名与调用时序（worker 里没直接用，可能被 `RecoverDbKey` 内部调用）。
3. `wx_key.dll` 是否也做反调试/授权校验（未扫它的字符串）。
4. 微信 4.1.10+ 上 `wx_key.dll` 是否仍有效（本机微信版本未记录）。
5. `safeStorage` 不可用时密钥是否明文落盘。
6. Frida 路径与 `wx_key.dll` 路径的选择条件（哪个平台/什么错误下切过去）。
