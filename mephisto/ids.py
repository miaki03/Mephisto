"""标识生成：ULID、随机令牌、令牌哈希。

三条规则：
1. 零第三方依赖（运行时约束）。
2. ULID 在本进程内**严格单调**——同一毫秒内连续生成也不会重复或乱序，
   这样它既能当主键，也能当增量同步的游标。
3. 令牌只存哈希，比较用恒定时间。
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
import threading
import time

CROCKFORD = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"
_TS_LEN = 10          # 48 位毫秒时间戳 = 10 个 base32 字符
_RAND_LEN = 16        # 80 位随机数 = 16 个 base32 字符
ULID_LEN = _TS_LEN + _RAND_LEN

_lock = threading.Lock()
_last_ms = 0
_last_rand = 0


def _encode(value: int, length: int) -> str:
    chars = []
    for _ in range(length):
        chars.append(CROCKFORD[value & 31])
        value >>= 5
    return "".join(reversed(chars))


def new_ulid(now_ms: int | None = None) -> str:
    """生成 ULID。两条语义，别混：

    - `now_ms is None`（生产路径）：读真实时钟，并做**单调保护**——同一毫秒内自增随机段，
      真实时钟回拨时沿用上一次的时间戳，保证永不倒退（主键与同步游标都靠它）。
    - `now_ms` 给了值（测试/回放路径）：**以注入值为准**，同毫秒内自增随机段。
      注入等于"假装时钟是这个值"，此时不做跨调用的单调保护——否则注入就没意义了。
    """
    global _last_ms, _last_rand
    injected = now_ms is not None
    ms = int(now_ms) if injected else int(time.time() * 1000)
    with _lock:
        if injected:
            if ms == _last_ms:
                _last_rand += 1
            else:
                _last_ms = ms
                _last_rand = secrets.randbits(80)
        elif ms > _last_ms:
            _last_ms = ms
            _last_rand = secrets.randbits(80)
        else:
            # 真实时钟没前进或回拨：沿用上次时间戳，只动随机段
            ms = _last_ms
            _last_rand += 1
            if _last_rand >= 1 << 80:
                _last_ms += 1
                ms = _last_ms
                _last_rand = 0
        rand = _last_rand
    return _encode(ms, _TS_LEN) + _encode(rand, _RAND_LEN)


def is_ulid(value: object) -> bool:
    if not isinstance(value, str) or len(value) != ULID_LEN:
        return False
    if any(ch not in CROCKFORD for ch in value):
        return False
    return value[0] <= "7"          # 48 位时间戳的首字符上限


def ulid_time_ms(value: str) -> int:
    if not is_ulid(value):
        raise ValueError(f"not a ULID: {value!r}")
    ms = 0
    for ch in value[:_TS_LEN]:
        ms = ms * 32 + CROCKFORD.index(ch)
    return ms


def new_token(nbytes: int = 32) -> str:
    """发给设备的令牌。只在生成时可见一次，服务端只留哈希。"""
    return secrets.token_urlsafe(nbytes)


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def verify_token(token: str, expected_hash: str) -> bool:
    return hmac.compare_digest(hash_token(token), expected_hash)


CLIENT_MSG_ID_MAX = 64


def is_client_msg_id(value: object) -> bool:
    """客户端幂等键：1–64 个可打印 ASCII，不含空白。

    为什么限制字符集：它会进 URL、日志、SQL 参数，放开等于给自己埋注入面。
    """
    if not isinstance(value, str) or not 1 <= len(value) <= CLIENT_MSG_ID_MAX:
        return False
    return all(33 <= ord(ch) <= 126 for ch in value)


def new_client_msg_id() -> str:
    """给客户端一个默认值；真正的幂等键应由客户端生成，并在弱网重试时复用。"""
    return f"c_{secrets.token_urlsafe(12)}"
