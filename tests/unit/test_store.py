from __future__ import annotations

import contextlib
import io
import shutil
import sqlite3
import unittest
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

from mephisto.domain import Attachment, Route, Session
from mephisto.ids import new_ulid
from mephisto.store import AttachmentRepo, EventRepo, MessageRepo, SessionRepo, open_db

TMP_ROOT = Path(__file__).resolve().parent / "_tmp"
T0 = datetime(2026, 10, 26, 7, 0, tzinfo=UTC)


class StoreTestBase(unittest.TestCase):
    def setUp(self) -> None:
        TMP_ROOT.mkdir(parents=True, exist_ok=True)
        self.dir = TMP_ROOT / f"store_{uuid.uuid4().hex[:12]}"
        self.dir.mkdir()
        self.db = open_db(self.dir)
        self.sessions = SessionRepo(self.db)
        self.messages = MessageRepo(self.db)
        self.attachments = AttachmentRepo(self.db)
        self.events = EventRepo(self.db)

    def tearDown(self) -> None:
        self.db.close()
        shutil.rmtree(self.dir, ignore_errors=True)

    def add_session(self, *, title: str = "交表这件事", seq: int = 0) -> Session:
        session = Session(
            id=new_ulid(),
            title=title,
            kind="task",
            created_at=T0,
            last_active_at=T0,
            seq=seq,
        )
        return self.sessions.insert(session)


class MigrationTest(StoreTestBase):
    def test_migrate_creates_latest_version(self) -> None:
        before, after = self.db.migrate()
        self.assertEqual((before, after), (0, 2))
        self.assertEqual(self.db.current_version(), 2)

    def test_migrate_is_idempotent(self) -> None:
        self.db.migrate()
        before, after = self.db.migrate()
        self.assertEqual((before, after), (2, 2), "第二次迁移不能改动版本号")

    def test_wal_mode(self) -> None:
        self.db.migrate()
        self.assertEqual(self.db.check()["journal_mode"], "wal")

    def test_all_tables_exist(self) -> None:
        self.db.migrate()
        tables = set(self.db.check()["tables"])
        expected = {
            "attachment", "device", "device_cursor", "device_token", "event_log", "job",
            "message", "notice", "reminder", "route_decision", "session", "session_alias", "upload",
        }
        self.assertEqual(expected - tables, set(), "DDL 缺表")

    def test_check_on_fresh_db_reports_uninitialised(self) -> None:
        info = self.db.check()
        self.assertEqual(info["user_version"], 0)
        self.assertEqual(info["integrity"], "ok")


class SessionRepoTest(StoreTestBase):
    def setUp(self) -> None:
        super().setUp()
        self.db.migrate()

    def test_insert_and_get(self) -> None:
        session = self.add_session(seq=3)
        loaded = self.sessions.get(session.id)
        self.assertIsNotNone(loaded)
        assert loaded is not None
        self.assertEqual(loaded.title, session.title)
        self.assertEqual(loaded.created_at, T0)
        self.assertEqual(loaded.seq, 3)

    def test_list_orders_by_activity(self) -> None:
        first = self.add_session(title="早")
        later = self.add_session(title="晚")
        self.sessions.update_activity(
            Session(
                id=later.id,
                title=later.title,
                kind=later.kind,
                created_at=later.created_at,
                last_active_at=T0 + timedelta(hours=2),
                seq=5,
            )
        )
        ids = [item.id for item in self.sessions.list()]
        self.assertEqual(ids, [later.id, first.id])

    def test_archive(self) -> None:
        session = self.add_session()
        self.assertTrue(self.sessions.archive(session.id))
        loaded = self.sessions.get(session.id)
        assert loaded is not None
        self.assertEqual(loaded.state, "archived")
        self.assertEqual(self.sessions.list(state="active"), [])

    def test_alias_is_unique_and_idempotent(self) -> None:
        session = self.add_session()
        self.assertTrue(self.sessions.add_alias("subject_tag", "[T:交表]", session.id))
        self.assertFalse(self.sessions.add_alias("subject_tag", "[T:交表]", session.id), "重复别名不该报错")
        self.assertEqual(self.sessions.resolve_alias("subject_tag", "[T:交表]"), session.id)
        self.assertIsNone(self.sessions.resolve_alias("subject_tag", "[T:不存在]"))
        self.assertEqual(self.sessions.aliases(session.id), [("subject_tag", "[T:交表]")])

    def test_record_decision_requires_evidence_for_classifier(self) -> None:
        session = self.add_session()
        route = Route(session_id=session.id, decided_by="classifier", evidence="主题含『交表』", confidence=0.7)
        decision_id = self.sessions.record_decision("env-1", route)
        rows = self.sessions.decisions()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["id"], decision_id)
        self.assertEqual(rows[0]["decided_by"], "classifier")


class MessageRepoTest(StoreTestBase):
    def setUp(self) -> None:
        super().setUp()
        self.db.migrate()
        self.session = self.add_session()

    def test_append_assigns_monotonic_seq(self) -> None:
        rows = [
            self.messages.append(
                session_id=self.session.id, direction="in", channel="http", text=f"第{i}条", at=T0
            )[0]
            for i in range(5)
        ]
        seqs = [row.seq for row in rows]
        self.assertEqual(seqs, sorted(seqs))
        self.assertEqual(len(set(seqs)), 5)

    def test_client_msg_id_dedupes(self) -> None:
        first, created = self.messages.append(
            session_id=self.session.id, direction="in", channel="http", text="你好", at=T0, client_msg_id="c_1"
        )
        second, created_again = self.messages.append(
            session_id=self.session.id, direction="in", channel="http", text="你好（重试）", at=T0, client_msg_id="c_1"
        )
        self.assertTrue(created)
        self.assertFalse(created_again, "同一 client_msg_id 必须幂等")
        self.assertEqual(first.seq, second.seq)
        self.assertEqual(second.text, "你好", "返回的是既有行，不是新内容")

    def test_channel_external_id_dedupes(self) -> None:
        _, created = self.messages.append(
            session_id=self.session.id, direction="in", channel="mail", text="a", at=T0, external_id="uid-1"
        )
        _, again = self.messages.append(
            session_id=self.session.id, direction="in", channel="mail", text="a", at=T0, external_id="uid-1"
        )
        self.assertTrue(created)
        self.assertFalse(again)

    def test_same_external_id_on_other_channel_is_fine(self) -> None:
        _, created = self.messages.append(
            session_id=self.session.id, direction="in", channel="mail", text="a", at=T0, external_id="1"
        )
        _, other = self.messages.append(
            session_id=self.session.id, direction="in", channel="http", text="a", at=T0, external_id="1"
        )
        self.assertTrue(created)
        self.assertTrue(other, "唯一性按 (channel, external_id) 组合算")

    def test_list_by_session_and_since(self) -> None:
        for i in range(3):
            self.messages.append(
                session_id=self.session.id, direction="in", channel="http", text=str(i), at=T0
            )
        all_rows = self.messages.list_by_session(self.session.id)
        self.assertEqual(len(all_rows), 3)
        tail = self.messages.list_by_session(self.session.id, since_seq=all_rows[0].seq)
        self.assertEqual(len(tail), 2)
        self.assertEqual(self.messages.last_seq(), all_rows[-1].seq)

    def test_refs_roundtrip(self) -> None:
        row, _ = self.messages.append(
            session_id=self.session.id,
            direction="in",
            channel="mail",
            text="a",
            at=T0,
            refs=("<a@x>", "<b@x>"),
        )
        loaded = self.messages.get(row.id)
        assert loaded is not None
        self.assertEqual(loaded.refs, ("<a@x>", "<b@x>"))

    def test_delete_session_with_messages_is_restricted(self) -> None:
        self.messages.append(session_id=self.session.id, direction="in", channel="http", text="a", at=T0)
        with self.assertRaises(sqlite3.IntegrityError):
            self.db.execute("DELETE FROM session WHERE id = ?", (self.session.id,))

    def test_delete_session_without_messages_works(self) -> None:
        self.db.execute("DELETE FROM session WHERE id = ?", (self.session.id,))
        self.assertIsNone(self.sessions.get(self.session.id))

    def test_attachments_ordered_by_idx(self) -> None:
        row, _ = self.messages.append(
            session_id=self.session.id, direction="in", channel="http", text="a", at=T0
        )
        for idx, name in ((1, "b.pdf"), (0, "a.pdf")):
            self.attachments.add(
                row.id, idx, Attachment(filename=name, size=idx + 1, digest="sha256:" + "c" * 64)
            )
        names = [item.filename for item in self.attachments.list_by_message(row.id)]
        self.assertEqual(names, ["a.pdf", "b.pdf"])


class EventRepoTest(StoreTestBase):
    def setUp(self) -> None:
        super().setUp()
        self.db.migrate()

    def test_append_and_read_since(self) -> None:
        first = self.events.append("job.running", {"session_id": "s1"}, at=T0)
        second = self.events.append("job.done", {"ok": True}, at=T0, session_id="s1")
        self.assertGreater(second, first)
        rows = self.events.list_since(0)
        self.assertEqual([row.kind for row in rows], ["job.running", "job.done"])
        self.assertEqual(rows[1].payload, {"ok": True})
        self.assertEqual(self.events.list_since(first), rows[1:])

    def test_last_seq_and_count(self) -> None:
        self.assertEqual(self.events.last_seq(), 0)
        self.events.append("x", {}, at=T0)
        self.assertEqual(self.events.last_seq(), 1)
        self.assertEqual(self.events.count(), 1)


class DbCheckTest(StoreTestBase):
    def test_check_reports_counts(self) -> None:
        self.db.migrate()
        session = self.add_session()
        self.messages.append(session_id=session.id, direction="in", channel="http", text="a", at=T0)
        info = self.db.check()
        self.assertEqual(info["integrity"], "ok")
        self.assertEqual(info["foreign_key_problems"], 0)
        self.assertEqual(info["tables"]["session"], 1)
        self.assertEqual(info["tables"]["message"], 1)


class CliDbTest(unittest.TestCase):
    def setUp(self) -> None:
        from mephisto.cli import main

        self.main = main
        TMP_ROOT.mkdir(parents=True, exist_ok=True)
        self.case = TMP_ROOT / f"cli_{uuid.uuid4().hex[:12]}"
        self.case.mkdir()
        self.config = self.case / "mephisto.toml"
        # data_dir 是相对**仓库根**解析的（单部署只有一个 state 目录），
        # 所以测试必须给绝对路径，否则会写进仓库自己的 state/。
        state_dir = (self.case / "state").as_posix()
        self.config.write_text(f'[storage]\ndata_dir = "{state_dir}"\n', encoding="utf-8")

    def tearDown(self) -> None:
        shutil.rmtree(self.case, ignore_errors=True)

    def test_db_check_before_migrate_exits_1(self) -> None:
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = self.main(["--config", str(self.config), "db", "check"])
        self.assertEqual(code, 1)
        self.assertIn("尚未初始化", out.getvalue())

    def test_db_migrate_then_check_exits_0(self) -> None:
        migrate_out = io.StringIO()
        with contextlib.redirect_stdout(migrate_out):
            self.assertEqual(self.main(["--config", str(self.config), "db", "migrate"]), 0)
        self.assertIn("v0 → v2", migrate_out.getvalue())

        check_out = io.StringIO()
        with contextlib.redirect_stdout(check_out):
            self.assertEqual(self.main(["--config", str(self.config), "db", "check"]), 0)
        self.assertIn("结论：通过", check_out.getvalue())

    def test_migrate_twice_reports_latest(self) -> None:
        with contextlib.redirect_stdout(io.StringIO()):
            self.main(["--config", str(self.config), "db", "migrate"])
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            self.assertEqual(self.main(["--config", str(self.config), "db", "migrate"]), 0)
        self.assertIn("已是最新", out.getvalue())


if __name__ == "__main__":
    unittest.main()
