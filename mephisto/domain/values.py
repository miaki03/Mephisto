"""领域值对象：不含 IO、不依赖数据库、可自由编解码。

它们对应 `contracts/envelope.schema.json` 里的嵌套结构。字段命名与契约一致，
避免"代码里叫 A、JSON 里叫 B"这种长期成本。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Mapping

from . import codec

# 契约里的枚举。放在这里而不是散落在各处，改契约时只有一处要动。
SPF = ("pass", "fail", "softfail", "neutral", "none", "unknown")
DKIM = ("pass", "fail", "none", "unknown")
DMARC = ("pass", "fail", "none", "unknown")
DECIDED_BY = (
    "message_id_token",
    "references",
    "address",
    "subject_tag",
    "classifier",
    "fallback_chatter",
)
E2EE_SCHEMES = ("none", "autocrypt", "signal")


@dataclass(frozen=True, slots=True)
class Address:
    address: str
    name: str | None = None

    def to_dict(self) -> dict[str, Any]:
        data: dict[str, Any] = {"address": self.address}
        if self.name is not None:
            data["name"] = self.name
        return data

    @classmethod
    def from_dict(cls, value: Any, where: str) -> Address:
        data = codec.expect_mapping(value, where)
        codec.expect_keys(data, ("address", "name"), where)
        return cls(
            address=codec.as_str(codec.get(data, "address", where, required=True), f"{where}.address", max_len=320),
            name=_opt_str(data, "name", where, 200),
        )


@dataclass(frozen=True, slots=True)
class Attachment:
    filename: str
    size: int
    digest: str          # 形如 sha256:<64 hex>
    media_type: str | None = None
    path: str | None = None

    def to_dict(self) -> dict[str, Any]:
        data: dict[str, Any] = {"filename": self.filename, "size": self.size, "digest": self.digest}
        if self.media_type is not None:
            data["media_type"] = self.media_type
        if self.path is not None:
            data["path"] = self.path
        return data

    @classmethod
    def from_dict(cls, value: Any, where: str) -> Attachment:
        data = codec.expect_mapping(value, where)
        codec.expect_keys(data, ("filename", "media_type", "size", "digest", "path"), where)
        digest = codec.as_str(codec.get(data, "digest", where, required=True), f"{where}.digest")
        if not (digest.startswith("sha256:") and len(digest) == 71):
            raise codec.DecodeError(f"{where}.digest 应形如 sha256:<64 hex>，实际 {digest!r}")
        return cls(
            filename=codec.as_str(codec.get(data, "filename", where, required=True), f"{where}.filename", max_len=255),
            size=codec.as_int(codec.get(data, "size", where, required=True), f"{where}.size", minimum=0),
            digest=digest,
            media_type=_opt_str(data, "media_type", where),
            path=_opt_str(data, "path", where),
        )


@dataclass(frozen=True, slots=True)
class ThreadRef:
    """邮件线程头。三个字段都可空——HTTP 信道根本没有它们，但模型里要留着。"""

    message_id: str | None = None
    in_reply_to: str | None = None
    references: tuple[str, ...] = field(default_factory=tuple)

    def to_dict(self) -> dict[str, Any]:
        data: dict[str, Any] = {}
        if self.message_id is not None:
            data["message_id"] = self.message_id
        if self.in_reply_to is not None:
            data["in_reply_to"] = self.in_reply_to
        if self.references:
            data["references"] = list(self.references)
        return data

    @classmethod
    def from_dict(cls, value: Any, where: str) -> ThreadRef:
        data = codec.expect_mapping(value, where)
        codec.expect_keys(data, ("message_id", "in_reply_to", "references"), where)
        refs: tuple[str, ...] = ()
        if "references" in data:
            refs = tuple(
                codec.as_str(item, f"{where}.references[{i}]")
                for i, item in enumerate(codec.as_list(data["references"], f"{where}.references"))
            )
        return cls(
            message_id=_opt_str(data, "message_id", where),
            in_reply_to=_opt_str(data, "in_reply_to", where),
            references=refs,
        )


@dataclass(frozen=True, slots=True)
class Delivery:
    """投递状态。全部可空——**null 表示未知，不是"没有"**。"""

    delivered_at: datetime | None = None
    seen_at: datetime | None = None
    acked_by: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "delivered_at": codec.to_iso(self.delivered_at) if self.delivered_at else None,
            "seen_at": codec.to_iso(self.seen_at) if self.seen_at else None,
            "acked_by": self.acked_by,
        }

    @classmethod
    def from_dict(cls, value: Any, where: str) -> Delivery:
        data = codec.expect_mapping(value, where)
        codec.expect_keys(data, ("delivered_at", "seen_at", "acked_by"), where)
        return cls(
            delivered_at=_opt_utc(data, "delivered_at", where),
            seen_at=_opt_utc(data, "seen_at", where),
            acked_by=_opt_str(data, "acked_by", where),
        )


@dataclass(frozen=True, slots=True)
class E2EE:
    scheme: str = "none"
    verified: bool = False
    key_fingerprint: str | None = None

    def to_dict(self) -> dict[str, Any]:
        data: dict[str, Any] = {"scheme": self.scheme, "verified": self.verified}
        if self.key_fingerprint is not None:
            data["key_fingerprint"] = self.key_fingerprint
        return data

    @classmethod
    def from_dict(cls, value: Any, where: str) -> E2EE:
        data = codec.expect_mapping(value, where)
        codec.expect_keys(data, ("scheme", "verified", "key_fingerprint"), where)
        return cls(
            scheme=codec.as_enum(codec.get(data, "scheme", where, default="none"), E2EE_SCHEMES, f"{where}.scheme"),
            verified=codec.as_bool(codec.get(data, "verified", where, default=False), f"{where}.verified"),
            key_fingerprint=_opt_str(data, "key_fingerprint", where),
        )


def _opt_str(data: Mapping[str, Any], key: str, where: str, max_len: int | None = None) -> str | None:
    if key not in data or data[key] is None:
        return None
    return codec.as_str(data[key], f"{where}.{key}", max_len=max_len)


def _opt_utc(data: Mapping[str, Any], key: str, where: str) -> datetime | None:
    if key not in data or data[key] is None:
        return None
    return codec.as_utc(data[key], f"{where}.{key}")
