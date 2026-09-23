# 0006 — One Protocol seam with a separate adapter per generation

## Context

v1 and v2 share the same framing, ACK discipline and link lifecycle, but every
payload-level decision — what to ask, how to read the answer, how to encode a
setting — differs. Without a seam these decisions become protocol conditionals
inside `Link`, and the demo stand-ins tend to grow a second, divergent copy of
the link logic.

## Decision

`Protocol` names the six operations a link uses — `negotiate`, `refresh_requests`,
`poll_requests`, `apply`, `setting_requests`, `power_off`. `negotiate` settles
whatever a command set needs before first use: a no-op on v1, the subtype
dialect queries on v2. `V1Protocol` and `V2Protocol` are adapters that delegate
to the builders and parsers written for their generation, so the layouts stay
separate while no layout is duplicated. `Link` holds one `adapter`, chosen once
by the init handshake's reply length; `Link._establish` runs the handshake, the
feature selection and then `adapter.negotiate` unconditionally, so a device
that drops during a dialect query has not given us a usable connection. After
that, `Link` never branches on the protocol name again. `DemoLink` is an
ordinary `Link` with the same adapter mechanism.

## Consequences

- A third command set means one adapter class and one ceiling, not edits
  scattered through `Link`.
- The framing, ACK, buffering, dispatch-boundary and daemon code are written
  once and shared by both generations and by the demo.
- Tests can substitute a recording adapter and assert that `Link` routes every
  decision through it, and the demo's correctness is the same code path as
  hardware.
