"""The stand-in devices: every setting round-tripped without a radio.

Both generations' stand-ins answer through the real framing, so these
fail if an encoder and its parser ever stop agreeing.
"""

import contextlib
import io
import json
import unittest
from unittest import mock

import support

sonyhp = support.sonyhp


class TestDemoDevice(unittest.TestCase):
    """Round trips through the stand-in device.

    Every request goes out through the real framing and comes back as a real
    reply payload, so these tests fail if an encoder and its parser ever stop
    agreeing — the failure mode that would otherwise only show up with
    headphones on your head.
    """

    def setUp(self):
        self.link = sonyhp.DemoLink()
        self.link.refresh()

    def test_refresh_fills_in_the_whole_state(self):
        state = self.link.state
        for key in ("firmware", "codec", "battery", "nc_mode", "ambient_level", "eq_preset",
                    "dsee", "speak_to_chat", "stc_sensitivity", "pause_when_taken_off",
                    "auto_power_off", "touch_sensor", "voice_notifications"):
            self.assertIsNotNone(state[key], key)

    def test_mode_changes_come_back_from_the_device(self):
        for mode in ("ambient-sound", "wind-noise-reduction", "off", "noise-cancelling"):
            sonyhp.apply_setting(self.link, "nc", mode)
            self.assertEqual(self.link.state["nc_mode"], mode)

    def test_cycle_walks_the_modes(self):
        sonyhp.apply_setting(self.link, "nc", "noise-cancelling")
        seen = []
        for _ in range(3):
            sonyhp.apply_setting(self.link, "nc", "cycle")
            seen.append(self.link.state["nc_mode"])
        self.assertEqual(seen, ["ambient-sound", "off", "noise-cancelling"])

    def test_ambient_level_survives_the_round_trip(self):
        sonyhp.apply_setting(self.link, "ambient-level", 17)
        self.assertEqual(self.link.state["ambient_level"], 17)
        self.assertEqual(self.link.state["nc_mode"], "ambient-sound")

    def test_focus_on_voice_does_not_disturb_the_level(self):
        sonyhp.apply_setting(self.link, "ambient-level", 6)
        sonyhp.apply_setting(self.link, "focus-on-voice", "on")
        self.assertIs(self.link.state["focus_on_voice"], True)
        self.assertEqual(self.link.state["ambient_level"], 6)

    def test_equalizer_preset(self):
        sonyhp.apply_setting(self.link, "eq", "speech")
        self.assertEqual(self.link.state["eq_preset"], "speech")

    def test_equalizer_custom_bands(self):
        sonyhp.apply_setting(self.link, "eq-bands", "3,-2,0,1,4,-1")
        self.assertEqual(self.link.state["eq_bass"], 3)
        self.assertEqual(self.link.state["eq_bands"], [-2, 0, 1, 4, -1])
        self.assertEqual(self.link.state["eq_preset"], "manual")

    def test_speak_to_chat_config(self):
        sonyhp.apply_setting(self.link, "stc-sensitivity", "low")
        sonyhp.apply_setting(self.link, "stc-timeout", "off")
        self.assertEqual(self.link.state["stc_sensitivity"], "low")
        self.assertEqual(self.link.state["stc_timeout"], "off")

    def test_auto_power_off(self):
        sonyhp.apply_setting(self.link, "auto-power-off", "when-taken-off")
        self.assertEqual(self.link.state["auto_power_off"], "when-taken-off")

    def test_every_toggle_flips_both_ways(self):
        for key, state_key in (("dsee", "dsee"), ("speak-to-chat", "speak_to_chat"),
                               ("pause-when-taken-off", "pause_when_taken_off"),
                               ("voice-notifications", "voice_notifications")):
            for value in (True, False):
                sonyhp.apply_setting(self.link, key, "on" if value else "off")
                self.assertIs(self.link.state[state_key], value, key)

    def test_settings_the_model_ignores_are_refused(self):
        # A WH-1000XM4 answers "still on" to every attempt at disabling its
        # touch panel, and "when taken off" to every timer, so do not ask.
        with self.assertRaises(ValueError):
            sonyhp.apply_setting(self.link, "touch-sensor", "off")
        with self.assertRaises(ValueError):
            sonyhp.apply_setting(self.link, "auto-power-off", "3-hour")
        sonyhp.apply_setting(self.link, "auto-power-off", "off")
        self.assertEqual(self.link.state["auto_power_off"], "off")


class TestV2DemoDevice(unittest.TestCase):
    """Round trips through the v2 stand-in device."""

    def setUp(self):
        self.link = sonyhp.DemoLinkV2()
        self.link.refresh()

    def test_refresh_fills_in_the_whole_state(self):
        state = self.link.state
        self.assertEqual(state["protocol"], "v2")
        for key in ("firmware", "codec", "battery", "nc_mode", "ambient_level",
                    "eq_preset", "speak_to_chat", "pause_when_taken_off",
                    "auto_power_off", "stc_sensitivity", "voice_notifications"):
            self.assertIsNotNone(state[key], key)
        self.assertEqual(state["codec"], "LDAC")

    def test_mode_changes_come_back_from_the_device(self):
        for mode in ("ambient-sound", "off", "noise-cancelling"):
            sonyhp.apply_setting(self.link, "nc", mode)
            self.assertEqual(self.link.state["nc_mode"], mode)

    def test_ambient_level_survives_the_round_trip(self):
        sonyhp.apply_setting(self.link, "ambient-level", 17)
        self.assertEqual(self.link.state["ambient_level"], 17)
        self.assertEqual(self.link.state["nc_mode"], "ambient-sound")

    def test_a_reported_noise_adaptation_sensitivity_survives_a_write(self):
        for byte in (0x01, 0x03):
            with self.subTest(sensitivity=hex(byte)):
                self.link.device.sensitivity = byte
                self.link.refresh()
                self.assertEqual(self.link.state["asc_noise_adapt_sensitivity"], byte)
                sonyhp.apply_setting(self.link, "nc", "ambient-sound")
                self.assertEqual(self.link.device.sensitivity, byte)

    def test_equalizer_preset(self):
        sonyhp.apply_setting(self.link, "eq", "clear")
        self.assertEqual(self.link.state["eq_preset"], "clear")

    def test_equalizer_custom_bands(self):
        sonyhp.apply_setting(self.link, "eq-bands", "1,0,-1,-2,-3,-4,-5,-6,0,0")
        self.assertEqual(self.link.state["eq_bands"], [1, 0, -1, -2, -3, -4, -5, -6, 0, 0])
        self.assertEqual(self.link.state["eq_preset"], "custom")

    def test_speak_to_chat_toggles_both_ways(self):
        for value in (True, False):
            sonyhp.apply_setting(self.link, "speak-to-chat", "on" if value else "off")
            self.assertIs(self.link.state["speak_to_chat"], value)

    def test_pause_when_taken_off_toggles_both_ways(self):
        for value in (False, True):
            sonyhp.apply_setting(self.link, "pause-when-taken-off", "on" if value else "off")
            self.assertIs(self.link.state["pause_when_taken_off"], value)

    def test_auto_power_off(self):
        sonyhp.apply_setting(self.link, "auto-power-off", "off")
        self.assertEqual(self.link.state["auto_power_off"], "off")

    def test_speak_to_chat_config(self):
        sonyhp.apply_setting(self.link, "stc-sensitivity", "low")
        sonyhp.apply_setting(self.link, "stc-timeout", "off")
        self.assertEqual(self.link.state["stc_sensitivity"], "low")
        self.assertEqual(self.link.state["stc_timeout"], "off")

    def test_voice_guidance_round_trip(self):
        for value in ("off", "on"):
            sonyhp.apply_setting(self.link, "voice-notifications", value)
            self.assertIs(self.link.state["voice_notifications"], value == "on")


class TestV2DemoOnlySurface(unittest.TestCase):
    """The v2 stand-in has to answer the controls only v2 has.

    DSEE, connection quality, listening mode, the BGM room and multipoint are
    exactly the controls a v1 stand-in cannot round-trip and nobody can check
    without the headphones.
    """

    def setUp(self):
        self.link = sonyhp.DemoLinkV2()
        self.link.refresh()

    def test_refresh_fills_the_v2_only_state(self):
        state = self.link.state
        self.assertIs(state["dsee"], True)
        self.assertEqual(state["connection_quality"], "sound-quality")
        self.assertEqual(state["listening_mode"], "standard")
        self.assertEqual(state["bgm_room_size"], "living-room")
        self.assertEqual([d["address"] for d in state["devices"]],
                         ["AA:BB:CC:DD:EE:FF", "11:22:33:44:55:66"])
        self.assertIsNone(state["playback_source"])

    def test_dsee_round_trip(self):
        for value in (False, True):
            sonyhp.apply_setting(self.link, "dsee", "on" if value else "off")
            self.assertIs(self.link.state["dsee"], value)

    def test_connection_quality_round_trip(self):
        for value in ("stable", "sound-quality"):
            sonyhp.apply_setting(self.link, "connection-quality", value)
            self.assertEqual(self.link.state["connection_quality"], value)

    def test_listening_mode_round_trip(self):
        for value in ("cinema", "background-music", "standard"):
            sonyhp.apply_setting(self.link, "listening-mode", value)
            self.assertEqual(self.link.state["listening_mode"], value)

    def test_bgm_room_size_round_trip(self):
        sonyhp.apply_setting(self.link, "listening-mode", "background-music")
        for room in ("cafe", "my-room", "living-room"):
            sonyhp.apply_setting(self.link, "bgm-room-size", room)
            self.assertEqual(self.link.state["bgm_room_size"], room)
            self.assertEqual(self.link.state["listening_mode"], "background-music")

    def test_playback_source_round_trip(self):
        for address in ("11:22:33:44:55:66", "AA:BB:CC:DD:EE:FF"):
            sonyhp.apply_setting(self.link, "playback-source", address)
            self.assertEqual(self.link.state["playback_source"], address)
            playing = [d["address"] for d in self.link.state["devices"] if d["playback"]]
            self.assertEqual(playing, [address])


class TestV2DemoMalformed(unittest.TestCase):
    """A malformed write is shrugged off, not a crash in the round trip."""

    def setUp(self):
        self.link = sonyhp.DemoLinkV2()
        self.link.refresh()

    def write_and_settle(self, msg_type, payload):
        self.link.write(msg_type, bytes(payload))
        self.link.pump(0.05)

    def test_a_truncated_dsee_write_is_ignored(self):
        self.write_and_settle(sonyhp.MSG_COMMAND_1, [0xE8, 0x01])
        self.assertIs(self.link.state["dsee"], True)

    def test_an_out_of_range_connection_quality_is_ignored(self):
        self.write_and_settle(sonyhp.MSG_COMMAND_1, [0xE8, 0x02, 0x07])
        self.assertEqual(self.link.state["connection_quality"], "sound-quality")

    def test_an_unknown_bgm_room_is_ignored(self):
        self.write_and_settle(sonyhp.MSG_COMMAND_1, [0xE8, 0x09, 0x00, 0x09])
        self.assertEqual(self.link.state["bgm_room_size"], "living-room")
        self.assertIs(self.link.state["bgm_enabled"], False)

    def test_a_truncated_source_switch_is_ignored(self):
        self.write_and_settle(sonyhp.MSG_COMMAND_2, [0x3C, 0x01] + list(b"AA:BB:CC"))
        self.assertIsNone(self.link.state["playback_source"])

    def test_a_source_switch_to_an_unknown_peer_is_ignored(self):
        self.write_and_settle(
            sonyhp.MSG_COMMAND_2, [0x3C, 0x01] + list(b"ZZ:ZZ:ZZ:ZZ:ZZ:ZZ"))
        self.assertIsNone(self.link.state["playback_source"])


class TestWriteThenVerify(unittest.TestCase):
    """A write is pending until the device's own read-back moves.

    The stand-in link has no daemon above it, so its `on_change` records every
    state the helper would publish; that is how the intermediate pending state
    is observed without a second process. A device in stubborn mode ACKs a
    write without changing the value, which is the refusal path.
    """

    def setUp(self):
        self.link = sonyhp.DemoLink()
        self.link.refresh()
        self.seen = []
        self.link.on_change = lambda state: self.seen.append(
            (list(state.get("pending") or []), dict(state.get("refused") or {})))

    def test_a_confirmed_write_is_pending_until_the_read_back_moves(self):
        sonyhp.apply_setting(self.link, "nc", "off")
        self.assertIn((["nc"], {}), self.seen,
                      "the write must be published as pending")
        self.assertEqual(self.link.state["pending"], [])
        self.assertEqual(self.link.state["refused"], {})
        self.assertEqual(self.link.state["nc_mode"], "off")

    def test_a_stubborn_write_ends_up_refused_with_a_reason(self):
        self.link.device.stubborn = True
        sonyhp.apply_setting(self.link, "nc", "off")
        self.assertEqual(self.link.state["pending"], [])
        reason = self.link.state["refused"]["nc"]
        self.assertIn("the headphones did not change nc", reason)
        self.assertTrue(any("nc" in refused for _, refused in self.seen),
                        "the refusal must be published")
        # The value the device never accepted is not the one reported.
        self.assertEqual(self.link.state["nc_mode"], "noise-cancelling")

    def test_a_refusal_clears_when_the_same_setting_later_succeeds(self):
        self.link.device.stubborn = True
        sonyhp.apply_setting(self.link, "nc", "off")
        self.assertIn("nc", self.link.state["refused"])
        self.link.device.stubborn = False
        sonyhp.apply_setting(self.link, "nc", "off")
        self.assertEqual(self.link.state["refused"], {})
        self.assertEqual(self.link.state["pending"], [])
        self.assertEqual(self.link.state["nc_mode"], "off")

    def test_the_v2_stand_in_is_stubborn_too(self):
        link = sonyhp.DemoLinkV2()
        link.refresh()
        link.device.stubborn = True
        sonyhp.apply_setting(link, "dsee", "off")
        self.assertEqual(link.state["pending"], [])
        self.assertIn("dsee", link.state["refused"])
        self.assertIs(link.state["dsee"], True)

    def test_stubborn_is_off_by_default(self):
        self.assertFalse(self.link.device.stubborn)
        self.assertFalse(sonyhp.DemoLinkV2().device.stubborn)
        # With it off, the ordinary round trip still moves the value.
        sonyhp.apply_setting(self.link, "dsee", "off")
        self.assertEqual(self.link.state["refused"], {})
        self.assertIs(self.link.state["dsee"], False)

    def test_a_write_that_raises_leaves_no_pending_behind(self):
        # A transport failure after the mark must clear the pending entry
        # (publishing the transition) rather than leave the row showing "…"
        # forever, and the failure itself must still reach the caller. No
        # refusal is recorded: refused is only for an acknowledged write the
        # device answered without moving.
        with mock.patch.object(self.link, "write",
                               side_effect=sonyhp.NotConnected("not connected")):
            with self.assertRaises(sonyhp.NotConnected):
                sonyhp.apply_setting(self.link, "nc", "off")
        self.assertEqual(self.link.state["pending"], [])
        self.assertNotIn("nc", self.link.state.get("refused") or {})
        self.assertEqual(self.link.state["nc_mode"], "noise-cancelling")
        # --json status carries the cleared pending, not a stuck entry.
        args = sonyhp.build_parser().parse_args(["--json", "status"])
        response = {"ok": True, "state": self.link.state}
        with mock.patch.object(sonyhp, "run_request", return_value=response):
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                self.assertEqual(sonyhp.cmd_status(args), 0)
        self.assertEqual(json.loads(output.getvalue())["pending"], [])


class TestStubbornCoversEverySetting(unittest.TestCase):
    """Stubborn mode must hold every setting the demo supports.

    The `and not self.stubborn` guard is threaded through one branch per
    control, so a new SET branch that forgets it would silently break stubborn
    fidelity. The key list comes from the helper's own SETTING_KEYS, minus the
    other generation's keys and the keys the device's feature list (built by
    features_for) refuses, so a newly added setting either arrives with a probe
    below or fails loudly on the set-equality check. Every probe is a value
    that moves the device when stubborn is off, so a forgotten guard fails the
    no-move/refused assertions rather than passing vacuously.
    """

    # Refreshed v1 demo reports noise-cancelling, level 0, no focus, eq off,
    # flat bands, dsee on, speak-to-chat off with auto/standard/no-focus,
    # pause on, voice guidance on and auto power off.
    V1_PROBES = {
        "nc": "off",
        "ambient-level": 17,
        "focus-on-voice": "on",
        "eq": "speech",
        "eq-bands": "3,-2,0,1,4,-1",
        "auto-power-off": "when-taken-off",
        "stc-sensitivity": "low",
        "stc-timeout": "long",
        "stc-focus-on-voice": "on",
        "dsee": "off",
        "speak-to-chat": "on",
        "pause-when-taken-off": "off",
        "voice-notifications": "off",
    }
    # Refreshed v2 demo reports noise-cancelling, level 12, no focus, eq off,
    # flat bands, dsee on, speak-to-chat off with auto/standard, pause on,
    # when-taken-off, sound-quality, standard, living-room and no source.
    V2_PROBES = {
        "nc": "off",
        "ambient-level": 17,
        "focus-on-voice": "on",
        "eq": "clear",
        "eq-bands": "1,0,-1,-2,-3,-4,-5,-6,0,0",
        "auto-power-off": "off",
        "stc-sensitivity": "low",
        "stc-timeout": "long",
        "dsee": "off",
        "speak-to-chat": "on",
        "pause-when-taken-off": "off",
        "voice-notifications": "off",
        "connection-quality": "stable",
        "listening-mode": "cinema",
        "bgm-room-size": "cafe",
        "playback-source": "11:22:33:44:55:66",
    }

    def check_device(self, make_link, exclusive, probes, skip):
        link = make_link()
        link.refresh()
        features = link.state.get("features") or []
        supported = set()
        for key in sonyhp.SETTING_KEYS:
            if key in exclusive or key in skip:
                continue
            needed = sonyhp.SETTING_FEATURES.get(key)
            if needed is not None and needed not in features:
                # The builder refuses it before any write, so no device
                # branch exists to guard (e.g. v1 touch-sensor on an XM4).
                continue
            supported.add(key)
        self.assertEqual(set(probes), supported,
                         "a new setting needs a stubborn probe, a removed one drops its probe")
        for key in sorted(supported):
            with self.subTest(key=key):
                target = make_link()
                target.refresh()
                target.device.stubborn = True
                before = {name: value for name, value in target.state.items()
                          if name not in ("pending", "refused")}
                sonyhp.apply_setting(target, key, probes[key])
                self.assertEqual(target.state.get("pending"), [])
                self.assertIn(key, target.state.get("refused") or {},
                              "an acknowledged write that did not move is refused")
                after = {name: value for name, value in target.state.items()
                         if name not in ("pending", "refused")}
                self.assertEqual(after, before,
                                 "stubborn must not move any reported value")

    def test_v1_stubborn_holds_every_supported_setting(self):
        self.check_device(sonyhp.DemoLink, sonyhp.V2_ONLY_SETTINGS, self.V1_PROBES, ())

    def test_v2_stubborn_holds_every_supported_setting(self):
        # stc-focus-on-voice is refused by the v2 builder outright (a v1-only
        # mapping onto the shared speak-to-chat feature), so no write ever
        # reaches the device and there is no branch to guard.
        self.check_device(sonyhp.DemoLinkV2, sonyhp.V1_ONLY_SETTINGS,
                          self.V2_PROBES, ("stc-focus-on-voice",))


if __name__ == "__main__":
    unittest.main(verbosity=2)
