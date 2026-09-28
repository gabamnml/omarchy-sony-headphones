# Sony Headphones for Omarchy

A bar widget for the [Omarchy](https://omarchy.org) shell that controls Sony
WH-1000XM headphones the way the phone app does — noise cancelling, ambient
sound, the equalizer, DSEE Extreme, Speak-to-Chat and battery — without a
phone, a vendor app, or a desktop GUI.

It talks Sony's own Bluetooth protocol directly over RFCOMM, in about seven
hundred lines of dependency-free Python.

![The panel, on a WH-1000XM4](preview.png)

The widget sits in the bar with the battery reading beside it, and the icon
follows the mode — headphones while noise cancelling, an ear in ambient sound,
struck through when the headphones are off or away.

![The widget in the bar](docs/bar.png)

```
Left click    open the panel
Right click   cycle noise cancelling → ambient sound → off
Scroll        ambient sound level
Middle click  refresh
```

## Requirements

- Omarchy 4.0 or newer
- `python3` and `bluez-utils` (both already on a stock Omarchy install)
- Headphones paired and connected

## Install

```bash
omarchy plugin add https://github.com/gabamnml/omarchy-sony-headphones --enable
```

To place it somewhere specific in the bar:

```bash
omarchy bar move gabamnml.sony-headphones --section right --index 0
```

## Uninstall

```bash
omarchy plugin remove gabamnml.sony-headphones
```

That takes the widget off the bar and deletes the plugin directory. Two things
live outside it and can go too, though nothing depends on them:

```bash
rm -rf ~/.cache/omarchy-sony-headphones     # the remembered RFCOMM channel
```

The helper process stops with the shell; it holds no state of its own and never
changes a setting on the headphones unless you ask it to.

## Supported models

The plugin speaks Sony's **v1** protocol, used by the over-ear 1000X line up to
the XM4:

| Model | Status |
|---|---|
| WH-1000XM4 | Verified on hardware, firmware 3.0.1 — every setting below round-tripped |
| WH-1000XM3, WH-1000XM2 | Same protocol, untested — reports welcome |
| WF-1000XM4/XM5, WH-1000XM5/XM6, LinkBuds, CH720N | **Not supported.** These speak the v2 protocol; the plugin detects them and says so instead of sending bytes they will ignore |
| WH-1000XM4C | **Not supported.** Despite the name a new-generation model (2026): it advertises no v1 control service at all, so there is nothing to connect to. `probe` lists it as connected-but-unsupported instead of the headphones merely looking absent |

Each model honours a different subset, so the panel only draws the rows that
model actually accepts — a control that silently does nothing is worse than one
that is not offered. On a WH-1000XM4 that means no touch-panel switch and no
auto-power-off timers: the headphones answer "still on" and "when taken off" to
every such request, whatever you send them. A model this plugin does not
recognise is offered everything.

## What you can change

| Setting | Notes |
|---|---|
| Noise cancelling / Ambient sound / Wind noise reduction / Off | Wind noise reduction appears only on models that report it |
| Ambient level | 0–20, with Focus on Voice |
| Equalizer | The nine presets plus Manual; custom bands via the CLI |
| DSEE Extreme | Upscaling of compressed audio |
| Speak-to-Chat | On/off, sensitivity, resume timeout, voice focus |
| Pause when taken off, voice guidance | |
| Touch controls | On models that allow it — not the WH-1000XM4 |
| Automatic power off | Never or when taken off; timers on models that honour them |
| Battery, firmware, codec | Read-only |

## Keyboard

Inside the panel: `j`/`k` or the arrow keys move, `h`/`l` adjust the row under
the cursor, `Enter` toggles, `Esc` closes. Shortcuts: `n` cycles the mode, `d`
toggles DSEE, `s` toggles Speak-to-Chat, `r` refreshes.

## A keybinding

The widget exposes IPC, so noise cancelling can hang off a key. In
`~/.config/hypr/bindings.lua`:

```lua
o.bind("SUPER", "N", "Cycle noise cancelling",
  "omarchy-shell gabamnml.sony-headphones cycle")
```

Also available: `open`, `close`, `toggle`, `mode <name>`, `ambient <0-20>`,
`status`.

## Settings

Configured from the shell's widget settings, or directly on the entry in
`~/.config/omarchy/shell.json`:

| Key | Default | Meaning |
|---|---|---|
| `showBattery` | `On` | Show the battery percentage next to the icon |
| `address` | — | Pin a MAC address when several Sony devices are connected |

## The command line

The helper works on its own, with or without the widget:

```bash
bin/sony-headphones status              # everything the headphones report
bin/sony-headphones probe               # diagnose the connection
bin/sony-headphones cycle               # noise cancelling → ambient → off
bin/sony-headphones set nc ambient-sound
bin/sony-headphones set ambient-level 12
bin/sony-headphones set eq bass-boost
bin/sony-headphones set eq-bands "2,0,1,0,-1,3"   # clear bass + 5 bands, -10..10
bin/sony-headphones set dsee toggle
bin/sony-headphones set speak-to-chat on
bin/sony-headphones watch               # stream state changes as JSON lines
```

## How it works

Sony's app protocol runs over an RFCOMM serial channel. Messages are framed as
`0x3e <type> <seq> <length:4> <payload> <checksum> 0x3c`, escaped so the marker
bytes never appear inside a message, and every message — in both directions —
is answered with an acknowledgement carrying the flipped sequence number.

`bin/sony-headphones watch` keeps that link open and serves a unix socket, so
the widget gets push updates when you press the button on the earcup, and short
CLI calls apply instantly instead of paying for a fresh connection each time.

The RFCOMM channel comes from the device's own SDP record, read over L2CAP by
a small SDP client in the helper. BlueZ exposes no API for a remote service
record and bluez-utils no longer ships `sdptool`, and guessing is not an
option: every other channel refuses the connection, a blocking connect to a
closed one takes seconds, and a WH-1000XM4 that has been walked channel by
channel starts refusing the right one too. The answer is cached under
`~/.cache/omarchy-sony-headphones/`, in a directory kept at mode 0700 and
written without following symlinks; a cache that cannot be made private is
declined and discovery simply runs again.

Everything the headphones send is treated as untrusted input with fixed
bounds. The SDP lookup is capped at 8 KiB of record data, 8 round trips and
8 seconds overall, a continuation must make progress and never repeat, and
the record parser limits nesting depth. The RFCOMM reader never holds more
than one frame's worth of bytes that lack an end marker. Requests on the local
control socket have a whole-request deadline and a line-size limit, so a slow
or runaway peer on either side cannot keep the helper busy or growing.

The headphones drop the control session on their own after a while and nothing
announces it, so the daemon treats silence in answer to its periodic battery
poll as a dead link, and a command that arrives on one reconnects and runs
rather than failing in your hands.

Nothing here touches the network. The only things it talks to are the
headphones and `bluetoothctl`, which is run from its absolute path with a
minimal environment rather than resolved through the inherited `PATH`. The
helper is launched the same way: the shell runs it with `/usr/bin/python3 -I`
— never a `python3` looked up on `PATH`, and isolated from `PYTHON*` variables
and user site-packages — and hands it only `HOME`, `XDG_RUNTIME_DIR`,
`XDG_CACHE_HOME` and a fixed `PATH`.

The daemon's control socket is worth guarding: anything that can write it can
drive the headphones and read their state. It lives in `XDG_RUNTIME_DIR`, but
only after that directory has been confirmed to be a real directory, owned by
you, with mode 0700 — the variable is inherited and can point anywhere. Without
a usable one the helper creates its own private directory under the temporary
directory and verifies it the same way rather than trusting a predictable name
someone else may have created first. The socket and the lock are opened
relative to a descriptor for that directory and never through a symlink, and a
socket left behind by a dead daemon is replaced only after it is confirmed to
be a socket you own.

## Development

```bash
python3 tests/test_protocol.py     # 126 tests, no headphones required
omarchy plugin validate .
```

There is a stand-in device for working without hardware. It answers requests
with real reply payloads through the real framing, so the widget can be driven
end to end and every setting is round-tripped in the test suite:

```bash
SONY_HEADPHONES_DEMO=1 bin/sony-headphones status
SONY_HEADPHONES_DEMO=1 bin/sony-headphones watch   # the widget attaches to this
```

Editing the QML reloads the widget on save. Editing `Model.js` does not — the
QML engine caches `.pragma library` imports, so run `omarchy restart shell`
after changing it.

## Credits

The v1 protocol — payload opcodes, byte layouts, and what each field means —
was reverse engineered by [Gadgetbridge](https://codeberg.org/Freeyourgadget/Gadgetbridge),
whose `SonyProtocolImplV1` is the reference this implementation follows. The
message framing was cross-checked against
[SonyHeadphonesClient](https://github.com/Plutoberth/SonyHeadphonesClient) and
[SonyBridge](https://github.com/AmitRajput-Dev/SonyBridge), and the RFCOMM
layer against [ohm-app's protocol notes](https://github.com/ohm-app/sony-headphones-bluetooth-documentation).

This is an independent project. Sony has nothing to do with it.

## License

AGPL-3.0-or-later, matching Gadgetbridge, from which the protocol knowledge
comes. See [LICENSE](LICENSE).
