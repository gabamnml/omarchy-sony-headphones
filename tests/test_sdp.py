"""SDP: the RFCOMM channel lookup, its record parsing and its cache.

The device is asked over L2CAP which channel carries its control service.
Scripted sockets and hostile records cover every refusal path without a
radio, and the cached channel that short-circuits the lookup sits here
alongside.
"""

import os
import shutil
import stat
import struct
import tempfile
import unittest
from unittest import mock

import support
from support import (
    FakeClock, FakeSdpSocket, RECORD, de_uint16, endless_continuations,
    sdp_response,
)

sonyhp = support.sonyhp


class TestChannelCache(unittest.TestCase):
    """The cached channel decides where the next connection is dialled."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.cache = os.path.join(self.tmp, "cache")
        self.patch = mock.patch.object(sonyhp, "CACHE_DIR", self.cache)
        self.patch.start()
        self.addCleanup(self.patch.stop)
        self.address = "AA:BB:CC:DD:EE:FF"

    def test_creates_the_cache_private(self):
        self.assertEqual(sonyhp.private_cache_dir(), self.cache)
        self.assertEqual(stat.S_IMODE(os.lstat(self.cache).st_mode), 0o700)

    def test_tightens_a_directory_left_loose(self):
        os.mkdir(self.cache, 0o755)
        os.chmod(self.cache, 0o755)
        self.assertEqual(sonyhp.private_cache_dir(), self.cache)
        self.assertEqual(stat.S_IMODE(os.lstat(self.cache).st_mode), 0o700)

    def test_declines_a_symlink(self):
        target = os.path.join(self.tmp, "target")
        os.mkdir(target, 0o700)
        os.symlink(target, self.cache)
        self.assertIsNone(sonyhp.private_cache_dir())

    def test_declines_a_file(self):
        open(self.cache, "w").close()
        self.assertIsNone(sonyhp.private_cache_dir())

    def test_round_trips_a_channel(self):
        sonyhp.remember_channel(self.address, 9)
        self.assertEqual(sonyhp.cached_channel(self.address), 9)
        path = sonyhp._channel_cache_path(self.address)
        self.assertEqual(stat.S_IMODE(os.lstat(path).st_mode), 0o600)

    def test_forget_removes_it(self):
        sonyhp.remember_channel(self.address, 9)
        sonyhp.forget_channel(self.address)
        self.assertIsNone(sonyhp.cached_channel(self.address))

    def test_does_not_write_through_a_symlink(self):
        os.mkdir(self.cache, 0o700)
        victim = os.path.join(self.tmp, "victim")
        with open(victim, "w") as handle:
            handle.write("untouched")
        os.symlink(victim, sonyhp._channel_cache_path(self.address))
        sonyhp.remember_channel(self.address, 9)  # swallowed, not followed
        with open(victim) as handle:
            self.assertEqual(handle.read(), "untouched")

    def test_does_not_read_through_a_symlink(self):
        os.mkdir(self.cache, 0o700)
        planted = os.path.join(self.tmp, "planted")
        with open(planted, "w") as handle:
            handle.write("7")
        os.symlink(planted, sonyhp._channel_cache_path(self.address))
        self.assertIsNone(sonyhp.cached_channel(self.address))

    def test_rejects_a_channel_outside_the_valid_range(self):
        os.mkdir(self.cache, 0o700)
        for written in ("0", "31", "-1", "nonsense", ""):
            with self.subTest(written=written):
                with open(sonyhp._channel_cache_path(self.address), "w") as handle:
                    handle.write(written)
                self.assertIsNone(sonyhp.cached_channel(self.address))


class TestSdpExchange(unittest.TestCase):
    def query(self, responses, **kwargs):
        clock = kwargs.pop("clock", FakeClock())
        sock = FakeSdpSocket(responses, clock=clock, recv_cost=kwargs.pop("recv_cost", 0.0))
        return sonyhp.sdp_query(sock, sonyhp.SERVICE_UUID_BYTES, kwargs.pop("timeout", 8.0), clock=clock), sock

    def test_a_single_round_record(self):
        record, sock = self.query([sdp_response(1, RECORD)])
        self.assertEqual(record, RECORD)
        self.assertEqual(sonyhp._find_rfcomm_channel(sonyhp.parse_sdp_record(record)), 9)
        self.assertEqual(len(sock.sent), 1)

    def test_a_record_split_across_a_continuation(self):
        state = bytes([2, 0xAB, 0xCD])
        record, sock = self.query([
            sdp_response(1, RECORD[:10], state),
            sdp_response(2, RECORD[10:]),
        ])
        self.assertEqual(record, RECORD)
        self.assertTrue(sock.sent[1].endswith(state), "the continuation is sent back verbatim")
        self.assertEqual(struct.unpack(">H", sock.sent[1][1:3])[0], 2)

    def test_a_missing_continuation_byte_still_ends_the_exchange(self):
        packet = sdp_response(1, RECORD, continuation=b"")
        record, _ = self.query([packet])
        self.assertEqual(record, RECORD)

    def test_endless_continuations_stop_at_the_round_limit(self):
        responses = [endless_continuations] * (sonyhp.SDP_MAX_ROUNDS + 5)
        sock = FakeSdpSocket(responses)
        with self.assertRaises(sonyhp.SdpError):
            sonyhp.sdp_query(sock, sonyhp.SERVICE_UUID_BYTES, 8.0, clock=FakeClock())
        self.assertEqual(len(sock.sent), sonyhp.SDP_MAX_ROUNDS)

    def test_a_repeated_continuation_state_is_rejected(self):
        state = bytes([1, 0x42])
        with self.assertRaisesRegex(sonyhp.SdpError, "repeated"):
            self.query([sdp_response(1, b"\x00", state), sdp_response(2, b"\x00", state)])

    def test_a_continuation_without_data_is_rejected(self):
        with self.assertRaisesRegex(sonyhp.SdpError, "without any data"):
            self.query([sdp_response(1, b"", bytes([1, 0x42]))])

    def test_the_aggregate_record_size_is_capped(self):
        # 1500 per round crosses the aggregate cap on round six, before the
        # round limit would; each packet still fits one read.
        piece = b"\x00" * 1500
        responses = [
            (lambda n: sdp_response(n, piece, bytes([1, n])))(n)
            for n in range(1, sonyhp.SDP_MAX_ROUNDS + 1)
        ]
        with self.assertRaisesRegex(sonyhp.SdpError, "larger"):
            _, sock = self.query(responses)
        self.assertLess(sonyhp.SDP_MAX_RECORD_BYTES // 1500 + 1, sonyhp.SDP_MAX_ROUNDS)

    def test_a_malformed_continuation_state_is_rejected(self):
        cases = {
            "too long": bytes([17]) + bytes(17),
            "truncated": bytes([4, 0x01, 0x02]),
            "trailing": bytes([1, 0x01, 0x02]),
        }
        for name, state in cases.items():
            with self.subTest(name):
                with self.assertRaisesRegex(sonyhp.SdpError, "continuation state is malformed"):
                    self.query([sdp_response(1, b"\x00", state)])

    def test_the_whole_exchange_has_a_deadline(self):
        # Each answer arrives just inside any per-read timeout; the total does not.
        clock = FakeClock()
        sock = FakeSdpSocket([endless_continuations] * sonyhp.SDP_MAX_ROUNDS, clock=clock, recv_cost=3.0)
        with self.assertRaisesRegex(sonyhp.SdpError, "in time"):
            sonyhp.sdp_query(sock, sonyhp.SERVICE_UUID_BYTES, 8.0, clock=clock)
        self.assertLess(len(sock.sent), sonyhp.SDP_MAX_ROUNDS)
        self.assertTrue(all(0 < t <= 8.0 for t in sock.timeouts))
        self.assertLessEqual(sock.timeouts[-1], 2.0, "later reads get only what is left")

    def test_an_already_spent_budget_sends_nothing(self):
        sock = FakeSdpSocket([])
        with self.assertRaises(sonyhp.SdpError):
            sonyhp.sdp_query(sock, sonyhp.SERVICE_UUID_BYTES, 0.0, clock=FakeClock())
        self.assertEqual(sock.sent, [])

    def test_malformed_responses_are_rejected(self):
        cases = {
            "short header": b"\x07\x00",
            "wrong pdu": sdp_response(1, RECORD, pdu=0x01),
            "wrong transaction": sdp_response(7, RECORD),
            "declared longer than sent": sdp_response(1, RECORD, declared=500),
            "count past the end": struct.pack(">BHH", 0x07, 1, 3) + b"\x00\x40\x00",
        }
        for name, packet in cases.items():
            with self.subTest(name):
                with self.assertRaises(sonyhp.SdpError):
                    self.query([packet])


class TestSdpRecordParsing(unittest.TestCase):
    def test_deep_nesting_is_an_sdp_error_not_a_crash(self):
        record = b""
        for _ in range(sonyhp.SDP_MAX_DEPTH + 2):
            record = bytes([0x36]) + len(record).to_bytes(2, "big") + record
        with self.assertRaisesRegex(sonyhp.SdpError, "nested"):
            sonyhp.parse_sdp_record(record)

    def test_depth_that_stack_would_not_survive(self):
        record = b""
        for _ in range(3000):
            record = bytes([0x37]) + len(record).to_bytes(4, "big") + record
        with self.assertRaises(sonyhp.SdpError):
            sonyhp.parse_sdp_record(record)

    def test_an_element_longer_than_the_record(self):
        with self.assertRaisesRegex(sonyhp.SdpError, "past the end"):
            sonyhp.parse_sdp_record(bytes([0x35, 0x40, 0x08, 0x01]))

    def test_a_child_that_overruns_its_sequence(self):
        # The outer sequence claims 2 bytes; its child needs 3.
        record = bytes([0x35, 0x02]) + de_uint16(0x0004) + b"\x00"
        with self.assertRaisesRegex(sonyhp.SdpError, "sequence"):
            sonyhp.parse_sdp_record(record)

    def test_trailing_bytes_are_ignored_as_before(self):
        tree = sonyhp.parse_sdp_record(RECORD + b"\x00\x00")
        self.assertEqual(sonyhp._find_rfcomm_channel(tree), 9)


class TestSdpChannel(unittest.TestCase):
    """sdp_channel turns every refusal into None, never an exception."""

    def run_channel(self, responses):
        sock = FakeSdpSocket(responses)
        sock.connect = lambda address: None
        sock.close = lambda: None
        with mock.patch.object(sonyhp.socket, "AF_BLUETOOTH", 31, create=True), \
                mock.patch.object(sonyhp.socket, "BTPROTO_L2CAP", 0, create=True), \
                mock.patch.object(sonyhp.socket, "socket", return_value=sock):
            return sonyhp.sdp_channel("AA:BB:CC:DD:EE:FF")

    def test_finds_the_channel(self):
        self.assertEqual(self.run_channel([sdp_response(1, RECORD)]), 9)

    def test_a_misbehaving_device_yields_none(self):
        self.assertIsNone(self.run_channel([endless_continuations] * sonyhp.SDP_MAX_ROUNDS))

    def test_a_hostile_record_yields_none(self):
        record = b""
        for _ in range(3000):
            record = bytes([0x37]) + len(record).to_bytes(4, "big") + record
        record = record[:sonyhp.SDP_MAX_RECORD_BYTES]
        self.assertIsNone(self.run_channel([sdp_response(1, record)]))


if __name__ == "__main__":
    unittest.main(verbosity=2)
