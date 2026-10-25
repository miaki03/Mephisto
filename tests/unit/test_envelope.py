from __future__ import annotations

import unittest
from datetime import UTC, datetime

from mephisto.domain import Envelope, Route, normalize_subject
from mephisto.domain.codec import DecodeError
from mephisto.ids import new_ulid

NOW = datetime(2026, 10, 26, 7, 0, tzinfo=UTC)


def make_envelope(**overrides) -> dict:
    data = {
        "v": 1,
        "id": new_ulid(),
        "direction": "in",
        "transport": "http",
        "received_at": "2026-10-26T07:00:00+00:00",
        "from": {"address": "owner@mephisto.local", "name": "我"},
        "to": [{"address": "agent@mephisto.local"}],
        "subject": "",
        "body": {"text": "帮我看看明天的天气"},
        "attachments": [],
    }
    data.update(overrides)
    return data


class SubjectNormalizeTest(unittest.TestCase):
    def test_strips_chinese_and_english_prefixes(self) -> None:
        self.assertEqual(normalize_subject("Re: 回复: 转发: 交表"), "交表")
        self.assertEqual(normalize_subject("RE[2]: 秋游"), "秋游")
        self.assertEqual(normalize_subject("  Fwd:   a   b  "), "a b")

    def test_keeps_plain_subject(self) -> None:
        self.assertEqual(normalize_subject("交表"), "交表")


class EnvelopeRoundtripTest(unittest.TestCase):
    def test_minimal_roundtrip(self) -> None:
        env = Envelope.from_dict(make_envelope())
        again = Envelope.from_dict(env.to_dict())
        self.assertEqual(env, again)
        self.assertEqual(env.subject_normalized, "")
        self.assertIsNone(env.route)
        self.assertIsNone(env.e2ee)

    def test_full_roundtrip_with_mail_fields(self) -> None:
        raw = make_envelope(
            transport="mail",
            subject="Re: 秋游报名",
            thread={
                "message_id": "<01J0000000000000000000000A@mephisto.local>",
                "in_reply_to": "<01J0000000000000000000000B@mephisto.local>",
                "references": ["<a@x>", "<b@x>"],
            },
            cc=[{"address": "b@x"}],
            attachments=[{"filename": "报名表.pdf", "size": 1024, "digest": "sha256:" + "a" * 64}],
            headers={"X-Whatever": "1"},
            auth={"spf": "pass", "dkim": "pass", "dmarc": "pass", "sender_verified": True},
            route={"session_id": "s1", "decided_by": "subject_tag", "confidence": 0.9},
            delivery={"delivered_at": "2026-10-26T07:00:10+00:00", "seen_at": None, "acked_by": None},
            e2ee={"scheme": "none", "verified": False},
            client_msg_id="c_abc",
        )
        env = Envelope.from_dict(raw)
        self.assertEqual(env.subject_normalized, "秋游报名")
        self.assertEqual(env.thread.references, ("<a@x>", "<b@x>"))
        self.assertEqual(env.route.decided_by, "subject_tag")
        self.assertIsNone(env.delivery.seen_at, "null 表示未知，不能变成 0 或 False")
        self.assertEqual(Envelope.from_dict(env.to_dict()), env)

    def test_thread_fields_are_optional(self) -> None:
        """HTTP 信道没有邮件头，但模型里要留着这三个可空字段。"""
        env = Envelope.from_dict(make_envelope())
        self.assertIsNone(env.thread.message_id)
        self.assertIsNone(env.thread.in_reply_to)
        self.assertEqual(env.thread.references, ())

    def test_unknown_field_rejected(self) -> None:
        with self.assertRaises(DecodeError) as ctx:
            Envelope.from_dict(make_envelope(surprise=1))
        self.assertIn("surprise", str(ctx.exception))

    def test_bad_direction_rejected(self) -> None:
        with self.assertRaises(DecodeError):
            Envelope.from_dict(make_envelope(direction="sideways"))

    def test_bad_transport_rejected(self) -> None:
        with self.assertRaises(DecodeError):
            Envelope.from_dict(make_envelope(transport="telegram"))

    def test_empty_recipients_rejected(self) -> None:
        with self.assertRaises(DecodeError):
            Envelope.from_dict(make_envelope(to=[]))

    def test_bad_digest_rejected(self) -> None:
        with self.assertRaises(DecodeError):
            Envelope.from_dict(make_envelope(attachments=[{"filename": "a", "size": 1, "digest": "md5:zz"}]))

    def test_version_mismatch_rejected(self) -> None:
        with self.assertRaises(DecodeError):
            Envelope.from_dict(make_envelope(v=2))

    def test_with_route_returns_new_object(self) -> None:
        env = Envelope.from_dict(make_envelope())
        routed = env.with_route(Route(session_id="s1", decided_by="fallback_chatter"))
        self.assertIsNone(env.route)
        self.assertEqual(routed.route.session_id, "s1")


class RouteTest(unittest.TestCase):
    def test_classifier_requires_evidence(self) -> None:
        with self.assertRaises(ValueError):
            Route(session_id="s1", decided_by="classifier")
        Route(session_id="s1", decided_by="classifier", evidence="主题含『交表』")

    def test_confidence_bounds(self) -> None:
        with self.assertRaises(ValueError):
            Route(session_id="s1", decided_by="address", confidence=1.5)

    def test_unknown_decider_rejected(self) -> None:
        with self.assertRaises(ValueError):
            Route(session_id="s1", decided_by="magic")


if __name__ == "__main__":
    unittest.main()
