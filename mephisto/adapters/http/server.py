"""HTTP 服务：`ThreadingHTTPServer` + 薄适配。

不做的事：不做 TLS（Cloudflare 在边缘终结）、不做 HTTP/2、不做连接池。
做三件事：把请求变成 `Request`、把 `Response` 写回去、把 SSE 交给流式函数。
"""

from __future__ import annotations

import json
import sys
import traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

from ... import __version__
from ...adapters.runner import DshRunner
from ...app.chat import ChatService
from ...blobs import BlobStore, UploadArea
from ...config import Config
from ...store import open_db
from .router import HttpError, Request, Response, StreamResponse
from .routes import Api
from .sse import stream_events

MAX_BODY_BYTES = 8 * 1024 * 1024          # 单请求 8 MB（分片 4 MB 留一倍余量）
PUBLIC_PATHS = frozenset({"/healthz"})


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = f"mephisto/{__version__}"
    api: Api | None = None

    def log_message(self, fmt: str, *args: Any) -> None:  # 默认日志太吵
        return

    # ---------- HTTP 方法 ----------
    def do_GET(self) -> None:
        self._dispatch("GET")

    def do_POST(self) -> None:
        self._dispatch("POST")

    def do_PUT(self) -> None:
        self._dispatch("PUT")

    def do_DELETE(self) -> None:
        self._dispatch("DELETE")

    # ---------- 分发 ----------
    def _dispatch(self, method: str) -> None:
        api = self.api
        assert api is not None
        parsed = urlparse(self.path)
        path = parsed.path
        query = {key: values[0] for key, values in parse_qs(parsed.query).items()}

        matched = api.router.match(method, path)
        if matched is None:
            allowed = api.router.methods_for(path)
            if allowed:
                self._json(405, {"error": {"code": "method_not_allowed", "message": f"支持 {allowed}"}})
            else:
                self._json(404, {"error": {"code": "not_found", "message": f"没有这个路径：{path}"}})
            return

        handler, params = matched
        device_id: str | None = None
        if path not in PUBLIC_PATHS:
            token = _bearer(self.headers.get("Authorization"))
            device = api.service.devices.authenticate(token, at=api.service.clock.now()) if token else None
            if device is None:
                self._json(401, {"error": {"code": "unauthorized", "message": "缺少或无效的 Bearer 令牌"}})
                return
            device_id = device.id

        try:
            body = self._read_body()
        except HttpError as exc:
            self._json(exc.status, {"error": {"code": exc.code, "message": exc.message}})
            return

        request = Request(
            method=method,
            path=path,
            params=params,
            query=query,
            headers={key.lower(): value for key, value in self.headers.items()},
            body=body,
            device_id=device_id,
        )

        try:
            result = handler(request)
        except HttpError as exc:
            self._json(exc.status, {"error": {"code": exc.code, "message": exc.message}})
            return
        except KeyError:
            self._json(404, {"error": {"code": "not_found", "message": "资源不存在"}})
            return
        except ValueError as exc:
            self._json(400, {"error": {"code": "bad_request", "message": str(exc)}})
            return
        except Exception as exc:  # 兜底：不把栈打给客户端，但要留在 stderr
            traceback.print_exc(file=sys.stderr)
            self._json(500, {"error": {"code": "internal", "message": f"{type(exc).__name__}: {exc}"}})
            return

        if isinstance(result, StreamResponse):
            self._sse(result)
        else:
            assert isinstance(result, Response)
            self._respond(result)

    # ---------- 读写 ----------
    def _read_body(self) -> Any:
        length = int(self.headers.get("Content-Length") or 0)
        if length > MAX_BODY_BYTES:
            raise HttpError(413, "body_too_large", f"请求体上限 {MAX_BODY_BYTES} 字节")
        raw = self.rfile.read(length) if length else b""
        if not raw:
            return None
        content_type = (self.headers.get("Content-Type") or "").lower()
        if "application/json" in content_type:
            try:
                return json.loads(raw.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise HttpError(400, "bad_json", f"JSON 解析失败：{exc}") from exc
        return raw

    def _respond(self, response: Response) -> None:
        body = response.body
        headers = dict(response.headers)
        if isinstance(body, (bytes, bytearray)):
            payload = bytes(body)
            headers.setdefault("Content-Type", "application/octet-stream")
        elif body is None:
            payload = b""
        else:
            payload = json.dumps(body, ensure_ascii=False).encode("utf-8")
            headers.setdefault("Content-Type", "application/json; charset=utf-8")
        self.send_response(response.status)
        self.send_header("Content-Length", str(len(payload)))
        for key, value in headers.items():
            self.send_header(key, value)
        self.end_headers()
        if payload:
            self.wfile.write(payload)

    def _json(self, status: int, body: Any) -> None:
        self._respond(Response(status, body))

    def _sse(self, stream: StreamResponse) -> None:
        api = self.api
        assert api is not None
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-cache, no-transform")
        self.send_header("Connection", "keep-alive")
        self.send_header("X-Accel-Buffering", "no")
        self.end_headers()
        self.wfile.flush()
        stream_events(api.service.events, self.wfile.write, self.wfile.flush, since_seq=stream.since_seq)
        self.close_connection = True


def _bearer(header: str | None) -> str | None:
    if not header:
        return None
    parts = header.split(None, 1)
    if len(parts) != 2 or parts[0].lower() != "bearer":
        return None
    return parts[1].strip() or None


def build_api(config: Config, *, service: ChatService, async_jobs: bool = False) -> Api:
    return Api(
        service,
        blobs=BlobStore(config.data_dir / "blobs"),
        uploads=UploadArea(config.data_dir / "uploads"),
        version=__version__,
        async_jobs=async_jobs,
    )


def build_service(config: Config, *, runner: Any = None) -> ChatService:
    db = open_db(config.data_dir)
    db.migrate()
    if runner is None:
        runner = DshRunner(
            profile=config.runner.profile,
            dsh_home=config.dsh_home,
            cwd=config.root,
            timeout_seconds=config.runner.timeout_seconds,
        )
    return ChatService(
        db,
        runner,
        blob_root=config.data_dir / "blobs",
        upload_root=config.data_dir / "uploads",
    )


def build_server(
    config: Config,
    *,
    service: ChatService | None = None,
    host: str | None = None,
    port: int | None = None,
    async_jobs: bool = False,
) -> ThreadingHTTPServer:
    service = service or build_service(config)
    api = build_api(config, service=service, async_jobs=async_jobs)
    bound = type("BoundHandler", (Handler,), {"api": api})
    server = ThreadingHTTPServer(
        (host if host is not None else config.server.host, port if port is not None else config.server.port),
        bound,
    )
    server.daemon_threads = True
    return server


def serve(config: Config, *, host: str | None = None, port: int | None = None, async_jobs: bool = True) -> int:
    from ...app.daemon import Daemon
    from ...app.reminders import ReminderService
    from ...app.scheduler import Scheduler

    data_dir: Path = config.data_dir
    data_dir.mkdir(parents=True, exist_ok=True)
    service = build_service(config)
    daemon = Daemon(service) if async_jobs else None
    scheduler = Scheduler(ReminderService(service.db, clock=service.clock, tz=config.timezone))
    server = build_server(config, service=service, host=host, port=port, async_jobs=async_jobs)
    actual_host, actual_port = server.server_address[:2]
    print(f"mephisto 监听 http://{actual_host}:{actual_port}")
    print(f"数据目录 {data_dir}")
    if daemon is not None:
        requeued = daemon.recover()
        daemon.start()
        print(f"worker 已启动（启动恢复重排 {requeued} 个任务）")
    else:
        print("worker 未启动：消息同步执行（仅调试用）")
    fired = scheduler.tick()
    scheduler.start()
    print(f"调度器已启动（首轮：触发 {fired['fired']} 条，过期 {fired['missed']} 条）")
    print("Ctrl-C 退出")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n退出")
    finally:
        server.shutdown()
        server.server_close()
        scheduler.stop()
        if daemon is not None:
            daemon.stop()
    return 0
