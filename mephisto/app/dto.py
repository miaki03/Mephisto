"""DTO：领域对象 ↔ JSON。只做形状转换，不做判断。

放在 app 层而不是 adapters：HTTP 与将来的 Web 调试页共用同一套形状，
改字段名时只改一处。
"""

from __future__ import annotations

from typing import Any

from ..domain import Notice, Session
from ..store import MessageRow
from ..store.db import from_ms
from ..store.uploads import UploadRow


def session_to_dict(session: Session) -> dict[str, Any]:
    return session.to_dict()


def message_to_dict(row: MessageRow, *, attachments: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    data: dict[str, Any] = {
        "id": row.id,
        "seq": row.seq,
        "session_id": row.session_id,
        "direction": row.direction,
        "channel": row.channel,
        "text": row.text,
        "created_at": row.created_at.isoformat(),
    }
    if row.client_msg_id is not None:
        data["client_msg_id"] = row.client_msg_id
    if row.external_id is not None:
        data["external_id"] = row.external_id
    if row.message_id is not None:
        data["message_id"] = row.message_id
    if row.refs:
        data["refs"] = list(row.refs)
    if attachments:
        data["attachments"] = attachments
    return data


def notice_to_dict(notice: Notice) -> dict[str, Any]:
    return notice.to_dict()


def upload_to_dict(row: UploadRow) -> dict[str, Any]:
    return {
        "upload_id": row.id,
        "filename": row.filename,
        "size": row.size,
        "chunk_size": row.chunk_size,
        "chunk_count": row.chunk_count,
        "received": list(row.received),
        "state": row.state,
        "expires_at": row.expires_at.isoformat(),
        "attachment_id": row.attachment_id,
    }


def ms_to_iso(value: int) -> str:
    return from_ms(value).isoformat()
