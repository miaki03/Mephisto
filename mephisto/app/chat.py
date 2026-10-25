"""一轮对话的编排：落库 → 跑 runner → 落库 → 发事件。

两条路径，共用同一套落库与事件：
- **同步** `post_message()`：调用线程里跑完（`mephisto run`、单元测试、调试用）。
- **异步** `enqueue_message()` + `execute_job()`：API 用这条，立刻返回 202，worker 去跑。

三条不变量：
1. 进来的消息**先落库再执行**：执行崩了也不丢用户输入。
2. `client_msg_id` 重复时**不再跑一次**（重复执行真的花钱）。
3. 会话 `last_active_at` 不回退，`seq` 单调递增。
"""

from __future__ import annotations

import mimetypes
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Sequence

from ..blobs import BlobStore, UploadArea
from ..clock import Clock, SystemClock
from ..domain import Attachment, Notice, Session
from ..ids import new_ulid
from ..ports import RunEvent, RunResult, Runner, Task
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
from ..store.jobs import JobRepo, JobRow

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
        self.jobs = JobRepo(db)
        self.blobs = BlobStore(blob_root) if blob_root is not None else None
        self.upload_area = UploadArea(upload_root) if upload_root is not None else None
        self.user_address = user_address

    # ---------- 会话 ----------
    def create_session(self, title: str, *, kind: str = "chatter") -> Session:
        now = self.clock.now()
        session = Session(
            id=new_ulid(),
            title=title.strip() or "（未命名）",
            kind=kind,
            created_at=now,
            last_active_at=now,
            seq=0,
        )
        return self.sessions.insert(session)

    def list_sessions(self, *, since_seq: int = 0, limit: int = 100) -> list[Session]:
        return [item for item in self.sessions.list(limit=limit) if item.seq >= since_seq]

    def get_session(self, session_id: str) -> Session | None:
        return self.sessions.get(session_id)

    # ---------- 消息 ----------
    def list_messages(self, session_id: str, *, since_seq: int = 0, limit: int = 100) -> list[MessageRow]:
        return self.messages.list_by_session(session_id, since_seq=since_seq, limit=limit)

    def ack_message(self, message_id: str) -> bool:
        row = self.messages.get(message_id)
        if row is None:
            return False
        self.events.append(
            "message.acked",
            {"message_id": message_id},
            at=self.clock.now(),
            session_id=row.session_id,
        )
        return True

    # ---------- 同步路径 ----------
    def post_message(
        self,
        session_id: str,
        text: str,
        *,
        client_msg_id: str | None = None,
        attachment_ids: Sequence[str] = (),
    ) -> ChatOutcome:
        in_row, duplicate = self._accept_message(session_id, text, client_msg_id, attachment_ids)
        if duplicate:
            return ChatOutcome(in_message=in_row, out_message=None, run=None, duplicate=True)

        run = self._run(session_id, text, in_row.id)
        out_row = self._append_reply(session_id, run.text)
        return ChatOutcome(in_message=in_row, out_message=out_row, run=run)

    # ---------- 异步路径 ----------
    def enqueue_message(
        self,
        session_id: str,
        text: str,
        *,
        client_msg_id: str | None = None,
        attachment_ids: Sequence[str] = (),
    ) -> tuple[MessageRow, JobRow | None, bool]:
        in_row, duplicate = self._accept_message(session_id, text, client_msg_id, attachment_ids)
        if duplicate:
            return in_row, None, True
        job = self.jobs.enqueue(session_id, in_row.id, at=self.clock.now())
        self._emit("job", {"state": "queued", "job_id": job.id, "message_id": in_row.id}, session_id)
        return in_row, job, False

    def execute_job(self, job: JobRow) -> RunResult:
        in_row = self.messages.get(job.message_id) if job.message_id else None
        prompt = in_row.text if in_row is not None else ""
        try:
            result = self._run(job.session_id, prompt, job.message_id or "")
        except Exception as exc:
            self.jobs.finish(job.id, "failed", at=self.clock.now(), error=str(exc))
            self._system_notice(job, f"任务执行异常：{exc}")
            raise

        self._append_reply(job.session_id, result.text)
        self.jobs.finish(
            job.id,
            "done" if result.ok else "failed",
            at=self.clock.now(),
            error=result.error,
        )
        if not result.ok:
            self._system_notice(job, result.error or "未知错误")
        return result

    # ---------- 内部 ----------
    def _accept_message(
        self,
        session_id: str,
        text: str,
        client_msg_id: str | None,
        attachment_ids: Sequence[str],
    ) -> tuple[MessageRow, bool]:
        session = self.sessions.get(session_id)
        if session is None:
            raise KeyError(session_id)

        if client_msg_id is not None:
            existing = self.messages.by_client_msg_id(client_msg_id)
            if existing is not None:
                return existing, True

        pending = self._validate_attachments(attachment_ids)
        in_row, _ = self.messages.append(
            session_id=session_id,
            direction="in",
            channel=CHANNEL,
            text=text,
            at=self.clock.now(),
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
        self._touch(session)
        return in_row, False

    def _validate_attachments(self, attachment_ids: Sequence[str]) -> list[tuple[str, Any]]:
        """先校验再落库——否则会留下"消息存在但附件无效"的孤儿。"""
        pending: list[tuple[str, Any]] = []
        for digest in attachment_ids:
            done = self.uploads.find_done(digest)
            if done is None:
                raise ValueError(f"附件尚未上传完成：{digest}")
            pending.append((digest, done))
        return pending

    def _append_reply(self, session_id: str, text: str) -> MessageRow | None:
        if not text:
            return None
        row, _ = self.messages.append(
            session_id=session_id, direction="out", channel=CHANNEL, text=text, at=self.clock.now()
        )
        self._emit("message", {"message": _message_event(row)}, session_id)
        return row

    def _run(self, session_id: str, prompt: str, message_id: str) -> RunResult:
        self._emit("job", {"state": "running", "message_id": message_id}, session_id)

        def forward(event: RunEvent) -> None:
            payload: dict[str, Any] = {"state": "running", "kind": event.kind}
            if event.phase:
                payload["phase"] = event.phase
            usage = event.payload.get("usage")
            if isinstance(usage, dict):
                payload["usage"] = usage
            self._emit("job", payload, session_id)

        result = self.runner.run(Task(prompt=prompt, session_id=session_id), on_event=forward)
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

    def _system_notice(self, job: JobRow, message: str) -> None:
        now = self.clock.now()
        notice = Notice(
            id=new_ulid(),
            kind="system",
            title="任务失败",
            body=message[:4000],
            due_utc=now,
            created_at=now,
            session_id=job.session_id,
            origin=f"job:{job.id}:failed",
        )
        self.notices.insert(notice)
        self._emit("notice", {"notice": {"id": notice.id, "title": notice.title}}, job.session_id)

    def _emit(self, kind: str, payload: dict[str, Any], session_id: str) -> int:
        return self.events.append(kind, payload, at=self.clock.now(), session_id=session_id)

    def _touch(self, session: Session) -> None:
        now = self.clock.now()
        if now >= session.last_active_at:
            self.sessions.update_activity(session.touch(now, seq=session.seq + 1))


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
