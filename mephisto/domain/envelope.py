"""`Envelope`：一条消息的统一形态（收发共用，`direction` 区分）。

对应 `contracts/envelope.schema.json`。三个设计要点：
1. `frozen=True` —— 落库后不可变，便于复盘；改状态请造新对象。
2. 时间一律 UTC 感知；`received_at` 是唯一必填时间。
3. `thread`（message_id / in_reply_to / references）**保留可空**：
   HTTP 信道没有它们，但邮件回来时不用改模型。

`Auth` / `Route` / `normalize_subject` 放在同一文件里，因为它们的唯一消费者就是信封，
且都属"关于这条消息的判断"。**不为了行数把它们拆出去**——拆分应当由职责驱动，
不是由行数驱动（见 docs/99 §9.2）。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field, replace
from datetime import datetime
from typing import Any, Mapping

from . import codec
from .values import Address, Attachment, Delivery, E2EE, ThreadRef

SCHEMA_VERSION = 1
DIRECTIONS = ("in", "out")
TRANSPORTS = ("mail", "http", "acp", "local")
SPF = ("pass", "fail", "softfail", "neutral", "none", "unknown")
DKIM = ("pass", "fail", "none", "unknown")
DMARC = ("pass", "fail", "none", "unknown")
DECIDERS = (
    "message_id_token",
    "references",
    "address",
    "subject_tag",
    "classifier",
    "fallback_chatter",
)

_PREFIX = re.compile(r"^\s*(?:re|aw|sv|回复|答复|转发|fwd|fw)\s*(?:\[\d+\])?\s*[:：]\s*", re.IGNORECASE)


def normalize_subject(text: str) -> str:
    """剥离 Re:/回复:/转发: 前缀与多余空白。路由 K3 只看这个结果。"""
    previous = None
    current = text.strip()
    while previous != current:
        previous = current
        current = _PREFIX.sub("", current).strip()
    return re.sub(r"\s+", " ", current)


@dataclass(frozen=True, slots=True)
class Auth:
    """发件人鉴权。缺失时**不要猜**——全部可空，`None` 表示未知。"""

    spf: str | None = None
    dkim: str | None = None
    dmarc: str | None = None
    sender_verified: bool | None = None

    def to_dict(self) -> dict[str, Any]:
        data: dict[str, Any] = {}
        for key in ("spf", "dkim", "dmarc", "sender_verified"):
            value = getattr(self, key)
            if value is not None:
                data[key] = value
        return data

    @classmethod
    def from_dict(cls, value: Any, where: str) -> Auth:
        data = codec.expect_mapping(value, where)
        codec.expect_keys(data, ("spf", "dkim", "dmarc", "sender_verified"), where)
        verified = data.get("sender_verified")
        return cls(
            spf=_opt_enum(data, "spf", SPF, where),
            dkim=_opt_enum(data, "dkim", DKIM, where),
            dmarc=_opt_enum(data, "dmarc", DMARC, where),
            sender_verified=codec.as_bool(verified, f"{where}.sender_verified") if verified is not None else None,
        )


@dataclass(frozen=True, slots=True)
class Route:
    """路由决策。`decided_by == "classifier"` 时 `evidence` 必须非空——可复盘是硬要求。"""

    session_id: str
    decided_by: str
    confidence: float | None = None
    evidence: str | None = None

    def __post_init__(self) -> None:
        if self.decided_by not in DECIDERS:
            raise ValueError(f"decided_by 非法：{self.decided_by!r}")
        if self.decided_by == "classifier" and not self.evidence:
            raise ValueError("classifier 决策必须给出 evidence（引用原文片段）")
        if self.confidence is not None and not 0.0 <= self.confidence <= 1.0:
            raise ValueError(f"confidence 应在 [0,1]，实际 {self.confidence}")
        if not self.session_id:
            raise ValueError("session_id 不能为空")

    def to_dict(self) -> dict[str, Any]:
        data: dict[str, Any] = {"session_id": self.session_id, "decided_by": self.decided_by}
        if self.confidence is not None:
            data["confidence"] = self.confidence
        if self.evidence is not None:
            data["evidence"] = self.evidence
        return data

    @classmethod
    def from_dict(cls, value: Any, where: str) -> Route:
        data = codec.expect_mapping(value, where)
        codec.expect_keys(data, ("session_id", "decided_by", "confidence", "evidence"), where)
        confidence = data.get("confidence")
        return cls(
            session_id=codec.as_str(codec.get(data, "session_id", where, required=True), f"{where}.session_id"),
            decided_by=codec.as_enum(
                codec.get(data, "decided_by", where, required=True), DECIDERS, f"{where}.decided_by"
            ),
            confidence=float(confidence) if confidence is not None else None,
            evidence=data.get("evidence"),
        )


@dataclass(frozen=True, slots=True)
class Envelope:
    id: str
    direction: str
    transport: str
    received_at: datetime
    sender: Address
    recipients: tuple[Address, ...]
    subject: str = ""
    text: str = ""
    cc: tuple[Address, ...] = field(default_factory=tuple)
    subject_normalized: str = ""
    html_path: str | None = None
    attachments: tuple[Attachment, ...] = field(default_factory=tuple)
    thread: ThreadRef = field(default_factory=ThreadRef)
    headers: Mapping[str, str] = field(default_factory=dict)
    auth: Auth | None = None
    route: Route | None = None
    delivery: Delivery = field(default_factory=Delivery)
    e2ee: E2EE | None = None
    client_msg_id: str | None = None
    spool_path: str | None = None
    raw_eml: str | None = None
    v: int = SCHEMA_VERSION

    def __post_init__(self) -> None:
        if self.v != SCHEMA_VERSION:
            raise ValueError(f"不支持的 Envelope 版本：{self.v}")
        if self.direction not in DIRECTIONS:
            raise ValueError(f"direction 非法：{self.direction!r}")
        if self.transport not in TRANSPORTS:
            raise ValueError(f"transport 非法：{self.transport!r}")
        if self.received_at.tzinfo is None:
            raise ValueError("received_at 必须带时区")
        if not self.recipients:
            raise ValueError("recipients 至少一个")
        if self.subject_normalized and normalize_subject(self.subject_normalized) != normalize_subject(self.subject):
            raise ValueError("subject_normalized 与 subject 不一致")

    def with_route(self, route: Route | None) -> Envelope:
        return replace(self, route=route)

    def to_dict(self) -> dict[str, Any]:
        data: dict[str, Any] = {
            "v": self.v,
            "id": self.id,
            "direction": self.direction,
            "transport": self.transport,
            "received_at": codec.to_iso(self.received_at),
            "from": self.sender.to_dict(),
            "to": [item.to_dict() for item in self.recipients],
            "subject": self.subject,
            "body": self._body_dict(),
            "attachments": [item.to_dict() for item in self.attachments],
            "delivery": self.delivery.to_dict(),
        }
        if self.cc:
            data["cc"] = [item.to_dict() for item in self.cc]
        if self.subject_normalized:
            data["subject_normalized"] = self.subject_normalized
        if self.thread.to_dict():
            data["thread"] = self.thread.to_dict()
        if self.headers:
            data["headers"] = dict(self.headers)
        if self.auth is not None:
            data["auth"] = self.auth.to_dict()
        if self.route is not None:
            data["route"] = self.route.to_dict()
        if self.e2ee is not None:
            data["e2ee"] = self.e2ee.to_dict()
        if self.client_msg_id is not None:
            data["client_msg_id"] = self.client_msg_id
        if self.spool_path is not None:
            data["spool_path"] = self.spool_path
        if self.raw_eml is not None:
            data["raw_eml"] = self.raw_eml
        return data

    def _body_dict(self) -> dict[str, Any]:
        body: dict[str, Any] = {"text": self.text}
        if self.html_path is not None:
            body["html_path"] = self.html_path
        return body

    @classmethod
    def from_dict(cls, value: Any, where: str = "envelope") -> Envelope:
        data = codec.expect_mapping(value, where)
        codec.expect_keys(
            data,
            (
                "v", "id", "direction", "transport", "received_at", "from", "to", "cc",
                "subject", "subject_normalized", "body", "attachments", "thread",
                "headers", "auth", "route", "delivery", "e2ee", "client_msg_id",
                "spool_path", "raw_eml",
            ),
            where,
        )
        version = codec.as_int(codec.get(data, "v", where, default=SCHEMA_VERSION), f"{where}.v")
        if version != SCHEMA_VERSION:
            raise codec.DecodeError(f"{where}.v = {version}，只支持 {SCHEMA_VERSION}")

        body = codec.expect_mapping(codec.get(data, "body", where, required=True), f"{where}.body")
        codec.expect_keys(body, ("text", "html_path"), f"{where}.body")

        recipients = tuple(
            Address.from_dict(item, f"{where}.to[{i}]")
            for i, item in enumerate(
                codec.as_list(codec.get(data, "to", where, required=True), f"{where}.to", min_items=1)
            )
        )
        cc = tuple(
            Address.from_dict(item, f"{where}.cc[{i}]")
            for i, item in enumerate(codec.as_list(codec.get(data, "cc", where, default=[]), f"{where}.cc"))
        )
        attachments = tuple(
            Attachment.from_dict(item, f"{where}.attachments[{i}]")
            for i, item in enumerate(
                codec.as_list(
                    codec.get(data, "attachments", where, default=[]), f"{where}.attachments", max_items=50
                )
            )
        )
        headers = codec.get(data, "headers", where, default={}) or {}
        if not isinstance(headers, Mapping):
            raise codec.DecodeError(f"{where}.headers 应为对象")

        subject = codec.as_str(codec.get(data, "subject", where, default=""), f"{where}.subject", max_len=998)
        explicit = data.get("subject_normalized")

        return cls(
            id=codec.as_ulid(codec.get(data, "id", where, required=True), f"{where}.id"),
            direction=codec.as_enum(
                codec.get(data, "direction", where, required=True), DIRECTIONS, f"{where}.direction"
            ),
            transport=codec.as_enum(
                codec.get(data, "transport", where, required=True), TRANSPORTS, f"{where}.transport"
            ),
            received_at=codec.as_utc(codec.get(data, "received_at", where, required=True), f"{where}.received_at"),
            sender=Address.from_dict(codec.get(data, "from", where, required=True), f"{where}.from"),
            recipients=recipients,
            subject=subject,
            text=codec.as_str(codec.get(body, "text", where, required=True), f"{where}.body.text"),
            cc=cc,
            subject_normalized=(
                codec.as_str(explicit, f"{where}.subject_normalized")
                if explicit is not None
                else normalize_subject(subject)
            ),
            html_path=body.get("html_path"),
            attachments=attachments,
            thread=ThreadRef.from_dict(codec.get(data, "thread", where, default={}) or {}, f"{where}.thread"),
            headers={str(k): str(v) for k, v in headers.items()},
            auth=Auth.from_dict(data["auth"], f"{where}.auth") if data.get("auth") is not None else None,
            route=Route.from_dict(data["route"], f"{where}.route") if data.get("route") is not None else None,
            delivery=Delivery.from_dict(codec.get(data, "delivery", where, default={}) or {}, f"{where}.delivery"),
            e2ee=E2EE.from_dict(data["e2ee"], f"{where}.e2ee") if data.get("e2ee") is not None else None,
            client_msg_id=data.get("client_msg_id"),
            spool_path=data.get("spool_path"),
            raw_eml=data.get("raw_eml"),
        )


def _opt_enum(data: Mapping[str, Any], key: str, allowed: tuple[str, ...], where: str) -> str | None:
    if data.get(key) is None:
        return None
    return codec.as_enum(data[key], allowed, f"{where}.{key}")
