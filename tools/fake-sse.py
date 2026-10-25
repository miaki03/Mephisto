"""Mephisto 接入前的自检服务：healthz + SSE。

用途：在写任何 Mephisto 代码之前，验证 VPS+frp 或 Cloudflare Tunnel
是否真的能把「实时事件流」送到手机。没有它，你无法区分
"隧道有问题" 和 "我们的服务还没写" 。

跑法：
    python tools\\fake-sse.py

然后：
    本机     curl -N http://127.0.0.1:8787/events      # 每 3 秒一行
    外网     curl -N https://你的域名/events            # 必须同样逐行出现
"""

from __future__ import annotations

import sys
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HOST = "127.0.0.1"
PORT = 8787
INTERVAL_SECONDS = 3.0
TICKS = 30


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def do_GET(self) -> None:  # noqa: N802 - http.server 的约定
        if self.path == "/healthz":
            body = b'{"ok":true,"service":"fake-sse"}'
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return

        if self.path == "/events":
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-cache")
            self.send_header("Connection", "keep-alive")
            # 给 nginx 系反向代理看的，关掉缓冲
            self.send_header("X-Accel-Buffering", "no")
            self.end_headers()
            try:
                for i in range(TICKS):
                    self.wfile.write(f"id: {i}\ndata: tick {i}\n\n".encode())
                    self.wfile.flush()
                    if i == 0:
                        print(f"[{time.strftime('%H:%M:%S')}] 客户端已连上，开始每 {INTERVAL_SECONDS}s 推送")
                    time.sleep(INTERVAL_SECONDS)
            except (BrokenPipeError, ConnectionResetError):
                print(f"[{time.strftime('%H:%M:%S')}] 客户端断开")
            return

        self.send_error(404)

    def log_message(self, *args) -> None:
        # 默认日志太吵，只在启动时打印一次
        return


def main() -> int:
    server = ThreadingHTTPServer((HOST, PORT), Handler)
    print(f"fake-sse 监听 http://{HOST}:{PORT}")
    print("  GET /healthz  -> {\"ok\":true}")
    print(f"  GET /events   -> 每 {INTERVAL_SECONDS}s 一行 SSE，共 {TICKS} 行后关闭")
    print("Ctrl-C 退出")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n退出")
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
