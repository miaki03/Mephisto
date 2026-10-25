"""一轮对话的编排：落库 → 跑 runner → 落库 → 发事件。

S4 是**同步**执行（在 POST 请求线程里跑完再返回）。S5 会换成队列 + worker，
但这一层的方法签名不变，所以 HTTP 层不用改——这是先做同步的原因。

三条不变量：
1. 每条进来的消息**先落库再执行**：执行崩了也不丢用户输入。
2. `client_msg_id` 重复时**不再跑一次**（重复执行真的花钱）。
3. 会话 `last_active_at` 不回退，`seq` 单调递增（列表同步靠它）。
"""

from __future__ import annotations

import mimetypes
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Sequence

from ..blobs import BlobStore, UploadArea
from ..clock import Clock, SystemClock
from ..domain import Attachment, Session
from ..ids import new_ulid
from ..ports import Runner, RunEvent, RunResult, Task
from ..store import (
    AttachmentRepo,
    Database,
    EventRepo,
    MessageRepo,
    MessageRow,
    NoticeRepo,
    SessionRepo,
    UploadRepo,
)
from ..store.devices import DeviceRepo

CHANNEL = "http"


@dataclass(frozen=True, slots=True)
class ChatOutcome:
    in_message: MessageRow
    out_message: MessageRow | None
    run: RunResult | None
    duplicate: bool = False


class ChatService:
    def __init__(
        self,
        db: Database,
        runner: Runner,
        *,
        clock: Clock | None = None,
        blob_root: Any = None,
        upload_root: Any = None,
        user_address: str = "owner@mephisto.local",
    ) -> None:
        self.db = db
        self.runner = runner
        self.clock = clock or SystemClock()
        self.sessions = SessionRepo(db)
        self.messages = MessageRepo(db)
        self.attachments = AttachmentRepo(db)
        self.notices = NoticeRepo(db)
        self.events = EventRepo(db)
        self.devices = DeviceRepo(db)
        self.uploads = UploadRepo(db)
        self.blobs = BlobStore(blob_root) if blob_root is not None else None
        self.upload_area = UploadArea(upload_root) if upload_root is not None else None
        self.user_address = user_address

    # ---------- 会话 ----------
    def create_session(self, title: str, *, kind: str = "chatter") -> Session:
        now = self.clock.now()
        session = Session(
            id=new_ulid(), title=title.strip() or "（未命名）", kind=kind,
            created_at=now, last_active_at=now, seq=0,
        )
        return self.sessions.insert(session)

    def list_sessions(self, *, since_seq: int = 0, limit: int = 100) -> list[Session]:
        items = self.sessions.list(limit=limit)
        return [item for item in items if item.seq >= since_seq]

    def get_session(self, session_id: str) -> Session | None:
        return self.sessions.get(session_id)

    # ---------- 消息 ----------
    def list_messages(self, session_id: str, *, since_seq: int = 0, limit: int = 100) -> list[MessageRow]:
        return self.messages.list_by_session(session_id, since_seq=since_seq, limit=limit)

    def ack_message(self, message_id: str) -> bool:
        row = self.messages.get(message_id)
        if row is None:
            return False
        self.events.append("message.acked", {"message_id": message_id}, at=self.clock.now(), session_id=row.session_id)
        return True

    def post_message(
        self,
        session_id: str,
        text: str,
        *,
        client_msg_id: str | None = None,
        attachment_ids: Sequence[str] = (),
    ) -> ChatOutcome:
        session = self.sessions.get(session_id)
        if session is None:
            raise KeyError(session_id)

        if client_msg_id is not None:
            existing = self.messages.by_client_msg_id(client_msg_id)
            if existing is not None:
                return ChatOutcome(in_message=existing, out_message=None, run=None, duplicate=True)

        # 先校验附件，避免"消息已落库但附件无效"留下孤儿
        pending: list[tuple[str, Any]] = []
        for digest in attachment_ids:
            done = self.uploads.find_done(digest)
            if done is None:
                raise ValueError(f"附件尚未上传完成：{digest}")
            pending.append((digest, done))

        now = self.clock.now()
        in_row, _ = self.messages.append(
            session_id=session_id,
            direction="in",
            channel=CHANNEL,
            text=text,
            at=now,
            client_msg_id=client_msg_id,
        )
        for index, (digest, done) in enumerate(pending):
            self.attachments.add(
                in_row.id,
                index,
                Attachment(
                    filename=done.filename,
                    size=done.size,
                    digest=digest,
                    media_type=_guess_media_type(done.filename),
                ),
            )
        self._emit("message", {"message": _message_event(in_row)}, session_id)

        run = self._run(session_id, text, in_row.id, now)
        out_row = None
        if run.text:
            out_row, _ = self.messages.append(
                session_id=session_id, direction="out", channel=CHANNEL, text=run.text, at=self.clock.now()
            )
            self._emit("message", {"message": _message_event(out_row)}, session_id)

        self._touch(session)
        return ChatOutcome(in_message=in_row, out_message=out_row, run=run)

    def _run(self, session_id: str, prompt: str, message_id: str, at: datetime) -> RunResult:
        self._emit("job", {"state": "running", "message_id": message_id}, session_id)

        def forward(event: RunEvent) -> None:
            payload: dict[str, Any] = {"state": "running", "kind": event.kind}
            if event.phase:
                payload["phase"] = event.phase
            usage = event.payload.get("usage")
            if isinstance(usage, dict):
                payload["usage"] = usage
            self._emit("job", payload, session_id)

        try:
            result = self.runner.run(Task(prompt=prompt, session_id=session_id), on_event=forward)
        except Exception as exc:  # 运行器炸了也要让调用方拿到明确失败
            self._emit("job", {"state": "failed", "error": str(exc)}, session_id)
            raise

        self._emit(
            "job",
            {
                "state": "done" if result.ok else "failed",
                "error": result.error,
                "duration_ms": result.duration_ms,
                "usage": dict(result.usage),
            },
            session_id,
        )
        return result

    def _emit(self, kind: str, payload: dict[str, Any], session_id: str) -> int:
        return self.events.append(kind, payload, at=self.clock.now(), session_id=session_id)

    def _touch(self, session: Session) -> None:
        now = self.clock.now()
        bumped = session.seq + 1
        if now >= session.last_active_at:
            self.sessions.update_activity(session.touch(now, seq=bumped))


def _guess_media_type(filename: str) -> str | None:
    return mimetypes.guess_type(filename)[0]


def _message_event(row: MessageRow) -> dict[str, Any]:
    return {
        "id": row.id,
        "seq": row.seq,
        "session_id": row.session_id,
        "direction": row.direction,
        "channel": row.channel,
        "text": row.text,
        "created_at": row.created_at.isoformat(),
    }
