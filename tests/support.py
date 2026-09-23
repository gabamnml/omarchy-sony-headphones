"""Shared building blocks for the test modules.

The helper is loaded from `bin/sony-headphones` by path, once, so every module
patches the same module object. Fixtures that more than one module needs live
here, and so do the builders the topic modules assemble payloads with.

Both `python3 tests/test_v1.py` and
`python3 -m unittest discover -s tests -t tests` put this directory on
`sys.path`, so a plain `import support` works either way.
"""

import importlib.machinery
import importlib.util
import os
import struct
import time

HERE = os.path.dirname(os.path.abspath(__file__))
HELPER = os.path.join(os.path.dirname(HERE), "bin", "sony-headphones")

spec = importlib.util.spec_from_loader(
    "sonyhp", importlib.machinery.SourceFileLoader("sonyhp", HELPER))
sonyhp = importlib.util.module_from_spec(spec)
spec.loader.exec_module(sonyhp)


# -- shared fakes ----------------------------------------------------------


class FakeStream:
    def __init__(self, chunks, delay=0.0):
        self.chunks = chunks
        self.delay = delay
        self.calls = 0
        self.closed = False

    def settimeout(self, value):
        pass

    def sendall(self, data):
        pass

    def recv(self, size):
        self.calls += 1
        if self.delay:
            time.sleep(self.delay)
        chunk = self.chunks(self.calls) if callable(self.chunks) else self.chunks
        return chunk[:size]

    def close(self):
        self.closed = True


# -- SDP builders ----------------------------------------------------------


def de_seq(*items):
    body = b"".join(items)
    return bytes([0x35, len(body)]) + body


def de_uuid16(value):
    return bytes([0x19]) + value.to_bytes(2, "big")


def de_uint8(value):
    return bytes([0x08, value])


def de_uint16(value):
    return bytes([0x09]) + value.to_bytes(2, "big")


# The shape a real device returns: one record whose protocol descriptor list
# (attribute 0x0004) is [[L2CAP], [RFCOMM, channel]].
RECORD = de_seq(de_seq(
    de_uint16(0x0004),
    de_seq(de_seq(de_uuid16(0x0100)), de_seq(de_uuid16(0x0003), de_uint8(9))),
))


def sdp_response(transaction, attributes, continuation=b"\x00", pdu=0x07, declared=None):
    body = len(attributes).to_bytes(2, "big") + attributes + continuation
    length = len(body) if declared is None else declared
    return struct.pack(">BHH", pdu, transaction, length) + body


class FakeClock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now


class FakeSdpSocket:
    """Plays back scripted responses; each may be a callable of the request."""

    def __init__(self, responses, clock=None, recv_cost=0.0):
        self.responses = list(responses)
        self.sent = []
        self.timeouts = []
        self.clock = clock
        self.recv_cost = recv_cost

    def settimeout(self, value):
        self.timeouts.append(value)

    def send(self, data):
        self.sent.append(data)
        return len(data)

    def recv(self, size):
        if self.clock is not None:
            self.clock.now += self.recv_cost
        if not self.responses:
            raise AssertionError("asked for more responses than were scripted")
        response = self.responses.pop(0)
        return response(self.sent[-1]) if callable(response) else response


def endless_continuations(request):
    # Always a fresh state and a byte of data: never repeats, never finishes.
    transaction = struct.unpack(">H", request[1:3])[0]
    return sdp_response(transaction, b"\x00", bytes([2]) + transaction.to_bytes(2, "big"))


# -- protocol v2 builders --------------------------------------------------


def build_v2_device_list(entries, playback_status, subtype=0x02, opcode=None):
    """Assemble a PERIPHERAL device-list payload the way the XM6 sends one.

    Each entry is ``(address, status, name[, class_bytes])``; the three class
    bytes are only written when the subtype says the list carries them.
    """
    opcode = sonyhp.V2_PERI_RET if opcode is None else opcode
    body = bytes([opcode, subtype, len(entries)])
    for entry in entries:
        address, status, name = entry[0], entry[1], entry[2]
        class_bytes = entry[3] if len(entry) > 3 else b"\x00\x00\x00"
        body += address.encode("ascii") + bytes([status])
        if subtype == 0x02:
            body += class_bytes
        name_bytes = name.encode("utf-8")
        body += bytes([len(name_bytes)]) + name_bytes
    return body + bytes([playback_status])
