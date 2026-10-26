"""提醒服务：把"一句话"变成规则，再把规则展开成 notice。

分工：
- `create_from_text("3分钟后 提醒我喝水")` → 规则（`reminder` 表）
- `materialize(reminder)` → 投递单元（`notice` 表），幂等靠 `origin` 唯一索引

**不生成 `.ics`、不发 SMTP**：手机本地闹钟与 SSE 才是投递通道（见 docs/15）。
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from ..clock import Clock, SystemClock
from ..domain import Notice
from ..domain.reminder import Reminder
from ..ids import new_ulid
from ..store import Database, EventRepo, NoticeRepo
from ..store.reminders import ReminderRepo
from .timeparse import DEFAULT_TZ, TimeParseError, parse_when


class ReminderService:
    def __init__(self, db: Database, *, clock: Clock | None = None, tz: str = DEFAULT_TZ) -> None:
        self.db = db
        self.clock = clock or SystemClock()
        self.tz = tz
        self.reminders = ReminderRepo(db)
        self.notices = NoticeRepo(db)
        self.events = EventRepo(db)

    # ---------- 创建 ----------
    def create(
        self,
        title: str,
        when: datetime | str,
        *,
        body: str | None = None,
        tz: str | None = None,
        origin_session: str | None = None,
        priority: str = "normal",
        channel: tuple[str, ...] = ("alarm",),
        rrule: str | None = None,
    ) -> Reminder:
        now = self.clock.now()
        zone = tz or self.tz
        due = parse_when(when, now=now, tz=zone) if isinstance(when, str) else when
        if due.tzinfo is None:
            raise ValueError("提醒时刻必须带时区")
        if due <= now:
            raise TimeParseError(f"提醒时刻在过去：{due.isoformat()}")
        reminder = Reminder(
            id=new_ulid(),
            title=title.strip(),
            body=body,
            due_at_utc=due,
            tz=zone,
            created_at=now,
            origin_session=origin_session,
            priority=priority,
            rrule=rrule,
            channels=channel,
        )
        self.reminders.insert(reminder)
        self._emit("reminder.created", {"reminder": reminder.to_dict()})
        return reminder

    def create_from_text(
        self,
        text: str,
        *,
        origin_session: str | None = None,
        priority: str = "normal",
        tz: str | None = None,
    ) -> Reminder:
        """`"3分钟后 提醒我喝水"` → 时间部分 + 标题。看不懂时间就报错，不猜。"""
        when, title = split_time_and_title(text, now=self.clock.now())
        return self.create(
            title, when, origin_session=origin_session, priority=priority, tz=tz
        )

    # ---------- 查询与取消 ----------
    def upcoming(self, *, until: datetime | None = None, limit: int = 50) -> list[Reminder]:
        items = self.reminders.list_scheduled(limit=limit)
        if until is None:
            return items
        return [item for item in items if item.due_at_utc <= until]

    def cancel(self, reminder_id: str) -> bool:
        reminder = self.reminders.get(reminder_id)
        if reminder is None or reminder.state != "scheduled":
            return False
        self.reminders.update(reminder.cancelled())
        self._emit("reminder.cancelled", {"reminder_id": reminder_id})
        return True

    def reschedule(self, reminder_id: str, when: datetime | str) -> Reminder:
        reminder = self.reminders.get(reminder_id)
        if reminder is None:
            raise KeyError(reminder_id)
        if reminder.state != "scheduled":
            raise ValueError(f"只有 scheduled 的提醒能改时间，当前 {reminder.state}")
        due = parse_when(when, now=self.clock.now(), tz=reminder.tz) if isinstance(when, str) else when
        if due.tzinfo is None:
            raise ValueError("提醒时刻必须带时区")
        updated = Reminder(
            id=reminder.id,
            title=reminder.title,
            body=reminder.body,
            due_at_utc=due,
            tz=reminder.tz,
            created_at=reminder.created_at,
            owner=reminder.owner,
            rrule=reminder.rrule,
            origin_session=reminder.origin_session,
            channels=reminder.channels,
            priority=reminder.priority,
            state=reminder.state,
            fire_count=reminder.fire_count,
        )
        self.reminders.update(updated)
        return updated

    # ---------- 展开 ----------
    def materialize(self, reminder: Reminder, *, title_suffix: str = "") -> Notice:
        """把规则展开成一条 notice。重复调用返回既有的那条（origin 唯一）。"""
        existing = self.notices.by_origin(reminder.origin_for(reminder.due_at_utc))
        if existing is not None:
            return existing
        notice = Notice(
            id=new_ulid(),
            kind="reminder",
            title=(reminder.title + title_suffix)[:120],
            body=reminder.body,
            due_utc=reminder.due_at_utc,
            created_at=self.clock.now(),
            session_id=reminder.origin_session,
            rrule=reminder.rrule,
            origin=reminder.origin_for(reminder.due_at_utc),
        )
        if not self.notices.insert(notice):
            found = self.notices.by_origin(notice.origin or "")
            if found is not None:
                return found
        self._emit("notice.created", {"notice": notice.to_dict()})
        return notice

    def _emit(self, kind: str, payload: dict[str, Any]) -> None:
        self.events.append(kind, payload, at=self.clock.now())


def split_time_and_title(text: str, *, now: datetime | None = None) -> tuple[str, str]:
    """在空格处试切：前半段能解析成时间就用它，否则试下一个空格。全失败则报错。"""
    reference = now or datetime.now(tz=UTC)
    raw = text.strip()
    if not raw:
        raise TimeParseError("提醒内容为空")
    positions = [i for i, ch in enumerate(raw) if ch.isspace()]
    for pos in positions:
        head, tail = raw[:pos].strip(), raw[pos:].strip()
        if not head or not tail:
            continue
        try:
            parse_when(head, now=reference)
        except TimeParseError:
            continue
        return head, tail
    raise TimeParseError(
        f"没找到可解析的时间：{text!r}（写法示例：\"3分钟后 喝水\" / \"明天15:00 交表\"）"
    )
