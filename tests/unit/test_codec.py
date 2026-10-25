from __future__ import annotations

import unittest
from datetime import UTC, datetime, timedelta, timezone

from mephisto.domain import codec


class CodecStrictnessTest(unittest.TestCase):
    def test_unknown_key_is_rejected(self) -> None:
        with self.assertRaises(codec.DecodeError) as ctx:
            codec.expect_keys({"a": 1, "b": 2}, ("a",), "root")
        self.assertIn("b", str(ctx.exception))

    def test_required_key_missing(self) -> None:
        with self.assertRaises(codec.DecodeError) as ctx:
            codec.get({}, "id", "envelope", required=True)
        self.assertIn("envelope.id", str(ctx.exception))

    def test_error_path_includes_index(self) -> None:
        self.assertEqual(codec._path("envelope.thread.references", 2), "envelope.thread.references[2]")


class CodecTypesTest(unittest.TestCase):
    def test_bool_is_not_int(self) -> None:
        """True 是 int 的子类，若不特判会把 true 当成 1 收下。"""
        with self.assertRaises(codec.DecodeError):
            codec.as_int(True, "x")

    def test_int_bounds(self) -> None:
        self.assertEqual(codec.as_int(5, "x", minimum=0, maximum=10), 5)
        with self.assertRaises(codec.DecodeError):
            codec.as_int(-1, "x", minimum=0)
        with self.assertRaises(codec.DecodeError):
            codec.as_int(11, "x", maximum=10)

    def test_str_length_bounds(self) -> None:
        with self.assertRaises(codec.DecodeError):
            codec.as_str("", "x", min_len=1)
        with self.assertRaises(codec.DecodeError):
            codec.as_str("abcd", "x", max_len=3)

    def test_list_rejects_string(self) -> None:
        with self.assertRaises(codec.DecodeError):
            codec.as_list("abc", "x")

    def test_enum(self) -> None:
        self.assertEqual(codec.as_enum("in", ("in", "out"), "x"), "in")
        with self.assertRaises(codec.DecodeError) as ctx:
            codec.as_enum("sideways", ("in", "out"), "x")
        self.assertIn("sideways", str(ctx.exception))


class CodecTimeTest(unittest.TestCase):
    def test_utc_z_suffix(self) -> None:
        self.assertEqual(codec.as_utc("2026-10-26T07:00:00Z", "t"), datetime(2026, 10, 26, 7, tzinfo=UTC))

    def test_offset_is_converted_to_utc(self) -> None:
        shanghai = timezone(timedelta(hours=8))
        parsed = codec.as_utc("2026-10-26T15:00:00+08:00", "t")
        self.assertEqual(parsed, datetime(2026, 10, 26, 7, tzinfo=UTC))
        self.assertEqual(parsed.utcoffset(), timedelta(0))
        _ = shanghai

    def test_naive_is_rejected(self) -> None:
        with self.assertRaises(codec.DecodeError):
            codec.as_utc("2026-10-26T07:00:00", "t")

    def test_garbage_is_rejected(self) -> None:
        with self.assertRaises(codec.DecodeError):
            codec.as_utc("昨天下午", "t")

    def test_roundtrip(self) -> None:
        moment = datetime(2026, 10, 26, 7, 0, tzinfo=UTC)
        self.assertEqual(codec.as_utc(codec.to_iso(moment), "t"), moment)

    def test_to_iso_rejects_naive(self) -> None:
        with self.assertRaises(ValueError):
            codec.to_iso(datetime(2026, 1, 1))


if __name__ == "__main__":
    unittest.main()
