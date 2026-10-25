-- Mephisto schema v1（迁移 001）
--
-- 两条约定：
--   1. 时间列一律 **epoch 毫秒**（不是秒）。与 ULID 的时间戳同精度，跨表比较不用换算。
--   2. 唯一性与外键**写进 DDL**，不靠代码自觉。能在库里挡住的错误，就不该靠人记住。
--
-- 表清单见 docs/99 §11。这里只建表与索引，不塞种子数据。

CREATE TABLE session (
    id             TEXT PRIMARY KEY,
    title          TEXT NOT NULL,
    kind           TEXT NOT NULL DEFAULT 'chatter'
                   CHECK (kind IN ('chatter', 'task', 'digest', 'system')),
    state          TEXT NOT NULL DEFAULT 'active'
                   CHECK (state IN ('active', 'archived')),
    created_at     INTEGER NOT NULL,
    last_active_at INTEGER NOT NULL,
    seq            INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX session_by_activity ON session(state, last_active_at DESC);

-- 别名：把外部标识（message_id / 地址 / 主题标签 / 群 id）映射到会话。
-- 邮件回归时不用改表，只是多几种 kind。
CREATE TABLE session_alias (
    kind       TEXT NOT NULL,
    key        TEXT NOT NULL,
    session_id TEXT NOT NULL REFERENCES session(id) ON DELETE RESTRICT,
    created_at INTEGER NOT NULL,
    PRIMARY KEY (kind, key)
);
CREATE INDEX session_alias_by_session ON session_alias(session_id);

-- 消息：收发共用一张表，方向只是属性。seq 是全局单调游标（增量同步靠它）。
CREATE TABLE message (
    seq             INTEGER PRIMARY KEY AUTOINCREMENT,
    id              TEXT NOT NULL UNIQUE,
    session_id      TEXT NOT NULL REFERENCES session(id) ON DELETE RESTRICT,
    direction       TEXT NOT NULL CHECK (direction IN ('in', 'out')),
    channel         TEXT NOT NULL,
    external_id     TEXT,
    message_id      TEXT,
    in_reply_to     TEXT,
    refs            TEXT,
    text            TEXT NOT NULL DEFAULT '',
    client_msg_id   TEXT,
    route_decided_by TEXT,
    created_at      INTEGER NOT NULL
);
CREATE UNIQUE INDEX message_by_channel_external ON message(channel, external_id)
    WHERE external_id IS NOT NULL;
CREATE UNIQUE INDEX message_by_client_msg_id ON message(client_msg_id)
    WHERE client_msg_id IS NOT NULL;
CREATE UNIQUE INDEX message_by_message_id ON message(message_id)
    WHERE message_id IS NOT NULL;
CREATE INDEX message_by_session ON message(session_id, seq);

CREATE TABLE attachment (
    message_id TEXT NOT NULL REFERENCES message(id) ON DELETE CASCADE,
    idx        INTEGER NOT NULL,
    filename   TEXT NOT NULL,
    media_type TEXT,
    size       INTEGER NOT NULL,
    digest     TEXT NOT NULL,
    path       TEXT,
    PRIMARY KEY (message_id, idx)
);
CREATE INDEX attachment_by_digest ON attachment(digest);

-- 投递单元：提醒、任务完成、新回复、系统消息共用一张表（见 docs/25、docs/55）。
CREATE TABLE notice (
    id           TEXT PRIMARY KEY,
    kind         TEXT NOT NULL CHECK (kind IN ('reminder', 'job', 'reply', 'system')),
    session_id   TEXT REFERENCES session(id) ON DELETE SET NULL,
    title        TEXT NOT NULL,
    body         TEXT,
    due_utc      INTEGER NOT NULL,
    state        TEXT NOT NULL DEFAULT 'pending'
                 CHECK (state IN ('pending', 'acked', 'dismissed', 'missed')),
    acked_at     INTEGER,
    snooze_until INTEGER,
    origin       TEXT,
    client_ref   TEXT,
    created_at   INTEGER NOT NULL
);
CREATE UNIQUE INDEX notice_by_origin ON notice(origin) WHERE origin IS NOT NULL;
CREATE INDEX notice_open ON notice(state, due_utc);

-- 提醒规则（会被展开成多条 notice；规则与投递分开，改时间不用重建历史）。
CREATE TABLE reminder (
    id             TEXT PRIMARY KEY,
    owner          TEXT NOT NULL DEFAULT 'user' CHECK (owner IN ('user', 'system')),
    title          TEXT NOT NULL,
    body           TEXT,
    due_at_utc     INTEGER NOT NULL,
    tz             TEXT NOT NULL,
    rrule          TEXT,
    origin_session TEXT REFERENCES session(id) ON DELETE SET NULL,
    channels       TEXT NOT NULL DEFAULT '["alarm"]',
    priority       TEXT NOT NULL DEFAULT 'normal' CHECK (priority IN ('high', 'normal')),
    state          TEXT NOT NULL DEFAULT 'scheduled'
                   CHECK (state IN ('scheduled', 'fired', 'cancelled', 'missed')),
    fire_count     INTEGER NOT NULL DEFAULT 0,
    depends_on     TEXT,
    created_at     INTEGER NOT NULL
);
CREATE INDEX reminder_due ON reminder(state, due_at_utc);

-- 任务队列。唯一索引保证「同一会话同一时刻只有一个 running」——这条由库挡住。
CREATE TABLE job (
    id          TEXT PRIMARY KEY,
    session_id  TEXT NOT NULL REFERENCES session(id) ON DELETE RESTRICT,
    message_id  TEXT REFERENCES message(id) ON DELETE SET NULL,
    state       TEXT NOT NULL DEFAULT 'queued'
                CHECK (state IN ('queued', 'running', 'done', 'failed', 'cancelled')),
    attempts    INTEGER NOT NULL DEFAULT 0,
    started_at  INTEGER,
    finished_at INTEGER,
    error       TEXT,
    created_at  INTEGER NOT NULL
);
CREATE UNIQUE INDEX job_one_running_per_session ON job(session_id) WHERE state = 'running';
CREATE INDEX job_queue ON job(state, created_at);

-- 事件流：SSE 的游标来源。
CREATE TABLE event_log (
    seq        INTEGER PRIMARY KEY AUTOINCREMENT,
    kind       TEXT NOT NULL,
    session_id TEXT,
    payload    TEXT NOT NULL,
    created_at INTEGER NOT NULL
);
CREATE INDEX event_log_by_session ON event_log(session_id, seq);

-- 路由决策审计：只追加。分类器判错时要能复盘。
CREATE TABLE route_decision (
    id          TEXT PRIMARY KEY,
    envelope_id TEXT NOT NULL,
    decided_by  TEXT NOT NULL,
    session_id  TEXT,
    confidence  REAL,
    evidence    TEXT,
    created_at  INTEGER NOT NULL
);
CREATE INDEX route_decision_by_time ON route_decision(created_at);

CREATE TABLE device (
    id         TEXT PRIMARY KEY,
    name       TEXT NOT NULL,
    kind       TEXT NOT NULL CHECK (kind IN ('android', 'desktop', 'mail', 'system')),
    created_at INTEGER NOT NULL,
    revoked_at INTEGER
);

CREATE TABLE device_token (
    token_hash TEXT PRIMARY KEY,
    device_id  TEXT NOT NULL REFERENCES device(id) ON DELETE CASCADE,
    expires_at INTEGER,
    last_seen  INTEGER,
    created_at INTEGER NOT NULL
);

CREATE TABLE device_cursor (
    device_id  TEXT NOT NULL REFERENCES device(id) ON DELETE CASCADE,
    stream     TEXT NOT NULL,
    cursor     TEXT NOT NULL,
    updated_at INTEGER NOT NULL,
    PRIMARY KEY (device_id, stream)
);

-- 分片上传会话（见 docs/17：单分片 4 MB，单文件 64 MB）。
CREATE TABLE upload (
    id           TEXT PRIMARY KEY,
    device_id    TEXT REFERENCES device(id) ON DELETE CASCADE,
    filename     TEXT NOT NULL,
    size         INTEGER NOT NULL,
    sha256       TEXT NOT NULL,
    chunk_size   INTEGER NOT NULL,
    chunk_count  INTEGER NOT NULL,
    received     TEXT NOT NULL DEFAULT '[]',
    state        TEXT NOT NULL DEFAULT 'open' CHECK (state IN ('open', 'done', 'abandoned')),
    attachment_id TEXT,
    expires_at   INTEGER NOT NULL,
    created_at   INTEGER NOT NULL
);
CREATE INDEX upload_expiry ON upload(state, expires_at);
