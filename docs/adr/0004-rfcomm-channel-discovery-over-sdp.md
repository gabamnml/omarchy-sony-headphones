# 0004 — Find the RFCOMM channel over SDP, and cache it

## Context

Sony's control service lives on one RFCOMM channel, and the number is not
fixed. BlueZ exposes no API for a remote device's service records, and
bluez-utils no longer ships `sdptool`. Guessing is not merely slow: a blocking
connect to a closed channel takes seconds, and a device walked channel by
channel starts refusing the right one. The device itself already knows the
answer.

## Decision

The helper speaks SDP itself over L2CAP: it sends a service-search-attribute
request for the Sony control UUID, follows continuation states to reassemble
the record, and reads the channel out of the RFCOMM protocol descriptor. Both
the v1 and v2 service UUIDs are searched; the v2 one is tried first by default,
and the order flips only when `bluetoothctl info` hinted v1. The search is
lazy: the first UUID in hint order is used as soon as it answers, without a
second query, and a first UUID that answers nothing, fails to open, or fails
the init handshake falls through to the other UUID and then to the cached
channel. The discovered channel is cached in the private
cache directory and used as a fallback candidate.

Everything the device controls about that exchange is bounded: 8 KiB of record
data, 8 request/response rounds, an 8-second wall-clock deadline for the whole
lookup, a 16-byte cap on continuation state, and a nesting depth cap in the
record parser. A continuation must make progress and must never repeat.

## Consequences

- Discovery is one round trip to the device rather than a scan of 30 channels,
  and the answer is not guessed from a service advertisement.
- The helper depends on `AF_BLUETOOTH`/`BTPROTO_L2CAP` sockets, which BlueZ
  provides; there is still no PyBluez and no D-Bus.
- A misbehaving or hostile device can slow one lookup for at most 8 seconds and
  cannot make the helper hold unbounded memory.
- The cache is only an optimisation: it is used after the SDP answer, and a
  candidate that fails the init handshake is closed and the search moves on.
