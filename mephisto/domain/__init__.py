"""领域层：纯函数与不可变数据，**不 import 任何 IO**。

这一层是唯一允许"业务判断"的地方——路由、状态机、约束都在这。
适配器（HTTP / DSH / SQLite）只负责翻译，不许在别处写分支。
"""

from .codec import DecodeError
from .envelope import DECIDERS, DKIM, DMARC, SPF, Auth, Envelope, Route, normalize_subject
from .notice import NOTICE_KINDS, NOTICE_STATES, TERMINAL_STATES, Notice
from .session import SESSION_KINDS, SESSION_STATES, Session
from .values import Address, Attachment, Delivery, E2EE, ThreadRef

__all__ = [
    "Address",
    "Attachment",
    "Auth",
    "DECIDERS",
    "DKIM",
    "DMARC",
    "DecodeError",
    "Delivery",
    "E2EE",
    "Envelope",
    "NOTICE_KINDS",
    "NOTICE_STATES",
    "Notice",
    "Route",
    "SESSION_KINDS",
    "SESSION_STATES",
    "SPF",
    "Session",
    "TERMINAL_STATES",
    "ThreadRef",
    "normalize_subject",
]
