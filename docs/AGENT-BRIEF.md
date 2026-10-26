# Agent 任务书

> 给"下一个接手这个项目的 agent"（包括未来的我，以及并行开出来的子 agent）。
> 读这份就够开工；细节再跳 `INDEX.md` / `STATE.md` / `99-implementation-plan.md`。

---

## 1. 一句话

Mephisto = 一个**用手机当入口、用家里那台电脑当唯一执行节点**的个人 agent。
dsh 是执行器（不是产品形态），手机是遥控器。它必须能真的执行命令、读写文件，而不只是聊天。

---

## 2. 协作约定（用户的偏好，别踩）

- **中文回答，简洁**。不吹捧、不复述、不写"总结如下"式的废话。
- 结论要带证据：`[确认-文档]` / `[实测]` / `[判断]` / `[未验证]` 这类标注，用户认这个。
- **反对过度设计**：能拆则拆、能不加就不加；说过"拒绝过度设计""不要硬拆"。
- **每步交付都要自测**，不要问"这样可以吗"，直接做，做完给证据。
- 他会在真机上验收；他不需要我替他点按钮（也点不了，见 §6）。
- 他明确说过：`E:\mephisto\mailbot` 是史山代码，**不要读**。

---

## 3. 铁律（改架构前先读）

1. **hexagonal**：`domain/` 零 IO；副作用只在 `ports.Source/Sink/Runner` 三条边界上。
2. **状态在文件/数据库里**，不在框架内。进程被杀不能丢任务（`job` 表是真相，内存队列只做唤醒）。
3. **单一出口**：所有对外投递都走同一条链路（`notice` 表 + 适配器）。
4. **幂等靠数据库约束**：`notice.origin` 唯一、`message.client_msg_id` 唯一、`job` 部分唯一索引。
   不靠"记得判重"。
5. **时间只有 UTC 感知值**；DB 存 epoch 毫秒；`null` = 未知，不用空字符串冒充。
6. **判成功看退出码**，不看事件流里有没有 `final`（dsh 失败时也会发 `final`）。
7. **约束写在 DDL 与 `__post_init__`**，不写在注释里。

---

## 4. 两个仓库、标签纪律

| 仓库 | 路径 | 远端 | 已有标签 |
|---|---|---|---|
| 服务端 | `E:\study\mephisto` | `git@github-mephisto:miaki03/Mephisto.git` | `step-0` … `step-6` |
| 安卓 | `E:\study\Mephisto-Android` | `git@github-mephisto:miaki03/Mephisto-Android.git` | `step-7` … `step-10` |

- 主干 `main`，**一步一个 commit + 一个 annotated tag**，tag message 写验收清单。
- 提交信息写清"为什么"，不写"改了哪些文件"。
- 服务端的契约变更**只加不改**；新增端点要更新 `STATE.md §5.1`。
- 文档更新与代码可以同 commit，也可以单独 docs commit（后者不打 tag）。

---

## 5. 当前状态（2026-10-25）

**已完成**：S0–S10（服务端 6 步 + 安卓 4 步）。服务端 201 用例、安卓 103 用例全绿。

| 部分 | 状态 |
|---|---|
| 领域/存储/迁移 | ✅ 13 张表，约束在 DDL |
| HTTP API + SSE | ✅ 17 端点冻结 |
| 队列 + 守护进程 | ✅ 202 异步、单 SQL 抢占、启动恢复 |
| 提醒内核 + MCP | ✅ 5 个工具；`mephisto remind` 一条命令建提醒 |
| DSH 运行器 | ✅ 子进程 + NDJSON + Job Object 收尾 |
| 安卓只读/发送/闹钟 | ✅ 界面与逻辑可用 |
| **端到端完整测试** | ❌ **从未完整走过** |
| **独立网络（移动数据）** | ❌ **从未成功过**（曾超时，已修未复验） |
| 附件上传（客户端） | ❌ 服务端就绪，客户端没接 |
| 分页加载更早消息 | ❌ 接口没有 `before` |
| 日历集成 | ❌ 未做（可选） |
| 邮件通道 | 🅿️ 搁置（S14） |

---

## 6. 环境事实（这些坑都踩过，别再踩）

| 事实 | 应对 |
|---|---|
| 只有 PowerShell 5.1（无 pwsh） | `powershell -NoProfile -ExecutionPolicy Bypass -File` |
| curl / `Invoke-WebRequest` 走 schannel 会证书失败 | 下载一律用 **Python**（TLS 正常） |
| `pip` 曾因 `%TEMP%` 权限失败 | 现已是 danger-full-access，pip 可用 |
| `tempfile.mkdtemp()` 目录不可写/不可删 | **禁用**；测试用 `tests/**/_tmp/` |
| `.ps1` 无 BOM 会被当 GBK 读 | `.ps1` 用 UTF-8 **带 BOM**；`.cmd` 用 CRLF |
| `sdkmanager` / `android sdk` 连不上仓库 | 用 `tools/install-sdk-packages.py` 直下 zip 解压 |
| Maven Central TLS 握手被中断 | Gradle 走阿里云镜像（`settings.gradle.kts` 已配） |
| `%TEMP%` / `%LOCALAPPDATA%` 不可写 | `java.io.tmpdir`、Kotlin in-process、adb 日志全部改道 `.toolchain\jtmp` |
| AGP 9 起 Kotlin 内置 | **不能**再应用 `org.jetbrains.kotlin.android` 插件 |
| Windows 沙箱给工作区加 ACL 会因缺 `WRITE_OWNER` 失败 → dsh 只能 read | 跑 `diagnose-windows-sandbox-acl` 技能修（已修 `mephisto` 与 `Mephisto-Android`） |
| MIUI 拦 `adb shell input tap/text`（`INJECT_EVENTS`） | 需要用户在开发者选项开「USB 调试（安全设置）」，否则只能让用户手点 |
| Cloudflare 挡默认 Python UA（403） | 请求必须带正常 User-Agent |
| 出站 **TCP 7844 被封**、UDP 7844 通 | `cloudflared` 必须 `protocol: quic`（写 http2 会一直 TLS 握手失败） |
| 隧道日志里 `198.18.0.x` 是 FlClash 的 fake-ip | 正常，别当成错误 |

---

## 7. 命令清单

```powershell
# 服务端
cd E:\study\mephisto
python -m mephisto doctor                        # 8 项体检
python tests\run.py                              # 201 用例
python -m mephisto serve --port 8787             # 起服务（worker + 调度器）
python -m mephisto run "1+1"                     # 跑一轮（同步，调试用）
python -m mephisto remind "3分钟后 喝水"          # 建提醒
python -m mephisto device add --name 手机         # 发令牌（只显示一次）
python -m mephisto mcp                           # MCP stdio

# 隧道（要 $env:DSH_HOME 已在 serve 里设好）
C:\cloudflared\bin\cloudflared.exe tunnel run mephisto

# 安卓
cd E:\study\Mephisto-Android
tools\build.ps1                                  # assembleDebug
tools\build.ps1 -Task testDebugUnitTest          # 103 用例
tools\build.ps1 -Task installDebug -Install      # 装到手机

# 设备
$adb = "E:\study\.toolchain\android-sdk\platform-tools\adb.exe"
& $adb reverse tcp:8787 tcp:8787                 # 手机 127.0.0.1:8787 → 电脑
& $adb shell screencap -p /sdcard/s.png
& $adb pull /sdcard/s.png E:\study\mephisto\state\s.png
```

---

## 8. 下一步候选（选一个，别并行）

| 优先级 | 事项 | 理由 | 预估 |
|---|---|---|---|
| **P0** | 按 `TEST-PLAN.md` 走通 T2→T4 | 没跑通的系统不算存在；用户明确"跑通是首要任务" | 1 次配合 |
| P1 | S9.1 客户端补附件上传（4 MB 分片） | 服务端接口早就冻结了，缺客户端就永远用不上 | 0.5 天 |
| P2 | 会话列表预览改服务端（`last_message` + `message_count`） | 现在只有打开过的会话才有预览 | 0.5 天 |
| P3 | 记忆与工作区（S11 原计划） | agent 现在每轮都是"失忆"的 | 1 天 |
| P4 | 第二个通道（Telegram，S12） | 验证"通道=适配器"这个架构成不成立 | 1 天 |
| P5 | 邮件通道（S14） | 已搁置，等前四项稳了再说 | — |

---

## 9. 交接检查清单（接手先做这个）

1. 读 `docs/INDEX.md`（知道东西在哪）→ `docs/STATE.md`（知道现在在哪）
2. `git status` 两个仓库都要干净；`git log --oneline -3` 看最后一步
3. 跑 T0：服务端 201 + 安卓 103，任一失败先修
4. 起服务 + 起隧道，确认 `healthz` 与隧道 `Registered tunnel connection`
5. 再读 `docs/CLIENT-CONTRACT.md`（客户端边界）与 `docs/TEST-PLAN.md`（怎么验）
