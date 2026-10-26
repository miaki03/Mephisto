# 测试流程（联调用）

> 现状：功能都实现了，但**没有走过一次完整的端到端**，也**没有在独立网络（移动数据）上成功连过**。
> 这份文档的目标：用最少的来回，把"跑通"这件事一次性落地。
>
> 约定：**我的部分我全做；需要你动手的，写在每条的「你做什么」里**——因为 MIUI 拦了模拟点击
> （`adb shell input tap` 报 `INJECT_EVENTS`），我点不了你手机上的按钮。

---

## 0. 每次开测前的准备（我来做，约 30 秒）

两个后台服务容易被回收，**每次开测前确认它们活着**：

```powershell
# 1) 服务端（必须）
cd E:\study\mephisto
$env:DSH_HOME = (Resolve-Path state\dsh-home).Path
python -m mephisto serve --port 8787

# 2) 隧道（要出门用才需要；只在局域网/adb 下不必开）
C:\cloudflared\bin\cloudflared.exe tunnel run mephisto
```

自检（我会先跑，结果贴给你）：

```powershell
python -m mephisto doctor                    # 8 项体检
python tests\run.py                          # 服务端 201 个用例
cd E:\study\Mephisto-Android
tools\build.ps1 -Task testDebugUnitTest      # 安卓 103 个用例
(Invoke-WebRequest http://127.0.0.1:8787/healthz).Content
```

服务端/隧道的健康判定：

| 检查 | 期望 |
|---|---|
| `healthz` 本机 | `{"ok":true,...}` |
| 隧道日志 | `Registered tunnel connection`（`protocol=quic`） |
| `https://mephisto.xcfx.com/healthz` | 200（**必须带 User-Agent**，Python 默认 UA 会被 Cloudflare 403） |

---

## 1. 测试分级（从稳到不稳，逐级往上）

### T0 单元测试（不需要手机，我做）

- 服务端 `python tests\run.py` → 201 passed
- 安卓 `tools\build.ps1 -Task testDebugUnitTest` → 103 passed

**意义**：协议解析、队列抢占、状态机、闹钟排程这些"逻辑对不对"的问题在这一层就该灭掉，不要带进真机联调。

---

### T1 本机 API（不需要手机，我做）

我不起 Android，直接用 Python 打服务端，验证契约。

| 用例 | 步骤 | 预期 |
|---|---|---|
| T1-1 鉴权 | 无令牌 GET `/api/v1/sessions` | 401 `unauthorized` |
| T1-2 建会话 | POST `/api/v1/sessions` | 201 + 26 位 ULID |
| T1-3 发消息 | POST `.../messages` | **202** + `job_id`，立刻返回（<100ms） |
| T1-4 回复 | 轮询 `.../messages` | 出现 `direction=out` |
| T1-5 幂等 | 同 `client_msg_id` 再发一次 | `duplicate=true`，**不再跑模型**（`job` 数不增） |
| T1-6 SSE | 从当前游标挂流，再发一条 | 事件**分散到达**（不是一批） |
| T1-7 取消 | 排队中 POST `/jobs/{id}/cancel` | `queued`；重复取消 409 `too_late` |
| T1-8 打断 | 运行中 cancel | `interrupted`，job=`cancelled`，**无回复**，无残留 `dsh-subprocess-local` |

---

### T2 真机 + `adb reverse`（最稳，不依赖任何网络）★ 优先做这个

把手机的 `127.0.0.1:8787` 转发到电脑，App 用默认地址即可。

```powershell
$adb = "E:\study\.toolchain\android-sdk\platform-tools\adb.exe"
& $adb reverse tcp:8787 tcp:8787      # 拔插 USB 后要重新执行
& $adb install -r E:\study\Mephisto-Android\app\build\outputs\apk\debug\app-debug.apk
```

App 里「连接」→ 地址填 `http://127.0.0.1:8787` → 令牌填 `state\dev-token.txt` 的内容。

| 用例 | 你做什么 | 预期 | 失败排查 |
|---|---|---|---|
| T2-1 启动即加载 | 打开 App | 直接出会话列表（不是空态） | 令牌没填 / `adb reverse` 掉了 |
| T2-2 进会话 | 点任意会话 | **立刻**出消息（有缓存）或短暂骨架（首次） | — |
| T2-3 切会话 | 来回点两个会话 | 第二次起**无加载态**、不卡 | 缓存被清（进程被杀会清） |
| T2-4 发送 | 输入「你好」→ 发送 | 气泡先显示"发送中"，随后"已排队"→"执行中 Ns"，最后整段回复 | 看 App 顶部状态条 |
| T2-5 发件箱收尾 | 等回复出现 | 发件箱那条**自动消失** | 服务端是否已重启（事件需带 `client_msg_id`） |
| T2-6 排队 | 快速连发两条 | 第二条显示「已排队」，第一条结束后合并执行 | — |
| T2-7 打断 | 第一条执行中点「打断」 | 立刻结束，**没有回复**；电脑上无残留 dsh 进程 | 用 `Get-Process node` 看有没有跑我们 profile 的 |
| T2-8 返回 | 系统返回手势/返回键 | **回到列表**，不掉桌面 | `BackHandler` 是否生效 |
| T2-9 新建会话 | 点「新会话」→ 输入名字 → 创建 | 进入新会话，列表里能看到 | — |
| T2-10 失败重发 | **开飞行模式**发一条 → 关飞行模式 → 点「重发」 | 显示红色「失败」；重发后**只有一条**（幂等） | 服务端按 `client_msg_id` 去重 |

---

### T3 真机 + 隧道 + 同一 Wi-Fi（验证隧道本身）

App 连接设置改成 `https://mephisto.xcfx.com`。

| 用例 | 预期 |
|---|---|
| T3-1 列表加载 | 出会话列表 |
| T3-2 发送 + 回复 | 正常，回包延迟明显高于 T2（多一跳到 Cloudflare） |
| T3-3 SSE 是活的 | 电脑上发一条消息，手机**几秒内**自己出现（不用手动刷新） |
| T3-4 断隧道 | 我停掉 cloudflared → App 显示「断开/重连中」；重启隧道后自动恢复 |

---

### T4 真机 + 隧道 + **移动数据**（独立网络，**从未成功过**）★ 关键

**你做什么**：手机上**关掉 Wi-Fi**（保留移动数据），然后打开 App。

| 用例 | 预期 | 已知风险 |
|---|---|---|
| T4-1 列表加载 | 出会话列表 | 之前在这里失败过一次（`SocketTimeoutException`），当时是自动加载缺失 + 首连超时；已修（超时 20s + 重试一次），需要复验 |
| T4-2 发送 | 202 后正常回复 | 移动网络抖动时客户端会重试一次 |
| T4-3 切会话 | 无加载、不卡 | 缓存命中 |
| T4-4 熄屏 5 分钟再打开 | 仍能收发 | 后台可能被杀，重进会重新加载 |
| T4-5 切飞行模式 30 秒再关 | 自动重连（SSE 退避重连） | — |

---

### T5 提醒链路（要改系统时间和权限，全部需要你操作）

| 用例 | 你做什么 | 预期 |
|---|---|---|
| T5-1 权限引导 | 「提醒」页点三个按钮 | 分别跳到通知权限 / 精确闹钟 / 电池优化设置页 |
| T5-2 建提醒 | 让我在电脑上建一条 2 分钟后的提醒（`mephisto remind "2分钟后 测试"`） | 「提醒」页刷新后能看到 |
| T5-3 到点响 | 等 2 分钟 | 手机响（精确闹钟）或 10 分钟内响（窗口闹钟） |
| T5-4 关 App 后仍响 | 杀掉 App，再等一次 | 仍然响（闹钟是系统级的） |
| T5-5 重启手机后仍排 | 重启手机，再等一次 | 仍然响（`RescheduleReceiver` 重排） |
| T5-6 时区变更 | 改手机时区 | 闹钟被重排，不错乱 |

---

## 2. 故障排查速查

| 现象 | 先看 | 常见原因 |
|---|---|---|
| App 显示「失败：timeout」 | `sseStatus`、隧道日志 | 隧道断了 / 服务端没起 / 移动网络首次握手慢 |
| 403 Forbidden | 请求的 User-Agent | Cloudflare 会挡默认 UA（我们的客户端带 `Dalvik`，正常） |
| 401 unauthorized | `state\dev-token.txt` | 令牌被吊销（`device revoke`）或换了库 |
| 有回复但发件箱不消失 | 服务端是否重启 | 旧进程还在跑没有 `client_msg_id` 的版本 |
| 打断后电脑还有 dsh 进程 | Job Object 是否创建成功 | `make_job()` 返回 None（非 Windows 或权限不足） |
| 手机连不上本机 | `adb reverse --list` | USB 断过、需重新 `adb reverse` |
| MIUI 让我点的地方我点不动 | — | `input tap` 被拦，需要「USB 调试（安全设置）」 |

---

## 3. 我需要你配合的事（清单）

| 场景 | 你的动作 | 大概耗时 |
|---|---|---|
| 真机基础联调 | 手机连 USB，允许调试 | 保持插着 |
| 失败重发 | 开/关飞行模式 | 1 分钟 |
| 独立网络 | 关 Wi-Fi 用移动数据 | 2 分钟 |
| 提醒到点 | 等 2 分钟，告诉我响没响 | 5 分钟 |
| 重启后闹钟 | 重启手机 | 5 分钟 |
| 想让**我**代点按钮 | 开发者选项里打开「USB 调试（安全设置）」 | 1 分钟 |

---

## 4. 每轮测试的收尾（我做）

1. 收集证据：`adb logcat` 相关行、截图、服务端 `event_log` 与 `job` 表
2. 更新 `docs/STATE.md` 的进度与已知问题
3. 修复后重跑 T0，再回到失败的那一级重测
4. 结论写成"通过/不通过/未验证"，不写"应该没问题"
