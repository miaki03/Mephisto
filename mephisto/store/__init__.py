"""存储层：SQLite 连接、迁移、各表的读写。

用法（装配在 `app/daemon.py`，第 5 步）：
    db = open_db(config.data_dir)
    db.migrate()
    sessions = SessionRepo(db)
    messages = MessageRepo(db)

**表全建、仓库按需写**：DDL 是一次性成本且是契约的一部分（12 张表 + upload），
但只为当前用得上的表写 repo，避免留下一堆没人调用的死代码。
`notice` / `reminder` / `job` / `device` / `upload` 的读写分别在 S6 / S5 / S5 / S9 / S9 落地。
"""

from __future__ import annotations

from pathlib import Path

from .db import Database, from_ms, to_ms
from .devices import DeviceInfo, DeviceRepo
from .jobs import JobRepo, JobRow
from .events import EventRepo, EventRow
from .messages import AttachmentRepo, MessageRepo, MessageRow
from .notices import NoticeRepo
from .sessions import SessionRepo
from .uploads import UploadRepo, UploadRow

DB_FILENAME = "mephisto.db"


def open_db(data_dir: Path | str) -> Database:
    return Database(Path(data_dir) / DB_FILENAME)


__all__ = [
    "DeviceInfo",
    "DeviceRepo",
    "JobRepo",
    "JobRow",
    "AttachmentRepo",
    "DB_FILENAME",
    "Database",
    "EventRepo",
    "EventRow",
    "MessageRepo",
    "MessageRow",
    "NoticeRepo",
    "SessionRepo",
    "UploadRepo",
    "UploadRow",
    "from_ms",
    "open_db",
    "to_ms",
]
