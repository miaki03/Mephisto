#Requires -Version 5.1
<#
.SYNOPSIS
    第 0 步的工具链预检：只回答"能不能装"，**不安装任何东西**。

.DESCRIPTION
    检查四件事：
      1. 磁盘余量够不够安卓工具链（约 2.5 GB 展开）
      2. 能不能下载 JDK（Adoptium API，需要 User-Agent，否则 403）
      3. 能不能下载 Android cmdline-tools（dl.google.com）
      4. 能不能下载 Maven 依赖（maven.google.com）与 Gradle 发行版
    外加报告 java / gradle / adb / sdkmanager 是否已在 PATH 上。

    网络探测走 Python 的 TLS，不走 schannel —— 在受限 shell 下
    curl / Invoke-WebRequest 的 HTTPS 会因证书存储不可用而失败，那是环境问题，
    不是网络问题，用它判断会误报。

.NOTES
    这一步失败不阻塞第 0 步，只决定第 7 步（安卓工具链）走哪条路。
#>
$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)

Write-Host '=== Mephisto 工具链预检（只检查，不安装）===' -ForegroundColor Cyan

# ---------- 1. 磁盘 ----------
$needGB = 3.0
$drive = [System.IO.DriveInfo]::new((Split-Path -Qualifier $root))
$freeGB = $drive.AvailableFreeSpace / 1GB
$diskOk = $freeGB -ge $needGB
$mark = if ($diskOk) { 'OK  ' } else { 'FAIL' }
$color = if ($diskOk) { 'Green' } else { 'Red' }
Write-Host ("[{0}] 磁盘 {1} 可用 {2:N1} GB（需 ≥{3} GB）" -f $mark, $drive.Name, $freeGB, $needGB) -ForegroundColor $color

# ---------- 2. 网络（Python 负载必须是纯 ASCII，否则经管道会被代码页搞坏）----------
$probe = @'
import socket, ssl, sys

TARGETS = [
    ("jdk (Adoptium API)",    "api.adoptium.net",    443),
    ("android cmdline-tools", "dl.google.com",       443),
    ("maven (google)",        "maven.google.com",    443),
    ("gradle dist",           "services.gradle.org", 443),
]

failed = 0
for label, host, port in TARGETS:
    try:
        ctx = ssl.create_default_context()
        with socket.create_connection((host, port), timeout=15) as raw:
            with ctx.wrap_socket(raw, server_hostname=host) as tls:
                ver = tls.version()
                tls.sendall(("HEAD / HTTP/1.0\r\nHost: %s\r\nUser-Agent: mephisto-probe/1.0\r\n\r\n" % host).encode())
                tls.recv(64)
        print("[OK  ] %-22s %s:%d  TLS %s" % (label, host, port, ver))
    except Exception as exc:
        failed += 1
        print("[FAIL] %-22s %s:%d  %s: %s" % (label, host, port, type(exc).__name__, exc))

sys.exit(1 if failed else 0)
'@

$probe | & python -
$netOk = ($LASTEXITCODE -eq 0)

# ---------- 3. 已有工具 ----------
Write-Host '--- 现有工具 ---'
$tools = 'java', 'javac', 'gradle', 'adb', 'sdkmanager'
$missing = @()
foreach ($tool in $tools) {
    $cmd = Get-Command $tool -ErrorAction SilentlyContinue
    if ($cmd) {
        Write-Host ("[OK  ] {0,-12} {1}" -f $tool, $cmd.Source) -ForegroundColor Green
    }
    else {
        Write-Host ("[----] {0,-12} 不在 PATH" -f $tool) -ForegroundColor DarkGray
        $missing += $tool
    }
}

# ---------- 4. 结论 ----------
Write-Host '--- 结论 ---'
if ($diskOk -and $netOk) {
    Write-Host '预检通过：第 7 步可以装工具链。' -ForegroundColor Green
    if ($missing.Count -gt 0) {
        Write-Host ("需要安装：{0}" -f ($missing -join ', ')) -ForegroundColor Yellow
    }
    exit 0
}
Write-Host '预检未通过：' -ForegroundColor Red
if (-not $diskOk) { Write-Host ("  磁盘不足，需再腾出 {0:N1} GB" -f ($needGB - $freeGB)) -ForegroundColor Red }
if (-not $netOk) { Write-Host '  有下载目标不可达，第 7 步需要手动下载（见 docs/16 的退路）' -ForegroundColor Red }
exit 1
