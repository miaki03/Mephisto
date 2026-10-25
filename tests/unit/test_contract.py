"""契约测试：用 `contracts/envelope.schema.json` 校对领域对象的产出。

不引 `jsonschema`（零依赖），只做键集合级别的校验——
它能抓住绝大多数真实错误（漏字段、多字段、改名字），而且跑得极快。
"""

from __future__ import annotations

import json
import re
import unittest
from datetime import UTC, datetime
from pathlib import Path

from mephisto.domain import Envelope
from mephisto.ids import new_ulid

ROOT = Path(__file__).resolve().parents[2]
SCHEMA = json.loads((ROOT / "contracts" / "envelope.schema.json").read_text(encoding="utf-8"))


def sample_envelope() -> Envelope:
    return Envelope.from_dict(
        {
            "v": 1,
            "id": new_ulid(),
            "direction": "in",
            "transport": "mail",
            "received_at": "2026-10-26T07:00:00+00:00",
            "from": {"address": "a@mephisto.local", "name": "我"},
            "to": [{"address": "agent@mephisto.local"}],
            "cc": [{"address": "c@mephisto.local"}],
            "subject": "Re: 交表",
            "body": {"text": "正文", "html_path": "files/x.html"},
            "attachments": [{"filename": "a.pdf", "size": 10, "digest": "sha256:" + "b" * 64, "path": "files/a.pdf"}],
            "thread": {"message_id": "<x@y>", "in_reply_to": "<w@y>", "references": ["<w@y>"]},
            "headers": {"X-A": "1"},
            "auth": {"spf": "pass", "dkim": "pass", "dmarc": "pass", "sender_verified": True},
            "route": {"session_id": "s1", "decided_by": "subject_tag", "confidence": 0.8},
            "delivery": {"delivered_at": "2026-10-26T07:00:01+00:00", "seen_at": None, "acked_by": None},
            "e2ee": {"scheme": "none", "verified": False},
            "client_msg_id": "c_x",
            "spool_path": "state/inbox/x",
            "raw_eml": "state/inbox/x/raw.eml",
        }
    )


class EnvelopeContractTest(unittest.TestCase):
    def setUp(self) -> None:
        self.data = sample_envelope().to_dict()
        self.declared = set(SCHEMA["properties"])

    def test_no_undeclared_top_level_keys(self) -> None:
        self.assertEqual(set(self.data) - self.declared, set())

    def test_all_required_present(self) -> None:
        self.assertEqual(set(SCHEMA["required"]) - set(self.data), set())

    def test_nested_keys_are_declared(self) -> None:
        for key in ("body", "thread", "delivery", "auth", "route", "e2ee"):
            with self.subTest(nested=key):
                allowed = set(SCHEMA["properties"][key].get("properties", {}))
                self.assertTrue(set(self.data[key]) <= allowed, f"{key} 有多余字段")

        addr_keys = set(SCHEMA["$defs"]["addr"]["properties"])
        for addr in self.data["to"] + self.data["cc"]:
            self.assertTrue(set(addr) <= addr_keys)

        item_keys = set(SCHEMA["properties"]["attachments"]["items"]["properties"])
        for item in self.data["attachments"]:
            self.assertTrue(set(item) <= item_keys)

    def test_id_matches_ulid_pattern(self) -> None:
        self.assertRegex(self.data["id"], SCHEMA["$defs"]["ulid"]["pattern"])

    def test_enums_stay_inside_schema(self) -> None:
        self.assertIn(self.data["transport"], SCHEMA["properties"]["transport"]["enum"])
        self.assertIn(self.data["direction"], SCHEMA["properties"]["direction"]["enum"])
        self.assertIn(
            self.data["route"]["decided_by"], SCHEMA["properties"]["route"]["properties"]["decided_by"]["enum"]
        )

    def test_digest_pattern(self) -> None:
        pattern = SCHEMA["properties"]["attachments"]["items"]["properties"]["digest"]["pattern"]
        self.assertTrue(all(re.fullmatch(pattern, item["digest"]) for item in self.data["attachments"]))


if __name__ == "__main__":
    unittest.main()
