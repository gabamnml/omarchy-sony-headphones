"""Framing: the wire format and the reader that reassembles it.

Escape and checksum round trips, the known frame shape, and the bounded
buffer behind Link's read loop — bytes only, no radio.
"""

import unittest

import support
from support import FakeStream

sonyhp = support.sonyhp


class TestFraming(unittest.TestCase):
    def test_escapes_marker_bytes(self):
        self.assertEqual(sonyhp.escape(bytes([0x3E, 0x3C, 0x3D])), bytes([0x3D, 0x2E, 0x3D, 0x2C, 0x3D, 0x2D]))

    def test_unescape_reverses_escape(self):
        for raw in (bytes(range(0x30, 0x50)), bytes([0x3D] * 4), b"", bytes([0x3C, 0x3C])):
            self.assertEqual(sonyhp.unescape(sonyhp.escape(raw)), raw)

    def test_known_frame_matches_the_wire(self):
        # Ambient sound control set, sequence 0, captured shape from the docs:
        # header, type, seq, 4-byte length, payload, checksum, trailer.
        frame = sonyhp.encode_message(sonyhp.MSG_COMMAND_1, 0, bytes([0x68, 0x02, 0x11, 0x02, 0x02, 0x01, 0x00, 0x00]))
        self.assertEqual(frame.hex(), "3e0c0000000008680211020201000094" + "3c")

    def test_round_trip(self):
        payload = bytes([0x68, 0x3E, 0x3C, 0x3D])
        msg_type, seq, decoded = sonyhp.decode_message(sonyhp.encode_message(sonyhp.MSG_COMMAND_1, 1, payload))
        self.assertEqual((msg_type, seq, decoded), (sonyhp.MSG_COMMAND_1, 1, payload))

    def test_ack_is_an_empty_payload(self):
        self.assertEqual(sonyhp.decode_message(sonyhp.encode_message(sonyhp.MSG_ACK, 1, b"")), (sonyhp.MSG_ACK, 1, b""))

    def test_rejects_bad_checksum(self):
        frame = bytearray(sonyhp.encode_message(sonyhp.MSG_COMMAND_1, 0, bytes([0x10, 0x00])))
        frame[-2] ^= 0xFF
        with self.assertRaises(sonyhp.ProtocolError):
            sonyhp.decode_message(bytes(frame))

    def test_rejects_missing_markers(self):
        with self.assertRaises(sonyhp.ProtocolError):
            sonyhp.decode_message(b"\x0c\x00")

    def test_rejects_length_mismatch(self):
        body = bytes([sonyhp.MSG_COMMAND_1, 0]) + (9).to_bytes(4, "big") + bytes([0x10, 0x00])
        frame = bytes([sonyhp.HEADER]) + sonyhp.escape(body + bytes([sonyhp.checksum(body)])) + bytes([sonyhp.TRAILER])
        with self.assertRaises(sonyhp.ProtocolError):
            sonyhp.decode_message(frame)


class TestRfcommBuffer(unittest.TestCase):
    def link(self, chunks):
        link = sonyhp.Link("AA:BB:CC:DD:EE:FF")
        link.sock = FakeStream(chunks)
        return link

    def test_bytes_without_a_trailer_do_not_accumulate(self):
        link = self.link(b"\x00" * 1024)
        for _ in range(5):
            self.assertIsNone(link._read_frame(0.02))
            self.assertLessEqual(len(link._buffer), sonyhp.MAX_MESSAGE_SIZE)
        self.assertGreater(link.sock.calls, 20, "the device really did keep sending")

    def test_a_frame_after_junk_still_arrives(self):
        frame = sonyhp.encode_message(sonyhp.MSG_COMMAND_1, 1, bytes([0x01, 0x02]))
        link = self.link(lambda n: b"\x00" * 1024 if n <= 10 else frame)
        self.assertEqual(link._read_frame(1.0), (sonyhp.MSG_COMMAND_1, 1, bytes([0x01, 0x02])))

    def test_a_frame_split_across_the_trim_point_survives(self):
        frame = sonyhp.encode_message(sonyhp.MSG_COMMAND_1, 0, bytes(range(40)))
        head, tail = frame[:20], frame[20:]
        # Junk that pushes past the limit, then the start of a frame, then the rest.
        script = [b"\x00" * 1024, b"\x00" * 1024, b"\x00" * 1000 + head, tail]
        link = self.link(lambda n: script[min(n, len(script)) - 1])
        self.assertEqual(link._read_frame(1.0)[2], bytes(range(40)))

    def test_an_overlong_frame_is_dropped(self):
        body = bytes([sonyhp.HEADER]) + b"\x01" * (sonyhp.MAX_MESSAGE_SIZE + 10) + bytes([sonyhp.TRAILER])
        good = sonyhp.encode_message(sonyhp.MSG_COMMAND_1, 0, b"\x05")
        link = sonyhp.Link("AA:BB:CC:DD:EE:FF")
        link.sock = FakeStream(b"")
        link._buffer.extend(body + good)
        self.assertEqual(link._read_frame(0.1)[2], b"\x05")


if __name__ == "__main__":
    unittest.main(verbosity=2)
