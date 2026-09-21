"""Protocol v2: requests, replies, settings, features and short frames.

The v2 command set reuses opcodes across features and tells them apart by
subtype, so the layout of each builder, the parser's subtype dispatch and
the XM6's feature set are all pinned here.
"""

import unittest

import support
from support import build_v2_device_list

sonyhp = support.sonyhp


class TestV2Requests(unittest.TestCase):
    def test_noise_cancelling_uses_the_noise_adaptation_shape(self):
        _, payload = sonyhp.v2_asc_request("noise-cancelling", 0, False)
        self.assertEqual(list(payload), [0x68, 0x19, 0x01, 0x01, 0x00, 0x00, 0x00, 0x00, 0x00])

    def test_ambient_carries_level_and_voice_focus(self):
        _, payload = sonyhp.v2_asc_request("ambient-sound", 10, True)
        self.assertEqual(list(payload), [0x68, 0x19, 0x01, 0x01, 0x01, 0x01, 0x0A, 0x00, 0x00])

    def test_off(self):
        _, payload = sonyhp.v2_asc_request("off", 5, False)
        self.assertEqual(list(payload), [0x68, 0x19, 0x01, 0x00, 0x00, 0x00, 0x05, 0x00, 0x00])

    def test_the_noise_adaptation_fields_are_preserved(self):
        _, payload = sonyhp.v2_asc_request("noise-cancelling", 0, False,
                                           auto_ambient=0x01, noise_adapt_sensitivity=0x02)
        self.assertEqual(list(payload[7:9]), [0x01, 0x02])

    def test_the_older_seven_byte_shape_is_still_built(self):
        _, payload = sonyhp.v2_asc_request("ambient-sound", 10, True, subtype=0x17)
        self.assertEqual(list(payload), [0x68, 0x17, 0x01, 0x01, 0x01, 0x01, 0x0A])

    def test_the_answered_subtype_is_echoed_back(self):
        _, payload = sonyhp.v2_asc_request("noise-cancelling", 0, False, subtype=0x15)
        self.assertEqual(payload[1], 0x15)

    def test_wind_is_refused_without_the_wind_subtype(self):
        with self.assertRaises(ValueError):
            sonyhp.v2_asc_request("wind-noise-reduction", 0, False, subtype=0x19)

    def test_level_is_clamped(self):
        _, payload = sonyhp.v2_asc_request("ambient-sound", 99, False)
        self.assertEqual(payload[6], sonyhp.MAX_AMBIENT_LEVEL)

    def test_ambient_at_zero_is_raised_to_one(self):
        _, payload = sonyhp.v2_asc_request("ambient-sound", 0, False)
        self.assertEqual(payload[6], 1)

    def test_equalizer_preset(self):
        _, payload = sonyhp.v2_eq_preset_request("heavy")
        self.assertEqual(list(payload), [0x58, 0x04, 0x30, 0x00])

    def test_equalizer_bands_use_the_ten_band_custom_layout(self):
        _, payload = sonyhp.v2_eq_bands_request([0, 1, -1, 0, 0, 0, 0, 0, 0, 0])
        self.assertEqual(list(payload), [0x58, 0x04, 0xA0, 0x0A, 6, 7, 5, 6, 6, 6, 6, 6, 6, 6])

    def test_equalizer_needs_ten_bands(self):
        with self.assertRaises(ValueError):
            sonyhp.v2_eq_bands_request([0, 1, 2])

    def test_equalizer_rejects_out_of_range_bands(self):
        with self.assertRaises(ValueError):
            sonyhp.v2_eq_bands_request([0, 0, 0, 0, 0, 0, 0, 0, 0, 7])

    def test_speak_to_chat_is_inverted(self):
        _, on = sonyhp.v2_stc_enabled_request(True)
        _, off = sonyhp.v2_stc_enabled_request(False)
        self.assertEqual(list(on), [0xF8, 0x0C, 0x00, 0x01])
        self.assertEqual(list(off), [0xF8, 0x0C, 0x01, 0x01])

    def test_pause_when_taken_off_is_inverted(self):
        _, on = sonyhp.v2_bool_request(sonyhp.V2_SYSTEM_SET, sonyhp.V2_SYSTEM_SUB_PAUSE_WHEN_TAKEN_OFF, True)
        self.assertEqual(list(on), [0xF8, 0x01, 0x00])

    def test_auto_power_off(self):
        _, payload = sonyhp.v2_auto_power_off_request("off")
        self.assertEqual(list(payload), [0x28, 0x05, 0x11, 0x00])

    def test_auto_power_off_rejects_a_timer(self):
        with self.assertRaises(ValueError):
            sonyhp.v2_auto_power_off_request("3-hour")

    def test_speak_to_chat_config(self):
        _, payload = sonyhp.v2_stc_config_request("low", "off")
        self.assertEqual(list(payload), [0xFC, 0x0C, 0x02, 0x03])

    def test_refresh_asks_ambient_under_the_chosen_subtype(self):
        requests = sonyhp.v2_refresh_requests(subtype=0x19)
        opcodes = [payload[0] for _, payload in requests]
        self.assertIn(sonyhp.V2_BATTERY_GET, opcodes)
        self.assertIn(sonyhp.V2_EQ_GET, opcodes)
        ambient = [payload for _, payload in requests if payload[0] == sonyhp.V2_ASC_GET]
        self.assertEqual(ambient, [bytes([sonyhp.V2_ASC_GET, 0x19])])

    def test_codec_get(self):
        _, payload = sonyhp.v2_audio_codec_get()
        self.assertEqual(list(payload), [0x12, 0x02])

    def test_dsee_request_is_not_inverted(self):
        _, on = sonyhp.v2_dsee_request(True)
        _, off = sonyhp.v2_dsee_request(False)
        self.assertEqual(list(on), [0xE8, 0x01, 0x01])
        self.assertEqual(list(off), [0xE8, 0x01, 0x00])

    def test_connection_quality_request_is_inverted_on_the_wire(self):
        _, sound = sonyhp.v2_connection_quality_request("sound-quality")
        _, stable = sonyhp.v2_connection_quality_request("stable")
        self.assertEqual(list(sound), [0xE8, 0x02, 0x00])
        self.assertEqual(list(stable), [0xE8, 0x02, 0x01])

    def test_cinema_get_and_request(self):
        _, get = sonyhp.v2_cinema_get()
        _, on = sonyhp.v2_cinema_request(True)
        _, off = sonyhp.v2_cinema_request(False)
        self.assertEqual(list(get), [0xE6, 0x04])
        self.assertEqual(list(on), [0xE8, 0x04, 0x00])
        self.assertEqual(list(off), [0xE8, 0x04, 0x01])

    def test_bgm_get_and_request(self):
        _, get = sonyhp.v2_bgm_get()
        _, cafe = sonyhp.v2_bgm_request(True, "cafe")
        _, my_room_alt = sonyhp.v2_bgm_request(False, "my-room", 0x03)
        self.assertEqual(list(get), [0xE6, 0x09])
        self.assertEqual(list(cafe), [0xE8, 0x09, 0x00, 0x02])
        self.assertEqual(list(my_room_alt), [0xE8, 0x03, 0x01, 0x00])

    def test_bgm_request_rejects_an_unknown_room(self):
        with self.assertRaises(ValueError):
            sonyhp.v2_bgm_request(True, "ballroom")

    def test_refresh_asks_the_equalizer_under_the_chosen_subtype(self):
        requests = sonyhp.v2_refresh_requests(eq_subtype=0x00)
        equalizer = [payload for _, payload in requests if payload[0] == sonyhp.V2_EQ_GET]
        self.assertEqual(equalizer, [bytes([sonyhp.V2_EQ_GET, 0x00])])

    def test_device_list_get(self):
        self.assertEqual(sonyhp.v2_device_list_get(), (sonyhp.MSG_COMMAND_2, b"\x36\x02"))

    def test_source_switch_request(self):
        self.assertEqual(
            sonyhp.v2_source_switch_request("AA:BB:CC:DD:EE:FF"),
            (sonyhp.MSG_COMMAND_2, b"\x3c\x01" + b"AA:BB:CC:DD:EE:FF"),
        )

    def test_source_switch_request_rejects_a_malformed_mac(self):
        for address in ("AA:BB:CC:DD:EE", "not-an-address", "AA:BB:CC:DD:EE:GG"):
            with self.subTest(address=address):
                with self.assertRaises(ValueError):
                    sonyhp.v2_source_switch_request(address)


class TestV2Replies(unittest.TestCase):
    def setUp(self):
        self.state = sonyhp.initial_state()

    def apply(self, payload, msg_type=None):
        return sonyhp.apply_payload_v2(self.state, msg_type or sonyhp.MSG_COMMAND_1, bytes(payload))

    def test_battery(self):
        self.assertTrue(self.apply([0x23, 0x00, 0x52, 0x01]))
        self.assertEqual(self.state["battery"], 82)
        self.assertIs(self.state["charging"], True)

    def test_firmware(self):
        self.apply([0x05, 0x02, 0x05] + list(b"2.5.0"))
        self.assertEqual(self.state["firmware"], "2.5.0")

    def test_codec_reply_and_notification(self):
        self.apply([0x13, 0x02, 0x10])
        self.assertEqual(self.state["codec"], "LDAC")
        self.apply([0x15, 0x02, 0x02])
        self.assertEqual(self.state["codec"], "AAC")

    def test_dsee(self):
        self.apply([0xE7, 0x01, 0x01])
        self.assertIs(self.state["dsee"], True)
        self.apply([0xE7, 0x01, 0x00])
        self.assertIs(self.state["dsee"], False)

    def test_connection_quality(self):
        self.apply([0xE7, 0x02, 0x00])
        self.assertEqual(self.state["connection_quality"], "sound-quality")
        self.apply([0xE7, 0x02, 0x01])
        self.assertEqual(self.state["connection_quality"], "stable")

    def test_bgm_sets_the_room_and_the_mode(self):
        self.apply([0xE7, 0x09, 0x00, 0x02])
        self.assertIs(self.state["bgm_enabled"], True)
        self.assertEqual(self.state["bgm_room_size"], "cafe")
        self.assertEqual(self.state["listening_mode"], "background-music")

    def test_bgm_off_reports_my_room(self):
        self.apply([0xE7, 0x03, 0x01, 0x00])
        self.assertIs(self.state["bgm_enabled"], False)
        self.assertEqual(self.state["bgm_room_size"], "my-room")
        self.assertEqual(self.state["listening_mode"], "standard")

    def test_cinema_sets_the_mode(self):
        self.apply([0xE7, 0x04, 0x00])
        self.assertIs(self.state["cinema_enabled"], True)
        self.assertEqual(self.state["listening_mode"], "cinema")
        self.apply([0xE7, 0x04, 0x01])
        self.assertIs(self.state["cinema_enabled"], False)
        self.assertEqual(self.state["listening_mode"], "standard")

    def test_background_music_takes_priority_over_cinema(self):
        self.apply([0xE7, 0x09, 0x01, 0x01])  # BGM off
        self.apply([0xE7, 0x04, 0x00])        # Cinema on
        self.assertEqual(self.state["listening_mode"], "cinema")
        self.apply([0xE7, 0x09, 0x00, 0x01])  # BGM on
        self.assertEqual(self.state["listening_mode"], "background-music")

    def test_a_bgm_reply_with_an_unknown_room_is_ignored(self):
        self.assertFalse(self.apply([0xE7, 0x09, 0x00, 0x05]))
        self.assertIsNone(self.state["bgm_room_size"])
        self.assertIsNone(self.state["listening_mode"])

    def test_a_short_cinema_reply_is_ignored(self):
        self.assertFalse(self.apply([0xE7, 0x04]))
        self.assertIs(self.state["cinema_enabled"], False)
        self.assertIsNone(self.state["listening_mode"])

    def test_a_wrong_subtype_or_short_payload_is_ignored(self):
        self.assertFalse(self.apply([0xE7, 0x03, 0x01]))
        self.assertIs(self.state["bgm_enabled"], False)
        self.assertIsNone(self.state["bgm_room_size"])
        self.assertFalse(self.apply([0xE7, 0x01]))
        self.assertFalse(self.apply([0x13, 0x01, 0x10]))
        self.assertIsNone(self.state["codec"])

    def test_ambient_sound_control_noise_cancelling(self):
        self.apply([0x67, 0x19, 0x01, 0x01, 0x00, 0x00, 0x0A, 0x00, 0x00])
        self.assertEqual(self.state["nc_mode"], "noise-cancelling")
        self.assertEqual(self.state["asc_subtype"], 0x19)
        self.assertIs(self.state["supports_wind"], False)

    def test_ambient_sound_control_ambient(self):
        self.apply([0x67, 0x19, 0x01, 0x01, 0x01, 0x01, 0x0A, 0x00, 0x00])
        self.assertEqual(self.state["nc_mode"], "ambient-sound")
        self.assertEqual(self.state["ambient_level"], 10)
        self.assertIs(self.state["focus_on_voice"], True)

    def test_ambient_sound_control_off_keeps_the_level(self):
        self.apply([0x67, 0x19, 0x01, 0x00, 0x00, 0x00, 0x0C, 0x00, 0x00])
        self.assertEqual(self.state["nc_mode"], "off")
        self.assertEqual(self.state["ambient_level"], 12)

    def test_the_noise_adaptation_fields_are_read(self):
        self.apply([0x69, 0x19, 0x01, 0x01, 0x01, 0x00, 0x0A, 0x01, 0x02])
        self.assertIs(self.state["asc_auto_ambient"], True)
        self.assertEqual(self.state["asc_noise_adapt_sensitivity"], 0x02)

    def test_the_older_seventeen_shape_is_still_read(self):
        self.apply([0x67, 0x17, 0x01, 0x01, 0x00, 0x00, 0x00])
        self.assertEqual(self.state["nc_mode"], "noise-cancelling")
        self.assertEqual(self.state["asc_subtype"], 0x17)

    def test_the_wind_subtype_marks_wind_support(self):
        self.apply([0x67, 0x15, 0x01, 0x01, 0x00, 0x02, 0x00, 0x00])
        self.assertIs(self.state["supports_wind"], True)

    def test_an_unknown_ambient_subtype_is_ignored(self):
        self.assertFalse(self.apply([0x69, 0x30, 0x01, 0x01, 0x01, 0x00, 0x0A, 0x00, 0x00]))
        self.assertIsNone(self.state["nc_mode"])

    def test_out_of_range_ambient_level_is_rejected(self):
        self.assertFalse(self.apply([0x67, 0x19, 0x01, 0x01, 0x01, 0x00, 0x63, 0x00, 0x00]))
        self.assertIsNone(self.state["nc_mode"])

    def test_equalizer_ten_bands(self):
        bands = [7, 6, 5, 4, 3, 2, 1, 0, 6, 6]
        self.apply([0x57, 0x04, 0x30, 0x0A] + bands)
        self.assertEqual(self.state["eq_preset"], "heavy")
        self.assertEqual(self.state["eq_bands"], [1, 0, -1, -2, -3, -4, -5, -6, 0, 0])
        self.assertEqual(self.state["eq_band_count"], 10)
        self.assertIsNone(self.state["eq_bass"])

    def test_speak_to_chat_enabled_is_inverted(self):
        self.apply([0xF7, 0x0C, 0x00, 0x01])
        self.assertIs(self.state["speak_to_chat"], True)
        self.apply([0xF7, 0x0C, 0x01, 0x01])
        self.assertIs(self.state["speak_to_chat"], False)

    def test_pause_when_taken_off(self):
        self.apply([0xF7, 0x01, 0x00])
        self.assertIs(self.state["pause_when_taken_off"], True)

    def test_auto_power_off(self):
        self.apply([0x27, 0x05, 0x11, 0x00])
        self.assertEqual(self.state["auto_power_off"], "off")

    def test_speak_to_chat_config(self):
        self.apply([0xFB, 0x0C, 0x02, 0x03])
        self.assertEqual(self.state["stc_sensitivity"], "low")
        self.assertEqual(self.state["stc_timeout"], "off")

    def test_garbage_is_ignored(self):
        self.assertFalse(self.apply([0x99, 0x01, 0x02]))
        self.assertFalse(self.apply([]))

    def test_device_list_with_class_bytes(self):
        payload = build_v2_device_list(
            [
                ("AA:BB:CC:DD:EE:FF", 0x01, "Phone", b"\x04\x01\x00"),
                ("11:22:33:44:55:66", 0x02, "Laptop", b"\x04\x0c\x00"),
            ],
            0x02,
        )
        self.assertTrue(self.apply(payload, sonyhp.MSG_COMMAND_2))
        self.assertEqual(self.state["devices"], [
            {"address": "AA:BB:CC:DD:EE:FF", "name": "Phone", "connected": True, "playback": False},
            {"address": "11:22:33:44:55:66", "name": "Laptop", "connected": True, "playback": True},
        ])
        self.assertEqual(self.state["playback_source"], "11:22:33:44:55:66")

    def test_device_list_without_class_bytes(self):
        payload = build_v2_device_list(
            [
                ("AA:BB:CC:DD:EE:FF", 0x00, "Phone"),
                ("11:22:33:44:55:66", 0x01, "Laptop"),
            ],
            0x01,
            subtype=0x00,
        )
        self.apply(payload, sonyhp.MSG_COMMAND_2)
        self.assertEqual(self.state["devices"], [
            {"address": "AA:BB:CC:DD:EE:FF", "name": "Phone", "connected": False, "playback": False},
            {"address": "11:22:33:44:55:66", "name": "Laptop", "connected": True, "playback": True},
        ])
        self.assertEqual(self.state["playback_source"], "11:22:33:44:55:66")

    def test_a_truncated_device_list_is_ignored(self):
        good = build_v2_device_list([("AA:BB:CC:DD:EE:FF", 0x01, "Phone")], 0x01)
        self.apply(good, sonyhp.MSG_COMMAND_2)
        kept = list(self.state["devices"])
        self.assertFalse(self.apply(good[:-4], sonyhp.MSG_COMMAND_2))
        self.assertEqual(self.state["devices"], kept)
        self.assertEqual(self.state["playback_source"], "AA:BB:CC:DD:EE:FF")

    def test_an_empty_device_list_clears_the_devices(self):
        self.apply(build_v2_device_list(
            [("AA:BB:CC:DD:EE:FF", 0x01, "Phone")], 0x01), sonyhp.MSG_COMMAND_2)
        self.apply(build_v2_device_list([], 0x00), sonyhp.MSG_COMMAND_2)
        self.assertEqual(self.state["devices"], [])
        self.assertIsNone(self.state["playback_source"])


class TestV2Settings(unittest.TestCase):
    def state(self):
        state = sonyhp.initial_state()
        state.update(
            name="WH-1000XM6",
            features=sonyhp.features_for("WH-1000XM6"),
            nc_mode="noise-cancelling",
            ambient_level=5,
            asc_subtype=0x17,
        )
        return state

    def test_cycle_asks_then_reads_back(self):
        requests = sonyhp.setting_requests_v2(self.state(), "nc", "cycle")
        self.assertEqual([payload[0] for _, payload in requests], [sonyhp.V2_ASC_SET, sonyhp.V2_ASC_GET])

    def test_ambient_level_switches_mode_and_reads_back(self):
        requests = sonyhp.setting_requests_v2(self.state(), "ambient-level", 8)
        self.assertEqual(requests[0][1][0], sonyhp.V2_ASC_SET)
        self.assertEqual(requests[0][1][4], 0x01)  # ambient flag
        self.assertEqual(requests[0][1][6], 8)
        self.assertEqual(requests[1][1][0], sonyhp.V2_ASC_GET)

    def test_eq_bands_needs_ten_values(self):
        sonyhp.setting_requests_v2(self.state(), "eq-bands", "0,1,2,3,4,5,6,-1,-2,-3")
        with self.assertRaises(ValueError):
            sonyhp.setting_requests_v2(self.state(), "eq-bands", "1,2,3")

    def test_the_zero_equalizer_subtype_is_not_collapsed_to_the_default(self):
        state = self.state()
        state["eq_subtype"] = 0
        for key, value in (("eq", "clear"), ("eq-bands", "0,0,0,0,0,0,0,0,0,0")):
            with self.subTest(key=key):
                requests = sonyhp.setting_requests_v2(state, key, value)
                self.assertEqual(requests[0][1][1], 0x00)
                self.assertEqual(requests[1][1], bytes([sonyhp.V2_EQ_GET, 0x00]))

    def test_a_reported_sensitivity_survives_the_next_write(self):
        for byte in (0x01, 0x03):
            with self.subTest(sensitivity=hex(byte)):
                state = self.state()
                sonyhp.apply_payload_v2(
                    state, sonyhp.MSG_COMMAND_1,
                    bytes([0x69, 0x19, 0x01, 0x01, 0x00, 0x00, 0x0A, 0x00, byte]))
                self.assertEqual(state["asc_noise_adapt_sensitivity"], byte)
                for key, value in (("nc", "ambient-sound"), ("ambient-level", 8)):
                    requests = sonyhp.setting_requests_v2(state, key, value)
                    self.assertEqual(requests[0][1][8], byte, key)

    def test_voice_focus_while_chatting_is_refused(self):
        with self.assertRaises(ValueError):
            sonyhp.setting_requests_v2(self.state(), "stc-focus-on-voice", "on")

    def test_features_the_model_lacks_are_refused(self):
        # These are valid v2 settings, so the feature gate is the only thing
        # that can refuse them.
        state = self.state()
        state["features"] = sorted(sonyhp.V2_FEATURES - {"dsee", "listening-mode"})
        for key, value in (("dsee", "on"), ("listening-mode", "cinema")):
            with self.subTest(key=key):
                with self.assertRaisesRegex(ValueError, "does not support"):
                    sonyhp.setting_requests_v2(state, key, value)

    def test_a_timer_auto_power_off_is_refused(self):
        with self.assertRaises(ValueError):
            sonyhp.setting_requests_v2(self.state(), "auto-power-off", "3-hour")

    def test_speak_to_chat_toggle(self):
        requests = sonyhp.setting_requests_v2(self.state(), "speak-to-chat", "on")
        self.assertEqual(list(requests[0][1]), [0xF8, 0x0C, 0x00, 0x01])

    def test_dsee_writes_then_reads_back(self):
        requests = sonyhp.setting_requests_v2(self.state(), "dsee", "on")
        self.assertEqual(list(requests[0][1]), [0xE8, 0x01, 0x01])
        self.assertEqual(list(requests[1][1]), [0xE6, 0x01])

    def test_connection_quality_writes_then_reads_back(self):
        requests = sonyhp.setting_requests_v2(self.state(), "connection-quality", "stable")
        self.assertEqual(list(requests[0][1]), [0xE8, 0x02, 0x01])
        self.assertEqual(list(requests[1][1]), [0xE6, 0x02])

    def test_listening_mode_writes_both_flags_then_reads_back(self):
        requests = sonyhp.setting_requests_v2(self.state(), "listening-mode", "cinema")
        self.assertEqual([payload[0] for _, payload in requests],
                         [sonyhp.V2_AUDIO_SET, sonyhp.V2_AUDIO_SET,
                          sonyhp.V2_AUDIO_GET, sonyhp.V2_AUDIO_GET])
        self.assertEqual(list(requests[0][1]), [0xE8, 0x09, 0x01, 0x01])
        self.assertEqual(list(requests[1][1]), [0xE8, 0x04, 0x00])
        self.assertEqual(list(requests[2][1]), [0xE6, 0x09])
        self.assertEqual(list(requests[3][1]), [0xE6, 0x04])

    def test_background_music_enables_bgm_and_disables_cinema(self):
        requests = sonyhp.setting_requests_v2(self.state(), "listening-mode", "background-music")
        self.assertEqual(requests[0][1][2], 0x00)  # BGM on
        self.assertEqual(requests[1][1][2], 0x01)  # Cinema off

    def test_bgm_room_size_keeps_the_current_mode_enabled(self):
        state = self.state()
        state["listening_mode"] = "background-music"
        requests = sonyhp.setting_requests_v2(state, "bgm-room-size", "cafe")
        self.assertEqual(list(requests[0][1]), [0xE8, 0x09, 0x00, 0x02])
        self.assertEqual(list(requests[1][1]), [0xE6, 0x09])

    def test_playback_source_writes_then_reads_the_device_list(self):
        requests = sonyhp.setting_requests_v2(self.state(), "playback-source", "AA:BB:CC:DD:EE:FF")
        self.assertEqual(requests, [
            (sonyhp.MSG_COMMAND_2, b"\x3c\x01" + b"AA:BB:CC:DD:EE:FF"),
            (sonyhp.MSG_COMMAND_2, b"\x36\x02"),
        ])

    def test_playback_source_rejects_a_malformed_mac(self):
        with self.assertRaises(ValueError):
            sonyhp.setting_requests_v2(self.state(), "playback-source", "not-an-address")

    def test_playback_source_is_refused_without_multipoint(self):
        state = self.state()
        state.update(name="WH-1000XM4", features=sonyhp.features_for("WH-1000XM4"))
        self.assertNotIn("multipoint", state["features"])
        with self.assertRaises(ValueError):
            sonyhp.setting_requests_v2(state, "playback-source", "AA:BB:CC:DD:EE:FF")


class TestV2Features(unittest.TestCase):
    def test_the_xm6_gets_its_own_set(self):
        features = sonyhp.features_for("WH-1000XM6")
        for feature in ("battery", "equalizer", "speak-to-chat", "pause-when-taken-off",
                        "auto-power-off", "dsee", "connection-quality"):
            self.assertIn(feature, features)
        for feature in ("touch-sensor", "voice-notifications", "speak-to-chat-focus"):
            self.assertNotIn(feature, features)

    def test_the_xm6_gets_listening_mode(self):
        features = sonyhp.features_for("WH-1000XM6")
        self.assertIn("listening-mode", features)

    def test_the_xm6_gets_multipoint(self):
        self.assertIn("multipoint", sonyhp.features_for("WH-1000XM6"))

    def test_v1_speak_to_chat_keeps_voice_focus(self):
        self.assertIn("speak-to-chat-focus", sonyhp.features_for("WH-1000XM4"))


class TestV2ShortFrames(unittest.TestCase):
    """A one-byte family frame must not walk off the end of its payload.

    The daemon's read loop only catches NotConnected and OSError, so an
    IndexError raised from here would take the whole daemon down with it.
    """

    def ignored(self, payload):
        state = sonyhp.initial_state()
        before = dict(state)
        self.assertFalse(sonyhp.apply_payload_v2(state, sonyhp.MSG_COMMAND_1, payload))
        self.assertEqual(state, before, payload.hex())

    def test_one_byte_family_frames_are_ignored(self):
        for code in (0xF7, 0xF9, 0x27, 0x29):
            with self.subTest(code=hex(code)):
                self.ignored(bytes([code]))

    def test_truncated_family_frames_are_ignored(self):
        for payload in (
            bytes([0xF7, 0x0C]),
            bytes([0xF7, 0x0C, 0x00]),
            bytes([0xF9, 0x0C]),
            bytes([0xF9, 0x01]),
            bytes([0x27, 0x05]),
            bytes([0x27, 0x05, 0x11]),
            bytes([0x29, 0x05]),
            bytes([0x29, 0x01]),
        ):
            with self.subTest(payload=payload.hex()):
                self.ignored(payload)


class TestUntrustedNameCleaning(unittest.TestCase):
    """The headset picks these names, so ingest makes them inert and bounded.

    The cleaning is about control/format neutrality and length, not ASCII:
    ordinary Unicode letters pass through untouched.
    """

    def has_controls(self, text):
        return any(ord(char) < 0x20 or 0x7F <= ord(char) <= 0x9F for char in text)

    def test_osc_and_ansi_escapes_do_not_survive(self):
        name = sonyhp._clean_name("\x1b]52;c;SGk=\x07\x1b[31mRed\x1b[0m", "AA:BB:CC:DD:EE:FF")
        self.assertFalse(self.has_controls(name), repr(name))

    def test_newlines_tabs_and_runs_of_space_collapse_to_one_space(self):
        self.assertEqual(
            sonyhp._clean_name("  Acme\n\tCorp\r\n   Ltd ", "AA:BB:CC:DD:EE:FF"),
            "Acme Corp Ltd",
        )

    def test_rich_text_markup_stays_literal_single_line_text(self):
        # PlainText in the panel is what makes this inert; ingest must not
        # mangle the characters or leave a way to break the line.
        self.assertEqual(
            sonyhp._clean_name("<b>Acme</b> & <img src=x>", "AA:BB:CC:DD:EE:FF"),
            "<b>Acme</b> & <img src=x>",
        )

    def test_a_control_only_name_falls_back_to_the_address(self):
        self.assertEqual(
            sonyhp._clean_name("\x00\x1b\x07\n\t", "AA:BB:CC:DD:EE:FF"),
            "AA:BB:CC:DD:EE:FF",
        )

    def test_an_overlong_name_is_truncated_without_a_trailing_space(self):
        name = sonyhp._clean_name("A" * 63 + " tail", "AA:BB:CC:DD:EE:FF")
        self.assertEqual(name, "A" * 63)
        self.assertLessEqual(len(name), 64)

    def test_unicode_letters_are_not_stripped(self):
        self.assertEqual(
            sonyhp._clean_name("Café 東京 Δ", "AA:BB:CC:DD:EE:FF"),
            "Café 東京 Δ",
        )

    def test_a_peer_name_is_cleaned_when_the_device_list_is_decoded(self):
        payload = build_v2_device_list(
            [("AA:BB:CC:DD:EE:FF", 0x01, "Phone\n\x1b[31m", b"\x00\x00\x00")], 0x01)
        devices = sonyhp.parse_v2_device_list(payload)
        self.assertEqual(devices[0]["name"], "Phone [31m")

    def test_a_peer_name_of_only_controls_falls_back_to_its_address(self):
        payload = build_v2_device_list(
            [("AA:BB:CC:DD:EE:FF", 0x01, "\x00\x07\x1b", b"\x00\x00\x00")], 0x01)
        devices = sonyhp.parse_v2_device_list(payload)
        self.assertEqual(devices[0]["name"], "AA:BB:CC:DD:EE:FF")

    def test_a_non_mac_peer_address_is_replaced_and_its_name_falls_back_safely(self):
        payload = build_v2_device_list(
            [("not-a-mac-address", 0x01, "\x00\x07\x1b", b"\x00\x00\x00")], 0x01)
        devices = sonyhp.parse_v2_device_list(payload)
        self.assertEqual(devices[0]["address"], "unknown")
        self.assertEqual(devices[0]["name"], "unknown")

    def test_a_valid_peer_mac_is_preserved(self):
        payload = build_v2_device_list(
            [("AA:BB:CC:DD:EE:FF", 0x01, "\x00\x07\x1b", b"\x00\x00\x00")], 0x01)
        devices = sonyhp.parse_v2_device_list(payload)
        self.assertEqual(devices[0]["address"], "AA:BB:CC:DD:EE:FF")
        self.assertEqual(devices[0]["name"], "AA:BB:CC:DD:EE:FF")


if __name__ == "__main__":
    unittest.main(verbosity=2)
