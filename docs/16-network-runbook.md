# 16 · 接入施工手册（终稿）

> 两条路径，**选一条做完即可**。所有数字都来自官方文档并附链接，不采用博客转述。
> 设计理由见 [15](15-http-channel.md)。做完把 §5 的资料回填给我。

**前置结论（本机实测，你不用再测）**：校园网不封出站端口（80/443/7000/8443 全通）、直连 TLS 正常、UDP 可用、`api.cloudflare.com` 与 `api.deepseek.com` 均可达。**两条路都没有前置障碍。**

---

## 0. 选哪条

| | **A · VPS + frp** | **B · Cloudflare Tunnel** |
|---|---|---|
| 月成本 | ¥20–40 | **0** |
| 打通耗时 | 买完 VPS 后约 30 分钟 | 约 10 分钟 |
| 手机访问 | `https://你的域名` | `https://你的域名` |
| TLS 终结在 | **你的 VPS** | **Cloudflare 边缘（可见明文）** |
| 要维护 | frps + Caddy + frpc | 只有 cloudflared |
| 失效条件 | VPS 欠费/故障 | CF 改规则/封禁 |

**一句判断**：Agent 能读写你本地文件 → 对话内容要不要经过第三方？在意 → A；想今天就用、不花钱 → B。
**换路的成本**：App 只改一个地址，代码不动。所以先 B 后 A 完全可行。

---

## 1. 路线 A：VPS + frp

### 1.1 买 VPS

**在哪买**（按类型，不推荐具体商家）：

| 类型 | 代表 | 适合 | 注意 |
|---|---|---|---|
| 大厂国际站 | 阿里云国际、腾讯云国际 | 省心、中文支持 | 需国际支付方式 |
| 海外 VPS | Vultr、DigitalOcean、Linode、Hetzner | 便宜、按小时计费、机房多 | 需信用卡/PayPal |
| CN 优化线路 | 搬瓦工一类 | 国内延迟低 | 贵，且营销噪音最大 |
| 免费/学生包 | 各家学生计划 | 试水 | 有期限，**别把长期服务建在上面** |
| ❌ 虚拟主机 / 共享主机 | — | — | 无 root、无固定端口，做不了 |

> **怎么辨别广告**：搜"VPS 推荐"出来的九成是带返利链接的榜单站，内容互相抄。只信两处——**商家官网的 pricing 页**（价格与流量真实）和**商家的 status/文档页**（看有没有在你选的机房出过长期故障）。别信"必须买 CN2 GIA 才能用"，我们不跑网页服务，普通线路够。

**参数怎么挑**：

| 参数 | 要求 | 不达标会怎样 |
|---|---|---|
| 地区 | **香港 / 日本 / 新加坡** | 太远延迟高（巴黎实测 160 ms；HK/JP 预期 30–80 ms，`[估算]`） |
| CPU / 内存 | 1 核 / 1 GB | 够用；证书签发时略吃 CPU 也无妨 |
| 带宽 | **≥ 5 Mbps** | 1 Mbps 传附件会很慢 |
| 流量 | ≥ 500 GB/月 | 文本+心跳极省，附件才是变量 |
| 系统 | Ubuntu 22.04 / 24.04 或 Debian 12 | 别的系统要自己翻文档 |
| 权限 | root、可开任意端口 | 部分"轻量套餐"限制端口 |

**备案**：服务器在**境外不需要备案**，可用域名；在**境内**用域名对外服务需 ICP 备案。不想备案就买境外。

**买完先验三件事**：

```powershell
ssh root@<IP>                      # 1. 能登
curl -I https://www.cloudflare.com # 2. 能出网
ss -lntp                           # 3. 能看端口
```

### 1.2 VPS 侧：frps

**版本**：`v0.71.1`（2026-10-14 发布，[GitHub releases](https://github.com/fatedier/frp/releases)）。

```bash
cd /tmp
curl -LO https://github.com/fatedier/frp/releases/download/v0.71.1/frp_0.71.1_linux_amd64.tar.gz
curl -LO https://github.com/fatedier/frp/releases/download/v0.71.1/frp_sha256_checksums.txt
sha256sum -c frp_sha256_checksums.txt --ignore-missing   # 校验，别跳过
tar xzf frp_0.71.1_linux_amd64.tar.gz
sudo install -m755 frp_0.71.1_linux_amd64/frps /usr/local/bin/frps

openssl rand -hex 24                # 生成 token，记下来
```

`/etc/frp/frps.toml`：

```toml
bindPort = 7000                     # 已实测可出站
transport.tls.force = true

auth.method = "token"
auth.token = "把上面那串粘进来"

webServer.addr = "127.0.0.1"        # 仪表盘只给本机
webServer.port = 7500
webServer.user = "admin"
webServer.password = "另一个随机串"
```

```bash
sudo tee /etc/systemd/system/frps.service > /dev/null <<'EOF'
[Unit]
Description=frps
After=network.target

[Service]
ExecStart=/usr/local/bin/frps -c /etc/frp/frps.toml
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF
sudo systemctl daemon-reload && sudo systemctl enable --now frps
sudo systemctl status frps --no-pager
```

**打开防火墙**（云控制台安全组 + 系统防火墙都要）：

```bash
sudo ufw allow 22/tcp && sudo ufw allow 80/tcp && sudo ufw allow 443/tcp && sudo ufw allow 7000/tcp
sudo ufw enable
```

### 1.3 VPS 侧：Caddy（自动 HTTPS）

```bash
sudo apt install -y debian-keyring debian-archive-keyring apt-transport-https curl
curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/gpg.key' | sudo gpg --dearmor -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg
curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt' | sudo tee /etc/apt/sources.list.d/caddy-stable.list
sudo apt update && sudo apt install -y caddy
```

**先把域名的 A 记录指向 VPS IP**，然后 `/etc/caddy/Caddyfile`：

```
mephisto.example.com {
    reverse_proxy 127.0.0.1:18787 {
        flush_interval -1          # ★ SSE 必须：关掉代理缓冲
    }
    log {
        output file /var/log/caddy/mephisto.log
        format json
    }
}
```

```bash
sudo systemctl reload caddy
sudo journalctl -u caddy -n 30 --no-pager     # 看证书是否签发成功
```

> Caddy 默认用 HTTP-01 挑战，需要 **80 端口可达**。若 80 被封，改用 DNS-01（Caddy 的 dns 插件），代价是多配一个 DNS API token。

### 1.4 PC 侧：frpc

下 `frp_0.71.1_windows_amd64.zip` 解压到 `E:\study\.toolchain\frp\`，**同样校验 sha256**。

`frpc.toml`：

```toml
serverAddr = "你的VPS IP"
serverPort = 7000
transport.tls.enable = true
transport.heartbeatInterval = 10
transport.heartbeatTimeout = 30
loginFailExit = false               # 连不上也继续重试，别退出

auth.method = "token"
auth.token = "与 frps 相同"

log.to = "E:/study/.toolchain/frp/frpc.log"

[[proxies]]
name = "mephisto"
type = "tcp"
localIP = "127.0.0.1"
localPort = 8787
remotePort = 18787                  # Caddy 反代这个端口
```

```powershell
cd E:\study\.toolchain\frp
.\frpc.exe -c .\frpc.toml           # 看到 login success + start proxy success
```

**开机自启**：

```powershell
schtasks /create /tn "frpc" /tr "E:\study\.toolchain\frp\frpc.exe -c E:\study\.toolchain\frp\frpc.toml" /sc onstart /ru SYSTEM /rl highest
```

### 1.5 验收（六条，顺序别跳）

```powershell
# 1. VPS：frps 在听
#    sudo ss -lntp | grep -E '7000|18787'
# 2. PC：frpc 日志有 login success
# 3. VPS 本机能打到我们的端口（先跑 python tools\fake-sse.py）
#    curl -s http://127.0.0.1:18787/healthz
# 4. 域名与证书
curl -I https://你的域名/healthz
# 5. 流式：必须逐行出，不能一次性吐
curl -N https://你的域名/events
# 6. 手机 4G 打开 https://你的域名/healthz
```

### 1.6 故障表

| 症状 | 原因 | 处理 |
|---|---|---|
| `token in login doesn't match` | 两边 token 不一致 | 重新复制，别带空格 |
| frpc `connect timeout` | 7000 未放行 | 云安全组 + `ufw allow 7000` |
| 502 | 本地服务没起 / remotePort 与 Caddy 不一致 | 先 `curl 127.0.0.1:8787/healthz` |
| 证书申请失败 | DNS 未生效 / 80 不通 | `dig 你的域名`；或改 DNS-01 |
| SSE 一次性吐出 | Caddy 缓冲 | 确认 `flush_interval -1`，且没开 `encode` |

---

## 2. 路线 B：Cloudflare Tunnel

### 2.1 免费版限制（官方数字，附出处）

| 项 | 免费计划 | 对我们的影响 | 出处 |
|---|---|---|---|
| 价格 | 0 | — | — |
| 前置 | 域名 NS 托管到 CF | 要改 NS 并等生效 | — |
| **上传体积上限** | **100 MB**（Pro 同，Business 200 MB，Enterprise 5 GB） | v1 不上传附件，暂时无关 | [default cache behavior](https://developers.cloudflare.com/cache/concepts/default-cache-behavior/) |
| **Proxy Read Timeout** | **125 秒 → 524** | **长任务必须异步**（已是我们的设计） | [connection limits](https://developers.cloudflare.com/fundamentals/reference/connection-limits/) |
| Proxy Write Timeout | **30 秒 → 524** | 大响应体要边算边发 | 同上 |
| **Proxy Idle Timeout** | **900 秒 → 520** | 我们的 SSE 每 15 秒 keepalive，远低于此 | 同上 |
| 客户端↔CF 空闲 | HTTP/1.1 400 秒 / HTTP/2 400 秒 | 同上 | 同上 |
| URL / 头部上限 | 16 KB / 128 KB | 够用 | 同上 |
| 可缓存文件上限 | 512 MB | 无关 | 同上 |
| 缓存策略 | 不缓存 `private`/`no-store`/`no-cache`，不缓存非 GET | **我们的 API 本就不该被缓存** | 同上 |

> **更正一处常见错误**：网上大量文章写"CF 超时 100 秒"，官方是 **125 秒**（2026-10-22 版文档）。这类转述错误在 VPS/CF 类中文文章里极普遍，以官方页面为准。

**两个真风险**（比上面这些限制重要）：

1. **TLS 在 CF 边缘终结**，它看得到明文。Agent 能读写你本地文件，这是信任选择。
2. **SSE 是否真实时，必须自己验一次**（第 §2.3 第 5 条）。被攒着就先加 `protocol: http2`，仍不行就放弃 B。

### 2.2 配置三步

**① 域名托管到 CF**：CF 控制台添加站点 → 把注册商处的 NS 改成 CF 给的两个地址 → 等生效。

**② 装 cloudflared**（Windows，官方下载页 <https://developers.cloudflare.com/tunnel/downloads/>）

```powershell
mkdir C:\Cloudflared\bin
# 把 cloudflared-windows-amd64.exe 放进去，改名为 cloudflared.exe
```

**③ 登录、建隧道、绑域名**

```powershell
cd C:\Cloudflared\bin
.\cloudflared.exe tunnel login                  # 浏览器里选你的域名
.\cloudflared.exe tunnel create mephisto        # 记下 Tunnel ID
.\cloudflared.exe tunnel route dns mephisto mephisto.example.com
```

`C:\Users\<你>\.cloudflared\config.yml`：

```yaml
tunnel: <TUNNEL_ID>
credentials-file: C:\Users\<你>\.cloudflared\<TUNNEL_ID>.json
protocol: http2              # ★ 流式更稳；QUIC 被拦时必加

ingress:
  - hostname: mephisto.example.com
    service: http://127.0.0.1:8787
  - service: http_status:404
```

```powershell
.\cloudflared.exe tunnel ingress validate
.\cloudflared.exe tunnel run                     # 前台先跑一次看日志
```

**开机自启**：

```powershell
schtasks /create /tn "cloudflared" /tr "C:\Cloudflared\bin\cloudflared.exe --config C:\Users\<你>\.cloudflared\config.yml tunnel run" /sc onstart /ru SYSTEM /rl highest
```

### 2.3 验收

同 §1.5 的 4 / 5 / 6 三条（域名换成你的 CF 域名）。**第 5 条是 B 的生死线。**

### 2.4 坑

| 坑 | 处理 |
|---|---|
| 别用 `trycloudflare.com` 快速隧道 | 它会缓冲 SSE；只用 named tunnel |
| 长请求被 524 | `POST /messages` 立刻返回 202，结果走 SSE |
| 流式被缓冲 | `protocol: http2` |
| 改 config 不生效 | 重启计划任务 |
| 想加一层身份 | Zero Trust → Access，免费版有用户数上限（以控制台为准，`[未核实]`） |

---

## 3. 两条路都要做的安全清单

- [ ] 服务只绑 `127.0.0.1:8787`，不直接暴露
- [ ] 所有 `/api/v1/*` 要 `Authorization: Bearer`
- [ ] 令牌只存 `sha256`，只打印一次
- [ ] VPS：`ufw` 只开 22 / 80 / 443 / 7000；SSH 改密钥登录
- [ ] 日志不记 `Authorization`、不记正文
- [ ] 每 IP 限流（防扫）
- [ ] frp / cloudflared 的安装包**校验 sha256**

## 4. 回滚

| 方案 | 清理 |
|---|---|
| A | `sudo systemctl disable --now frps caddy`；删 DNS A 记录；退订 VPS |
| B | `cloudflared tunnel delete mephisto`；删 DNS CNAME；`schtasks /delete /tn cloudflared /f` |
| 共同 | `schtasks /end /tn frpc`（否则它一直重试） |

## 5. 回填给我的资料

| # | 内容 |
|---|---|
| 1 | 选了 A 还是 B |
| 2 | 域名（子域即可） |
| 3 | VPS 地区 / 规格 / 月费（若 A） |
| 4 | §1.5 或 §2.3 六条验收的通过情况 |
| 5 | 报错原文（原样贴，别转述） |

## 6. 参考（只用官方）

- frp：<https://github.com/fatedier/frp/releases>（当前 v0.71.1）· 文档 <https://gofrp.org>
- Caddy：<https://caddyserver.com/docs/install> · `reverse_proxy` <https://caddyserver.com/docs/caddyfile/directives/reverse_proxy>
- Cloudflare Tunnel：<https://developers.cloudflare.com/tunnel/> · 下载 <https://developers.cloudflare.com/tunnel/downloads/>
- CF 连接与超时：<https://developers.cloudflare.com/fundamentals/reference/connection-limits/>
- CF 上传上限：<https://developers.cloudflare.com/cache/concepts/default-cache-behavior/>
- CF 524 说明：<https://developers.cloudflare.com/support/troubleshooting/http-status-codes/cloudflare-5xx-errors/error-524/>
