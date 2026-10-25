from __future__ import annotations

import unittest

from mephisto.ids import (
    ULID_LEN,
    hash_token,
    is_ulid,
    new_token,
    new_ulid,
    ulid_time_ms,
    verify_token,
)


class UlidTest(unittest.TestCase):
    def test_shape(self) -> None:
        value = new_ulid()
        self.assertEqual(len(value), ULID_LEN)
        self.assertTrue(is_ulid(value))

    def test_monotonic_within_process(self) -> None:
        values = [new_ulid() for _ in range(2000)]
        self.assertEqual(values, sorted(values), "同一毫秒内也必须严格递增")
        self.assertEqual(len(set(values)), len(values))

    def test_injected_timestamp(self) -> None:
        value = new_ulid(now_ms=1_700_000_000_000)
        self.assertEqual(ulid_time_ms(value), 1_700_000_000_000)

    def test_survives_clock_going_backwards(self) -> None:
        later = new_ulid(now_ms=2_000_000_000_000)
        earlier = new_ulid(now_ms=1_000_000_000_000)
        self.assertGreater(earlier, later, "回拨时不能倒退，否则主键与游标都会错")

    def test_rejects_garbage(self) -> None:
        for bad in ("", "too-short", "0" * 25, "I" * 26, "8" + "0" * 25, None, 123):
            with self.subTest(bad=bad):
                self.assertFalse(is_ulid(bad))

    def test_time_ms_rejects_invalid(self) -> None:
        with self.assertRaises(ValueError):
            ulid_time_ms("not-a-ulid")


class TokenTest(unittest.TestCase):
    def test_url_safe_and_unique(self) -> None:
        tokens = {new_token() for _ in range(200)}
        self.assertEqual(len(tokens), 200)
        for token in tokens:
            self.assertNotIn("/", token)
            self.assertNotIn("+", token)
            self.assertNotIn("=", token)

    def test_hash_roundtrip(self) -> None:
        token = new_token()
        digest = hash_token(token)
        self.assertNotEqual(digest, token)
        self.assertTrue(verify_token(token, digest))
        self.assertFalse(verify_token(token + "x", digest))


if __name__ == "__main__":
    unittest.main()
