"""Protocol v1: requests, replies, settings and the v1 model table.

The byte layouts of the "MDR" command set, the parser that folds its
replies into state, and the per-model features that gate them.
"""

import unittest

import support

sonyhp = support.sonyhp


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
        # Some keys are v1-only (the six-band equalizer) and some are v2-only
        # (connection quality), so each is built by whichever side supports it.
        state = sonyhp.initial_state()
        state.update(
            name="WH-1000XM6",
            features=sorted(sonyhp.ALL_FEATURES),
            nc_mode="ambient-sound",
            ambient_level=10,
            supports_wind=True,
        )
        values = {
            "nc": "noise-cancelling", "ambient-level": "5", "focus-on-voice": "on",
            "eq": "vocal", "eq-bands": "0,0,0,0,0,0", "auto-power-off": "off",
            "stc-sensitivity": "auto", "stc-timeout": "short", "stc-focus-on-voice": "on",
            "voice-notifications": "on", "dsee": "on", "speak-to-chat": "on",
            "pause-when-taken-off": "on", "touch-sensor": "on",
            "connection-quality": "stable", "listening-mode": "cinema",
            "bgm-room-size": "cafe", "playback-source": "AA:BB:CC:DD:EE:FF",
        }
        self.assertEqual(sorted(values), sonyhp.SETTING_KEYS)
        for key, value in values.items():
            try:
                requests = sonyhp.setting_requests(state, key, value)
            except ValueError:
                requests = sonyhp.setting_requests_v2(state, key, value)
            self.assertTrue(requests, key)


class TestFeatures(unittest.TestCase):
    def test_known_models(self):
        self.assertIn("speak-to-chat", sonyhp.features_for("WH-1000XM4"))
        self.assertNotIn("touch-sensor", sonyhp.features_for("WH-1000XM4"))
        self.assertNotIn("auto-power-off-timer", sonyhp.features_for("WH-1000XM4"))
        self.assertIn("touch-sensor", sonyhp.features_for("WH-1000XM3"))
        self.assertIn("auto-power-off-timer", sonyhp.features_for("WH-1000XM3"))
        self.assertNotIn("speak-to-chat", sonyhp.features_for("WH-1000XM3"))
        self.assertNotIn("auto-power-off", sonyhp.features_for("WH-1000XM2"))

    def test_the_eq_sbc_only_marker_scopes_to_xm2_xm3(self):
        # The presentation marker behind Model.availabilityFor's equalizer
        # rule: XM2/XM3 carry it, XM4 and every v2 set do not.
        self.assertIn("eq-sbc-only", sonyhp.features_for("WH-1000XM2"))
        self.assertIn("eq-sbc-only", sonyhp.features_for("WH-1000XM3"))
        self.assertNotIn("eq-sbc-only", sonyhp.features_for("WH-1000XM4"))
        for model in sonyhp.FEATURE_SETS["v2"]:
            with self.subTest(model=model):
                self.assertNotIn("eq-sbc-only", sonyhp.features_for(model, "v2"))

    def test_an_unknown_v1_device_gets_the_ceiling(self):
        # The ceiling carries wire commands only, so an unrecognised v1 name
        # gets no presentation marker and its equalizer stays usable.
        self.assertEqual(set(sonyhp.features_for("WH-XB910N", "v1")), sonyhp.V1_FEATURES)
        self.assertNotIn("eq-sbc-only", sonyhp.features_for("WH-XB910N", "v1"))

    def test_the_name_only_has_to_contain_the_model(self):
        self.assertEqual(sonyhp.features_for("Gabriel's WH-1000XM4"), sonyhp.features_for("WH-1000XM4"))

    def test_an_unknown_device_gets_everything(self):
        self.assertEqual(set(sonyhp.features_for("WH-XB910N")), sonyhp.ALL_FEATURES)
        self.assertEqual(set(sonyhp.features_for(None)), sonyhp.ALL_FEATURES)


if __name__ == "__main__":
    unittest.main(verbosity=2)
