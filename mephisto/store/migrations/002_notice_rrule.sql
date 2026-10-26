-- 提醒的重复规则透传到 notice：客户端据此区分「↻ 每天」与「1× 一次性」。
-- 只加列不改语义；NULL 表示一次性，老数据自然落在一次性。
ALTER TABLE notice ADD COLUMN rrule TEXT;