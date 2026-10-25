# 17 · 附件：分片上传与大小限制

> 背景：接入走 **Cloudflare Tunnel**（你已选 B）。CF 免费版单请求上限 100 MB，**但真正先到的是超时**——
> 官方原话：「Requests that exceed the connection or read timeout can fail mid-upload before reaching the size limit」
> （[cache 文档](https://developers.cloudflare.com/cache/concepts/default-cache-behavior/)）。
> 所以"能不能传 100 MB"这个问题没有意义，正确问法是"**在超时前能传完多大一片**"。

---

## 1. 硬约束（官方数字）

| 约束 | 值 | 出处 |
|---|---|---|
| 单请求体上限 | 100 MB（Free/Pro） | cache 文档 |
| Proxy Read Timeout | 125 s → 524 | [connection limits](https://developers.cloudflare.com/fundamentals/reference/connection-limits/) |
| Proxy Write Timeout | 30 s → 524 | 同上 |
| Proxy Idle Timeout | 900 s → 520 | 同上 |
| 客户端↔CF 空闲 | 400 s | 同上 |

**推算单请求上限**：取最坏上行 512 kbps（校园网/4G 弱信号），要在 125 s 内传完：

```
125 s × 512 kbps ÷ 8 = 8 MB    ← 理论极限，不留余量
取 4 MB                        ← 留一倍余量，且 4 MB @ 1 Mbps = 32 s，很安全
```

## 2. 结论（三条数字）

| 项 | 值 | 理由 |
|---|---|---|
| **单分片** | **4 MB** | 最坏上行也能在 125 s 内传完，留一倍余量 |
| **单文件** | **64 MB**（16 片） | 覆盖手机视频以外的一切常见附件 |
| **单消息总量** | **100 MB** | 与 CF 单请求上限同量级，但由分片保证不触发 |

超过 64 MB 的东西（视频、安装包、PSD）**不走上传**，走 §7 的投递区——你已经有别的办法把文件放进工作区，这条正好接上。

## 3. 协议：三个端点

```
POST   /api/v1/attachments                       → {upload_id, chunk_size:4194304, expires_at}
PUT    /api/v1/attachments/{upload_id}/chunks/{i} → 204（每片 ≤4 MB，i 从 0 起）
POST   /api/v1/attachments/{upload_id}/complete   → {attachment_id, digest, size}
DELETE /api/v1/attachments/{upload_id}            → 204（放弃）
```

发消息时引用：`POST /sessions/{id}/messages {client_msg_id, text, attachment_ids:[...]}`

**幂等与续传**：

| 点 | 规则 |
|---|---|
| 重传同一片 | 服务端按 `(upload_id, chunk_index)` 覆盖，天然幂等 |
| 断线续传 | `GET /attachments/{upload_id}` 返回 `received:[0,1,3]`，客户端只补缺的 |
| 重复文件 | 按 sha256 去重，`attachment_id` 指向同一 blob，DB 里只多一行引用 |
| 过期 | `expires_at` 默认 24 h，后台清理未完成的分片目录 |

**校验**：`complete` 时服务端检查 `size` 与 `sha256`，不符直接 400，**不落盘**。

## 4. 服务端存储

```
state/
├─ blobs/ab/cd/abcdef…            ← 内容寻址，按 sha256 前 4 位分两级目录
└─ uploads/<upload_id>/            ← 未完成的分片，2 小时无进展即清理
```

- **blob 按 digest 命名**，原始文件名只存数据库 → 天然防路径穿越、天然去重。
- 引用计数：`attachment` 表记账，消息删除后 blob 由夜间任务回收。
- 下载 `GET /api/v1/attachments/{id}`：带 token，支持 `Range`（大文件断点续传）。

## 5. 客户端行为

| 场景 | 做法 |
|---|---|
| 拍照 | **先压到 ≤2 MB**（长边 2048、JPEG q80）→ 单分片就够 |
| 选图 | 逐张上传，串行（并行反而抢上行） |
| 选文件 | 按 4 MB 切片，显示「3/16 片」进度 |
| 弱网 | 失败片自动重试 3 次（指数退避），仍失败则提示"可先放工作区" |
| 前端限制 | 选文件时就算总大小，超 64 MB 直接提示走投递区 |

**不要在客户端做"压缩后再传"以外的事**（比如转码视频）——那是无底洞。

## 6. 服务端限制（不可绕过）

| 项 | 值 | 说明 |
|---|---|---|
| 单请求体 | 8 MB（硬拒） | 给 4 MB 分片留一倍余量；超了直接 413 |
| 单文件 | 64 MB | 由分片数 × 分片大小保证 |
| 单设备每日 | 500 MB | 防失控；超了 429 |
| 类型白名单 | 图片 / PDF / 纯文本 / Office 常见格式 | **拒绝可执行文件**（`.exe .msi .bat .ps1 .js .scr`） |
| 文件名 | 存库前去掉路径分隔符与控制字符 | 防穿越 |

## 7. 大文件的退路：工作区投递（你已有的方式）

```
你 ──任意方式（网盘/SMB/数据线/剪贴板）──►  E:\study\mephisto\workspace\inbox\
                                                    │
                    在手机上说："看 inbox 里那个 xxx.pdf"
                                                    ▼
                                    agent 用 fs 工具直接读本地文件
```

这条比上传更好用，原因三条：

1. **不受任何超时/大小限制**（视频、几个 G 的项目都行）；
2. **不占手机流量**；
3. agent 本来就对工作区有完全读权限，不需要额外接口。

所以 App 里那行提示应该直说：「> 64 MB 的文件请放到工作区 `inbox/` 再告诉我文件名」。

## 8. 验收

```powershell
# 1. 单分片（4 MB）在弱网下也能传完
# 2. 传到一半断网 → 恢复后只补缺的分片（看日志里的 received 列表）
# 3. 同一文件传两次 → attachment 表多一行引用，blobs 目录只有一个文件
# 4. 故意传 9 MB 单请求 → 413
# 5. 传 .exe → 415
# 6. 64 MB 文件分 16 片传完，sha256 校验通过
# 7. 手机拍照 → 压缩到 2 MB 以内 → 单分片直传
# 8. 90 MB 文件被客户端直接拦住，提示"放工作区"
```
