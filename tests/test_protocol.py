#!/usr/bin/env python3
"""Protocol tests that need no headphones.

Everything here is byte-level: framing, the shape of each request, and folding
replies into state. The parts that need a real device (channel discovery, the
daemon, reconnects) are covered by `sony-headphones probe`.

Run with: python3 tests/test_protocol.py
"""

import importlib.util
import json
import os
import shutil
import socket
import stat
import struct
import sys
import tempfile
import time
import unittest
from unittest import mock

HELPER = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "bin", "sony-headphones")
spec = importlib.util.spec_from_loader("sonyhp", importlib.machinery.SourceFileLoader("sonyhp", HELPER))
sonyhp = importlib.util.module_from_spec(spec)
spec.loader.exec_module(sonyhp)


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


class TestRequests(unittest.TestCase):
    def test_noise_cancelling(self):
        _, payload = sonyhp.asc_request("noise-cancelling", 12, False, True)
        self.assertEqual(list(payload), [0x68, 0x02, 0x11, 0x02, 0x02, 0x01, 0x00, 0x00])

    def test_ambient_carries_level_and_voice_focus(self):
        _, payload = sonyhp.asc_request("ambient-sound", 15, True, True)
        self.assertEqual(list(payload), [0x68, 0x02, 0x11, 0x02, 0x00, 0x01, 0x01, 15])

    def test_off(self):
        _, payload = sonyhp.asc_request("off", 5, False, True)
        self.assertEqual(list(payload), [0x68, 0x02, 0x00, 0x02, 0x00, 0x01, 0x00, 5])

    def test_wind_noise_reduction(self):
        _, payload = sonyhp.asc_request("wind-noise-reduction", 0, False, True)
        self.assertEqual(payload[4], 0x01)

    def test_device_without_wind_support_uses_the_short_mode_table(self):
        _, payload = sonyhp.asc_request("noise-cancelling", 0, False, False)
        self.assertEqual(list(payload[3:5]), [0x00, 0x01])

    def test_wind_is_refused_when_unsupported(self):
        with self.assertRaises(ValueError):
            sonyhp.asc_request("wind-noise-reduction", 0, False, False)

    def test_level_is_clamped(self):
        _, payload = sonyhp.asc_request("ambient-sound", 99, False, True)
        self.assertEqual(payload[7], sonyhp.MAX_AMBIENT_LEVEL)

    def test_unknown_mode(self):
        with self.assertRaises(ValueError):
            sonyhp.asc_request("quiet-please", 0, False, True)

    def test_equalizer_preset(self):
        _, payload = sonyhp.eq_preset_request("bass-boost")
        self.assertEqual(list(payload), [0x58, 0x01, 0x16, 0x00])

    def test_equalizer_custom_bands_are_offset_by_ten(self):
        _, payload = sonyhp.eq_bands_request(-10, [0, 10, -5, 5, 1])
        self.assertEqual(list(payload), [0x58, 0x01, 0xFF, 0x06, 0, 10, 20, 5, 15, 11])

    def test_equalizer_rejects_out_of_range(self):
        with self.assertRaises(ValueError):
            sonyhp.eq_bands_request(0, [0, 0, 0, 0, 11])

    def test_auto_power_off(self):
        _, payload = sonyhp.auto_power_off_request("when-taken-off")
        self.assertEqual(list(payload), [0xF8, 0x04, 0x01, 0x10, 0x00])

    def test_speak_to_chat_config(self):
        _, payload = sonyhp.stc_config_request("high", True, "long")
        self.assertEqual(list(payload), [0xFC, 0x05, 0x00, 0x01, 0x01, 0x02])

    def test_refresh_asks_for_everything_once(self):
        requests = sonyhp.refresh_requests()
        opcodes = [payload[0] for _, payload in requests]
        self.assertEqual(len(opcodes), len(set(opcodes)) + 2)  # the 0xf6 family repeats, with different sub-types
        self.assertIn(sonyhp.ASC_GET, opcodes)
        self.assertIn(sonyhp.BATTERY_GET, opcodes)


class TestReplies(unittest.TestCase):
    def setUp(self):
        self.state = sonyhp.initial_state()

    def apply(self, payload, msg_type=None):
        return sonyhp.apply_payload(self.state, msg_type or sonyhp.MSG_COMMAND_1, bytes(payload))

    def test_ambient_sound_control(self):
        self.assertTrue(self.apply([0x67, 0x02, 0x01, 0x02, 0x00, 0x01, 0x01, 0x11]))
        self.assertEqual(self.state["nc_mode"], "ambient-sound")
        self.assertEqual(self.state["ambient_level"], 17)
        self.assertIs(self.state["focus_on_voice"], True)
        self.assertIs(self.state["supports_wind"], True)

    def test_ambient_sound_control_noise_cancelling(self):
        self.apply([0x67, 0x02, 0x01, 0x02, 0x02, 0x01, 0x00, 0x00])
        self.assertEqual(self.state["nc_mode"], "noise-cancelling")

    def test_ambient_sound_control_off(self):
        self.apply([0x67, 0x02, 0x00, 0x02, 0x00, 0x01, 0x00, 0x00])
        self.assertEqual(self.state["nc_mode"], "off")

    def test_ambient_sound_control_without_wind_support(self):
        self.apply([0x67, 0x02, 0x01, 0x00, 0x01, 0x01, 0x00, 0x00])
        self.assertEqual(self.state["nc_mode"], "noise-cancelling")
        self.assertIs(self.state["supports_wind"], False)

    def test_notification_is_parsed_like_a_reply(self):
        self.apply([0x69, 0x02, 0x01, 0x02, 0x02, 0x01, 0x00, 0x00])
        self.assertEqual(self.state["nc_mode"], "noise-cancelling")

    def test_battery(self):
        self.apply([0x11, 0x00, 0x5A, 0x01])
        self.assertEqual(self.state["battery"], 90)
        self.assertIs(self.state["charging"], True)

    def test_firmware(self):
        self.apply([0x05, 0x02, 0x05] + list(b"2.5.0"))
        self.assertEqual(self.state["firmware"], "2.5.0")

    def test_codec(self):
        self.apply([0x19, 0x00, 0x10])
        self.assertEqual(self.state["codec"], "LDAC")

    def test_equalizer(self):
        self.apply([0x57, 0x01, 0x16, 0x06, 12, 10, 11, 9, 10, 10])
        self.assertEqual(self.state["eq_preset"], "bass-boost")
        self.assertEqual(self.state["eq_bass"], 2)
        self.assertEqual(self.state["eq_bands"], [0, 1, -1, 0, 0])

    def test_dsee(self):
        self.apply([0xE7, 0x02, 0x00, 0x01])
        self.assertIs(self.state["dsee"], True)

    def test_ldac_is_not_mistaken_for_dsee(self):
        self.apply([0xE7, 0x01, 0x00, 0x01])
        self.assertIsNone(self.state["dsee"])

    def test_speak_to_chat_enabled(self):
        self.apply([0xF7, 0x05, 0x01, 0x01])
        self.assertIs(self.state["speak_to_chat"], True)

    def test_pause_when_taken_off(self):
        self.apply([0xF7, 0x03, 0x00, 0x01])
        self.assertIs(self.state["pause_when_taken_off"], True)

    def test_auto_power_off(self):
        self.apply([0xF7, 0x04, 0x01, 0x10, 0x00])
        self.assertEqual(self.state["auto_power_off"], "when-taken-off")

    def test_speak_to_chat_config(self):
        self.apply([0xFB, 0x05, 0x00, 0x02, 0x01, 0x03])
        self.assertEqual(self.state["stc_sensitivity"], "low")
        self.assertEqual(self.state["stc_timeout"], "off")
        self.assertIs(self.state["stc_focus_on_voice"], True)

    def test_touch_sensor(self):
        self.apply([0xD7, 0xD2, 0x01, 0x00])
        self.assertIs(self.state["touch_sensor"], False)

    def test_voice_notifications_live_on_the_second_command_family(self):
        self.apply([0x47, 0x01, 0x01, 0x01], sonyhp.MSG_COMMAND_2)
        self.assertIs(self.state["voice_notifications"], True)

    def test_same_payload_twice_reports_no_change(self):
        payload = [0x11, 0x00, 0x5A, 0x00]
        self.assertTrue(self.apply(payload))
        self.assertFalse(self.apply(payload))

    def test_garbage_is_ignored(self):
        self.assertFalse(self.apply([0x99, 0x01, 0x02]))
        self.assertFalse(self.apply([]))

    def test_out_of_range_ambient_level_is_rejected(self):
        self.assertFalse(self.apply([0x67, 0x02, 0x01, 0x02, 0x00, 0x01, 0x00, 99]))
        self.assertIsNone(self.state["nc_mode"])


class TestSettings(unittest.TestCase):
    def test_cycle_order(self):
        self.assertEqual(sonyhp.next_nc_mode("noise-cancelling"), "ambient-sound")
        self.assertEqual(sonyhp.next_nc_mode("ambient-sound"), "off")
        self.assertEqual(sonyhp.next_nc_mode("off"), "noise-cancelling")
        self.assertEqual(sonyhp.next_nc_mode(None), "noise-cancelling")

    def test_setting_the_level_switches_out_of_noise_cancelling(self):
        state = sonyhp.initial_state()
        state.update(nc_mode="noise-cancelling", ambient_level=0, supports_wind=True)
        (_, payload), = sonyhp.setting_requests(state, "ambient-level", 8)
        self.assertEqual(payload[4], sonyhp.ASC_MODE_CODE["ambient-sound"])
        self.assertEqual(payload[7], 8)

    def test_voice_focus_keeps_the_current_mode_and_level(self):
        state = sonyhp.initial_state()
        state.update(nc_mode="ambient-sound", ambient_level=13, supports_wind=True)
        (_, payload), = sonyhp.setting_requests(state, "focus-on-voice", "true")
        self.assertEqual(list(payload[4:8]), [0x00, 0x01, 0x01, 13])

    def test_toggle_reads_the_current_value(self):
        state = sonyhp.initial_state()
        state["dsee"] = True
        (_, payload), = sonyhp.setting_requests(state, "dsee", "toggle")
        self.assertEqual(payload[3], 0x00)

    def test_bool_settings_accept_common_spellings(self):
        for text in ("on", "yes", "1", "true", "enabled"):
            self.assertIs(sonyhp.parse_bool(text), True)
        for text in ("off", "no", "0", "false", "disabled"):
            self.assertIs(sonyhp.parse_bool(text), False)

    def test_eq_bands_setting_parses_a_list(self):
        (_, payload), = sonyhp.setting_requests(sonyhp.initial_state(), "eq-bands", "0, 1, 2, 3, 4, 5")
        self.assertEqual(list(payload[4:]), [10, 11, 12, 13, 14, 15])

    def test_eq_bands_needs_six_values(self):
        with self.assertRaises(ValueError):
            sonyhp.setting_requests(sonyhp.initial_state(), "eq-bands", "1,2,3")

    def test_unknown_setting(self):
        with self.assertRaises(ValueError):
            sonyhp.setting_requests(sonyhp.initial_state(), "loudness", "on")

    def test_every_advertised_key_can_build_a_request(self):
        state = sonyhp.initial_state()
        state.update(nc_mode="ambient-sound", ambient_level=10, supports_wind=True)
        values = {
            "nc": "noise-cancelling", "ambient-level": "5", "focus-on-voice": "on",
            "eq": "vocal", "eq-bands": "0,0,0,0,0,0", "auto-power-off": "off",
            "stc-sensitivity": "auto", "stc-timeout": "short", "stc-focus-on-voice": "on",
            "voice-notifications": "on", "dsee": "on", "speak-to-chat": "on",
            "pause-when-taken-off": "on", "touch-sensor": "on",
        }
        # The priority is v2's and the codec PipeWire's; both have tests of their own.
        self.assertEqual(sorted(set(values) | {"priority", "codec"}), sonyhp.SETTING_KEYS)
        for key, value in values.items():
            requests = sonyhp.setting_requests(state, key, value)
            self.assertTrue(requests, key)


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


class TestFeatures(unittest.TestCase):
    def test_known_models(self):
        self.assertIn("speak-to-chat", sonyhp.features_for("WH-1000XM4"))
        self.assertNotIn("touch-sensor", sonyhp.features_for("WH-1000XM4"))
        self.assertNotIn("auto-power-off-timer", sonyhp.features_for("WH-1000XM4"))
        self.assertIn("touch-sensor", sonyhp.features_for("WH-1000XM3"))
        self.assertIn("auto-power-off-timer", sonyhp.features_for("WH-1000XM3"))
        self.assertNotIn("speak-to-chat", sonyhp.features_for("WH-1000XM3"))
        self.assertNotIn("auto-power-off", sonyhp.features_for("WH-1000XM2"))

    def test_the_name_only_has_to_contain_the_model(self):
        self.assertEqual(sonyhp.features_for("Gabriel's WH-1000XM4"), sonyhp.features_for("WH-1000XM4"))

    def test_an_unknown_device_gets_everything(self):
        self.assertEqual(set(sonyhp.features_for("WH-XB910N")), sonyhp.ALL_FEATURES)
        self.assertEqual(set(sonyhp.features_for(None)), sonyhp.ALL_FEATURES)


# -- Protocol v2 -----------------------------------------------------------------
#
# Reply payloads below are the ones a WH-1000XM5 on firmware 2.5.1 sent.

XM5_INIT_REPLY = bytes.fromhex("01 00 03 00 20 16 00 00")


def v2_state(**changes):
    state = sonyhp.initial_state()
    state.update(protocol=2, features=[], name="WH-1000XM5")
    link = sonyhp.DemoLink(model="WH-1000XM5")
    link.refresh()
    state.update(functions=link.state["functions"], touch_slot=link.state["touch_slot"],
                 features=link.state["features"])
    state.update(changes)
    return state


class TestV2Handshake(unittest.TestCase):
    def handshake(self, reply):
        link = sonyhp.Link("AA:BB:CC:DD:EE:FF")
        link.sock = FakeStream(sonyhp.encode_message(sonyhp.MSG_COMMAND_1, 0, reply))
        return link, link._handshake()

    def test_an_eight_byte_init_reply_means_v2(self):
        link, ok = self.handshake(XM5_INIT_REPLY)
        self.assertTrue(ok)
        self.assertEqual(link.state["protocol"], 2)
        self.assertEqual(link.state["features"], [], "nothing is offered before the device lists it")

    def test_a_four_byte_init_reply_means_v1(self):
        link, ok = self.handshake(bytes([0x01, 0x00, 0x40, 0x10]))
        self.assertTrue(ok)
        self.assertEqual(link.state["protocol"], 1)

    def test_an_unknown_init_reply_is_refused(self):
        with self.assertRaises(sonyhp.NotConnected):
            self.handshake(bytes([0x01, 0x00, 0x03]))


class TestV2Discovery(unittest.TestCase):
    def devices(self, info):
        def fake(*args, **kwargs):
            if args[0] == "devices":
                return "Device AC:80:0A:57:32:9D WH-1000XM5\n"
            return info
        with mock.patch.object(sonyhp, "bluetoothctl", side_effect=fake):
            return sonyhp.connected_devices()

    def test_the_v2_service_is_recognised(self):
        device, = self.devices(f"\tUUID: Vendor specific ({sonyhp.SERVICE_UUID_V2})\n")
        self.assertEqual(device["service"], sonyhp.SERVICE_UUID_V2_BYTES)

    def test_the_v2_service_wins_when_both_are_offered(self):
        device, = self.devices(f"UUID: ({sonyhp.SERVICE_UUID})\nUUID: ({sonyhp.SERVICE_UUID_V2})\n")
        self.assertEqual(device["service"], sonyhp.SERVICE_UUID_V2_BYTES)

    def test_a_v1_device_keeps_the_v1_service(self):
        device, = self.devices(f"UUID: ({sonyhp.SERVICE_UUID})\n")
        self.assertEqual(device["service"], sonyhp.SERVICE_UUID_BYTES)

    def test_the_link_looks_up_the_service_the_device_offers(self):
        link = sonyhp.Link.for_device({"address": "AC:80:0A:57:32:9D", "name": "WH-1000XM5",
                                       "service": sonyhp.SERVICE_UUID_V2_BYTES})
        with mock.patch.object(sonyhp, "sdp_channel", return_value=None) as sdp, \
                mock.patch.object(sonyhp, "cached_channel", return_value=None):
            with self.assertRaises(sonyhp.NotConnected):
                link.connect()
        self.assertEqual(sdp.call_args[0][1], sonyhp.SERVICE_UUID_V2_BYTES)


class TestV2Replies(unittest.TestCase):
    def setUp(self):
        self.state = sonyhp.initial_state()
        self.state.update(protocol=2, features=[])

    def apply(self, payload, msg_type=None):
        return sonyhp.apply_payload(self.state, msg_type or sonyhp.MSG_COMMAND_1, bytes.fromhex(payload))

    def test_support_lists_become_features(self):
        self.apply("07 00 06 10 ff 20 ff 50 0a fc 09 f1 23 25 21")
        self.apply("07 00 01 41 25", sonyhp.MSG_COMMAND_2)
        self.assertEqual(self.state["features"], ["auto-power-off", "battery", "equalizer",
                                                  "pause-when-taken-off", "speak-to-chat",
                                                  "voice-notifications"])
        self.assertEqual(self.state["functions"]["table2"], [0x41])

    def test_a_truncated_support_list_is_ignored(self):
        self.assertFalse(self.apply("07 00 06 10 ff 20"))
        self.assertIsNone(self.state["functions"])

    def test_the_touch_panel_slot_is_found_by_name(self):
        self.apply("d1 d2 00 01 12" + b"MULTIPOINT_SETTING".hex())
        self.assertIsNone(self.state["touch_slot"])
        self.apply("d1 d1 00 01 13" + b"TOUCH_PANEL_SETTING".hex() + "00")
        self.assertEqual(self.state["touch_slot"], 0xD1)
        self.assertIn("touch-sensor", self.state["features"])
        self.apply("d7 d1 00 00")
        self.assertIs(self.state["touch_sensor"], True)
        self.apply("d9 d1 00 01")
        self.assertIs(self.state["touch_sensor"], False)

    def test_another_slot_is_not_mistaken_for_the_touch_panel(self):
        self.state["touch_slot"] = 0xD1
        self.apply("d7 d2 00 01")
        self.assertIsNone(self.state["touch_sensor"])

    def test_battery_codec_and_firmware(self):
        self.apply("23 00 1b 00")
        self.apply("13 02 10")
        self.apply("05 02 05" + b"2.5.1".hex())
        self.assertEqual((self.state["battery"], self.state["charging"]), (27, False))
        self.assertEqual(self.state["codec"], "LDAC")
        self.assertEqual(self.state["firmware"], "2.5.1")
        self.apply("25 00 1c 01")
        self.assertEqual((self.state["battery"], self.state["charging"]), (28, True))

    def test_ambient_sound_control(self):
        self.apply("67 17 01 01 01 01 0f")
        self.assertEqual(self.state["nc_mode"], "ambient-sound")
        self.assertEqual(self.state["ambient_level"], 15)
        self.assertIs(self.state["focus_on_voice"], True)
        self.assertIs(self.state["supports_wind"], False)
        self.apply("69 17 01 01 00 01 0f")
        self.assertEqual(self.state["nc_mode"], "noise-cancelling")
        self.apply("69 17 01 00 00 01 0f")
        self.assertEqual(self.state["nc_mode"], "off")

    def test_equalizer(self):
        self.apply("59 00 16 06 11 0a 0a 0a 0a 0a")
        self.assertEqual(self.state["eq_preset"], "bass-boost")
        self.assertEqual(self.state["eq_bass"], 7)
        self.apply("57 00 a0 06 0b 0c 07 0a 0e 09")
        self.assertEqual(self.state["eq_preset"], "manual")
        self.assertEqual(self.state["eq_bands"], [2, -3, 0, 4, -1])

    def test_ten_band_equalizers_are_left_alone(self):
        self.assertFalse(self.apply("57 00 a0 0a " + "06 " * 10))

    def test_on_off_bytes_are_inverted(self):
        self.apply("f7 01 00")
        self.apply("f7 0c 01 01")
        self.apply("47 01 00 01", sonyhp.MSG_COMMAND_2)
        self.assertIs(self.state["pause_when_taken_off"], True)
        self.assertIs(self.state["speak_to_chat"], False)
        self.assertIs(self.state["voice_notifications"], True)

    def test_priority(self):
        self.apply("e7 00 00")
        self.assertEqual(self.state["priority"], "sound-quality")
        self.apply("e9 00 01")
        self.assertEqual(self.state["priority"], "connection")
        self.assertFalse(self.apply("e9 00 07"))

    def test_dsee_is_not_inverted(self):
        self.apply("e7 01 00")
        self.assertIs(self.state["dsee"], False)
        self.apply("e9 01 01")
        self.assertIs(self.state["dsee"], True)

    def test_speak_to_chat_config_and_auto_power_off(self):
        self.apply("fb 0c 00 01")
        self.apply("27 05 10 00")
        self.assertEqual((self.state["stc_sensitivity"], self.state["stc_timeout"]), ("auto", "standard"))
        self.assertEqual(self.state["auto_power_off"], "when-taken-off")
        self.apply("29 05 11 00")
        self.assertEqual(self.state["auto_power_off"], "off")

    def test_v1_payloads_are_not_read_as_v2(self):
        self.assertFalse(self.apply("67 02 01 02 00 01 01 11"))
        self.assertFalse(self.apply("11 00 50 00"))


class TestV2Requests(unittest.TestCase):
    def setUp(self):
        self.state = v2_state(nc_mode="ambient-sound", ambient_level=15, focus_on_voice=True)

    def payloads(self, key, value):
        return [(t, p.hex(" ")) for t, p in sonyhp.setting_requests(self.state, key, value)]

    def test_modes(self):
        self.assertEqual(self.payloads("nc", "noise-cancelling"), [(0x0C, "68 17 01 01 00 01 0f")])
        self.assertEqual(self.payloads("nc", "off"), [(0x0C, "68 17 01 00 00 01 0f")])
        self.assertEqual(self.payloads("ambient-level", 7), [(0x0C, "68 17 01 01 01 01 07")])
        self.assertEqual(self.payloads("focus-on-voice", "off"), [(0x0C, "68 17 01 01 01 00 0f")])

    def test_equalizer(self):
        self.assertEqual(self.payloads("eq", "bass-boost"), [(0x0C, "58 00 16 00")])
        self.assertEqual(self.payloads("eq-bands", "1,2,-3,0,4,-1"), [(0x0C, "58 00 a0 06 0b 0c 07 0a 0e 09")])

    def test_toggles(self):
        self.assertEqual(self.payloads("speak-to-chat", "on"), [(0x0C, "f8 0c 00 01")])
        self.assertEqual(self.payloads("pause-when-taken-off", "off"), [(0x0C, "f8 01 01")])
        self.assertEqual(self.payloads("dsee", "on"), [(0x0C, "e8 01 01")])
        self.assertEqual(self.payloads("voice-notifications", "off"), [(0x0E, "48 01 01")])

    def test_speak_to_chat_config_and_auto_power_off(self):
        self.assertEqual(self.payloads("stc-sensitivity", "high"), [(0x0C, "fc 0c 01 01")])
        self.assertEqual(self.payloads("auto-power-off", "off"), [(0x0C, "28 05 11 00")])

    def test_priority(self):
        self.assertEqual(self.payloads("priority", "connection"), [(0x0C, "e8 00 01")])
        self.assertEqual(self.payloads("priority", "sound-quality"), [(0x0C, "e8 00 00")])
        with self.assertRaises(ValueError):
            sonyhp.setting_requests(self.state, "priority", "loudest")
        with self.assertRaises(ValueError, msg="v1 has no priority setting here"):
            sonyhp.setting_requests(sonyhp.initial_state(), "priority", "connection")

    def test_touch_panel_off_asks_for_alerts_first(self):
        self.assertEqual(self.payloads("touch-sensor", "off"), [(0x0C, "94 00 00"), (0x0C, "d8 d1 00 01")])
        self.assertEqual(self.payloads("touch-sensor", "on"), [(0x0C, "d8 d1 00 00")])

    def test_what_the_device_did_not_list_is_refused(self):
        for key, value in (("auto-power-off", "30-min"), ("stc-focus-on-voice", "on"),
                           ("nc", "wind-noise-reduction")):
            with self.assertRaises(ValueError, msg=key):
                sonyhp.setting_requests(self.state, key, value)
        bare = v2_state(functions={"table1": [], "table2": []}, touch_slot=None, features=[])
        for key, value in (("eq", "vocal"), ("dsee", "on"), ("touch-sensor", "off"), ("nc", "off")):
            with self.assertRaises(ValueError, msg=key):
                sonyhp.setting_requests(bare, key, value)

    def test_poll_and_power_off(self):
        self.assertEqual([p.hex(" ") for _, p in sonyhp.battery_requests(self.state)], ["22 00"])
        self.assertEqual(sonyhp.power_off_request(self.state)[1].hex(" "), "24 03 01")
        self.assertEqual(sonyhp.power_off_request(sonyhp.initial_state())[1].hex(" "), "22 00")

    def test_refresh_only_asks_for_what_is_listed(self):
        codes = [p[:2].hex(" ") for _, p in sonyhp.refresh_requests(self.state)]
        self.assertEqual(codes, ["04 02", "12 02", "66 17", "56 00", "e6 01", "e6 00", "f6 0c",
                                 "fa 0c", "f6 01", "26 05", "d6 d1", "46 01", "22 00"])
        bare = v2_state(functions={"table1": [], "table2": []}, touch_slot=None)
        self.assertEqual([p.hex(" ") for _, p in sonyhp.refresh_requests(bare)], ["04 02"])


class TestV2Alerts(unittest.TestCase):
    def test_only_touch_panel_alerts_are_answered(self):
        answer = sonyhp.alert_confirmation(sonyhp.MSG_COMMAND_1, bytes.fromhex("99 00 0b 01"))
        self.assertEqual(answer[1].hex(" "), "98 00 0b 01")
        for other in ("99 00 07 01", "99 01 0b 01", "99 00 0b", "69 17 01 01 00 01 0f"):
            self.assertIsNone(sonyhp.alert_confirmation(sonyhp.MSG_COMMAND_1, bytes.fromhex(other)), other)

    def test_an_alert_nobody_asked_for_goes_unanswered(self):
        link = sonyhp.Link("AA:BB:CC:DD:EE:FF")
        link.dispatch(sonyhp.MSG_COMMAND_1, bytes.fromhex("99 00 0b 01"))
        self.assertEqual(link._outbox, [])

    def test_a_drop_after_confirming_is_reported_as_reconnecting(self):
        link = sonyhp.DemoLink(model="WH-1000XM5")
        link.refresh()

        def drop(msg_type, payload, wait_ack=True):
            if payload[0] == sonyhp.ALERT_SET_PARAM:
                raise ConnectionResetError(104, "Connection reset by peer")
            return sonyhp.DemoLink.write(link, msg_type, payload, wait_ack)

        with mock.patch.object(link, "write", side_effect=drop):
            with self.assertRaises(sonyhp.Reconnecting):
                sonyhp.apply_setting(link, "touch-sensor", "off")
        self.assertFalse(link.confirm_alerts)

    def test_any_other_drop_is_still_an_error(self):
        link = sonyhp.DemoLink(model="WH-1000XM5")
        link.refresh()
        with mock.patch.object(link, "write", side_effect=ConnectionResetError(104, "reset")):
            with self.assertRaises(ConnectionResetError):
                sonyhp.apply_setting(link, "dsee", "on")


class TestV2DemoDevice(unittest.TestCase):
    """Round trips through the WH-1000XM5 stand-in."""

    def setUp(self):
        self.link = sonyhp.DemoLink(model="WH-1000XM5")
        self.link.refresh()

    def test_refresh_discovers_features_and_fills_in_the_state(self):
        state = self.link.state
        self.assertEqual(state["protocol"], 2)
        self.assertEqual(state["features"], ["auto-power-off", "battery", "connection-priority", "dsee",
                                             "equalizer", "pause-when-taken-off", "speak-to-chat",
                                             "touch-sensor", "voice-notifications"])
        for key in ("firmware", "codec", "battery", "nc_mode", "ambient_level", "eq_preset",
                    "dsee", "speak_to_chat", "stc_sensitivity", "pause_when_taken_off",
                    "auto_power_off", "touch_sensor", "voice_notifications"):
            self.assertIsNotNone(state[key], key)

    def test_modes_and_level(self):
        for mode in ("noise-cancelling", "off", "ambient-sound"):
            sonyhp.apply_setting(self.link, "nc", mode)
            self.assertEqual(self.link.state["nc_mode"], mode)
        sonyhp.apply_setting(self.link, "nc", "noise-cancelling")
        sonyhp.apply_setting(self.link, "ambient-level", 4)
        self.assertEqual((self.link.state["nc_mode"], self.link.state["ambient_level"]), ("ambient-sound", 4))

    def test_equalizer(self):
        sonyhp.apply_setting(self.link, "eq-bands", "3,-2,0,1,4,-1")
        self.assertEqual(self.link.state["eq_preset"], "manual")
        self.assertEqual(self.link.state["eq_bands"], [-2, 0, 1, 4, -1])
        sonyhp.apply_setting(self.link, "eq", "vocal")
        self.assertEqual(self.link.state["eq_preset"], "vocal")

    def test_every_toggle_flips_both_ways(self):
        for key, state_key in (("dsee", "dsee"), ("speak-to-chat", "speak_to_chat"),
                               ("pause-when-taken-off", "pause_when_taken_off"),
                               ("voice-notifications", "voice_notifications")):
            for value in (True, False, True):
                sonyhp.apply_setting(self.link, key, "on" if value else "off")
                self.assertIs(self.link.state[state_key], value, key)

    def test_speak_to_chat_config(self):
        sonyhp.apply_setting(self.link, "stc-sensitivity", "low")
        sonyhp.apply_setting(self.link, "stc-timeout", "off")
        self.assertEqual((self.link.state["stc_sensitivity"], self.link.state["stc_timeout"]), ("low", "off"))

    def test_priority_changes_the_codecs_on_offer(self):
        self.assertIn("LDAC", self.link.state["codecs"])
        sonyhp.apply_setting(self.link, "priority", "connection")
        self.assertEqual(self.link.state["priority"], "connection")
        self.assertNotIn("LDAC", self.link.state["codecs"])
        self.assertEqual(self.link.state["a2dp_codec"], "SBC-XQ")
        self.assertEqual(self.link.state["codec"], "SBC")
        sonyhp.apply_setting(self.link, "priority", "sound-quality")
        self.assertIn("LDAC", self.link.state["codecs"])

    def test_codec(self):
        sonyhp.apply_setting(self.link, "codec", "SBC-XQ")
        self.assertEqual(self.link.state["a2dp_codec"], "SBC-XQ")
        self.assertEqual(self.link.state["codec"], "SBC", "the headphones are asked, since they do not say")
        with self.assertRaises(ValueError):
            sonyhp.apply_setting(self.link, "codec", "aptX")

    def test_the_touch_panel_turns_off_once_confirmed(self):
        sonyhp.apply_setting(self.link, "touch-sensor", "off")
        self.assertIs(self.link.state["touch_sensor"], False)
        sonyhp.apply_setting(self.link, "touch-sensor", "on")
        self.assertIs(self.link.state["touch_sensor"], True)

    def test_without_the_confirmation_the_touch_panel_stays_on(self):
        self.link.write(*sonyhp.req(bytes.fromhex("d8 d1 00 01")))
        self.assertIs(self.link.state["touch_sensor"], True)


# -- The codec, via PipeWire ------------------------------------------------------

def pipewire_card(active="a2dp-sink", address="AC:80:0A:57:32:9D", **extra_profiles):
    profiles = {
        "off": {"description": "Off", "available": True},
        "a2dp-sink-sbc": {"description": "High Fidelity Playback (A2DP Sink, codec SBC)", "available": True},
        "a2dp-sink-sbc_xq": {"description": "High Fidelity Playback (A2DP Sink, codec SBC-XQ)", "available": True},
        "a2dp-sink": {"description": "High Fidelity Playback (A2DP Sink, codec LDAC)", "available": True},
        "headset-head-unit": {"description": "Headset Head Unit (HSP/HFP, codec MSBC)", "available": True},
    }
    profiles.update(extra_profiles)
    return {"name": "bluez_card." + address.replace(":", "_"), "active_profile": active,
            "properties": {"api.bluez5.address": address}, "profiles": profiles}


class TestPipeWireCodecs(unittest.TestCase):
    ADDRESS = "AC:80:0A:57:32:9D"

    def cards(self, *cards):
        return mock.patch.object(sonyhp, "pactl", return_value=json.dumps(list(cards)))

    def test_codecs_come_from_the_playback_profiles_best_first(self):
        with self.cards(pipewire_card()):
            self.assertEqual(sonyhp.audio_codecs(self.ADDRESS), {"codecs": ["LDAC", "SBC-XQ", "SBC"], "a2dp_codec": "LDAC"})

    def test_the_card_is_matched_by_address(self):
        with self.cards(pipewire_card(address="2C:FD:B4:49:3D:DF")):
            self.assertEqual(sonyhp.audio_codecs(self.ADDRESS), {"codecs": [], "a2dp_codec": None})

    def test_unavailable_and_headset_profiles_are_left_out(self):
        card = pipewire_card(**{"a2dp-sink-aac": {"description": "(A2DP Sink, codec AAC)", "available": False}})
        with self.cards(card):
            self.assertNotIn("AAC", sonyhp.audio_codecs(self.ADDRESS)["codecs"])
            self.assertNotIn("MSBC", sonyhp.audio_codecs(self.ADDRESS)["codecs"])

    def test_anything_unreadable_means_no_codecs(self):
        for output in (None, "", "not json", "{}", "[1, 2]"):
            with mock.patch.object(sonyhp, "pactl", return_value=output):
                self.assertEqual(sonyhp.audio_codecs(self.ADDRESS)["codecs"], [], output)

    def test_switching_picks_the_profile_by_codec(self):
        calls = []
        cards = [pipewire_card(), pipewire_card(active="a2dp-sink-sbc_xq")]

        def pactl(*args, **kwargs):
            calls.append(args)
            if args[0] == "set-card-profile":
                return ""
            return json.dumps([cards.pop(0) if len(cards) > 1 else cards[0]])

        with mock.patch.object(sonyhp, "pactl", side_effect=pactl):
            sonyhp.switch_codec(self.ADDRESS, "SBC-XQ", sleep=lambda _: None)
        self.assertIn(("set-card-profile", "bluez_card.AC_80_0A_57_32_9D", "a2dp-sink-sbc_xq"), calls)

    def test_a_switch_that_does_not_take_is_an_error(self):
        # PipeWire accepts the request and then fails it in its log, as it does
        # when another device already holds the codec's endpoint.
        clock = FakeClock()
        with mock.patch.object(sonyhp, "pactl", side_effect=lambda *a, **k: "" if a[0] == "set-card-profile"
                               else json.dumps([pipewire_card()])):
            with self.assertRaises(ValueError):
                sonyhp.switch_codec(self.ADDRESS, "SBC", clock=clock,
                                    sleep=lambda seconds: setattr(clock, "now", clock.now + seconds))

    def test_a_codec_not_on_offer_is_refused_without_asking_pipewire(self):
        with self.cards(pipewire_card()) as pactl:
            with self.assertRaises(ValueError):
                sonyhp.switch_codec(self.ADDRESS, "aptX")
        self.assertTrue(all(call.args[0] != "set-card-profile" for call in pactl.call_args_list))

    def test_pactl_runs_by_absolute_path_with_a_minimal_environment(self):
        with mock.patch.object(sonyhp, "PACTL", "/usr/bin/pactl"), \
                mock.patch.object(sonyhp.subprocess, "run") as run:
            run.return_value = mock.Mock(stdout="[]", returncode=0)
            sonyhp.pactl("list", "cards")
        argv, kwargs = run.call_args
        self.assertEqual(argv[0][0], "/usr/bin/pactl")
        self.assertLessEqual(set(kwargs["env"]), {"PATH", "LC_ALL", "XDG_RUNTIME_DIR"})


class TestPrioritySettle(unittest.TestCase):
    """What happens on the computer's side after the priority changes."""

    def run_settle(self, codec_reads, codecs_before=("LDAC", "SBC-XQ", "SBC")):
        link = sonyhp.Link("AC:80:0A:57:32:9D")
        link.state.update(protocol=2)
        reads = list(codec_reads)
        link.read_audio_codecs = lambda: link.state.update(codecs=reads.pop(0) if len(reads) > 1 else reads[0])
        link.pump = lambda timeout: clock.update(now=clock["now"] + timeout)
        link.request = lambda requests, settle=0.25: None
        clock = {"now": 0.0}
        with mock.patch.object(sonyhp.time, "monotonic", side_effect=lambda: clock["now"]), \
                mock.patch.object(sonyhp, "reconnect_audio") as reconnect:
            sonyhp.settle_priority(link, list(codecs_before))
        return reconnect, link.state["codecs"]

    def test_headphones_that_renegotiate_are_left_to_it(self):
        reconnect, codecs = self.run_settle([[], [], [], ["SBC"]])
        reconnect.assert_not_called()
        self.assertEqual(codecs, ["SBC"])

    def test_headphones_that_do_not_get_the_audio_profile_reconnected(self):
        stale = ["SBC-XQ", "SBC"]
        reconnect, codecs = self.run_settle([stale] * 8 + [["LDAC", "SBC-XQ", "SBC"]], codecs_before=stale)
        reconnect.assert_called_once()
        self.assertEqual(codecs, ["LDAC", "SBC-XQ", "SBC"])

    def test_without_audio_there_is_nothing_to_wait_for(self):
        reconnect, codecs = self.run_settle([[]], codecs_before=())
        reconnect.assert_not_called()
        self.assertEqual(codecs, [])


class TestRuntimeDirectory(unittest.TestCase):
    """The socket's parent has to be a directory only we can write.

    Anything that can reach the socket can drive the headphones, so these
    check the gate rather than the protocol.
    """

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def make(self, name, mode):
        path = os.path.join(self.tmp, name)
        os.mkdir(path, mode)
        os.chmod(path, mode)  # mkdir's mode is filtered through the umask
        return path

    def test_accepts_a_private_directory(self):
        self.assertTrue(sonyhp.is_private_dir(self.make("good", 0o700)))

    def test_rejects_group_or_world_access(self):
        for mode in (0o750, 0o770, 0o755, 0o777, 0o701):
            with self.subTest(mode=oct(mode)):
                self.assertFalse(sonyhp.is_private_dir(self.make(f"m{mode:o}", mode)))

    def test_rejects_a_symlink_even_to_a_private_directory(self):
        target = self.make("target", 0o700)
        link = os.path.join(self.tmp, "link")
        os.symlink(target, link)
        self.assertFalse(sonyhp.is_private_dir(link))

    def test_rejects_a_file_and_a_missing_path(self):
        regular = os.path.join(self.tmp, "file")
        open(regular, "w").close()
        self.assertFalse(sonyhp.is_private_dir(regular))
        self.assertFalse(sonyhp.is_private_dir(os.path.join(self.tmp, "nope")))

    def test_uses_a_valid_xdg_runtime_dir(self):
        good = self.make("xdg", 0o700)
        with mock.patch.dict(os.environ, {"XDG_RUNTIME_DIR": good}):
            self.assertEqual(sonyhp.runtime_dir(), good)

    def temp_root(self):
        """Point the fallback at our sandbox.

        gettempdir() caches its answer on first use, so setting TMPDIR in the
        environment would steer nothing; the lookup itself is what has to move.
        """
        return mock.patch.object(sonyhp.tempfile, "gettempdir", return_value=self.tmp)

    def test_falls_back_when_xdg_runtime_dir_is_not_private(self):
        loose = self.make("loose", 0o777)
        with self.temp_root(), mock.patch.dict(os.environ, {"XDG_RUNTIME_DIR": loose}):
            fallback = sonyhp.runtime_dir()
        self.assertEqual(os.path.dirname(fallback), self.tmp)
        self.assertNotEqual(fallback, loose)
        self.assertTrue(sonyhp.is_private_dir(fallback))
        self.assertEqual(stat.S_IMODE(os.lstat(fallback).st_mode), 0o700)

    def test_the_fallback_is_private_even_under_a_loose_umask(self):
        previous = os.umask(0o000)
        self.addCleanup(os.umask, previous)
        with self.temp_root(), mock.patch.dict(os.environ, {}, clear=True):
            fallback = sonyhp.runtime_dir()
        self.assertEqual(stat.S_IMODE(os.lstat(fallback).st_mode), 0o700)

    def test_the_fallback_is_reused_once_made(self):
        with self.temp_root(), mock.patch.dict(os.environ, {}, clear=True):
            self.assertEqual(sonyhp.runtime_dir(), sonyhp.runtime_dir())

    def test_refuses_a_fallback_someone_else_left_loose(self):
        # The old code would have used this directory exactly as it found it.
        squatted = os.path.join(self.tmp, f"omarchy-sony-headphones-{os.getuid()}")
        os.mkdir(squatted, 0o777)
        os.chmod(squatted, 0o777)
        with self.temp_root(), mock.patch.dict(os.environ, {}, clear=True):
            with self.assertRaises(SystemExit):
                sonyhp.runtime_dir()

    def test_refuses_a_fallback_that_is_a_symlink(self):
        target = self.make("target", 0o700)
        os.symlink(target, os.path.join(self.tmp, f"omarchy-sony-headphones-{os.getuid()}"))
        with self.temp_root(), mock.patch.dict(os.environ, {}, clear=True):
            with self.assertRaises(SystemExit):
                sonyhp.runtime_dir()


class TestDaemonFiles(unittest.TestCase):
    """The lock and the socket, opened inside a directory we trust."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        os.chmod(self.tmp, 0o700)
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.dir_fd = os.open(self.tmp, os.O_RDONLY | os.O_DIRECTORY)
        self.addCleanup(os.close, self.dir_fd)
        self.daemon = sonyhp.Daemon()

    def test_claims_and_then_refuses_a_second_claim(self):
        lock = self.daemon.claim_lock(self.dir_fd)
        self.addCleanup(lock.close)
        with self.assertRaises(SystemExit):
            sonyhp.Daemon().claim_lock(self.dir_fd)

    def test_the_lock_is_not_truncated_on_open(self):
        path = os.path.join(self.tmp, sonyhp.LOCK_NAME)
        with open(path, "w") as handle:
            handle.write("kept")
        lock = self.daemon.claim_lock(self.dir_fd)
        self.addCleanup(lock.close)
        with open(path) as handle:
            self.assertEqual(handle.read(), "kept")

    def test_refuses_a_lock_that_is_a_symlink(self):
        elsewhere = os.path.join(self.tmp, "elsewhere")
        open(elsewhere, "w").close()
        os.symlink(elsewhere, os.path.join(self.tmp, sonyhp.LOCK_NAME))
        with self.assertRaises(OSError):  # O_NOFOLLOW
            self.daemon.claim_lock(self.dir_fd)

    def test_removes_a_socket_we_own(self):
        path = os.path.join(self.tmp, sonyhp.SOCKET_NAME)
        stale = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.addCleanup(stale.close)
        stale.bind(path)
        self.daemon.remove_stale_socket(self.dir_fd)
        self.assertFalse(os.path.lexists(path))

    def test_a_missing_socket_is_not_an_error(self):
        self.daemon.remove_stale_socket(self.dir_fd)

    def test_refuses_to_remove_anything_that_is_not_a_socket(self):
        path = os.path.join(self.tmp, sonyhp.SOCKET_NAME)
        open(path, "w").close()
        with self.assertRaises(SystemExit):
            self.daemon.remove_stale_socket(self.dir_fd)
        self.assertTrue(os.path.lexists(path))

    def test_refuses_to_remove_a_symlink_standing_in_for_the_socket(self):
        victim = os.path.join(self.tmp, "victim")
        open(victim, "w").close()
        os.symlink(victim, os.path.join(self.tmp, sonyhp.SOCKET_NAME))
        with self.assertRaises(SystemExit):
            self.daemon.remove_stale_socket(self.dir_fd)
        self.assertTrue(os.path.exists(victim))


class TestChannelCache(unittest.TestCase):
    """The cached channel decides where the next connection is dialled."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.cache = os.path.join(self.tmp, "cache")
        self.patch = mock.patch.object(sonyhp, "CACHE_DIR", self.cache)
        self.patch.start()
        self.addCleanup(self.patch.stop)
        self.address = "AA:BB:CC:DD:EE:FF"

    def test_creates_the_cache_private(self):
        self.assertEqual(sonyhp.private_cache_dir(), self.cache)
        self.assertEqual(stat.S_IMODE(os.lstat(self.cache).st_mode), 0o700)

    def test_tightens_a_directory_left_loose(self):
        os.mkdir(self.cache, 0o755)
        os.chmod(self.cache, 0o755)
        self.assertEqual(sonyhp.private_cache_dir(), self.cache)
        self.assertEqual(stat.S_IMODE(os.lstat(self.cache).st_mode), 0o700)

    def test_declines_a_symlink(self):
        target = os.path.join(self.tmp, "target")
        os.mkdir(target, 0o700)
        os.symlink(target, self.cache)
        self.assertIsNone(sonyhp.private_cache_dir())

    def test_declines_a_file(self):
        open(self.cache, "w").close()
        self.assertIsNone(sonyhp.private_cache_dir())

    def test_round_trips_a_channel(self):
        sonyhp.remember_channel(self.address, 9)
        self.assertEqual(sonyhp.cached_channel(self.address), 9)
        path = sonyhp._channel_cache_path(self.address)
        self.assertEqual(stat.S_IMODE(os.lstat(path).st_mode), 0o600)

    def test_forget_removes_it(self):
        sonyhp.remember_channel(self.address, 9)
        sonyhp.forget_channel(self.address)
        self.assertIsNone(sonyhp.cached_channel(self.address))

    def test_does_not_write_through_a_symlink(self):
        os.mkdir(self.cache, 0o700)
        victim = os.path.join(self.tmp, "victim")
        with open(victim, "w") as handle:
            handle.write("untouched")
        os.symlink(victim, sonyhp._channel_cache_path(self.address))
        sonyhp.remember_channel(self.address, 9)  # swallowed, not followed
        with open(victim) as handle:
            self.assertEqual(handle.read(), "untouched")

    def test_does_not_read_through_a_symlink(self):
        os.mkdir(self.cache, 0o700)
        planted = os.path.join(self.tmp, "planted")
        with open(planted, "w") as handle:
            handle.write("7")
        os.symlink(planted, sonyhp._channel_cache_path(self.address))
        self.assertIsNone(sonyhp.cached_channel(self.address))

    def test_rejects_a_channel_outside_the_valid_range(self):
        os.mkdir(self.cache, 0o700)
        for written in ("0", "31", "-1", "nonsense", ""):
            with self.subTest(written=written):
                with open(sonyhp._channel_cache_path(self.address), "w") as handle:
                    handle.write(written)
                self.assertIsNone(sonyhp.cached_channel(self.address))


# -- SDP ---------------------------------------------------------------------

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


class TestSdpExchange(unittest.TestCase):
    def query(self, responses, **kwargs):
        clock = kwargs.pop("clock", FakeClock())
        sock = FakeSdpSocket(responses, clock=clock, recv_cost=kwargs.pop("recv_cost", 0.0))
        return sonyhp.sdp_query(sock, sonyhp.SERVICE_UUID_BYTES, kwargs.pop("timeout", 8.0), clock=clock), sock

    def test_a_single_round_record(self):
        record, sock = self.query([sdp_response(1, RECORD)])
        self.assertEqual(record, RECORD)
        self.assertEqual(sonyhp._find_rfcomm_channel(sonyhp.parse_sdp_record(record)), 9)
        self.assertEqual(len(sock.sent), 1)

    def test_a_record_split_across_a_continuation(self):
        state = bytes([2, 0xAB, 0xCD])
        record, sock = self.query([
            sdp_response(1, RECORD[:10], state),
            sdp_response(2, RECORD[10:]),
        ])
        self.assertEqual(record, RECORD)
        self.assertTrue(sock.sent[1].endswith(state), "the continuation is sent back verbatim")
        self.assertEqual(struct.unpack(">H", sock.sent[1][1:3])[0], 2)

    def test_a_missing_continuation_byte_still_ends_the_exchange(self):
        packet = sdp_response(1, RECORD, continuation=b"")
        record, _ = self.query([packet])
        self.assertEqual(record, RECORD)

    def test_endless_continuations_stop_at_the_round_limit(self):
        responses = [endless_continuations] * (sonyhp.SDP_MAX_ROUNDS + 5)
        sock = FakeSdpSocket(responses)
        with self.assertRaises(sonyhp.SdpError):
            sonyhp.sdp_query(sock, sonyhp.SERVICE_UUID_BYTES, 8.0, clock=FakeClock())
        self.assertEqual(len(sock.sent), sonyhp.SDP_MAX_ROUNDS)

    def test_a_repeated_continuation_state_is_rejected(self):
        state = bytes([1, 0x42])
        with self.assertRaisesRegex(sonyhp.SdpError, "repeated"):
            self.query([sdp_response(1, b"\x00", state), sdp_response(2, b"\x00", state)])

    def test_a_continuation_without_data_is_rejected(self):
        with self.assertRaisesRegex(sonyhp.SdpError, "without any data"):
            self.query([sdp_response(1, b"", bytes([1, 0x42]))])

    def test_the_aggregate_record_size_is_capped(self):
        # 1500 per round crosses the aggregate cap on round six, before the
        # round limit would; each packet still fits one read.
        piece = b"\x00" * 1500
        responses = [
            (lambda n: sdp_response(n, piece, bytes([1, n])))(n)
            for n in range(1, sonyhp.SDP_MAX_ROUNDS + 1)
        ]
        with self.assertRaisesRegex(sonyhp.SdpError, "larger"):
            _, sock = self.query(responses)
        self.assertLess(sonyhp.SDP_MAX_RECORD_BYTES // 1500 + 1, sonyhp.SDP_MAX_ROUNDS)

    def test_a_malformed_continuation_state_is_rejected(self):
        cases = {
            "too long": bytes([17]) + bytes(17),
            "truncated": bytes([4, 0x01, 0x02]),
            "trailing": bytes([1, 0x01, 0x02]),
        }
        for name, state in cases.items():
            with self.subTest(name):
                with self.assertRaisesRegex(sonyhp.SdpError, "continuation state is malformed"):
                    self.query([sdp_response(1, b"\x00", state)])

    def test_the_whole_exchange_has_a_deadline(self):
        # Each answer arrives just inside any per-read timeout; the total does not.
        clock = FakeClock()
        sock = FakeSdpSocket([endless_continuations] * sonyhp.SDP_MAX_ROUNDS, clock=clock, recv_cost=3.0)
        with self.assertRaisesRegex(sonyhp.SdpError, "in time"):
            sonyhp.sdp_query(sock, sonyhp.SERVICE_UUID_BYTES, 8.0, clock=clock)
        self.assertLess(len(sock.sent), sonyhp.SDP_MAX_ROUNDS)
        self.assertTrue(all(0 < t <= 8.0 for t in sock.timeouts))
        self.assertLessEqual(sock.timeouts[-1], 2.0, "later reads get only what is left")

    def test_an_already_spent_budget_sends_nothing(self):
        sock = FakeSdpSocket([])
        with self.assertRaises(sonyhp.SdpError):
            sonyhp.sdp_query(sock, sonyhp.SERVICE_UUID_BYTES, 0.0, clock=FakeClock())
        self.assertEqual(sock.sent, [])

    def test_malformed_responses_are_rejected(self):
        cases = {
            "short header": b"\x07\x00",
            "wrong pdu": sdp_response(1, RECORD, pdu=0x01),
            "wrong transaction": sdp_response(7, RECORD),
            "declared longer than sent": sdp_response(1, RECORD, declared=500),
            "count past the end": struct.pack(">BHH", 0x07, 1, 3) + b"\x00\x40\x00",
        }
        for name, packet in cases.items():
            with self.subTest(name):
                with self.assertRaises(sonyhp.SdpError):
                    self.query([packet])


class TestSdpRecordParsing(unittest.TestCase):
    def test_deep_nesting_is_an_sdp_error_not_a_crash(self):
        record = b""
        for _ in range(sonyhp.SDP_MAX_DEPTH + 2):
            record = bytes([0x36]) + len(record).to_bytes(2, "big") + record
        with self.assertRaisesRegex(sonyhp.SdpError, "nested"):
            sonyhp.parse_sdp_record(record)

    def test_depth_that_stack_would_not_survive(self):
        record = b""
        for _ in range(3000):
            record = bytes([0x37]) + len(record).to_bytes(4, "big") + record
        with self.assertRaises(sonyhp.SdpError):
            sonyhp.parse_sdp_record(record)

    def test_an_element_longer_than_the_record(self):
        with self.assertRaisesRegex(sonyhp.SdpError, "past the end"):
            sonyhp.parse_sdp_record(bytes([0x35, 0x40, 0x08, 0x01]))

    def test_a_child_that_overruns_its_sequence(self):
        # The outer sequence claims 2 bytes; its child needs 3.
        record = bytes([0x35, 0x02]) + de_uint16(0x0004) + b"\x00"
        with self.assertRaisesRegex(sonyhp.SdpError, "sequence"):
            sonyhp.parse_sdp_record(record)

    def test_trailing_bytes_are_ignored_as_before(self):
        tree = sonyhp.parse_sdp_record(RECORD + b"\x00\x00")
        self.assertEqual(sonyhp._find_rfcomm_channel(tree), 9)


class TestSdpChannel(unittest.TestCase):
    """sdp_channel turns every refusal into None, never an exception."""

    def run_channel(self, responses):
        sock = FakeSdpSocket(responses)
        sock.connect = lambda address: None
        sock.close = lambda: None
        with mock.patch.object(sonyhp.socket, "AF_BLUETOOTH", 31, create=True), \
                mock.patch.object(sonyhp.socket, "BTPROTO_L2CAP", 0, create=True), \
                mock.patch.object(sonyhp.socket, "socket", return_value=sock):
            return sonyhp.sdp_channel("AA:BB:CC:DD:EE:FF")

    def test_finds_the_channel(self):
        self.assertEqual(self.run_channel([sdp_response(1, RECORD)]), 9)

    def test_a_misbehaving_device_yields_none(self):
        self.assertIsNone(self.run_channel([endless_continuations] * sonyhp.SDP_MAX_ROUNDS))

    def test_a_hostile_record_yields_none(self):
        record = b""
        for _ in range(3000):
            record = bytes([0x37]) + len(record).to_bytes(4, "big") + record
        record = record[:sonyhp.SDP_MAX_RECORD_BYTES]
        self.assertIsNone(self.run_channel([sdp_response(1, record)]))


# -- RFCOMM and the local socket -----------------------------------------------

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


class TestLocalSocketBounds(unittest.TestCase):
    def test_a_trickling_client_is_cut_off_by_the_deadline(self):
        daemon = sonyhp.Daemon()
        daemon.REQUEST_TIMEOUT = 0.1
        client = FakeStream(b"{", delay=0.02)
        listener = mock.Mock()
        listener.accept.return_value = (client, None)
        started = time.monotonic()
        daemon.accept(listener)
        self.assertLess(time.monotonic() - started, 1.0)
        self.assertTrue(client.closed)
        self.assertLess(client.calls, 20)

    def test_ask_daemon_gives_up_on_a_reply_that_never_ends(self):
        endless = FakeStream(b"a" * 4096)
        with mock.patch.object(sonyhp, "daemon_socket", return_value=endless):
            self.assertIsNone(sonyhp.ask_daemon({"cmd": "status"}))
        self.assertLessEqual(endless.calls, sonyhp.MAX_LINE_BYTES // 4096 + 2)
        self.assertTrue(endless.closed)

    def test_ask_daemon_still_reads_a_normal_reply(self):
        reply = FakeStream(b'{"ok": true}\n')
        with mock.patch.object(sonyhp, "daemon_socket", return_value=reply):
            self.assertEqual(sonyhp.ask_daemon({"cmd": "status"}), {"ok": True})


class TestBluetoothctlInvocation(unittest.TestCase):
    def test_candidates_are_absolute(self):
        for candidate in sonyhp.BLUETOOTHCTL_CANDIDATES:
            self.assertTrue(os.path.isabs(candidate), candidate)

    def test_the_environment_does_not_carry_anything_inherited(self):
        self.assertEqual(set(sonyhp.BLUETOOTHCTL_ENV), {"PATH", "LC_ALL"})
        self.assertEqual(sonyhp.BLUETOOTHCTL_ENV["LC_ALL"], "C")
        for entry in sonyhp.BLUETOOTHCTL_ENV["PATH"].split(":"):
            self.assertTrue(os.path.isabs(entry), entry)

    def test_a_missing_binary_is_not_looked_up_on_path(self):
        with mock.patch.object(sonyhp, "BLUETOOTHCTL", None):
            with mock.patch.object(sonyhp.subprocess, "run") as run:
                self.assertEqual(sonyhp.bluetoothctl("devices"), "")
                run.assert_not_called()

    def test_the_absolute_binary_is_what_runs(self):
        with mock.patch.object(sonyhp, "BLUETOOTHCTL", "/usr/bin/bluetoothctl"):
            with mock.patch.object(sonyhp.subprocess, "run") as run:
                run.return_value = mock.Mock(stdout="")
                sonyhp.bluetoothctl("info", "AA:BB:CC:DD:EE:FF")
        argv, kwargs = run.call_args
        self.assertEqual(argv[0][0], "/usr/bin/bluetoothctl")
        self.assertEqual(kwargs["env"], sonyhp.BLUETOOTHCTL_ENV)

    def test_only_names_bluetoothctl_installs_under(self):
        for candidate in sonyhp.BLUETOOTHCTL_CANDIDATES:
            self.assertEqual(os.path.basename(candidate), "bluetoothctl")


class TestShellLaunch(unittest.TestCase):
    """The QML side is what starts the helper, so it has to hold the same line.

    There is no QML engine here, so these read Service.qml as text: enough to
    catch the interpreter going back to a bare name or a Process losing its
    minimal environment.
    """

    SERVICE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "Service.qml")

    def setUp(self):
        with open(self.SERVICE, encoding="utf-8") as handle:
            self.source = handle.read()

    def test_the_interpreter_is_bound_by_absolute_path(self):
        self.assertIn('readonly property string interpreter: "/usr/bin/python3"', self.source)
        self.assertIn('[interpreter, "-I", helperPath]', self.source)
        self.assertNotIn('"python3"', self.source)

    def test_every_process_runs_with_the_minimal_environment(self):
        processes = self.source.count("Process {")
        self.assertEqual(processes, 3)
        self.assertEqual(self.source.count("clearEnvironment: true"), processes)
        self.assertEqual(self.source.count("environment: root.helperEnvironment"), processes)

    def test_the_environment_carries_only_what_the_helper_reads(self):
        self.assertIn('var env = { PATH: "/usr/bin:/bin" }', self.source)
        self.assertIn('["HOME", "XDG_RUNTIME_DIR", "XDG_CACHE_HOME", "SONY_HEADPHONES_DEMO"]', self.source)

    def test_the_helper_itself_names_the_system_interpreter(self):
        with open(HELPER, encoding="utf-8") as handle:
            self.assertEqual(handle.readline().strip(), "#!/usr/bin/python3")


if __name__ == "__main__":
    unittest.main(verbosity=2)
