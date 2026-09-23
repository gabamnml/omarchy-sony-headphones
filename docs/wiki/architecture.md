# Architecture

The plugin is one self-contained helper and a thin QML front end. The helper
owns the headphones; the widget owns the display; a unix socket joins them.
Most of the code sits behind two seams — `Protocol` and `Transport` — so the
two command sets and the two ways of moving bytes never meet.

```
Panel.qml ── IPC / clicks ──> Service.qml
                                   │  spawns once, keeps alive
                                   ▼
                         sony-headphones watch  (Daemon)
                                   │  state JSON line per change
                                   │  unix socket: set / cycle / refresh / logging /
                                   │               power-off / release / reclaim / session
                                   ▼
                                  Link ── adapter ──> V1Protocol ─┐
                                   │                             ├── the generation-specific
                                   │                             └── builders and parsers
                                   └── transport ──> RfcommTransport (radio)
                                                     DemoTransport  (stand-in)
```

This page is for a contributor who is about to change the code. The owner
guide is [usage.md](usage.md); the agent reference — symbol map, wire bytes,
per-model settings — is [../agents.md](../agents.md).

## The widget and the helper

`bin/sony-headphones` is the only file that talks to the headphones. `watch`
never sees the GUI, and the GUI never sees a byte of Bluetooth: `Service.qml`
reads JSON lines, and short-lived helper calls ask the daemon over the same
socket instead of dialling the headphones again. Each action costs one spawn:
the `--json` reply carries the authoritative state, so the widget parses it
rather than running a second `status` for it; only a failed action spawns a
`status` refresh to resynchronise.

The front end is three files:

- **`Panel.qml`** draws the rows and turns clicks, keys and IPC commands into
  requests. It renders; it does not decide what a device can do.
- **`Service.qml`** owns the conversation with the helper: it spawns `watch`,
  folds its JSON lines into `state`, and drives short-lived
  `set`/`logging`/`status` calls. It forwards the widget's settings as the
  helper's environment variables.
- **`Model.js`** holds the panel's decisions as pure functions, so they can be
  tested without a QML engine. `Model.rowsFor(state)` returns the rows to draw
  in order, and `hasRow(rows, key)` answers whether a row exists; the panel
  binds to those lists instead of repeating per-row conditions. The same file
  maps the protocol to its equalizer presets (`eqPresets`), its auto-power-off
  options, and the logging cycle (`LOG_LEVELS`, `nextLogging`, `loggingLabel`).

## Vocabulary

The terms the rest of this page leans on, tied to where each lives in the
code.

**Helper** — the single self-contained Python program at `bin/sony-headphones`
that speaks Sony's RFCOMM protocol directly. It is the only thing that touches
the headphones; the shell starts it either as a short-lived CLI command or as
the long-lived daemon.

**Daemon** — the helper running under `watch`, which holds the Bluetooth link
open and lends it out over a unix socket (`Daemon`). Opening RFCOMM costs the
better part of a second and the headphones push state on their own, so the
connection is worth keeping.

**Control socket** — the unix socket at
`$XDG_RUNTIME_DIR/omarchy-sony-headphones.sock` that short-lived
`sony-headphones` calls and the widget's `watch` process talk to. One JSON line
is one request; subscribers receive one JSON line per state change
(`Daemon.handle_command`, `Daemon.accept`).

**State** — the single dictionary describing everything known about the
headphones, created by `initial_state()` and filled in by `apply_payload` /
`apply_payload_v2`. It is what the daemon serialises to JSON and the panel
receives; `"connected"`, `"protocol"` and `"features"` say what the link is and
what it can do.

**Protocol v1** — the older Sony command set, nicknamed "MDR", used by the
WH-1000XM2/XM3/XM4 and others. Its framing, opcodes, request builders, parser
and adapter are the `V1Protocol` half of the helper.

**Protocol v2** — the current command set used by the WH-1000XM5/XM6,
WF-1000XM5, LinkBuds, CH720N and others. It reuses opcodes across unrelated
features and tells them apart by a subtype byte; it lives in `V2Protocol` and
the `v2_*` functions around it.

**Init handshake** — the INIT request sent on a fresh link and the reply that
identifies the device (`Link._handshake`). The reply's length is the only
reliable v1/v2 discriminator: four bytes means v1, eight means v2.

**Frame** — one message on the wire: `0x3e <type> <seq> <length:4> <payload>
<checksum> 0x3c`, escaped so marker bytes never appear inside, and answered
with an ACK carrying the flipped sequence number (`encode_message`,
`decode_message`, `Link._ack`).

**Capability / feature** — a string naming one thing a device can do, such as
`"equalizer"`, `"connection-quality"` or `"multipoint"`. `features_for()` gives
a device the features of its model or protocol, and the panel and the setting
builders use the list to decide what to offer (`FEATURE_SETS`,
`FEATURE_CEILINGS`, `SETTING_FEATURES`).

**Ceiling** — the complete set of controls one protocol can carry
(`V1_FEATURES`, `V2_FEATURES`). A per-model feature set is always a refinement
of its own generation's ceiling, never a mix of the two.

**Setting** — one addressable change the CLI and the panel can ask for, such as
`"nc"`, `"eq"` or `"playback-source"` (`SETTING_KEYS`). Each generation has its
own builder — `setting_requests` for v1, `setting_requests_v2` for v2 — and
each refuses the other's exclusive keys outright (`V1_ONLY_SETTINGS`,
`V2_ONLY_SETTINGS`).

**Mode** — the noise-cancelling mode the headphones are in:
`"noise-cancelling"`, `"ambient-sound"`, `"wind-noise-reduction"` or `"off"`
(`NC_MODES`). The panel and the earcup button walk the same cycle through
`next_nc_mode` / `Model.nextMode`.

**Ambient-sound subtype** — the v2 dialect code under which ambient-sound
control is read and written (`V2_ASC_SUBTYPES`). Firmware answers different
shapes — `0x19` for the current noise-adaptation parameter, plus the older
`0x17`, `0x15` and `0x22` — so the device is asked which it speaks and writes
echo the subtype it answered under (`Link.select_asc_subtype`).

**Equalizer preset** — a named tone curve sent as a one-byte code
(`EQ_PRESETS` for v1, `V2_EQ_PRESETS` for v2). The v1 table has the classic
names plus `manual`, `custom-1` and `custom-2`; the v2 table is Off, Heavy,
Clear, Hard, Soft and Custom. The v2 codes are hardware-confirmed on the
WH-1000XM6 only; the other v2 models are offered the same table without a
hardware read.

**Equalizer bands** — the custom curve values. v1 carries a clear-bass band
plus five bands in the range -10..10 (`eq_bands_request`); v2 carries ten bands
in the range -6..6 (`v2_eq_bands_request`).

**Listening mode** — the mode the vendor app reports on v2: `"standard"`,
`"background-music"` or `"cinema"`. It is derived from two independent flags,
with Background Music taking priority (`derive_listening_mode`).

**Background Music (BGM)** — one of the two flags behind listening mode
(`V2_AUDIO_SUB_BGM`, `v2_bgm_request`). It is inverted on the wire and carries
the room being simulated; some firmware uses subtype `0x03` rather than `0x09`,
so the helper discovers and echoes it.

**Upmix Cinema** — the other listening-mode flag
(`V2_AUDIO_SUB_UPMIX_CINEMA`, `v2_cinema_request`), also inverted on the wire.

**Multipoint** — the v2 feature for listing the headphones' paired Bluetooth
peers and choosing which one owns playback (`DemoDeviceV2.device_list`,
`parse_v2_device_list`). Its frames ride message table 2, the PERIPHERAL
family, not the usual table 1.

**Playback source** — the MAC address of the multipoint peer currently playing
audio, read from the trailing status byte of the device list
(`state["playback_source"]`). Choosing a peer other than the reported source
pauses every local MPRIS player that can pause
(`Model.pausesLocalPlayback`, `Service.choosePlaybackSource`): selection-only,
so a switch the phone makes never touches local playback.

**DSEE Extreme** — Sony's upscaling of compressed audio. On v1 it is read and
written through the shared boolean table (`BOOL_SETTINGS["dsee"]`); on v2 it
shares the audio family with connection quality and has its own builders, and
unlike connection quality it is not inverted: `0x01` means on.

**Connection quality** — the v2-only trade between sound quality (LDAC) and a
stable connection (SBC) (`v2_connection_quality_request`). The wire value names
the compromise, not the feature, so the helper flips the sense on the way in
and out.

**Speak-to-Chat** — the feature that pauses playback when you start talking,
with sensitivity, resume timeout and (v1 only) voice focus. On v2 it is
inverted on the wire.

**Voice guidance** — the headphones' spoken notifications
(`voice-notifications`, `state["voice_notifications"]`). v1 shape: GET
`46 01 01`, SET `48 01 01 VV` non-inverted (`0x01` = on), value at index 3. v2
has its own shorter shape, confirmed on a WH-1000XM6 (firmware 3.1.5, capture
probe `0e 46 01 01` answered by `47 01 00 03`): GET `46 01 01`, SET
`48 01 VV` inverted (`0x00` = on), 4-byte RET `0x47` / NOTIFY `0x49` with the
value at index 2 and an unknown trailing byte at index 3. Never reuse the v1
shape on v2. Scope is WH-1000XM5/XM6 only for now.

**Transport** — the bytes-moving seam behind a `Link` (`Transport`).
`RfcommTransport` is the real radio; `DemoTransport` is a stand-in whose other
end is a demo device. A `Link` never looks past its transport.

**RFCOMM** — the serial-over-Bluetooth channel Sony's control service runs on.
The helper dials it directly, with no PyBluez and no D-Bus bindings.

**SDP** — the Service Discovery Protocol lookup that asks the device which
RFCOMM channel carries its control service (`sdp_query`, `sdp_channel`). BlueZ
exposes no API for a remote service record, so the helper reads it over L2CAP
itself and bounds everything the device can make it hold or wait for.

**Channel cache** — the remembered RFCOMM channel under
`$XDG_CACHE_HOME/omarchy-sony-headphones/<MAC>.channel` (`remember_channel`,
`cached_channel`). It is an optimisation only: a cache that cannot be made
private is declined and discovery simply runs again.

**Demo mode** — `SONY_HEADPHONES_DEMO` selecting a stand-in device instead of a
radio: `1` for the v1 `DemoLink`, `v2` or `2` for the v2 `DemoLinkV2`
(`demo_mode`, `demo_link`). The stand-in answers through the real framing, so
the whole stack is exercised without hardware.

**Trace** — the opt-in, byte-level frame log (`SONY_HEADPHONES_TRACE`). A value
of `1` writes to stderr for the CLI; any other value is a file path, which is
what the daemon needs because the widget reads its stderr as an error message.

**Logging level** — how much the helper keeps in its local, rotating log:
`off`, `errors` (the default) or `all` (`LOG_LEVELS`, `configure_logging`). The
level belongs to the daemon, can be changed at runtime over the socket, and
rides in the state so the panel can read and cycle it.

## The Protocol seam

`Protocol` names the six operations a link uses: `negotiate` (whatever a
command set must settle before first use — the v2 dialect queries, a no-op on
v1), what to ask after connecting (`refresh_requests`), what to ask to keep the
link alive (`poll_requests`), what a reply means (`apply`), what a setting
change becomes (`setting_requests`), and how to turn the headphones off
(`power_off`). `V1Protocol` and `V2Protocol` are thin adapters over the
existing per-generation functions; the wire layouts genuinely differ, so each
adapter delegates rather than reimplementing, and no layout is copied.

`Link` makes every wire decision through `self.adapter` and never looks at the
protocol name. The adapter is chosen once, from the init handshake
(`Link._set_protocol`, called by `Link._handshake`), and stays for the life of
the link. The v1/v2 reply *length* is the discriminator: four bytes selects
v1, eight selects v2. `Link.connect` settles each candidate channel through
`Link._establish`, which runs the handshake, the feature selection and then
`adapter.negotiate` unconditionally — the dialect queries are part of opening
the link, not an optional extra — before the channel counts as good.

## RFCOMM framing and ACK discipline

Sony's app protocol runs over an RFCOMM serial channel. A message is framed as
`0x3e <type> <seq> <length:4 big-endian> <payload> <checksum> 0x3c`, escaped so
the marker bytes never appear inside a message, and every message — in both
directions — is answered with an acknowledgement carrying the flipped sequence
number. The checksum is a plain byte sum from `<type>` through the last payload
byte; escaping happens after the checksum is appended, so a checksum that lands
on a marker byte is escaped too. The full byte reference — marker values,
message-table selectors, opcode families and subtypes — is in
[../agents.md](../agents.md#wire-protocol).

`bin/sony-headphones watch` keeps that link open and serves the unix socket, so
the widget gets push updates when you press the button on the earcup, and short
CLI calls apply instantly instead of paying for a fresh connection each time.

## SDP discovery and the channel cache

The RFCOMM channel comes from the device's own SDP record, read over L2CAP by a
small SDP client in the helper. BlueZ exposes no API for a remote service
record and bluez-utils no longer ships `sdptool`, and guessing is not an
option: every other channel refuses the connection, a blocking connect to a
closed one takes seconds, and a WH-1000XM4 that has been walked channel by
channel starts refusing the right one too. Both the v1 and v2 control service
UUIDs are searched — a device answers only the one it speaks — and the service
`bluetoothctl info` advertises is a hint for which UUID to try first. It is
only a hint: the init handshake alone chooses the command set, by the length of
its reply. The answer is cached under `~/.cache/omarchy-sony-headphones/`, in a
directory kept at mode 0700 and written without following symlinks; a cache
that cannot be made private is declined and discovery simply runs again.

## Protocol-aware capabilities

A device is never offered a control its command set cannot carry. The model is
two levels:

- **Ceilings** — `V1_FEATURES` and `V2_FEATURES` are everything each protocol
  can express; `FEATURE_CEILINGS` maps a protocol name to its set.
- **Refinements** — `FEATURE_SETS` lists what each known model actually
  honours. A refinement is validated at import against its own generation's
  ceiling and fails loudly if it strays.

`features_for(name, protocol)` searches inside the confirmed protocol's models
first, falls back to that protocol's ceiling, and only with no protocol at all
offers the union. The confirmed handshake, not the `bluetoothctl` hint or the
advertised UUID, decides which side is searched. The feature list travels in
the state, and both the panel (`Model.rowsFor`) and the setting builders
(`SETTING_FEATURES`, `V1_ONLY_SETTINGS`, `V2_ONLY_SETTINGS`) use it. The full
model-by-setting breakdown is in [../agents.md](../agents.md#settings-by-model).

`UI_FEATURES` carries presentation-only markers — currently `eq-sbc-only`,
which greys the equalizer on the WH-1000XM2/XM3 while the codec is not SBC.
They ride in `FEATURE_SETS` so `Model.availabilityFor` can read them, but are
exempt from the generation-ceiling check because they gate a row, not a wire
command.

## The Transport seam

`Transport` moves bytes and says when the peer is gone; `Link` owns framing,
sequence numbers, the handshake, dispatch and the parser boundary.
`RfcommTransport` is the real radio: it asks the device's SDP record for the
channel (searching the v2 UUID first by default, and flipping to the v1 UUID
first only when `bluetoothctl` hinted v1), falls back to the cached channel,
and offers each candidate to `Link._establish` before accepting it.
`DemoTransport` is the stand-in: it wraps a `DemoDevice` and answers in the real
frame format, so the demo exercises the same encoders and schemas as hardware.

`Link.sock` is still reachable under its old name — the daemon selects on it
and the tests stand streams in for it — but it lives on the transport. Swapping
transports therefore changes where the bytes come from without touching a line
of framing or parsing.

## The demo stand-ins

`DemoDevice` answers requests the way a WH-1000XM4 does; `DemoDeviceV2` does
the same for a WH-1000XM6, including the v2-only surface (DSEE, connection
quality, listening mode, the BGM room, multipoint). `DemoLink` and `DemoLinkV2`
are ordinary `Link`s with a `DemoTransport` and their identity already settled,
so nothing in the framing path has a demo-shaped branch. `SONY_HEADPHONES_DEMO`
selects one; `1` is the v1 stand-in and `v2`/`2` the v2 one.

## The row model and the logging link

Logging is a link at the bottom of the panel: clicking it calls
`Service.setLogging`, which sends a `logging <level>` call to the running
daemon, and the daemon stamps the new level into the state it publishes. The
level also boots from the widget's `logging` setting (`SONY_HEADPHONES_LOG`),
so the two controls meet in the same state field.

The panel's rows also carry the write-then-verify contract. A setting the
widget writes joins the helper's `pending` list for roughly two seconds until
the device's own read-back moves, and a write the read-back never confirms
lands in `refused` with its reason; both ride in the state. `Model.isPending`
appends an ellipsis to the row label while a write is in flight,
`Model.refusedReason` paints the refused row in the urgent colour, and
`Model.refusedSummary` collects every reason into one quiet line. The widget
paints the change optimistically first (`Service.pendingPatch`, mirroring the
helper's `_mark_pending`) and lets the daemon's next state line be
authoritative.

## The session lifecycle and the presence signal

A Sony headset allows exactly one control session at a time, so the daemon's
link is a shared resource with a policy. The policy is `hold` (keep the
session, as always) or `on-demand` (release it after `idleSeconds` quiet
seconds so a phone paired over multipoint can take it). A fresh daemon boots
from `SONY_HEADPHONES_SESSION` and `SONY_HEADPHONES_SESSION_IDLE`, forwarded
from the widget's `sessionPolicy` and `idleSeconds` settings exactly the way
`SONY_HEADPHONES_LOG` is; a running one is read and changed through the
`session` socket command, and the live policy, delay and session stamp every
state line (`Daemon.state`, `Daemon._stamp`).

The daemon's session is one of `held`, `connecting` or `released`. A release
(`Daemon.release`, via the `release` command, the panel's session row, the idle
timer or a presence loss) closes the transport, keeps the last known values so
the panel still draws the device (`Model.hasState` counts a released session as
drawable), sets no error, and keeps the cached channel. A reclaim
(`Daemon.reclaim`, or any device command through `Daemon.with_link`, which
reconnects once on a stale or released link) returns through the normal connect
path and refreshes. The idle timer (`Daemon.release_if_idle`, checked on the
run loop's one-second tick) measures only client commands that touched the
device: the periodic poll (`Daemon.poll_link`) and a subscriber reading the
state deliberately never feed `last_activity`.

Presence is a second, event-driven signal. `Service.present` reads
Quickshell's Bluetooth service and folds it through the pure `Model.present`
rule — a pinned address counts only its exact MAC (case-insensitively),
otherwise any connected device in Sony's headset name families counts — so
connect and disconnect arrive as property changes rather than through the
helper's poll. Under `on-demand`, losing presence releases the session and
regaining it reclaims, but only a release presence itself caused
(`Service.presenceReleased`): a release made by hand is never undone by a
connect event, and `hold` does nothing automatically. The helper's own
reconnect, backoff and poll stay the fallback when the Bluetooth service is
unavailable. The session row (`Model.sessionLabel`, `Model.rowsFor` offering
`"session"` for every connected device) reads `held` and flips either way, and
a write attempted while released is allowed: the daemon reclaims first, which
is the "any command reclaims" promise made visible.

The daemon backs off instead of fighting the phone for that one session:
`Daemon.retry_delay` climbs from `RETRY_MIN` (2 s) to `RETRY_MAX` (30 s); a
link that held past `STABLE_AFTER` (10 s) resets the delay, one that died young
doubles it, and a deliberate `release` always resets to the minimum. A busy
RFCOMM open (`EBUSY`, the phone holding the session over multipoint) reports
`another device may be using the control channel` without burning the remaining
candidates. A set/cycle the confirmed generation cannot carry is refused by
`Daemon.precheck_setting` before any link is opened.

## The daemon and the control socket

`Daemon` owns the one Bluetooth link. It listens on
`$XDG_RUNTIME_DIR/omarchy-sony-headphones.sock`, which it may only create
inside a directory confirmed to be a private, user-owned 0700 directory (or one
it makes and verifies itself). Election runs through an exclusive `flock` on a
lock file, opened relative to a descriptor for that directory and never through
a symlink; a stale socket is replaced only after confirming it is a socket the
user owns.

Requests are one JSON line, with a whole-request deadline and a line-size
limit: a client that trickles bytes is dropped instead of holding the
daemon's only thread. A subscriber (`cmd: "subscribe"`) gets a line
immediately and a line per state change; everything else gets one response
line. `run()` selects over the listener, the subscribers, the link socket and a
one-second tick, reconnecting with capped backoff and treating silence in
answer to the periodic poll as a dead link. `publish()` serialises once and
fans the same bytes out to every subscriber (at most 16, one second each — a
subscriber that stops reading is dropped rather than blocking the rest), and
skips a state identical to the last emitted one. Every command the socket
accepts — `set`, `cycle`, `refresh`, `power-off`, `logging`, `release`,
`reclaim`, `session` — reaches the link through `with_link`, except `logging`,
`release` and the `session` read, which belong to the daemon and need no link.
The connected-device list behind `find_device` is cached in memory for two
seconds, collapsing one operation's several lookups into a single scan.

## Why these seams

The two seams exist because there are exactly two independent axes of
variation. The command set (v1 vs v2) and the source of bytes (radio vs
stand-in) vary independently: four combinations, one framing implementation,
one parser boundary, one daemon. Without the `Protocol` seam every wire
decision would need a protocol test in `Link`, and the demo would drift from
the hardware path. Without the `Transport` seam the demo would need its own
`Link`.

The capabilities model is the third seam-like decision: features are data
(ceilings and per-model refinements) validated at import, not conditionals
scattered through the panel and the builders. Adding a device means adding
data; adding a command set means adding an adapter and a ceiling.

## Security posture

The ADRs under [../adr/](../adr/) are the source of truth; this is the
one-paragraph summary of each, with the reason it exists. Do not weaken one
without a new ADR.

- **[0001 — One self-contained helper, launched with the system
  interpreter](../adr/0001-single-self-contained-helper.md).** All the
  protocol lives in one stdlib-only file, run as `/usr/bin/python3 -I`. There
  is no install step and no dependency that can drift; the script directory is
  deliberately not on `sys.path`.
- **[0002 — A minimal environment for the helper and for
  `bluetoothctl`](../adr/0002-minimal-helper-environment.md).** The widget
  passes only a named set of variables and a fixed `PATH`; `bluetoothctl` is
  resolved from an absolute candidate list and run with `LC_ALL=C`. Locale and
  binary resolution cannot be hijacked by the inherited shell environment.
- **[0003 — Private runtime and cache directories, opened without following
  symlinks](../adr/0003-private-runtime-and-cache-directories.md).** The
  socket is bound through a descriptor for a verified, user-owned 0700
  directory; the lock, socket and cache file are opened `O_NOFOLLOW`; the log
  is `fchmod`ed 0600. A hostile local user cannot create the path first or
  redirect a write through a symlink.
- **[0004 — Find the RFCOMM channel over SDP, and cache
  it](../adr/0004-rfcomm-channel-discovery-over-sdp.md).** The helper speaks
  SDP itself over L2CAP, following continuation states, with every lookup
  bounded (8 KiB, 8 rounds, 8 seconds, 16-byte continuation, depth-capped
  parser). A hostile device can slow one lookup and nothing more.
- **[0005 — Protocol-aware capability ceilings](../adr/0005-protocol-aware-capability-ceilings.md).**
  Features are data in two layers, validated at import, so a model can never
  be offered a control its protocol cannot route. Adding a model is a data edit
  plus a test.
- **[0006 — One Protocol seam with a separate adapter per
  generation](../adr/0006-protocol-seam-with-separate-adapters.md).** `Link`
  branches only through its adapter; framing, ACK, buffering and the daemon are
  written once and shared by both generations and the demo. A third command set
  is one adapter and one ceiling.
- **[0007 — Local-only rotating logging with runtime
  levels](../adr/0007-local-only-rotating-logging.md).** One named logger and
  one `RotatingFileHandler` in the private cache directory: 0600, 256 KiB,
  two backups, Bluetooth addresses redacted. A logging failure cannot corrupt
  the conversation, and nothing is written to stdout or stderr.
- **[0008 — One control session with a hold/on-demand policy and a manual
  toggle](../adr/0008-session-policy-and-manual-release.md).** The daemon can
  hand the one session to a phone deliberately and take it back, backs off
  rather than fighting, and keeps a released session visible rather than blank.
- **[0009 — Bluetooth threat model](../adr/0009-bluetooth-threat-model.md).**
  The link layer, firmware and stack are untrusted; the plugin bounds only the
  parser, the socket and the strings. Remote names are sanitised once at
  ingest (controls stripped, whitespace squeezed, 64-character cap, address
  fallback) and only tab-indented `UUID:` lines steer discovery. Link-layer
  eavesdropping, impersonation and firmware flaws are out of scope and said so
  plainly.

## Tests

`tests/` mirrors the seams:

| Module | What it covers |
|---|---|
| `test_framing.py` | escape/checksum, frame shape, the bounded read buffer |
| `test_sdp.py` | the L2CAP lookup, record parsing, the channel cache |
| `test_v1.py` | v1 request/reply/setting layouts and the v1 model table |
| `test_v2.py` | v2 request/reply/setting layouts, subtypes, model table |
| `test_demo.py` | both stand-ins round-tripping every setting |
| `test_protocol_seam.py` | adapter selection, negotiation, transports, features |
| `test_daemon.py` | runtime files, socket bounds, logging, publish, sessions |
| `test_cli.py` | the shell launch line, the direct path, demo selection, one-spawn actions |
| `test_model_js.py` | runs `tests/test_model.mjs` under node or deno |
| `test_verify_tool.py` | the `tools/verify.py` verdicts and restore flow |

`tests/support.py` loads the helper once by path and holds the fixtures shared
across modules. Every module runs standalone, and `tests/run.py` runs the whole
suite by discovery.

## The gate

`make check` is the single source of truth for "green": it runs the Python
suite, the JS model harness, lint, `make qmltest`, and plugin validation. The
direct commands are listed in [Verifying a
change](../agents.md#verifying-a-change).

Each Python test module also runs on its own (`python3 tests/test_v1.py`); the
JS model tests need `node` or `deno`, and the Python `test_model_js` module
skips itself when neither is installed. `make doctor` reports which of these
tools are present; a missing optional tool skips its check with a warning
rather than failing. `omarchy plugin validate` refuses any symlink inside the
plugin folder, so validation fails on a checkout that holds symlinked tooling
directories — the code is not at fault, and validating a copy without those
directories passes. The exact verification workflow for a change, including
the demo-mode isolation rule, is in
[../agents.md](../agents.md#verifying-a-change).
