#!/usr/bin/python3
"""Check whether the headphones actually honour each control.

An acknowledgement only means the message arrived. A Sony headset acknowledges
writes it then discards, and the session that made a write keeps reporting the
old value to its own writer for up to a minute, so neither the ACK nor a
read-back on the same session proves anything. For each control this:

    reads the current value, writes a different legal one, drops the control
    session with `release`, reclaims it, reads the value back, compares, and
    restores the original.

A control whose value moved is HONOURED; one that came back unchanged after the
fresh session read it is IGNORED; one that came back as neither the written nor
the original value is FAILED; one the daemon refused outright is REFUSED.

    python3 tools/verify.py <mac> [control ...]
    python3 tools/verify.py --address <mac> [control ...]

With no controls named, every control the device reports as supported is
verified. The tool reaches the daemon only through the CLI control socket
(`status --json`, `set`, `release`, `reclaim`): it never opens a second RFCOMM
connection. It refuses to run when no daemon is reachable, and exits non-zero
if any control could not be restored, so a script can trust the result.

`eq-bands` (a list of per-band values) and `playback-source` (a connected
peer, not a fixed option) are not scalar controls and are left to a by-hand
check; every other setting the panel offers is covered here.

The real hardware run is manual. Demo mode and a throwaway `XDG_RUNTIME_DIR`
keep the test suite off the radio, and the demo stand-in keeps its values across
the release/reclaim the way real hardware does, so a demo run reports HONOURED
and still exercises the whole flow. The repository README documents this tool.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys

# The helper this drives. It lives beside this file's parent, and every call
# goes through the same `--json` CLI the widget uses.
HELPER = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "bin",
    "sony-headphones",
)

CALL_TIMEOUT = 60.0
MAC_RE = re.compile(r"^([0-9A-Fa-f]{2}:){5}[0-9A-Fa-f]{2}$")

# The option tables, mirrored from the helper and Model.js. This file is
# standard-library-only developer tooling and cannot import either, so the
# values are repeated here; keep them in step when a table changes.
MODE_CANDIDATES = ("noise-cancelling", "ambient-sound", "off", "wind-noise-reduction")
MAX_AMBIENT_LEVEL = 20
V1_EQ_PRESETS = (
    "off", "bright", "excited", "mellow", "relaxed",
    "vocal", "treble-boost", "bass-boost", "speech",
)
V2_EQ_PRESETS = ("off", "heavy", "clear", "hard", "soft")
V1_AUTO_POWER_OFF = ("off", "when-taken-off", "5-min", "30-min", "1-hour", "3-hour")
V2_AUTO_POWER_OFF = ("when-taken-off", "off")
STC_SENSITIVITY = ("auto", "high", "low")
STC_TIMEOUT = ("short", "standard", "long", "off")
CONNECTION_QUALITY = ("sound-quality", "stable")
LISTENING_MODE = ("standard", "background-music", "cinema")
BGM_ROOM_SIZE = ("my-room", "living-room", "cafe")

# Control name to the state key it is read from and compared on.
FIELD = {
    "nc": "nc_mode",
    "ambient-level": "ambient_level",
    "focus-on-voice": "focus_on_voice",
    "eq": "eq_preset",
    "dsee": "dsee",
    "speak-to-chat": "speak_to_chat",
    "stc-sensitivity": "stc_sensitivity",
    "stc-timeout": "stc_timeout",
    "stc-focus-on-voice": "stc_focus_on_voice",
    "pause-when-taken-off": "pause_when_taken_off",
    "touch-sensor": "touch_sensor",
    "voice-notifications": "voice_notifications",
    "auto-power-off": "auto_power_off",
    "connection-quality": "connection_quality",
    "listening-mode": "listening_mode",
    "bgm-room-size": "bgm_room_size",
}

# Display order; also the default order of a full run.
ORDER = tuple(FIELD)

# The feature that must be reported for a control to exist, mirroring the
# helper's SETTING_FEATURES. Controls left out need no feature.
FEATURE_FOR = {
    "eq": "equalizer",
    "dsee": "dsee",
    "connection-quality": "connection-quality",
    "speak-to-chat": "speak-to-chat",
    "stc-sensitivity": "speak-to-chat",
    "stc-timeout": "speak-to-chat",
    "stc-focus-on-voice": "speak-to-chat",
    "pause-when-taken-off": "pause-when-taken-off",
    "touch-sensor": "touch-sensor",
    "voice-notifications": "voice-notifications",
    "auto-power-off": "auto-power-off",
    "listening-mode": "listening-mode",
    "bgm-room-size": "listening-mode",
}

TOGGLES = {
    "dsee",
    "speak-to-chat",
    "pause-when-taken-off",
    "touch-sensor",
    "voice-notifications",
    "focus-on-voice",
    "stc-focus-on-voice",
}

# Keys that only exist on one generation. The feature gate normally excludes
# them, but a stale feature list must not let the tool drive the wrong opcode.
V1_ONLY = {"touch-sensor", "voice-notifications", "stc-focus-on-voice"}
V2_ONLY = {"connection-quality", "listening-mode", "bgm-room-size"}

# The controls that share the ambient block: writing one moves the others, so
# restoring them means restoring the whole tuple in the right order.
AMBIENT = ("nc", "ambient-level", "focus-on-voice")


def call(mac: str, *args: str) -> subprocess.CompletedProcess:
    """Run one helper subcommand through the control socket."""
    argv = [HELPER, "--json", "--address", mac, *args]
    try:
        return subprocess.run(argv, capture_output=True, text=True, timeout=CALL_TIMEOUT)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return subprocess.CompletedProcess(argv, 1, stdout="", stderr=str(exc))


def parse(response: subprocess.CompletedProcess) -> dict:
    """The last JSON object the helper printed, or an empty dict."""
    for line in reversed(response.stdout.splitlines()):
        line = line.strip()
        if not line:
            continue
        try:
            payload = json.loads(line)
        except ValueError:
            continue
        if isinstance(payload, dict):
            return payload
    return {}


def reason(response: subprocess.CompletedProcess) -> str:
    state = parse(response)
    return state.get("error") or response.stderr.strip() or "command failed"


def read_state(mac: str) -> dict:
    return parse(call(mac, "status"))


def fmt(value) -> str:
    if isinstance(value, bool):
        return "on" if value else "off"
    if value is None:
        return "-"
    return str(value)


def is_supported(control: str, state: dict, protocol: str | None) -> bool:
    if not state.get("connected"):
        return False
    if state.get(FIELD[control]) is None:
        # The device has not reported the value; there is nothing to restore.
        return False
    feature = FEATURE_FOR.get(control)
    if feature and feature not in (state.get("features") or []):
        return False
    if control in V1_ONLY and protocol != "v1":
        return False
    if control in V2_ONLY and protocol != "v2":
        return False
    return True


def choose_value(control: str, state: dict, protocol: str | None,
                 features: list) -> str | None:
    """A legal value for the control that differs from the current one."""
    current = state.get(FIELD[control])
    if current is None:
        return None
    if control in TOGGLES:
        return "off" if current else "on"
    if control == "nc":
        for mode in MODE_CANDIDATES:
            if mode != current:
                return mode
    elif control == "ambient-level":
        if protocol == "v2":
            # v2 has no ambient level 0: the encoder lifts it to 1, so a trial
            # writing 0 would read back 1 and be misreported as FAILED.
            return str(MAX_AMBIENT_LEVEL if int(current) != MAX_AMBIENT_LEVEL else 1)
        return str(0 if int(current) != 0 else MAX_AMBIENT_LEVEL)
    elif control == "eq":
        options = V2_EQ_PRESETS if protocol == "v2" else V1_EQ_PRESETS
        for preset in options:
            if preset != current:
                return preset
    elif control == "auto-power-off":
        if protocol == "v2":
            options = V2_AUTO_POWER_OFF
        elif "auto-power-off-timer" in features:
            options = V1_AUTO_POWER_OFF
        else:
            options = V1_AUTO_POWER_OFF[:2]
        for value in options:
            if value != current:
                return value
    elif control == "stc-sensitivity":
        for value in STC_SENSITIVITY:
            if value != current:
                return value
    elif control == "stc-timeout":
        for value in STC_TIMEOUT:
            if value != current:
                return value
    elif control == "connection-quality":
        for value in CONNECTION_QUALITY:
            if value != current:
                return value
    elif control == "listening-mode":
        for value in LISTENING_MODE:
            if value != current:
                return value
    elif control == "bgm-room-size":
        for value in BGM_ROOM_SIZE:
            if value != current:
                return value
    return None


def write(mac: str, control: str, value) -> subprocess.CompletedProcess:
    # `fmt` turns a bool into the CLI's "on"/"off" spelling and leaves strings
    # and numbers alone, so a restore passes the same shape the trial did.
    return call(mac, "set", control, fmt(value))


def fresh_read(mac: str, control: str):
    """Drop the session, take it back, and read the control from the new one."""
    call(mac, "release")
    call(mac, "reclaim")
    return read_state(mac).get(FIELD[control])


def restore_scalar(mac: str, control: str, original) -> tuple[bool, str | None]:
    write(mac, control, original)
    final = fresh_read(mac, control)
    if final == original:
        return True, None
    return False, f"read back {fmt(final)}, wanted {fmt(original)}"


def restore_ambient(mac: str, baseline: dict) -> tuple[bool, str | None]:
    """Put the shared ambient tuple back: level, then mode, then voice focus.

    Adjusting the level forces ambient sound, and the mode and focus are
    carried on the same write, so restoring them separately in this order is
    the only sequence that ends on the original reading.
    """
    for control, key in (("ambient-level", "ambient_level"),
                         ("nc", "nc_mode"),
                         ("focus-on-voice", "focus_on_voice")):
        if baseline.get(key) is not None:
            write(mac, control, baseline[key])
    call(mac, "release")
    call(mac, "reclaim")
    final = read_state(mac)
    for key in ("nc_mode", "ambient_level", "focus_on_voice"):
        wanted = baseline.get(key)
        if wanted is not None and final.get(key) != wanted:
            return False, (
                f"{key} read back {fmt(final.get(key))}, wanted {fmt(wanted)}"
            )
    return True, None


def restore(mac: str, control: str, before: dict) -> tuple[bool, str | None]:
    if control in AMBIENT:
        return restore_ambient(mac, before)
    original = before.get(FIELD[control])
    if original is None:
        return True, None
    return restore_scalar(mac, control, original)


def verify_one(mac: str, control: str, protocol: str | None) -> dict:
    initial = read_state(mac)
    if not is_supported(control, initial, protocol):
        return {"control": control, "status": "SKIPPED",
                "reason": "not supported by this device"}
    before = initial
    value = choose_value(control, initial, protocol, initial.get("features") or [])
    if value is None:
        return {"control": control, "status": "SKIPPED",
                "reason": "no alternative value is available"}

    written = write(mac, control, value)
    if written.returncode != 0:
        status = "REFUSED"
        after = read_state(mac).get(FIELD[control])
        detail = reason(written)
    else:
        after = fresh_read(mac, control)
        # The state types differ from the write's strings — bools, ints — so
        # compare through the same display form the line prints.
        if fmt(after) == fmt(value):
            status = "HONOURED"
        elif after == before.get(FIELD[control]):
            status = "IGNORED"
        else:
            status = "FAILED"
        detail = None

    restored, restore_detail = restore(mac, control, before)
    return {
        "control": control,
        "status": status,
        "before": before.get(FIELD[control]),
        "after": after,
        "reason": detail,
        "restored": restored,
        "restore_reason": restore_detail,
    }


def resolve(parser: argparse.ArgumentParser, args) -> tuple[str, list[str]]:
    """The address and the controls to verify, from positional or --address."""
    positionals = list(args.words)
    if args.address_option:
        address = args.address_option
        controls = positionals
    elif positionals:
        address = positionals[0]
        controls = positionals[1:]
    else:
        parser.error("a MAC is required: pass it positionally or with --address")
        raise AssertionError("unreachable")
    return address, controls


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="verify.py",
        description="Check whether the headphones honour each control.",
    )
    parser.add_argument(
        "words", nargs="*", metavar="MAC|CONTROL",
        help="the headset MAC, then any controls to restrict the run to",
    )
    parser.add_argument(
        "--address", dest="address_option",
        help="the headset MAC, when it is not the first positional",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    address, controls = resolve(parser, args)

    if not MAC_RE.match(address):
        print(f"verify: {address!r} is not a Bluetooth address", file=sys.stderr)
        return 2

    unknown = [name for name in controls if name not in FIELD]
    if unknown:
        print(
            f"verify: unknown control(s): {', '.join(unknown)}; "
            f"expected one of {', '.join(ORDER)}",
            file=sys.stderr,
        )
        return 2

    probe = call(address, "session")
    if probe.returncode != 0:
        print(
            f"verify: cannot reach the daemon: {reason(probe)}",
            file=sys.stderr,
        )
        return 2

    held = call(address, "reclaim")
    if held.returncode != 0:
        print(f"verify: {reason(held)}", file=sys.stderr)
        return 2

    state = read_state(address)
    if not state.get("connected"):
        print(
            f"verify: {state.get('error') or 'headset not connected'}",
            file=sys.stderr,
        )
        return 2

    protocol = state.get("protocol")
    named = bool(controls)
    wanted = controls if named else [c for c in ORDER if is_supported(c, state, protocol)]
    if not wanted:
        print("verify: no supported controls to verify", file=sys.stderr)
        return 2

    print(
        f"Verifying {state.get('name') or address} "
        f"({address}, {protocol or 'unknown'}) - {len(wanted)} control(s)"
    )

    results = []
    for control in wanted:
        outcome = verify_one(address, control, protocol)
        if outcome["status"] == "SKIPPED":
            print(f"verify: skipping {control}: {outcome['reason']}", file=sys.stderr)
            continue
        results.append(outcome)
        line = (
            f"{outcome['control']:24} {outcome['status']:8} "
            f"{fmt(outcome['before'])} -> {fmt(outcome['after'])}"
        )
        if outcome["reason"]:
            line += f"  ({outcome['reason']})"
        print(line)

    if not results:
        print("verify: no controls were verified", file=sys.stderr)
        return 2

    counts = {name: 0 for name in ("HONOURED", "IGNORED", "FAILED", "REFUSED")}
    for outcome in results:
        counts[outcome["status"]] += 1
    print()
    print(
        f"summary: {len(results)} verified - "
        f"{counts['HONOURED']} honoured, {counts['IGNORED']} ignored, "
        f"{counts['FAILED']} failed, {counts['REFUSED']} refused"
    )

    unrestored = [o["control"] for o in results if not o["restored"]]
    if unrestored:
        for outcome in results:
            if not outcome["restored"]:
                print(f"verify: could not restore {outcome['control']}: "
                      f"{outcome['restore_reason']}", file=sys.stderr)
        print(f"not restored: {', '.join(unrestored)}")
        return 1

    print("every changed value was restored")
    return 0


if __name__ == "__main__":
    sys.exit(main())
