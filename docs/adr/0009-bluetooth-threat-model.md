# 0009 — Bluetooth threat model: what the plugin trusts and what it bounds

## Context

This plugin speaks to a headset over Bluetooth Classic: an SDP lookup over
L2CAP, an RFCOMM control session carrying Sony's app protocol, and device
selection through `bluetoothctl`. Bluetooth brings published, practical
attacks below the application layer. The link key is negotiated with Just
Works pairing — no authentication, no protection against an active
eavesdropper. The documented link-layer attacks apply as stated: KNOB forces
a short session key, BIAS impersonates a bonded peer, BLUFFS derives fresh
session keys across sessions, and a spoofed MAC with a copied friendly name
is trivially cheap. Device firmware has its own flaws, and the BlueZ stack
and kernel drivers beneath us have had remotely reachable CVEs in the past
and will again. There is no application-layer authentication in Sony's
control protocol to make up for any of this: every frame the headphones send
arrives from a peer we have not authenticated, and every string they supply
is attacker-controlled text.

None of that is fixable from inside a bar widget. The plugin cannot repair
pairing, patch firmware, or audit BlueZ; pretending otherwise would be the
dangerous decision. What it can do is narrower and worth stating plainly:
bound everything the remote side can make it hold, wait for, run, or display,
so a hostile or broken peer degrades into a failed connection rather than a
compromised helper. This ADR records where that line is drawn, so a future
change does not re-litigate it or accidentally undo it.

## Decision

Treat the link layer, the firmware, and the stack as inherently untrusted,
and make the plugin's job the bounding of three surfaces: the parser, the
socket, and the strings.

The parser is bounded everywhere the device dictates size or pace. The SDP
lookup is capped at 8 KiB of record data, 8 round trips and 8 seconds
overall; a continuation must make progress and never repeat; the record
parser limits nesting depth. The RFCOMM reader never holds more than one
frame's worth of bytes that lack an end marker. The init handshake alone
chooses the command set, by reply length — the advertised service UUID is a
discovery hint only, never proof.

The socket is bounded against local peers too. The control socket lives only
inside a runtime directory verified as a real, user-owned 0700 directory (or
one the helper makes and verifies itself); the lock opens relative to a
directory descriptor and never through a symlink; a stale socket is replaced
only after a descriptor-relative check confirms a user-owned socket. Every
request carries a whole-request deadline and a line-size limit, so a client
that trickles bytes is dropped rather than holding the daemon's only thread;
state fans out to at most 16 subscribers with a one-second send timeout each,
and a subscriber that stops reading is dropped rather than blocking the
rest. The interpreter runs isolated (`/usr/bin/python3 -I`, absolute path,
minimal environment), the log file is mode 0600 and rotating, and Bluetooth
addresses are redacted from it.

The strings are sanitised once at ingest. The sanitisation rule: any
device- or peer-supplied name is stripped of C0/C1 control characters, has
whitespace squeezed to single spaces, is capped at 64 characters, and falls
back to the address when nothing readable survives (`_clean_name`, applied to
the headset's friendly name and to every multipoint peer name before display
or logging). Legitimate Unicode letters pass through — this is
control/format neutrality, not ASCII folding — because the threat is
terminal escape sequences (clipboard rewriting, link spoofing), log forgery
through embedded newlines, and rich-text behaviour in shared QML components,
all of which the rule removes.

The discovery rule is the `UUID:`-only discriminator with a tab-indentation
requirement (`service_protocol`): only tab-indented `UUID:` lines count,
because `bluetoothctl info` prints the `Name:`/`Alias:` value verbatim and a
hostile friendly name containing a newline plus `UUID: <sony-uuid>` starts a
fresh unindented line that a strip-then-match would trust. Genuine property
lines always carry BlueZ's leading-tab indentation, so requiring the tab
tells a real UUID property apart from name content spilling onto a later
line. (A bare first line keeps the historical single-line match: spillover
always follows the `Name:`/`Alias:` line, so line zero cannot be forged
content.)

Prior art informed the shape but was not copied: the v1 protocol knowledge
follows Gadgetbridge's `SonyProtocolImplV1`, with framing cross-checked
against `SonyHeadphonesClient` (Plutoberth) and `SonyBridge` and the RFCOMM
layer against ohm-app's protocol notes; the v2 command set was additionally
checked against mehrshaad/nullpoint (`PROTOCOL.md`, Apache-2.0) and
mos9527/SonyHeadphonesClient (`libmdr`, MIT), with itsgg/omarchy-headset
(MIT) as the prior-art comparison.

## Consequences

- A hostile headset, a hostile neighbour spoofing one, or a broken BlueZ
  gets at most a failed connection and a log line: nothing it sends can grow
  memory without bound, stall the daemon, execute through the environment,
  forge a log entry, or steer discovery.
- What is *not* promised is explicit: link-layer eavesdropping, impersonation
  and firmware flaws are inherent to the transport and out of scope; anyone
  auditing this plugin for those is auditing the wrong layer.
- The two hardened rules — sanitise every remote name at ingest, trust only
  indented `UUID:` lines — are load-bearing and covered by tests that fail if
  either is reverted; weakening them needs a new ADR, not a quiet edit.
- The residual tab-embedded forgery (`\n\tUUID:` inside a name, affecting
  hint ordering only) and the line-zero carve-out (kept for single-line
  synthetic fixtures) are known and documented; removing the carve-out wants
  faithful tab-indented fixtures first.
