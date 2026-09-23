# Agent reference

Everything an agent needs to change this repository: the rules for working
here, a one-line role for each file, the full helper symbol map, the wire
protocol, every setting's per-model availability, and how to verify a change.

This file is the maintainer reference; the reader-facing wiki is
[index.md](wiki/index.md), with [usage.md](wiki/usage.md) for owners and
[architecture.md](wiki/architecture.md) for how the pieces fit.

## Working here

- The helper is one self-contained file, `bin/sony-headphones`, Python standard
  library only. It is launched as `/usr/bin/python3 -I <plugin>/bin/sony-headphones`;
  the script directory is deliberately not on `sys.path`, so it cannot import
  local modules. Read
  [adr/0001-single-self-contained-helper.md](adr/0001-single-self-contained-helper.md)
  before proposing a split.
- Run both suites before and after a change: `python3 tests/run.py` (Python)
  and `node tests/test_model.mjs` (`Model.js`). Validate the plugin with
  `omarchy plugin validate .`. `make check` is the gate: it runs both suites,
  `make lint`, `make qmltest`, and plugin validation. The helper is
  extensionless, so `make lint` names it explicitly — `ruff check .` alone
  never lints it.
- Find anything in the helper by the symbol map below, not by reading the whole
  file. Grep the symbol (`rg -n 'def apply_setting' bin/sony-headphones`) and
  read that region plus maybe one caller. Names are stable; line ranges are
  not.
- Shared test fakes live in `tests/support.py` (`FakeClock`, `FakeStream`, the
  loader that puts the helper on the test path) and reusable mixins live beside
  their module. Reuse them; do not re-define a helper such as `run_main` in
  another test class.
- Dev tools on this machine: `ruff` is installed system-wide
  (`omarchy pkg add ruff`); `qmllint` and `qmltestrunner` ship in
  `qt6-declarative` at `/usr/lib/qt6/bin/` and are symlinked into `~/.local/bin`
  so the gate finds them.
- Security invariants — isolated interpreter, minimal environment,
  `O_NOFOLLOW`/0700 private directories, bounded parsing, 0600 logging — are
  recorded in [adr/0001](adr/0001-single-self-contained-helper.md) through
  [adr/0009](adr/0009-bluetooth-threat-model.md). Do not weaken them; add an
  ADR when a change touches them.
- Standards: the [ADRs](adr/0001-single-self-contained-helper.md) are the source
  for security and wire-format rules, and the
  [architecture page](wiki/architecture.md) records the design rules. Read both
  before changing a seam or a wire format.

### Adding a setting

A setting crosses five places: the `manifest.json` schema, `Panel.qml` (read it
and draw its row), `Service.qml` (forward it as an environment variable or
command), `bin/sony-headphones` (accept and apply it), and the
[Settings by model](#settings-by-model) table here plus tests. Missing one is
the usual bug.

## File roles

One line each.

| Path | Role |
|---|---|
| `bin/sony-headphones` | The helper: framing, protocol adapters, SDP, daemon, CLI — stdlib only, one file. |
| `Panel.qml` | The bar widget: draws rows, turns clicks/keys/IPC into requests. |
| `Service.qml` | Owns the helper conversation: spawns `watch`, folds JSON state, forwards widget settings. |
| `Model.js` | Pure panel decisions: `rowsFor`, availability, presets, labels — testable without QML. |
| `manifest.json` | Plugin metadata and the widget settings schema and defaults. |
| `README.md` | Owner-facing overview, install, gestures and settings tables. |
| `Makefile` | The gate and dev helpers: `test`, `lint`, `qmltest`, `check`, `demo`, `verify`, `doctor`. |
| `docs/agents.md` | This file. |
| `docs/adr/` | The nine architecture decisions: security, seams, sessions, framing. |
| `docs/wiki/` | Reader-facing pages: the usage guide and the architecture write-up. |
| `tests/` | The Python suite, the JS harness and the QML tests. |
| `tools/verify.py` | On-hardware write-then-read-back verifier for what a device honours. |
| `hooks/`, `.github/` | The pre-commit hook and CI. |

## Verifying a change

`make check` is the single source of truth for "green": the Python suite, the
JS model harness, lint, `make qmltest`, and plugin validation. The direct
commands work too:

```bash
python3 tests/run.py                             # the whole suite, no headphones required
python3 -m unittest discover -s tests -t tests   # the same through unittest
node tests/test_model.mjs                        # Model.js, or: deno run --allow-read
omarchy plugin validate .
```

Each Python test module also runs on its own (`python3 tests/test_v1.py`); the
JS model tests need `node` or `deno`, and the Python `test_model_js` module
skips itself when neither is installed. `make doctor` reports which tools are
present; a missing optional tool skips its check with a warning rather than
failing. `omarchy plugin validate` refuses any symlink inside the plugin
folder, so validation fails on a checkout that holds symlinked tooling
directories — the code is not at fault, and validating a copy without those
directories passes.

`tests/` covers one seam per module: `test_framing.py` (escape/checksum, frame
shape, bounded read buffer), `test_sdp.py` (L2CAP lookup, record parsing,
channel cache), `test_v1.py` and `test_v2.py` (request/reply/setting layouts
and model tables), `test_demo.py` (both stand-ins round-tripping every
setting), `test_protocol_seam.py` (adapter selection, negotiation, transports,
features), `test_daemon.py` (runtime files, socket bounds, logging, publish,
sessions), `test_cli.py` (shell launch line, direct path, demo selection,
one-spawn actions), `test_model_js.py` (runs the JS harness), and
`test_verify_tool.py` (the verifier's verdicts and restore flow). See
[wiki/architecture.md](wiki/architecture.md#tests) for the table.

### Isolation (hard rule)

Every run of the helper needs `SONY_HEADPHONES_DEMO=1` (or `v2`) and a throwaway
`XDG_RUNTIME_DIR=$(mktemp -d)`. This machine has the widget running against real
headphones; a run without both can change real settings. Do not export
`SONY_HEADPHONES_DEMO` into the test suite — the tests set it themselves, and
an ambient value makes `direct()` take the demo branch.

```bash
XDG_RUNTIME_DIR=$(mktemp -d) SONY_HEADPHONES_DEMO=1  bin/sony-headphones status   # v1 (XM4) stand-in
XDG_RUNTIME_DIR=$(mktemp -d) SONY_HEADPHONES_DEMO=v2 bin/sony-headphones status   # v2 (XM6) stand-in
XDG_RUNTIME_DIR=$(mktemp -d) SONY_HEADPHONES_DEMO=1  bin/sony-headphones watch    # a widget can attach
```

Never touch real hardware from a test: demo mode and a throwaway
`XDG_RUNTIME_DIR` always, and no test may open the machine's real daemon
socket. Never write the real cache or log either — a throwaway
`XDG_RUNTIME_DIR` is not enough, because `main()` opens the log under
`CACHE_DIR` and the daemon writes its channel cache there. Point `CACHE_DIR`
(and any handler built from it) at a temp directory, as `tests/test_daemon.py`
does, or set a throwaway `XDG_CACHE_HOME`.

### Tracing and logging

`SONY_HEADPHONES_TRACE` writes every frame the helper sends and receives, so
the wire protocol can be read directly. Stop the widget's helper before tracing
a one-off command — it owns the Bluetooth link, and two helpers cannot hold the
same control channel:

```bash
SONY_HEADPHONES_TRACE=/tmp/sony.trace bin/sony-headphones probe
SONY_HEADPHONES_TRACE=/tmp/sony.trace bin/sony-headphones set nc ambient-sound
```

A value of `1` writes the trace to stderr instead. That is fine for the CLI,
but not for the daemon the widget runs: the widget parses its helper's stderr
as an error message, so a daemon has to be given a file path. Each line carries
the direction (`tx` or `rx`), the time since the previous frame, the raw frame
in hex, and a decoded `type`, sequence, opcode and payload.

`SONY_HEADPHONES_LOG` decides how much the helper keeps in its local log at
`$XDG_CACHE_HOME/omarchy-sony-headphones/sony-headphones.log` (mode 0600,
rotated at 256 KiB with two backups). The values are `off`, `errors` (the
default) and `all`. Nothing is ever written to stdout or stderr, and Bluetooth
addresses are redacted. The widget's `logging` setting is the level the helper
boots with; the running daemon's level changes over the socket with
`bin/sony-headphones logging [off|errors|all]`.

### Verifying what the headphones honour

An acknowledgement only means a write arrived; a headset can answer a write and
then discard it. `tools/verify.py` checks what actually landed: for each
control it reads the current value, writes a different legal one, drops the
session with `release`, reclaims it, reads the value back from the fresh
session, compares, and restores the original. It talks to the running daemon
through the CLI's control socket and never opens a second RFCOMM connection, so
the widget must be running (or a demo daemon started by hand):

```bash
make verify MAC=AA:BB:CC:DD:EE:FF
make verify MAC=AA:BB:CC:DD:EE:FF CONTROLS='nc eq dsee'
python3 tools/verify.py AA:BB:CC:DD:EE:FF nc
```

Each control reports one verdict: HONOURED, IGNORED, FAILED, or REFUSED (the
daemon would not attempt the write, with its reason). `eq-bands` and
`playback-source` are not scalar controls and are left to a check by hand.

## Settings by model

The 18 `SETTING_KEYS` (the CLI's accepted keys, `bin/sony-headphones`):

`ambient-level`, `auto-power-off`, `bgm-room-size`, `connection-quality`,
`dsee`, `eq`, `eq-bands`, `focus-on-voice`, `listening-mode`, `nc`,
`pause-when-taken-off`, `playback-source`, `speak-to-chat`,
`stc-focus-on-voice`, `stc-sensitivity`, `stc-timeout`, `touch-sensor`,
`voice-notifications`.

Whether a key is offered is decided by `_setting_context` in each builder:

- **Cross-generation refusal first.** `V1_ONLY_SETTINGS = {"touch-sensor"}` is
  refused by the v2 builder; `V2_ONLY_SETTINGS = {"connection-quality",
  "listening-mode", "bgm-room-size", "playback-source"}` is refused by the v1
  builder. `stc-focus-on-voice` maps to the shared `speak-to-chat` feature, so
  the v2 builder refuses it explicitly.
- **Then the feature gate.** `SETTING_FEATURES` maps a key to the feature it
  needs (`eq`/`eq-bands` → `equalizer`, `stc-*` → `speak-to-chat`,
  `bgm-room-size` → `listening-mode`, `playback-source` → `multipoint`, and so
  on). A key whose feature is missing from the device's `features` is refused.
- **Three keys have no gate at all:** `nc`, `ambient-level` and
  `focus-on-voice` are offered to every model, including one whose feature set
  carries no noise-control flag. That is a known over-offer, not a bug to
  "fix" without evidence.

The table below is derived from `FEATURE_SETS` and those rules: `Y` means the
builder offers the key on that model, `.` means it refuses. Rows for
`WH-1000XM4`, `WH-1000XM3` and `WH-1000XM2` are the v1 refinements; the `v1
ceiling` row is any other v1 model (or an unrecognised one); the v2 rows are
the v2 refinements; the `v2 ceiling` row is any other v2 model. `features_for`
matches the model name by case-insensitive substring in dict order, so
`LinkBuds S` is listed before `LinkBuds`.

Headers: `amb` = `ambient-level`, `fov` = `focus-on-voice`, `eqb` = `eq-bands`,
`sts` = `stc-sensitivity`, `stt` = `stc-timeout`, `stf` =
`stc-focus-on-voice`, `pau` = `pause-when-taken-off`, `tou` = `touch-sensor`,
`voi` = `voice-notifications`, `apo` = `auto-power-off`, `cq` =
`connection-quality`, `lm` = `listening-mode`, `bgm` = `bgm-room-size`, `src` =
`playback-source`.

| Model | Set | nc | amb | fov | eq | eqb | dsee | stc | sts | stt | stf | pau | tou | voi | apo | cq | lm | bgm | src |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| WH-1000XM4 | XM4 | Y | Y | Y | Y | Y | Y | Y | Y | Y | Y | Y | . | Y | Y | . | . | . | . |
| WH-1000XM3 | XM3 | Y | Y | Y | Y | Y | Y | . | . | . | . | . | Y | Y | Y | . | . | . | . |
| WH-1000XM2 | XM2 | Y | Y | Y | Y | Y | Y | . | . | . | . | . | . | . | . | . | . | . | . |
| other v1 | ceiling | Y | Y | Y | Y | Y | Y | Y | Y | Y | Y | Y | Y | Y | Y | . | . | . | . |
| WH-1000XM6 | XM6 | Y | Y | Y | Y | Y | Y | Y | Y | Y | . | Y | . | Y | Y | Y | Y | Y | Y |
| WH-1000XM5 | WH-1000XM5 | Y | Y | Y | Y | Y | Y | Y | Y | Y | . | Y | . | Y | Y | Y | . | . | . |
| WF-1000XM5 | WF-1000XM5 | Y | Y | Y | Y | Y | Y | Y | Y | Y | . | Y | . | . | Y | Y | . | . | . |
| LinkBuds S | LinkBuds S | Y | Y | Y | Y | Y | Y | Y | Y | Y | . | Y | . | . | Y | . | . | . | . |
| LinkBuds | LinkBuds | Y | Y | Y | Y | Y | Y | Y | Y | Y | . | Y | . | . | Y | . | . | . | . |
| WH-CH720N | WH-CH720N | Y | Y | Y | Y | Y | Y | . | . | . | . | . | . | . | Y | Y | . | . | . |
| other v2 | ceiling | Y | Y | Y | Y | Y | Y | Y | Y | Y | . | Y | . | Y | Y | Y | Y | Y | Y |

Notes the table cannot carry:

- **Touch sensor on the XM4** reads `.` on purpose: the hardware answers
  "still on" to every disable, so the set omits `touch-sensor` rather than
  drawing a dead row.
- **Auto-power-off timers.** On v1, a value other than `off`/`when-taken-off`
  needs the separate `auto-power-off-timer` feature. Only the `v1 ceiling` and
  `WH-1000XM3` carry it, so those are the only v1 rows offering 5/30-minute,
  1/3-hour timers.
- **`voice-notifications` on v2** is confirmed on the WH-1000XM6 (firmware
  3.1.5) and reported on the WH-1000XM5; the `v2 ceiling` row offers it to any
  unrecognised v2 model as an over-offer pending capture.
- **`eq-bands` shape follows the generation:** v1 takes 6 values (clear bass +
  5 bands, each -10..10); v2 takes 10 values (each -6..6, stored biased by 6).
- **`listening-mode` is XM6-only** among the named v2 sets, because only the
  XM6 carries the `listening-mode` feature.
- The model-by-model research that these sets come from — including the
  unverified cells and the sources — is in
  [Capability and exclusion matrix](#capability-and-exclusion-matrix) below.

## Capability and exclusion matrix

What the headphones refuse to honour at once, what each model is known to
carry, and where the per-model feature sets still over-offer. This is the cited
research behind the `FEATURE_SETS` table above; the code-derived availability
is [Settings by model](#settings-by-model). Every row keeps the status the
research ticket reported: *confirmed* is read on the cited page,
*reported-but-unverified* is seen in one source only, and *unverified* means
the cited page could not be fetched.

### Exclusion table

One setting (or app surface) against what it conflicts with. A refused write
matching one of these pairs is the headphones saying no, not a transport fault.

| Setting | Conflicts with | Source | Status |
|---|---|---|---|
| `connection-quality` (LDAC) | `multipoint` — WH-1000XM5 launch firmware; either/or until fw 2.0.2 | https://www.notebookcheck.net/Sony-WH-1000XM5-receives-Multipoint-LDAC-and-head-tracking-support-via-new-update.736850.0.html | reported-but-unverified |
| `dsee` | active phone call — "DSEE Extreme is disabled during a call" (XM6) | https://helpguide.sony.net/mdr/2984/v1/en/contents/TP1001861254.html | confirmed |
| `dsee` | LDAC playback — the *device* mutes the DSEE effect; both settings stay on, so this is not a grey-out rule (XM6, XM5) | https://helpguide.sony.net/mdr/2984/v1/en/contents/TP1001861254.html · https://helpguide.sony.net/mdr/wh1000xm5/v1/en/contents/TP1000539063.html | confirmed (device-side, not an exclusion) |
| `dsee` Auto | reduces full operating time (XM6) | https://helpguide.sony.net/mdr/2984/v1/en/contents/TP1001861254.html | confirmed |
| `listening-mode` BGM | active phone call — "BGM effects are disabled during a call" (XM6) | https://helpguide.sony.net/mdr/2984/v1/en/contents/TP1001856857.html | confirmed |
| `listening-mode` BGM / `bgm-room-size` | a digital assistant is set ("check Voice control / Voice assistant") — Sound Connect greys Background music, and a BGM write makes the device reset. Not present in any record the helper reads, so it cannot be detected from state today | Sound Connect app, observed on the user's WH-1000XM6; no public URL | confirmed (app-level) |
| `listening-mode` Upmix Cinema | active call; wired headphone-cable use (XM6) | https://helpguide.sony.net/mdr/2984/v1/en/contents/TP1001924935.html | confirmed |
| `connection-quality` low-latency | any non-LE-Audio peer (XM6, WF-1000XM5) | https://helpguide.sony.net/mdr/2984/v1/en/contents/TP1001856706.html · https://helpguide.sony.net/mdr/2963/v1/en/contents/TP1000781983.html | confirmed |
| `multipoint` (Classic) | LE Audio connection (WF-1000XM5) | https://helpguide.sony.net/mdr/2963/v1/en/contents/TP1000781304.html | confirmed |
| Sound Connect app features (XM6; per-model on their guides) | LE Audio connection | https://helpguide.sony.net/mdr/2984/v1/en/contents/TP1001856857.html · https://helpguide.sony.net/mdr/2963/v1/en/contents/TP1000781980.html | confirmed |
| Equalizer / sound-position / surround | any codec other than SBC — Gadgetbridge `AudioSettingsOnlyOnSbcCodec` (scoped to WH-1000XM2/XM3; Gadgetbridge hardcodes the runtime check to XM3) | https://raw.githubusercontent.com/Freeyourgadget/Gadgetbridge/master/app/src/main/java/nodomain/freeyourgadget/gadgetbridge/devices/sony/headphones/SonyHeadphonesCapabilities.java · https://raw.githubusercontent.com/Freeyourgadget/Gadgetbridge/master/app/src/main/java/nodomain/freeyourgadget/gadgetbridge/devices/sony/headphones/SonyHeadphonesSettingsCustomizer.java | reported-but-unverified |
| `dsee` | wired connection — Sony support article 00252477 | https://www.sony.com/electronics/support/articles/00252477 | unverified — source unreachable (HTTP 403) |

A **device-side disable** (DSEE muted during LDAC, BGM effects muted during a
call) leaves the setting on and is not a grey-out rule. A **blocked setting** is
one the user would otherwise change: only the equalizer-on-non-SBC row qualifies
from state the helper publishes today (`codec`), and only on WH-1000XM2/XM3,
whose `FEATURE_SETS` entries carry the `eq-sbc-only` presentation marker. The
call / LE-Audio / wired rows need a signal the helper does not publish yet.

### Per-model support matrix

For each of the 16 models, which of the plugin's own setting keys it honours.
The "Helper set" column is the model's `FEATURE_SETS` entry (`XM2`, `XM3`,
`XM4`, `XM6`, `WH-1000XM5`, `WF-1000XM5`, `LinkBuds S`, `LinkBuds`,
`WH-CH720N`) or the generation ceiling it falls back to (`ceil1` =
`V1_FEATURES`, `ceil2` = `V2_FEATURES`). Generation follows the helper's own
comments (`V1Protocol` names WH-1000XM2/XM3/XM4 and WF-SP800N; `V2Protocol`
names WH-1000XM5/XM6, WF-1000XM5, LinkBuds and CH720N) and, for models it never
names, Gadgetbridge's discovery preference: base `preferServiceV2()` is false
and only the two LinkBuds coordinators override it. The init handshake still
decides on the wire. A cell counts as supported only when sourced; `?` means
unknown, needing on-device capture.

Cell codes: `Y` supported (see the row's source); `.` not offered — the GB
coordinator carries no flag for it, or the Sony guide lists nothing for it;
`?` unknown; `*` the device has it but the helper set omits it; `†` the
hardware has it but the key is refused cross-generation (`V1_ONLY_SETTINGS` /
`V2_ONLY_SETTINGS`). Statuses keep the research ticket's meaning: confirmed is
read on the cited page, reported-but-unverified is one source only. Headers:
`amb` = `ambient-level`, `fov` = `focus-on-voice`, `eqb` = `eq-bands`, `sts` =
`stc-sensitivity`, `stt` = `stc-timeout`, `stf` = `stc-focus-on-voice`, `pau` =
`pause-when-taken-off`, `tou` = `touch-sensor`, `voi` = `voice-notifications`,
`apo` = `auto-power-off`, `apt` = `auto-power-off-timer`, `cq` =
`connection-quality`, `lm` = `listening-mode`, `bgm` = `bgm-room-size`, `src` =
`playback-source`. `Src` cites the row's sources; `St` is the row's status
(`C` confirmed, `R` reported-but-unverified).

**v1 models** (10) — `touch-sensor` is a v1-only key and `voice-notifications`
reads `Y` here wherever the hardware has it (on v2 it is scoped to
WH-1000XM5/XM6); the four v2-only columns always read `.` here.

| Model | Set | nc | amb | fov | eq | eqb | dsee | stc | sts | stt | stf | pau | tou | voi | apo | apt | cq | lm | bgm | src | Src | St |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| WH-1000XM4 | XM4 | Y | Y | Y | Y | Y | Y | Y | Y | Y | Y | Y | Y* | Y | Y | . | . | . | . | . | [2][16] | C |
| WH-1000XM3 | XM3 | Y | Y | Y | Y | Y | Y | . | . | . | . | . | Y | Y | Y | Y | . | . | . | . | [3][16] | C |
| WH-1000XM2 | XM2 | Y | Y | Y | Y | Y | Y | . | . | . | . | . | . | . | . | . | . | . | . | . | [4][16] | C |
| WF-1000XM3 | ceil1 | Y | Y | Y | Y | Y | Y | . | . | . | . | Y | . | Y | Y | . | . | . | . | . | [5][16] | C |
| WF-1000XM4 | ceil1 | Y | Y | Y | Y | . | Y | . | . | . | . | Y | . | . | Y | . | . | . | . | . | [6][16] | C |
| WF-C500 | ceil1 | . | . | . | Y | Y | Y | . | . | . | . | . | . | Y | . | . | . | . | . | . | [7][16] | C |
| WF-C700N | ceil1 | Y | Y | Y | Y | Y | Y | . | . | . | . | . | . | . | ? | ? | . | . | . | . | [8][16] | C |
| WF-SP800N | ceil1 | Y | Y | Y | Y | Y | . | . | . | . | . | Y | . | Y | Y | . | . | . | . | . | [9][16] | C |
| WI-C100 | ceil1 | . | . | . | Y | Y | Y | . | . | . | . | . | . | Y | . | . | . | . | . | . | [10][16] | C |
| WI-SP600N | ceil1 | Y | Y | Y | Y | Y | . | . | . | . | . | . | . | Y | Y | Y | . | . | . | . | [11][16] | C |

**v2 models** (6) — `stc-focus-on-voice` and `touch-sensor` are refused by the
v2 builder, so they read `.` / `.†` even where the hardware has them;
`voice-notifications` is implemented on v2 for WH-1000XM5/XM6 (confirmed on XM6
hardware, fw 3.1.5) and reported-not-confirmed elsewhere, so it reads `.†`
outside those two rows. The four v2-only columns are Sony-guide sourced (no GB
flag exists for them). Two rows disagree with their GB coordinator and follow
the guide instead: WH-1000XM5 `dsee` (GB TODOs `AudioUpsampling` out; the guide
lists DSEE Extreme) and WF-1000XM5 `eqb` / `stc` / `sts` / `stt` (GB carries
neither an equalizer-custom nor any STC flag; the guide lists Custom EQ and
Speak-to-Chat setup) — GB-side gaps, both needing capture.

| Model | Set | nc | amb | fov | eq | eqb | dsee | stc | sts | stt | stf | pau | tou | voi | apo | apt | cq | lm | bgm | src | Src | St |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| WH-1000XM6 | XM6 | Y | Y | Y | Y | Y | Y | Y | Y | Y | . | Y | .† | Y | Y | . | Y | Y | Y | Y | [17] | C |
| WH-1000XM5 | WH-1000XM5 | Y | Y | Y | Y | Y | Y | Y | Y | Y | .† | Y | .† | Y | Y | . | Y | . | . | Y | [12][18][19] | C |
| WF-1000XM5 | WF-1000XM5 | Y | Y | Y | Y | Y | Y | Y | Y | Y | . | Y | . | .† | Y | . | Y | . | . | Y | [13][20] | C |
| LinkBuds | LinkBuds | . | . | . | Y | Y | Y | Y | Y | Y | . | Y | . | .† | Y | . | . | . | . | . | [14][16] | C |
| LinkBuds S | LinkBuds S | Y | Y | Y | Y | Y | Y | Y | Y | Y | . | Y | . | .† | Y | . | . | . | . | . | [15] | R |
| WH-CH720N | WH-CH720N | Y | Y | Y | Y | Y | Y | . | . | . | . | . | . | .† | Y | . | Y | . | . | Y | [21][22] | C |

**XM5-vs-XM6 delta** — the XM6 adds the `listening-mode` capability (Background
Music effects + Cinematic/Upmix, so `lm` and `bgm` read `Y` only on its row) on
top of everything the XM5 has; both carry voice guidance (`voi` reads `Y` on
both rows). The XM5 keeps a touch panel (plus an STC voice-focus flag in its GB
coordinator) that the v2 builder refuses outright, so `stf` / `tou` read `.†`
on its row.

**Feature-set gaps** — v1 names three sets (XM4, XM3, XM2) and v2 names six
(XM6, equal to the whole `V2_FEATURES` ceiling, plus WH-1000XM5, WF-1000XM5,
LinkBuds S, LinkBuds and WH-CH720N); every other name falls through to its
generation's ceiling. Each `.` in a `ceil1` row is a control the helper offers
that the model was never seen to honour (notably the whole STC block on every
`ceil1` row — no `ceil1` model carries an STC flag). Under-offers go the other
way: XM4 `tou Y*` (the helper omits `touch-sensor` because the hardware answers
"still on" to every disable), XM4 `apt .` (same reason for timer values), and
every `.†` (hardware present, key refused cross-generation). Proposed v1
refinements, all strict subsets of `V1_FEATURES` and each needing on-device
capture before merge, derived from the GB capabilities above: WF-1000XM3 →
`battery`, `equalizer`, `dsee`, `pause-when-taken-off`, `auto-power-off`,
`voice-notifications`; WF-1000XM4 → the same minus `voice-notifications`;
WF-SP800N → `battery`, `equalizer`, `pause-when-taken-off`, `auto-power-off`,
`voice-notifications` (no upsampling in its GB caps, so no `dsee`); WF-C500 →
`battery`, `equalizer`, `dsee`, `voice-notifications`; WF-C700N → `battery`,
`equalizer`, `dsee` (its GB coordinator TODOs the auto-power-off payload as
incorrect); WI-C100 → `battery`, `equalizer`, `dsee`, `voice-notifications`;
WI-SP600N → `battery`, `equalizer`, `voice-notifications`,
`auto-power-off-timer`. `multipoint` stays XM6-only until a non-XM6 on-device
capture confirms the PERIPHERAL frames — that is the open follow-up.

### Gadgetbridge flag map

GB `SonyHeadphonesCapabilities` [1] to plugin key: `AmbientSoundControl` /
`AmbientSoundControl2` plus `WindNoiseReduction` feed `nc` / `ambient-level` /
`focus-on-voice`; `EqualizerSimple` feeds `eq`, `EqualizerWithCustomBands` feeds
`eq` + `eq-bands`; `AudioUpsampling` feeds `dsee`; `SpeakToChatEnabled` +
`SpeakToChatConfig` feed `speak-to-chat` + `stc-sensitivity` + `stc-timeout`,
`SpeakToChatFocusOnVoice` feeds `stc-focus-on-voice` (v1-only);
`PauseWhenTakenOff` feeds `pause-when-taken-off`; `TouchSensorSingle` feeds
`touch-sensor`; `VoiceNotifications` feeds `voice-notifications`;
`AutomaticPowerOffWhenTakenOff` feeds `auto-power-off` (`off` /
`when-taken-off` only), `AutomaticPowerOffByTime` feeds `auto-power-off` +
`auto-power-off-timer`.

GB flags with no plugin key — exactly the gap the reader needs:
`AncOptimizer`, `SoundPosition`, `SurroundMode`, `AudioSettingsOnlyOnSbcCodec`,
`Volume`, `ButtonModesLeftRight`, `AmbientSoundControlButtonMode`, `QuickAccess`,
`WideAreaTap`, `AdaptiveVolumeControl`, `PowerOffFromPhone`, and the `Battery*`
flags (battery is a feature, never a setting key). Plugin keys with no GB flag:
`connection-quality`, `listening-mode`, `bgm-room-size` and `playback-source`
are v2 app-level, Sony guides only — and `nc` / `ambient-level` /
`focus-on-voice` have no `SETTING_FEATURES` gate at all, so the helper offers
them to every model, including the WF-C500 and WI-C100, which carry no ASC flag.

### Code support (builder coverage)

The helper implements every plugin key, but a model gets only its generation's
builder, and each builder refuses the other generation's keys outright
(`V1_ONLY_SETTINGS` / `V2_ONLY_SETTINGS` plus the v2 voice-focus refusal). This
table is what the code can send, read from `setting_requests` /
`setting_requests_v2` / `SETTING_FEATURES`, independent of what a model honours;
the `†` cells above are exactly these refusals.

| Plugin keys | v1 builder | v2 builder | Feature gate |
|---|---|---|---|
| `nc`, `ambient-level`, `focus-on-voice` | yes | yes | none — offered to every model |
| `eq`, `eq-bands` | yes | yes | `equalizer` |
| `dsee` | yes | yes | `dsee` |
| `speak-to-chat`, `stc-sensitivity`, `stc-timeout` | yes | yes | `speak-to-chat` |
| `stc-focus-on-voice` | yes | refused — v1-only in practice | `speak-to-chat` |
| `pause-when-taken-off` | yes | yes | `pause-when-taken-off` |
| `auto-power-off` | yes (timer values need `auto-power-off-timer`) | yes | `auto-power-off` |
| `touch-sensor` | yes | refused (`V1_ONLY_SETTINGS`) | `touch-sensor` |
| `voice-notifications` | yes | yes — WH-1000XM5/XM6 only for now; other v2 models reported-not-confirmed | `voice-notifications` |
| `connection-quality` | refused (`V2_ONLY_SETTINGS`) | yes | `connection-quality` |
| `listening-mode`, `bgm-room-size` | refused (`V2_ONLY_SETTINGS`) | yes | `listening-mode` |
| `playback-source` | refused (`V2_ONLY_SETTINGS`) | yes | `multipoint` |

So two keys are unreachable on v2 (`touch-sensor`, `stc-focus-on-voice`) and
four on v1 (`connection-quality`, `listening-mode`, `bgm-room-size`,
`playback-source`); the rest are implemented on both, with `voice-notifications`
on v2 scoped to the WH-1000XM5/XM6. Two over-offers are code, not hardware: the
three ungated keys go to every model (even the WF-C500 / WI-C100, which carry no
ASC flag), and a sibling key's feature hides the gap — `eq-bands` rides
`equalizer`, `stc-*` ride `speak-to-chat`, `bgm-room-size` rides
`listening-mode`, so a model with the simple form is still offered the custom
form.

### Sources

Each URL fetched for this matrix.

- [1] GB capability vocabulary — https://raw.githubusercontent.com/Freeyourgadget/Gadgetbridge/master/app/src/main/java/nodomain/freeyourgadget/gadgetbridge/devices/sony/headphones/SonyHeadphonesCapabilities.java
- [2]–[15] per-model GB coordinators (same directory): `SonyWH1000XM4Coordinator.java`, `SonyWH1000XM3Coordinator.java`, `SonyWH1000XM2Coordinator.java`, `SonyWF1000XM3Coordinator.java`, `SonyWF1000XM4Coordinator.java`, `SonyWFC500Coordinator.java`, `SonyWFC700NCoordinator.java`, `SonyWFSP800NCoordinator.java`, `SonyWIC100Coordinator.java`, `SonyWISP600NCoordinator.java`, `SonyWH1000XM5Coordinator.java`, `SonyWF1000XM5Coordinator.java`, `SonyLinkBudsCoordinator.java`, `SonyLinkBudsSCoordinator.java` (full raw URLs are the directory plus each filename)
- [16] Sony Headphones Connect compatible-device list — https://helpguide.sony.net/mdr/hpc/v1/en/contents/TP0001548861.html
- [17] WH-1000XM6 app surface — https://helpguide.sony.net/mdr/2984/v1/en/contents/TP1001856857.html
- [18] WH-1000XM5 app surface — https://helpguide.sony.net/mdr/wh1000xm5/v1/en/contents/TP1000534533.html
- [19] WH-1000XM5 sound-quality mode — https://helpguide.sony.net/mdr/wh1000xm5/v1/en/contents/TP1000534501.html
- [20] WF-1000XM5 app surface — https://helpguide.sony.net/mdr/2963/v1/en/contents/TP1000781980.html
- [21] WH-CH720N app surface — https://helpguide.sony.net/mdr/2966/v1/en/contents/TP1000776844.html
- [22] on-device NC/ASM + wear captures (CH720N, XM5, XM4) — https://raw.githubusercontent.com/ncr/omarchy-headphones/main/PROTOCOL.md
- [23] the fetched coordinator listing (14 files, none for XM6/CH720N) — https://api.github.com/repos/Freeyourgadget/Gadgetbridge/contents/app/src/main/java/nodomain/freeyourgadget/gadgetbridge/devices/sony/headphones/coordinators

`itsgg/headset docs/protocol.md` was 404 (not cited); Sony's sony.com compat
article was 403 from here, like support article 00252477 in the exclusion
table, so app-level confirmation is the fetchable helpguide compat list [16].
Row counts: 11 exclusion rows (8 confirmed, 3 unverified); 16 model rows in 2
tables (10 v1 + 6 v2; 15 confirmed, 1 reported-but-unverified — LinkBuds S, GB
only). Cells: 16 models × 19 settings = 304; 302 sourced (`Y` / `.` / `*` /
`†`), 2 `?` (WF-C700N `apo` / `apt`: GB TODOs the payload as incorrect while
the app offers timer values).

## Wire protocol

Framing and ACK are shared by both generations; the payload opcodes and their
layouts are not. The reference implementation is in the helper sections
"Framing", "Payload opcodes (protocol v1)" and "Payload opcodes (protocol v2)".

### Framing

```
0x3e  ESCAPE(<type> <seq> <len:4 BE> <payload> <checksum>)  0x3c
```

| Byte | Name | Meaning |
|---|---|---|
| `0x3e` | `HEADER` | Start-of-frame marker |
| `0x3c` | `TRAILER` | End-of-frame marker |
| `0x3d` | `ESCAPE` | Byte-stuffing marker |
| `0x01` | `MSG_ACK` | Acknowledgement message table |
| `0x0c` | `MSG_COMMAND_1` | The usual command table (the default for `req`) |
| `0x0e` | `MSG_COMMAND_2` | The second command table (voice guidance; multipoint) |

`checksum` is a plain byte sum from `<type>` through the last payload byte,
mod 256, and is appended before escaping, so a checksum that lands on a marker
byte is escaped too. Escaping replaces a marker byte `b` with `ESCAPE,
b & 0xEF`; unescaping ORs `0x10` back. The maximum accepted frame is
`MAX_MESSAGE_SIZE` = 2048 bytes.

Every message — in both directions — is answered with an ACK carrying the
flipped sequence number (`Link.write`, `Link.wait_for_ack`, `Link._ack`). An
ACK means the bytes arrived, never that a setting changed: only a read-back
that moved proves that.

### The v1 opcode families

| Feature | GET | RET | SET | NOTIFY |
|---|---|---|---|---|
| INIT | `0x00` | `0x01` | — | — |
| Firmware | `0x04` | `0x05` | — | — |
| Battery | `0x10` | `0x11` | — | `0x13` |
| Codec | `0x18` | `0x19` | — | `0x1B` |
| Power off | — | — | `0x22` | — |
| Equalizer | `0x56` | `0x57` | `0x58` | `0x59` |
| Ambient sound | `0x66` | `0x67` | `0x68` | `0x69` |
| NC optimizer | `0x86` | `0x87` | — | `0x89` |
| Touch panel | `0xD6` | `0xD7` | `0xD8` | `0xD9` |
| Upsampling (DSEE) | `0xE6` | `0xE7` | `0xE8` | `0xE9` |
| APO multiplex | `0xF6` | `0xF7` | `0xF8` | `0xF9` |
| Speak-to-Chat config | `0xFA` | `0xFB` | `0xFC` | `0xFD` |
| Voice guidance (table 2) | `0x46` | `0x47` | `0x48` | `0x49` |

v1 subtype rules: the `0xF6` APO family is multiplexed by `payload[1]` —
`0x03` pause-when-taken-off, `0x04` auto-power-off, `0x05` speak-to-chat.
DSEE rides the upsampling family under subtype `0x02`. v1 voice guidance is
`MSG_COMMAND_2`, GET `46 01 01`, SET `48 01 01 VV` non-inverted (`0x01` = on),
value at reply index 3.

### The v2 opcode families

| Feature | GET | RET | SET | NOTIFY | Table |
|---|---|---|---|---|---|
| INIT | `0x00` | `0x01` | — | — | 1 |
| Audio codec | `0x12` | `0x13` | — | `0x15` | 1 |
| Audio family (DSEE / connection quality / cinema / BGM) | `0xE6` | `0xE7` | `0xE8` | `0xE9` | 1 |
| Battery | `0x22` | `0x23` | — | `0x25` | 1 |
| Equalizer | `0x56` | `0x57` | `0x58` | `0x59` | 1 |
| Ambient sound | `0x66` | `0x67` | `0x68` | `0x69` | 1 |
| Power (auto power off) | `0x26` | `0x27` | `0x28` | `0x29` | 1 |
| System (pause / speak-to-chat) | `0xF6` | `0xF7` | `0xF8` | `0xF9` | 1 |
| Speak-to-Chat config | `0xFA` | `0xFB` | `0xFC` | `0xFD` | 1 |
| Peripheral (multipoint) | `0x36` | `0x37` | `0x3C` | `0x39` | **2** |
| Voice guidance | `0x46` | `0x47` | `0x48` | `0x49` | **2** |

v2 subtypes (`payload[1]` names the feature inside a reused family):

- **Audio `0xE6`–`0xE9`:** `0x01` DSEE Extreme (not inverted, `0x01` = on),
  `0x02` connection quality (inverted), `0x04` Upmix Cinema (inverted), `0x09`
  Background Music (inverted, carries a room byte), `0x03` the alternate BGM
  subtype some firmware answers. BGM and Cinema are two independent flags; the
  app's listening mode is derived from them with BGM first.
- **System `0xF6`–`0xF9`:** `0x01` pause-when-taken-off, `0x0C` speak-to-chat.
  Both are inverted (`0x00` = on).
- **Speak-to-Chat config `0xFA`–`0xFD`:** sub `0x0C`, carrying sensitivity and
  resume timeout.
- **Peripheral `0x36`/`0x37`/`0x39`/`0x3C`:** `0x02` device list, `0x01` source
  switch.
- **Ambient-sound dialect:** the device is asked which subtype it speaks;
  writes echo the answered subtype. `V2_ASC_PROBE_ORDER` is `0x19` (noise
  adaptation), `0x17` (legacy), `0x15` (wind), `0x22` (ambient-only); a device
  that supports `0x19` still answers `0x17` out of a dormant slot, so the first
  that answers wins.
- **Equalizer subtype:** asked, defaulting to `0x04` (XM6); `0x00` is the
  XM5-style dialect. The parser accepts `0x00`, `0x02` and `0x04`, and picks
  the band offset (10 for six-band replies, 6 for ten-band).

### v2 values that are inverted on the wire

Read these as "the byte names the compromise or the disabled state, not the
feature", and flip on the way in and out:

| Setting | Wire meaning |
|---|---|
| `connection-quality` | `0x00` = sound quality (LDAC); `0x01` = stable (SBC) |
| `speak-to-chat` | `0x00` = on |
| `pause-when-taken-off` | `0x00` = on |
| `listening-mode` Background Music | `0x00` = on |
| `listening-mode` Upmix Cinema | `0x00` = on |
| `voice-notifications` | `0x00` = on |

DSEE Extreme on v2 is the exception: not inverted, `0x01` = on. On v1, DSEE
(`0xE8` subtype `0x02`) and voice guidance (`0x48 01 01 VV`) are non-inverted
too, which is why the generations never share a boolean builder.

## Helper symbol map: `bin/sony-headphones`

A symbol index of the single stdlib-only helper (~4,700 lines). The file is
deliberately one piece, so no module split tells you where anything lives; this
map does. Grep the symbol and read only that region, plus maybe one caller. The
names are the stable part; line numbers are not. Full-line security rationale
lives in [adr/](adr/) and [wiki/architecture.md](wiki/architecture.md).

### Module constants and limits

| Symbol | Role |
| --- | --- |
| `HEADER` / `TRAILER` / `ESCAPE` / `ESCAPE_MASK` | Frame marker bytes |
| `MSG_ACK` / `MSG_COMMAND_1` / `MSG_COMMAND_2` | Message-table selectors |
| `MAX_MESSAGE_SIZE` | Largest acceptable frame |
| `SERVICE_UUID` / `SERVICE_UUID_V2` | v1 / v2 SPP service UUIDs |
| `SDP_PSM` … `SDP_MAX_DEPTH` | SDP constants and parse bounds |
| `CACHE_DIR` | Channel-cache directory under `XDG_CACHE_HOME` |
| `MAC_RE` | Strict Bluetooth address pattern |
| `DISCOVERY_TTL` | How long a device scan is trusted (2 s) |
| `INFO_MAX_LINES` | Cap on `bluetoothctl info` lines scanned by `service_protocol` |
| `LOG_FILE_NAME` / `LOG_MAX_BYTES` / `LOG_BACKUPS` / `LOG_DEFAULT` / `LOG_OFF` / `LOG_LEVELS` | Log-file bounds and level map |
| `SETTLE_TIMEOUT` | Read-back window after a write (2 s) |
| `SOCKET_NAME` / `LOCK_NAME` | Control socket and lock file names |
| `SESSION_POLICIES` / `SESSION_POLICY_DEFAULT` / `SESSION_IDLE_DEFAULT` / `SESSION_IDLE_MIN` / `SESSION_IDLE_MAX` | Control-session policy values and idle-delay bounds |
| `MAX_LINE_BYTES` | Cap on one JSON line over the socket |

### Binary framing

| Symbol | Role |
| --- | --- |
| `ProtocolError` | A malformed frame; recoverable, drop and read on |
| `escape` / `unescape` | Byte-stuff and unstuff marker bytes |
| `checksum` | Plain byte sum from type through the last payload byte |
| `encode_message` | `(type, seq, payload)` to a framed message |
| `decode_message` | A framed message to `(type, seq, payload)` |

### Wire opcodes and option tables

| Symbol | Role |
| --- | --- |
| v1 opcode block (`INIT_REQUEST` … `VOICE_NOTIFICATIONS_NOTIFY`) | v1 ("MDR") opcodes |
| v2 opcode block (`V2_INIT_REQUEST` … `V2_PERI_SUB_SOURCE_SWITCH`) | v2 opcodes and subtype selectors |
| `AUDIO_CODECS` | Codec byte to name |
| `BATTERY_SINGLE` / `BATTERY_CASE` | Battery subtypes |
| `NC_MODES` / `ASC_MODE_CODE` / `ASC_MODE_FROM_CODE` | Noise-control modes, with and without wind |
| `EQ_PRESETS` / `EQ_PRESET_FROM_CODE` | v1 equalizer table |
| `V2_EQ_PRESETS` / `V2_EQ_PRESET_FROM_CODE` / `V2_EQ_BAND_*` | v2 equalizer table and band limits |
| `AUTO_POWER_OFF` / `V2_AUTO_POWER_OFF` (+ `_FROM_CODE`) | Auto power off values per generation |
| `STC_SENSITIVITY` / `STC_TIMEOUT` (+ `_FROM_CODE`) | Speak-to-chat options |
| `V2_BGM_ROOM` / `V2_BGM_ROOM_FROM_CODE` | Background-music room sizes |
| `BGM_WRITE_KEYS` / `BGM_RESET_REASON` | BGM keys whose lost link is explained as a digital assistant |
| `V2_CONNECTION_QUALITY_*` | Connection-quality values |
| `MAX_AMBIENT_LEVEL` | Ambient slider ceiling |
| `V2_ASC_SUBTYPES` / `V2_ASC_PROBE_ORDER` | v2 ambient dialect probe order |

### Request builders

| Symbol | Role |
| --- | --- |
| `req` | Wrap a payload as a `(msg_type, payload)` request |
| `refresh_requests` | Full post-connect v1 query list |
| `finite_int` | Parse a bounded integer setting |
| `asc_request` | v1 noise-control / ambient request |
| `eq_preset_request` / `eq_bands_request` | v1 equalizer writes |
| `bool_request` | Generic v1 on/off write |
| `auto_power_off_request` | v1 auto power off |
| `stc_config_request` | v1 speak-to-chat config |
| `v2_audio_codec_get` / `v2_dsee_get` / `v2_dsee_request` / `v2_connection_quality_get` / `v2_connection_quality_request` / `v2_cinema_get` / `v2_cinema_request` / `v2_bgm_get` / `v2_bgm_request` | v2 audio-family builders |
| `v2_asc_request` / `v2_asc_get` | v2 noise-control / ambient |
| `v2_eq_get` / `v2_eq_preset_request` / `v2_eq_bands_request` | v2 equalizer |
| `v2_system_get` / `v2_stc_enabled_request` / `v2_bool_request` / `v2_stc_config_request` / `v2_auto_power_off_request` / `v2_stc_get` / `v2_apo_get` | v2 system / speak-to-chat |
| `v2_device_list_get` / `v2_source_switch_request` | v2 multipoint |
| `v2_refresh_requests` | Full post-connect v2 query list (incl. the 3-byte voice-guidance GET) |

### Reply parsers and state

| Symbol | Role |
| --- | --- |
| `initial_state` | The complete empty state dict (the JSON contract), with empty `pending: []` and `refused: {}` |
| `apply_payload` | Fold one v1 reply/notification into state |
| `apply_payload_v2` | Fold one v2 reply/notification into state (incl. inverted `voice-notifications` at index 2) |
| `_apply_firmware` / `_apply_codec` / `_apply_battery` | Small shared field parsers |
| `parse_v2_device_list` | Decode the v2 multipoint peer list |
| `derive_listening_mode` | BGM/cinema flags to one listening mode |
| `_bool` | Byte to `True`/`False`/`None` |

### SDP discovery

| Symbol | Role |
| --- | --- |
| `SdpError` | Malformed, oversized or unfinished SDP answer |
| `_de_sequence` / `_de_uuid128` / `_de_uint32` | SDP data-element encoders |
| `_parse_element` | Recursive SDP data-element decoder, depth-capped |
| `_find_rfcomm_channel` | First valid RFCOMM channel in a record |
| `sdp_query` | Bounded service-search loop on an SDP socket |
| `parse_sdp_record` | Top-level record decode |
| `sdp_channel` | Address + UUID to RFCOMM channel, or `None` |

### Channel cache (on disk)

| Symbol | Role |
| --- | --- |
| `_channel_cache_path` | Per-MAC cache file path |
| `cached_channel` | Read the remembered channel (`O_NOFOLLOW`) |
| `private_cache_dir` | Create/tighten the 0700 cache directory, or `None` |
| `remember_channel` | Write the last working channel at 0600 |
| `forget_channel` | Drop a stale channel after a failed connect |

### Bluetooth discovery (`bluetoothctl`)

| Symbol | Role |
| --- | --- |
| `_clean_name` | Strip controls, squeeze whitespace, bound length, fall back to address |
| `find_bluetoothctl` / `BLUETOOTHCTL` / `BLUETOOTHCTL_CANDIDATES` / `BLUETOOTHCTL_ENV` | Resolve `bluetoothctl` once, run it in a minimal environment |
| `bluetoothctl` | Run a `bluetoothctl` subcommand, return stdout |
| `service_protocol` | Read v1/v2 from the `info` UUID list |
| `_normalize_mac` | Case/separator-insensitive cache key |
| `_DiscoveryCache` / `_DISCOVERY` | In-memory device-snapshot and protocol-hint cache |
| `connected_devices` | Connected Sony devices, briefly cached |
| `find_device` | Pick the preferred or first connected device |

### Transport classes

| Symbol | Role |
| --- | --- |
| `Transport` | Abstract byte-mover: `open`/`send`/`recv`/`close`/`connected` |
| `RfcommTransport` | Real radio: SDP candidates, RFCOMM sockets, cached fallback |
| `RfcommTransport._candidates` | Yield control channels in priority order |
| `RfcommTransport._open_channel` | Build one blocking RFCOMM socket; `EBUSY` becomes `NotConnected` (the control channel is held elsewhere) |
| `DemoTransport` | Loopback transport speaking the real wire format to a stand-in |

### Protocol adapters

| Symbol | Role |
| --- | --- |
| `Protocol` | The adapter interface Link talks to |
| `V1Protocol` | v1 adapter: delegates to the v1 builders/parsers |
| `V2Protocol` | v2 adapter: delegates to the v2 builders/parsers |

### Link (the conversation)

| Symbol | Role |
| --- | --- |
| `NotConnected` | The peer is gone; the failure signal throughout |
| `Link` | One lock-step conversation over a transport |
| `Link.interrupted_key` | A BGM key whose write raised, for `Daemon.lose_link` to explain |
| `Link.connect` | Open the transport, establish, keep the channel |
| `Link._establish` | Handshake, features and dialect negotiation for one candidate |
| `Link._handshake` | INIT exchange; length decides v1 (4) vs v2 (8) |
| `Link._set_protocol` | Adopt the confirmed adapter |
| `Link.close` | Close the transport and mark disconnected |
| `Link._read_frame` / `Link._trim_buffer` | Framing reads and buffer bounding |
| `Link.write` / `Link.wait_for_ack` / `Link._ack` | Lock-step writes and ACKs |
| `Link.dispatch` / `Link.pump` | Fold frames into state; pump for a window |
| `Link.notify_change` | Fan a state change made outside dispatch out to the owner via `on_change` |
| `Link.request` / `Link.refresh` | Send a request list and settle |
| `Link._select_subtype` / `select_asc_subtype` / `select_eq_subtype` / `select_bgm_subtype` | v2 dialect probes |
| `Link.poll_requests` / `Link.power_off` | Periodic liveness ask; power off via the adapter |
| `configure_link` | Stamp device identity, then `Link.connect` |

### Settings builders

| Symbol | Role |
| --- | --- |
| `BOOL_SETTINGS` / `SETTING_KEYS` | Toggle setting map; the CLI's accepted keys |
| `parse_bool` | Boolean parser incl. `toggle` |
| `next_nc_mode` | NC / ambient / off cycle |
| `SETTING_FEATURES` | Setting key to required feature |
| `V1_ONLY_SETTINGS` / `V2_ONLY_SETTINGS` | Keys each builder refuses across generations (v1-only is only `touch-sensor`; `voice-notifications` is on both, v2 scoped to WH-1000XM5/XM6) |
| `SettingContext` | Named tuple of the shared preamble fields |
| `_setting_context` | Shared gate: exclusivity, features, ambient and v2 discovery fields |
| `setting_requests` | v1: key + value to requests |
| `setting_requests_v2` | v2: key + value to requests (incl. the inverted 3-byte `voice-notifications` shape) |
| `apply_setting` | Mark the key pending (clearing a prior refusal), write then pump until the read-back moves or `SETTLE_TIMEOUT`, recording a refusal on a no-move settle; a BGM key that raises is remembered in `Link.interrupted_key` |
| `_mark_pending` / `_settle_pending` | Publish/clear the `pending` key and record a `refused` entry |

### Feature tables and ceilings

| Symbol | Role |
| --- | --- |
| `V1_FEATURES` / `V2_FEATURES` | Per-generation command-set ceilings |
| `FEATURE_CEILINGS` | Protocol to ceiling |
| `FEATURE_SETS` | Per-model refinements, checked against the ceiling at import (v2 names six models; XM5/XM6 carry `voice-notifications`) |
| `UI_FEATURES` | Presentation-only markers (e.g. `eq-sbc-only`), exempt from the ceiling check |
| `ALL_FEATURES` | Union, only for a device with no known protocol |
| `features_for` | The controls a named device should be offered |

### Demo mode

| Symbol | Role |
| --- | --- |
| `DemoDevice` / `DemoLink` | v1 stand-in device and link |
| `DemoDeviceV2` / `DemoLinkV2` | v2 stand-in device and link (carries inverted `voice_notifications`) |
| `DemoDevice.stubborn` / `DemoDeviceV2.stubborn` | Opt-in flag (off by default) that acknowledges a write without changing the value |
| `demo_mode` | Read `SONY_HEADPHONES_DEMO`; `v1`, `v2` or off |
| `demo_link` | Build the stand-in Link for the selected demo |

### Logging and the trace file

| Symbol | Role |
| --- | --- |
| `_open_trace_file` / `_TRACE_FILE` / `trace` | Hardened frame trace, off unless `SONY_HEADPHONES_TRACE` |
| `LOG` | The one module-level named logger |
| `_LogFormatter` | Log line shape |
| `_PrivateLogFile` | Rotating 0600 handler |
| `_open_log_handler` | Build the handler, or `None` |
| `_log_level_name` / `log_level` | Resolve the level name |
| `configure_logging` | Configure once, then only move the handler's level |

### Runtime directory and socket

| Symbol | Role |
| --- | --- |
| `is_private_dir` | Real 0700 directory owned by this user |
| `runtime_dir` | Validated `XDG_RUNTIME_DIR`, or a private fallback |
| `socket_path` | The control socket path |

### The daemon

| Symbol | Role |
| --- | --- |
| `Daemon` | Holds the link open and lends it over a unix socket |
| `Daemon.POLL_INTERVAL` / `RETRY_MIN` / `RETRY_MAX` / `STABLE_AFTER` / `REQUEST_TIMEOUT` / `MAX_SUBSCRIBERS` / `SUBSCRIBER_TIMEOUT` | Timing, retry-hysteresis (`STABLE_AFTER` 10 s) and subscriber bounds |
| `Daemon.connected_since` | When the current link opened, for the `STABLE_AFTER` flapping check |
| `Daemon.reset_refusal` | BGM-reset explanation carried across a link loss, cleared on the next attempt at that key |
| `_env_session_policy` / `_env_session_idle` | Read the boot policy from the environment, with safe defaults |
| `Daemon.state` | Link state, or a disconnected state, with the daemon fields stamped |
| `Daemon._stamp` | Overlay logging + session policy onto a state dict, plus any `reset_refusal` |
| `Daemon.publish` | Fan one state line out to subscribers |
| `Daemon.drop` | Remove and close one subscriber |
| `Daemon.try_connect` | Find a device and open the link, with backoff; stamps `connected_since` |
| `Daemon.lose_link` | Close, clear and publish a disconnected state; a link lost on a BGM write records `Daemon.reset_refusal`; a link that held past `STABLE_AFTER` resets the retry delay to `RETRY_MIN`, a younger one doubles it to `RETRY_MAX` |
| `Daemon.release` | Hand the control session back deliberately, keeping last values; resets the retry delay to `RETRY_MIN` |
| `Daemon.reclaim` | Take the control session back and refresh it |
| `Daemon.release_if_idle` | Release an on-demand session after its quiet spell |
| `Daemon.poll_link` | Periodic liveness request, kept out of the run loop |
| `Daemon.set_session` | Validate and apply a runtime policy/idle change |
| `Daemon.with_link` | Run an action, reconnecting or reclaiming first if needed |
| `Daemon._known_state` | Best confirmed state with a protocol, without opening a link |
| `Daemon.precheck_setting` | Refuse a cross-generation set/cycle before opening a link |
| `Daemon.handle_command` | Dispatch one request dict to a response dict; prechecks set/cycle and clears `reset_refusal` on a fresh set |
| `Daemon.accept` | Read one client request, subscribe or answer |
| `Daemon._reply` | Send one JSON response and hang up |
| `Daemon.listen` | Claim the daemon role via lock and socket |
| `Daemon.claim_lock` | Take the exclusive lock, or refuse to start |
| `Daemon.bind_socket` | Bind the 0600 socket through the directory fd |
| `Daemon.remove_stale_socket` | Unlink a dead daemon's socket, and only that |
| `Daemon.run` | The select loop: accepts, pumps, polls, backoff |

### Talking to the daemon

| Symbol | Role |
| --- | --- |
| `daemon_socket` | Connect to the control socket, or `None` |
| `ask_daemon` | Send one request, read one response, or `None` |
| `emit` | Write one JSON line to stdout and return it |
| `_command_summary` | One log-sized description of a request |
| `apply_command` | Run one request directly on a link |
| `direct` | Do it ourselves when no daemon is running |
| `run_request` | Daemon if present, else `direct` |

### CLI subcommands

| Symbol | Role |
| --- | --- |
| `describe` | Human-readable state block |
| `cmd_watch` | Subscribe to a daemon, or become one |
| `cmd_status` | `status` (`--json` or human) |
| `cmd_set` / `cmd_cycle` / `cmd_power_off` | Device-write subcommands |
| `cmd_release` / `cmd_reclaim` / `cmd_session` | Control-session subcommands |
| `_json_state` | Merge a response's refusal reason into emitted state |
| `cmd_logging` | Show or change the daemon's log level |
| `_report` | Shared JSON/stderr reporting for write subcommands |
| `cmd_probe` | Connection diagnostics |
| `build_parser` | argparse wiring; the subcommand list |
| `main` | Entry point |
