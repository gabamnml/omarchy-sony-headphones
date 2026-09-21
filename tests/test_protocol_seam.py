"""The Protocol and Transport seams, and the connect path built on them.

The handshake picks one adapter per link, Link makes every wire decision
through it, and the transport owns the bytes — including candidate retry
on connect. Negotiation failures, protocol-aware features and the parser
boundary hang off the same seam.
"""

import contextlib
import socket
import unittest
from unittest import mock

import support
from support import FakeStream, de_seq, de_uint8, de_uint16, de_uuid16

sonyhp = support.sonyhp


class TestHandshake(unittest.TestCase):
    """The init reply's length is the only reliable v1/v2 discriminator."""

    def link_with_reply(self, reply_payload):
        link = sonyhp.Link("AA:BB:CC:DD:EE:FF")
        link.sock = FakeStream(sonyhp.encode_message(sonyhp.MSG_COMMAND_1, 1, reply_payload))
        return link

    def test_a_four_byte_reply_selects_v1(self):
        link = self.link_with_reply(bytes([sonyhp.INIT_REPLY, 0x00, 0x40, 0x10]))
        self.assertTrue(link._handshake())
        self.assertEqual(link.protocol, "v1")
        self.assertEqual(link.state["protocol"], "v1")

    def test_an_eight_byte_reply_selects_v2(self):
        link = self.link_with_reply(bytes([sonyhp.V2_INIT_REPLY, 0x00, 0x03, 0x00, 0x30, 0x32, 0x00, 0x00]))
        self.assertTrue(link._handshake())
        self.assertEqual(link.protocol, "v2")
        self.assertEqual(link.state["protocol"], "v2")


class TestServiceProtocol(unittest.TestCase):
    def test_the_v2_uuid_identifies_v2(self):
        self.assertEqual(sonyhp.service_protocol("UUID: Vendor specific (%s)" % sonyhp.SERVICE_UUID_V2), "v2")

    def test_the_v1_uuid_identifies_v1(self):
        self.assertEqual(sonyhp.service_protocol("UUID: Vendor specific (%s)" % sonyhp.SERVICE_UUID), "v1")

    def test_an_unrelated_record_is_neither(self):
        self.assertIsNone(sonyhp.service_protocol("UUID: Audio Sink (0000110b-0000-1000-8000-00805f9b34fb)"))

    def test_a_uuid_in_the_name_is_not_a_service(self):
        info = "\n".join([
            "Device AA:BB:CC:DD:EE:FF (public)",
            "\tName: %s" % sonyhp.SERVICE_UUID_V2,
            "\tAlias: %s" % sonyhp.SERVICE_UUID,
        ])
        self.assertIsNone(sonyhp.service_protocol(info))

    def test_a_uuid_line_is_matched_case_insensitively(self):
        info = "\n".join([
            "Device AA:BB:CC:DD:EE:FF (public)",
            "\tName: WH-1000XM6",
            "\tUUID: Vendor specific (%s)" % sonyhp.SERVICE_UUID_V2.upper(),
        ])
        self.assertEqual(sonyhp.service_protocol(info), "v2")

    def test_the_v2_uuid_wins_when_both_lines_are_present(self):
        info = "\n".join([
            "\tUUID: Vendor specific (%s)" % sonyhp.SERVICE_UUID,
            "\tUUID: Vendor specific (%s)" % sonyhp.SERVICE_UUID_V2,
        ])
        self.assertEqual(sonyhp.service_protocol(info), "v2")

    def test_a_newline_in_the_name_does_not_forge_a_service(self):
        # `bluetoothctl info` prints the Name/Alias value verbatim, so a
        # hostile friendly name spills its `UUID:` line onto a fresh
        # unindented line. Only tab-indented property lines count.
        for uuid in (sonyhp.SERVICE_UUID, sonyhp.SERVICE_UUID_V2):
            for key in ("Name", "Alias"):
                with self.subTest(uuid=uuid, key=key):
                    info = "\n".join([
                        "Device AA:BB:CC:DD:EE:FF (public)",
                        "\t%s: x" % key,
                        "UUID: Vendor specific (%s)" % uuid,
                    ])
                    self.assertIsNone(sonyhp.service_protocol(info))

    def test_a_forged_line_beside_a_real_one_still_classifies(self):
        info = "\n".join([
            "Device AA:BB:CC:DD:EE:FF (public)",
            "\tName: x",
            "UUID: Vendor specific (%s)" % sonyhp.SERVICE_UUID_V2,
            "\tUUID: Vendor specific (%s)" % sonyhp.SERVICE_UUID,
        ])
        self.assertEqual(sonyhp.service_protocol(info), "v1")

    def test_an_unindented_uuid_after_the_header_is_ignored(self):
        info = "\n".join([
            "Device AA:BB:CC:DD:EE:FF (public)",
            "UUID: Vendor specific (%s)" % sonyhp.SERVICE_UUID_V2,
        ])
        self.assertIsNone(sonyhp.service_protocol(info))


class TestProtocolFeatures(unittest.TestCase):
    """A device is only ever offered its own generation's controls."""

    V2_ONLY = {"connection-quality", "listening-mode", "multipoint"}
    V1_ONLY = {"touch-sensor", "speak-to-chat-focus",
               "auto-power-off-timer"}

    def test_an_unknown_v1_device_gets_the_v1_ceiling(self):
        features = set(sonyhp.features_for("WH-XB910N", "v1"))
        self.assertEqual(features, sonyhp.V1_FEATURES)
        self.assertFalse(features & self.V2_ONLY)

    def test_an_unknown_v2_device_gets_the_v2_ceiling(self):
        features = set(sonyhp.features_for("WH-XB910N", "v2"))
        self.assertEqual(features, sonyhp.V2_FEATURES)
        self.assertFalse(features & self.V1_ONLY)

    def test_a_name_cannot_pull_in_the_other_generation(self):
        # A v1 model name on a confirmed v2 link must not surface v1 controls.
        self.assertLessEqual(set(sonyhp.features_for("WH-1000XM4", "v2")), sonyhp.V2_FEATURES)

    def test_every_model_is_inside_its_protocol_ceiling(self):
        for protocol, ceiling in sonyhp.FEATURE_CEILINGS.items():
            for model, features in sonyhp.FEATURE_SETS[protocol].items():
                # Only presentation markers (UI_FEATURES) may sit outside the
                # wire-command ceiling: they gate a row, not a command.
                self.assertLessEqual(features - ceiling, sonyhp.UI_FEATURES, model)
                self.assertLessEqual(
                    set(sonyhp.features_for(model, protocol)) - ceiling,
                    sonyhp.UI_FEATURES, model)

    def test_the_known_models_keep_their_quirks(self):
        self.assertIn("speak-to-chat-focus", sonyhp.features_for("WH-1000XM4", "v1"))
        self.assertNotIn("touch-sensor", sonyhp.features_for("WH-1000XM4", "v1"))
        self.assertNotIn("auto-power-off-timer", sonyhp.features_for("WH-1000XM4", "v1"))
        self.assertIn("touch-sensor", sonyhp.features_for("WH-1000XM3", "v1"))
        self.assertIn("auto-power-off-timer", sonyhp.features_for("WH-1000XM3", "v1"))
        self.assertIn("nc-optimizer", sonyhp.features_for("WH-1000XM2", "v1"))
        self.assertIn("multipoint", sonyhp.features_for("WH-1000XM6", "v2"))
        self.assertIn("voice-notifications", sonyhp.features_for("WH-1000XM6", "v2"))

    def test_initial_state_advertises_nothing_before_a_connect(self):
        self.assertEqual(sonyhp.initial_state()["features"], [])


class TestFeaturesAfterHandshake(unittest.TestCase):
    """The confirmed protocol, not the advertised name, decides the features."""

    def connect(self, reply, name, protocol):
        link = sonyhp.Link("AA:BB:CC:DD:EE:FF")
        link.state["name"] = name
        stream = FakeStream(sonyhp.encode_message(sonyhp.MSG_COMMAND_1, 1, reply))
        with contextlib.ExitStack() as stack:
            stack.enter_context(mock.patch.object(sonyhp, "sdp_channel", return_value=9))
            stack.enter_context(mock.patch.object(sonyhp, "cached_channel", return_value=None))
            stack.enter_context(mock.patch.object(sonyhp, "remember_channel"))
            stack.enter_context(mock.patch.object(sonyhp.Link, "_open", return_value=stream))
            if protocol == "v2":
                for step in ("select_asc_subtype", "select_eq_subtype", "select_bgm_subtype"):
                    stack.enter_context(
                        mock.patch.object(sonyhp.Link, step, mock.Mock(return_value=None)))
            link.connect()
        link.close()
        return link

    def test_a_v1_handshake_offers_only_v1_controls(self):
        link = self.connect(bytes([sonyhp.INIT_REPLY, 0x00, 0x40, 0x10]), "WH-9000 mystery", "v1")
        self.assertEqual(link.state["protocol"], "v1")
        self.assertEqual(set(link.state["features"]), sonyhp.V1_FEATURES)

    def test_a_v2_handshake_offers_only_v2_controls(self):
        link = self.connect(
            bytes([sonyhp.V2_INIT_REPLY, 0x00, 0x03, 0x00, 0x30, 0x32, 0x00, 0x00]),
            "WH-9000 mystery", "v2")
        self.assertEqual(link.state["protocol"], "v2")
        self.assertEqual(set(link.state["features"]), sonyhp.V2_FEATURES)

    def test_a_known_model_keeps_its_refinement_after_the_handshake(self):
        link = self.connect(bytes([sonyhp.INIT_REPLY, 0x00, 0x40, 0x10]), "WH-1000XM4", "v1")
        self.assertEqual(set(link.state["features"]), sonyhp.FEATURE_SETS["v1"]["WH-1000XM4"])


class TestConfigureLink(unittest.TestCase):
    """One helper sets a device's identity and confirms it through the handshake."""

    def connect(self, device, reply):
        link = sonyhp.Link(device["address"])
        stream = FakeStream(sonyhp.encode_message(sonyhp.MSG_COMMAND_1, 1, reply))
        with contextlib.ExitStack() as stack:
            stack.enter_context(mock.patch.object(sonyhp, "sdp_channel", return_value=9))
            stack.enter_context(mock.patch.object(sonyhp, "cached_channel", return_value=None))
            stack.enter_context(mock.patch.object(sonyhp, "remember_channel"))
            stack.enter_context(mock.patch.object(sonyhp.Link, "_open", return_value=stream))
            if reply[0] == sonyhp.V2_INIT_REPLY:
                for step in ("select_asc_subtype", "select_eq_subtype", "select_bgm_subtype"):
                    stack.enter_context(
                        mock.patch.object(sonyhp.Link, step, mock.Mock(return_value=None)))
            channel = sonyhp.configure_link(link, device)
        link.close()
        return link, channel

    def test_the_confirmed_protocol_replaces_the_bluetoothctl_hint(self):
        device = {"address": "AA:BB:CC:DD:EE:FF", "name": "WH-9000 mystery", "protocol": "v1"}
        link, channel = self.connect(
            device, bytes([sonyhp.V2_INIT_REPLY, 0x00, 0x03, 0x00, 0x30, 0x32, 0x00, 0x00]))
        self.assertEqual(channel, 9)
        self.assertEqual(link.state["name"], device["name"])
        self.assertEqual(set(link.state["features"]), sonyhp.V2_FEATURES)

    def test_a_failed_connect_keeps_the_hint_for_the_error_state(self):
        device = {"address": "AA:BB:CC:DD:EE:FF", "name": "WH-1000XM6", "protocol": "v2"}
        link = sonyhp.Link(device["address"])
        with mock.patch.object(sonyhp, "sdp_channel", return_value=None), \
                mock.patch.object(sonyhp, "cached_channel", return_value=None):
            with self.assertRaises(sonyhp.NotConnected):
                sonyhp.configure_link(link, device)
        self.assertEqual(link.state["name"], device["name"])
        self.assertEqual(link.state["features"], sonyhp.features_for(device["name"], "v2"))


class TestSettingProtocols(unittest.TestCase):
    """Each builder serves its own ceiling and refuses the other's keys."""

    V1_VALUES = {
        "nc": "noise-cancelling", "ambient-level": "5", "focus-on-voice": "on",
        "eq": "vocal", "eq-bands": "0,0,0,0,0,0", "auto-power-off": "5-min",
        "stc-sensitivity": "auto", "stc-timeout": "short", "stc-focus-on-voice": "on",
        "voice-notifications": "on", "dsee": "on", "speak-to-chat": "on",
        "pause-when-taken-off": "on", "touch-sensor": "on",
    }
    V2_VALUES = {
        "nc": "noise-cancelling", "ambient-level": "5", "focus-on-voice": "on",
        "eq": "clear", "eq-bands": "0,0,0,0,0,0,0,0,0,0", "auto-power-off": "off",
        "stc-sensitivity": "auto", "stc-timeout": "short",
        "speak-to-chat": "on", "pause-when-taken-off": "on", "dsee": "on",
        "voice-notifications": "on",
        "connection-quality": "stable", "listening-mode": "cinema",
        "bgm-room-size": "cafe", "playback-source": "AA:BB:CC:DD:EE:FF",
    }

    def state(self, protocol, features):
        state = sonyhp.initial_state()
        state.update(
            name="WH-9000 mystery",
            protocol=protocol,
            features=sorted(features),
            nc_mode="ambient-sound",
            ambient_level=10,
            supports_wind=True,
            asc_subtype=0x19,
        )
        return state

    def advertised_keys(self, ceiling, refused=()):
        keys = {key for key, feature in sonyhp.SETTING_FEATURES.items() if feature in ceiling}
        return (keys | {"nc", "ambient-level", "focus-on-voice"}) - set(refused)

    def test_the_full_v1_ceiling_builds_v1_requests(self):
        self.assertEqual(set(self.V1_VALUES), self.advertised_keys(sonyhp.V1_FEATURES))
        state = self.state("v1", sonyhp.V1_FEATURES)
        for key, value in self.V1_VALUES.items():
            self.assertTrue(sonyhp.setting_requests(state, key, value), key)

    def test_the_full_v2_ceiling_builds_v2_requests(self):
        # Voice focus while chatting is v1-only even though it maps to the
        # shared speak-to-chat feature, so it is not part of the v2 set.
        refused = set(sonyhp.V1_ONLY_SETTINGS) | {"stc-focus-on-voice"}
        self.assertEqual(set(self.V2_VALUES),
                         self.advertised_keys(sonyhp.V2_FEATURES, refused))
        state = self.state("v2", sonyhp.V2_FEATURES)
        for key, value in self.V2_VALUES.items():
            self.assertTrue(sonyhp.setting_requests_v2(state, key, value), key)

    def test_the_v1_builder_refuses_v2_keys_even_with_wrong_features(self):
        state = self.state("v1", sonyhp.ALL_FEATURES)
        for key in sonyhp.V2_ONLY_SETTINGS:
            with self.assertRaisesRegex(ValueError, "v2", msg=key):
                sonyhp.setting_requests(state, key, self.V2_VALUES[key])

    def test_the_v2_builder_refuses_v1_keys_even_with_wrong_features(self):
        state = self.state("v2", sonyhp.ALL_FEATURES)
        for key in sonyhp.V1_ONLY_SETTINGS:
            with self.assertRaisesRegex(ValueError, "v1", msg=key):
                sonyhp.setting_requests_v2(state, key, self.V1_VALUES[key])

    def test_empty_features_fall_back_to_the_builders_own_ceiling(self):
        # initial_state carries no features; each builder still knows its side.
        state = sonyhp.initial_state()
        state.update(nc_mode="off", ambient_level=10)
        self.assertTrue(sonyhp.setting_requests(state, "touch-sensor", "on"))
        with self.assertRaises(ValueError):
            sonyhp.setting_requests(state, "connection-quality", "stable")
        self.assertTrue(sonyhp.setting_requests_v2(state, "connection-quality", "stable"))
        with self.assertRaises(ValueError):
            sonyhp.setting_requests_v2(state, "touch-sensor", "on")


class TestSettingPreamble(unittest.TestCase):
    """The preamble both builders share: same accept/refuse, same bytes and messages.

    These pin the exact strings the panel and tests rely on, so a refactor of
    the shared preamble that changes a message or a request byte fails here.
    """

    def state(self, protocol, features):
        state = sonyhp.initial_state()
        state.update(
            name="WH-9000 mystery",
            protocol=protocol,
            features=sorted(features),
            nc_mode="ambient-sound",
            ambient_level=13,
            supports_wind=True,
            asc_subtype=0x19,
            asc_auto_ambient=True,
            asc_noise_adapt_sensitivity=0x03,
        )
        return state

    def test_each_v2_only_key_is_refused_by_v1_with_its_message(self):
        # Features are the full union, so only the protocol refusal can fire.
        state = self.state("v1", sonyhp.ALL_FEATURES)
        for key in sorted(sonyhp.V2_ONLY_SETTINGS):
            with self.subTest(key=key):
                with self.assertRaises(ValueError) as caught:
                    sonyhp.setting_requests(state, key, TestSettingProtocols.V2_VALUES[key])
                self.assertEqual(
                    str(caught.exception),
                    f"{key!r} is a v2 setting and cannot be sent over v1",
                )

    def test_each_v1_only_key_is_refused_by_v2_with_its_message(self):
        state = self.state("v2", sonyhp.ALL_FEATURES)
        for key in sorted(sonyhp.V1_ONLY_SETTINGS):
            with self.subTest(key=key):
                with self.assertRaises(ValueError) as caught:
                    sonyhp.setting_requests_v2(state, key, TestSettingProtocols.V1_VALUES[key])
                self.assertEqual(
                    str(caught.exception),
                    f"{key!r} is a v1 setting and cannot be sent over v2",
                )

    def test_an_unsupported_feature_is_refused_with_its_message(self):
        cases = (
            ("v1", sonyhp.setting_requests),
            ("v2", sonyhp.setting_requests_v2),
        )
        for protocol, builder in cases:
            with self.subTest(protocol=protocol):
                state = self.state(protocol, sonyhp.FEATURE_CEILINGS[protocol] - {"dsee"})
                with self.assertRaises(ValueError) as caught:
                    builder(state, "dsee", "on")
                self.assertEqual(
                    str(caught.exception),
                    "WH-9000 mystery does not support dsee",
                )

    def test_the_timer_gate_refuses_a_timer_on_a_v1_device_without_one(self):
        state = self.state("v1", sonyhp.V1_FEATURES - {"auto-power-off-timer"})
        with self.assertRaises(ValueError) as caught:
            sonyhp.setting_requests(state, "auto-power-off", "5-min")
        self.assertEqual(
            str(caught.exception),
            "WH-9000 mystery only supports auto power off 'off' or 'when-taken-off'",
        )

    def test_the_timer_gate_still_allows_off_on_a_v1_device_without_one(self):
        state = self.state("v1", sonyhp.V1_FEATURES - {"auto-power-off-timer"})
        self.assertTrue(sonyhp.setting_requests(state, "auto-power-off", "off"))

    def test_the_timer_gate_refuses_any_timer_on_v2(self):
        state = self.state("v2", sonyhp.V2_FEATURES)
        with self.assertRaises(ValueError) as caught:
            sonyhp.setting_requests_v2(state, "auto-power-off", "5-min")
        self.assertEqual(
            str(caught.exception),
            "unknown auto power off value '5-min'; expected 'off' or 'when-taken-off'",
        )

    def test_the_v1_builder_still_sends_the_same_bytes(self):
        state = self.state("v1", sonyhp.V1_FEATURES)
        self.assertEqual(
            sonyhp.setting_requests(state, "focus-on-voice", "true"),
            [(sonyhp.MSG_COMMAND_1, bytes([0x68, 0x02, 0x11, 0x02, 0x00, 0x01, 0x01, 13]))],
        )

    def test_the_v2_builder_still_sends_the_same_bytes(self):
        state = self.state("v2", sonyhp.V2_FEATURES)
        self.assertEqual(
            sonyhp.setting_requests_v2(state, "focus-on-voice", "true"),
            [
                (sonyhp.MSG_COMMAND_1, bytes([0x68, 0x19, 0x01, 0x01, 0x01, 0x01, 13, 0x01, 0x03])),
                (sonyhp.MSG_COMMAND_1, bytes([0x66, 0x19])),
            ],
        )


class RecordingProtocol(sonyhp.Protocol):
    """An adapter that records which operation it was asked for."""

    def __init__(self):
        self.calls = []

    def negotiate(self, link):
        self.calls.append("negotiate")

    def refresh_requests(self, state):
        self.calls.append("refresh")
        return []

    def poll_requests(self, state):
        self.calls.append("poll")
        return []

    def apply(self, state, msg_type, payload):
        self.calls.append("apply")
        return False

    def setting_requests(self, state, key, value):
        self.calls.append("setting")
        return []

    def power_off(self, link):
        self.calls.append("power_off")


class TestProtocolSeam(unittest.TestCase):
    """Link makes every wire decision through one adapter."""

    def handshake(self, reply):
        link = sonyhp.Link("AA:BB:CC:DD:EE:FF")
        link.sock = FakeStream(sonyhp.encode_message(sonyhp.MSG_COMMAND_1, 1, reply))
        self.assertTrue(link._handshake())
        return link

    def test_the_handshake_selects_the_adapter_with_the_name(self):
        cases = (
            (bytes([sonyhp.INIT_REPLY, 0x00, 0x40, 0x10]), "v1", sonyhp.V1Protocol),
            (bytes([sonyhp.V2_INIT_REPLY, 0x00, 0x03, 0x00, 0x30, 0x32, 0x00, 0x00]),
             "v2", sonyhp.V2Protocol),
        )
        for reply, protocol, adapter_type in cases:
            with self.subTest(protocol=protocol):
                link = self.handshake(reply)
                self.assertEqual(link.protocol, protocol)
                self.assertIsInstance(link.adapter, adapter_type)

    def test_each_adapter_delegates_to_its_own_builders(self):
        self.assertEqual(sonyhp.V1Protocol().refresh_requests({}), sonyhp.refresh_requests())
        self.assertEqual(sonyhp.V2Protocol().refresh_requests({}), sonyhp.v2_refresh_requests())
        state = sonyhp.initial_state()
        self.assertEqual(
            sonyhp.V1Protocol().setting_requests(state, "dsee", "on"),
            sonyhp.setting_requests(state, "dsee", "on"),
        )
        self.assertEqual(
            sonyhp.V2Protocol().setting_requests(state, "connection-quality", "stable"),
            sonyhp.setting_requests_v2(state, "connection-quality", "stable"),
        )

    def test_each_adapter_parses_with_its_own_parser(self):
        v1 = sonyhp.initial_state()
        self.assertTrue(
            sonyhp.V1Protocol().apply(v1, sonyhp.MSG_COMMAND_1, bytes([0x11, 0x00, 0x5A, 0x01])))
        self.assertEqual(v1["battery"], 90)
        v2 = sonyhp.initial_state()
        sonyhp.V2Protocol().apply(v2, sonyhp.MSG_COMMAND_1, bytes([0x23, 0x00, 0x52, 0x01]))
        self.assertEqual(v2["battery"], 82)

    def test_the_poll_question_comes_from_the_adapter(self):
        state = sonyhp.initial_state()
        self.assertEqual(
            sonyhp.V1Protocol().poll_requests(state),
            [(sonyhp.MSG_COMMAND_1, bytes([sonyhp.BATTERY_GET, sonyhp.BATTERY_SINGLE]))],
        )
        self.assertEqual(sonyhp.V2Protocol().poll_requests(state), [
            (sonyhp.MSG_COMMAND_1, bytes([sonyhp.V2_BATTERY_GET, sonyhp.V2_BATTERY_SINGLE])),
            (sonyhp.MSG_COMMAND_1, bytes([sonyhp.V2_ASC_GET, sonyhp.V2_ASC_SUBTYPE_QUERY])),
        ])

    def test_power_off_is_the_adapters_decision(self):
        link = sonyhp.DemoLink()
        sonyhp.V1Protocol().power_off(link)
        with self.assertRaises(ValueError):
            sonyhp.V2Protocol().power_off(link)

    def test_link_routes_every_decision_through_its_adapter(self):
        link = sonyhp.Link("AA:BB:CC:DD:EE:FF")
        recorder = RecordingProtocol()
        link.adapter = recorder
        link.answers_immediately = True

        link.poll_requests()
        link.power_off()
        link.refresh()
        link.dispatch(sonyhp.MSG_COMMAND_1, bytes([0x11, 0x00, 0x5A, 0x01]))
        sonyhp.apply_setting(link, "dsee", "on")

        self.assertEqual(recorder.calls, ["poll", "power_off", "refresh", "apply", "setting"])


class TestProtocolNegotiation(unittest.TestCase):
    """Each protocol decides for itself what opening a link has to settle."""

    def test_v1_negotiation_is_a_no_op(self):
        link = sonyhp.Link("AA:BB:CC:DD:EE:FF")
        with mock.patch.object(link, "write") as write, mock.patch.object(link, "pump"):
            self.assertIsNone(sonyhp.V1Protocol().negotiate(link))
        write.assert_not_called()

    def test_v2_negotiation_runs_the_three_dialect_probes(self):
        link = sonyhp.Link("AA:BB:CC:DD:EE:FF")
        with mock.patch.object(link, "select_asc_subtype") as asc, \
                mock.patch.object(link, "select_eq_subtype") as eq, \
                mock.patch.object(link, "select_bgm_subtype") as bgm:
            sonyhp.V2Protocol().negotiate(link)
        asc.assert_called_once_with()
        eq.assert_called_once_with()
        bgm.assert_called_once_with()

    def test_establish_negotiates_through_the_adapter(self):
        link = sonyhp.Link("AA:BB:CC:DD:EE:FF")
        recorder = RecordingProtocol()
        link.adapter = recorder
        with mock.patch.object(link, "_handshake", return_value=True), \
                mock.patch.object(sonyhp, "remember_channel"):
            link._establish(9)
        self.assertEqual(recorder.calls, ["negotiate"])

    def test_a_v1_establish_sends_no_subtype_queries(self):
        link = sonyhp.Link("AA:BB:CC:DD:EE:FF")
        link.adapter = sonyhp.V1Protocol()
        link.write = mock.Mock()
        with mock.patch.object(link, "_handshake", return_value=True), \
                mock.patch.object(sonyhp, "remember_channel"):
            link._establish(9)
        link.write.assert_not_called()


class RecordingTransport(sonyhp.Transport):
    """A transport that keeps what Link sends and replays what is queued."""

    def __init__(self):
        self.sock = object()
        self.sent = []
        self.pending = []
        self.closed = False

    def open(self, timeout):
        return 5

    def send(self, frame):
        self.sent.append(frame)

    def recv(self, timeout):
        if not self.pending:
            raise socket.timeout("nothing queued")
        return self.pending.pop(0)

    def close(self):
        self.closed = True
        self.sock = None


class TestTransportSeam(unittest.TestCase):
    """The bytes come from a transport; Link never looks past it."""

    def test_the_demo_links_inherit_the_one_framing(self):
        for link in (sonyhp.DemoLink(), sonyhp.DemoLinkV2()):
            with self.subTest(link=type(link).__name__):
                self.assertIsInstance(link.transport, sonyhp.DemoTransport)
                self.assertIs(type(link).write, sonyhp.Link.write)
                self.assertIs(type(link).pump, sonyhp.Link.pump)
                self.assertIs(type(link)._read_frame, sonyhp.Link._read_frame)

    def test_a_real_link_gets_the_rfcomm_transport(self):
        link = sonyhp.Link("AA:BB:CC:DD:EE:FF")
        self.assertIsInstance(link.transport, sonyhp.RfcommTransport)
        self.assertIs(link.transport.link, link)

    def test_link_sends_through_its_transport(self):
        link = sonyhp.Link("AA:BB:CC:DD:EE:FF")
        transport = RecordingTransport()
        link.transport = transport
        link.write(sonyhp.MSG_COMMAND_1, bytes([0x10, 0x00]), wait_ack=False)
        self.assertEqual(sonyhp.decode_message(transport.sent[0]),
                         (sonyhp.MSG_COMMAND_1, 0, bytes([0x10, 0x00])))

    def test_link_reads_through_its_transport(self):
        link = sonyhp.Link("AA:BB:CC:DD:EE:FF")
        link.transport = RecordingTransport()
        link.transport.pending.append(sonyhp.encode_message(
            sonyhp.MSG_COMMAND_1, 1, bytes([0x11, 0x00, 0x5A, 0x01])))
        link.pump(0.05)
        self.assertEqual(link.state["battery"], 90)
        self.assertGreater(link.last_rx, 0.0)

    def test_close_reaches_the_transport(self):
        link = sonyhp.Link("AA:BB:CC:DD:EE:FF")
        transport = RecordingTransport()
        link.transport = transport
        link.state["connected"] = True
        link.close()
        self.assertTrue(transport.closed)
        self.assertIsNone(link.sock)
        self.assertFalse(link.state["connected"])

    def test_assigning_sock_moves_the_transports_stream(self):
        stream = FakeStream(b"")
        link = sonyhp.Link("AA:BB:CC:DD:EE:FF")
        link.sock = stream
        self.assertIs(link.transport.sock, stream)
        self.assertIs(link.sock, stream)

    def test_the_rfcomm_transport_tries_candidates_in_order(self):
        link = sonyhp.Link("AA:BB:CC:DD:EE:FF")
        streams = {7: FakeStream(b"")}
        opened = []

        def open_channel(channel, timeout):
            opened.append(channel)
            if channel not in streams:
                raise OSError("connection refused")
            return streams[channel]

        with mock.patch.object(sonyhp, "sdp_channel", side_effect=[9, 7]), \
                mock.patch.object(sonyhp, "cached_channel", return_value=None), \
                mock.patch.object(sonyhp.Link, "_open", side_effect=open_channel):
            channel = link.transport.open(1.0)
        self.assertEqual(channel, 7)
        self.assertEqual(opened, [9, 7])
        self.assertIs(link.sock, streams[7])

    def test_a_total_failure_forgets_the_cached_channel(self):
        link = sonyhp.Link("AA:BB:CC:DD:EE:FF")
        with mock.patch.object(sonyhp, "sdp_channel", return_value=9), \
                mock.patch.object(sonyhp, "cached_channel", return_value=None), \
                mock.patch.object(sonyhp.Link, "_open", side_effect=OSError("refused")), \
                mock.patch.object(sonyhp, "forget_channel") as forget:
            with self.assertRaises(sonyhp.NotConnected):
                link.transport.open(1.0)
        forget.assert_called_once_with(link.address)

    def test_the_demo_transport_acks_then_answers(self):
        transport = sonyhp.DemoLink().transport
        transport.send(sonyhp.encode_message(sonyhp.MSG_COMMAND_1, 0, bytes([0x10, 0x00])))
        self.assertEqual(sonyhp.decode_message(transport.recv(0.0)), (sonyhp.MSG_ACK, 1, b""))
        reply_type, _, reply = sonyhp.decode_message(transport.recv(0.0))
        self.assertEqual((reply_type, reply[0]), (sonyhp.MSG_COMMAND_1, sonyhp.BATTERY_RET))

    def test_the_demo_transport_does_not_answer_an_ack(self):
        transport = sonyhp.DemoLink().transport
        transport.send(sonyhp.encode_message(sonyhp.MSG_ACK, 1, b""))
        self.assertEqual(transport.pending, [])


class TestLinkNegotiationFailure(unittest.TestCase):
    """A failed v2 dialect negotiation is a connect failure, not a leak.

    All three dialect queries are part of opening the link, so any of them
    failing has to close the socket and surface as NotConnected rather than
    escaping as a raw error the daemon does not catch.
    """

    REPLY = bytes([sonyhp.V2_INIT_REPLY, 0x00, 0x03, 0x00, 0x30, 0x32, 0x00, 0x00])
    NEGOTIATIONS = ("select_asc_subtype", "select_eq_subtype", "select_bgm_subtype")

    def patches(self, stream, negotiate=True, **selects):
        patched = [
            mock.patch.object(sonyhp, "sdp_channel", return_value=9),
            mock.patch.object(sonyhp, "cached_channel", return_value=None),
            mock.patch.object(sonyhp, "remember_channel"),
            mock.patch.object(sonyhp.Link, "_open", return_value=stream),
        ]
        if negotiate:
            for name in self.NEGOTIATIONS:
                patched.append(mock.patch.object(
                    sonyhp.Link, name, selects.get(name, mock.Mock(return_value=None))))
        return patched

    def test_a_device_that_drops_during_negotiation_closes_and_raises(self):
        link = sonyhp.Link("AA:BB:CC:DD:EE:FF")
        link.state["name"] = "WH-1000XM6"
        reply = sonyhp.encode_message(sonyhp.MSG_COMMAND_1, 1, self.REPLY)
        stream = FakeStream(lambda n: reply if n == 1 else b"")
        with self.assertRaises(sonyhp.NotConnected):
            with contextlib.ExitStack() as stack:
                for patch in self.patches(stream, negotiate=False):
                    stack.enter_context(patch)
                link.connect()
        self.assertTrue(stream.closed)
        self.assertIsNone(link.sock)
        self.assertFalse(link.state["connected"])

    def test_every_negotiation_step_fails_the_connect_the_same_way(self):
        reply = sonyhp.encode_message(sonyhp.MSG_COMMAND_1, 1, self.REPLY)
        for failing in self.NEGOTIATIONS:
            with self.subTest(failing=failing):
                stream = FakeStream(reply)
                link = sonyhp.Link("AA:BB:CC:DD:EE:FF")
                link.state["name"] = "WH-1000XM6"
                broken = mock.Mock(side_effect=sonyhp.ProtocolError("boom"))
                with self.assertRaises(sonyhp.NotConnected):
                    with contextlib.ExitStack() as stack:
                        for patch in self.patches(stream, **{failing: broken}):
                            stack.enter_context(patch)
                        link.connect()
                self.assertTrue(stream.closed, failing)
                self.assertIsNone(link.sock, failing)


class TestConnectCandidateRetry(unittest.TestCase):
    """A candidate that connects but will not settle falls through to the next.

    The transport opens candidates in order; the init handshake and the v2
    dialect queries are what decide whether a socket really is the control
    channel. A candidate that refuses the handshake has to be closed and the
    next one tried before the connect is declared a failure.
    """

    REPLY = bytes([sonyhp.INIT_REPLY, 0x00, 0x40, 0x10])

    @staticmethod
    def silent(size):
        raise socket.timeout("the candidate never answers")

    @staticmethod
    def open_streams(streams):
        def open_channel(channel, timeout):
            return streams[channel]
        return open_channel

    def test_a_silent_candidate_falls_through_to_the_next(self):
        first = FakeStream(self.silent)
        second = FakeStream(sonyhp.encode_message(sonyhp.MSG_COMMAND_1, 1, self.REPLY))
        link = sonyhp.Link("AA:BB:CC:DD:EE:FF")
        link.state["name"] = "WH-1000XM4"
        with mock.patch.object(sonyhp, "sdp_channel", side_effect=[9, 7]), \
                mock.patch.object(sonyhp, "cached_channel", return_value=None), \
                mock.patch.object(sonyhp.Link, "_open",
                                  side_effect=self.open_streams({9: first, 7: second})), \
                mock.patch.object(sonyhp, "remember_channel") as remember, \
                mock.patch.object(sonyhp, "forget_channel") as forget, \
                mock.patch.object(sonyhp.Link, "ACK_TIMEOUT", 0.01):
            channel = link.connect()
        self.assertEqual(channel, 7)
        self.assertEqual(link.protocol, "v1")
        self.assertTrue(link.state["connected"])
        self.assertTrue(first.closed, "the silent candidate has to be closed")
        remember.assert_called_once_with(link.address, 7)
        forget.assert_not_called()

    def channel_queries(self, mapping):
        """A scripted `sdp_channel` that records the UUIDs it was asked for."""
        asked = []

        def sdp(address, uuid=sonyhp.SERVICE_UUID_BYTES, timeout=8.0):
            asked.append(uuid)
            return mapping.get(uuid)

        return sdp, asked

    def test_the_second_uuid_is_queried_only_after_the_first_candidate_fails(self):
        first = FakeStream(self.silent)
        second = FakeStream(sonyhp.encode_message(sonyhp.MSG_COMMAND_1, 1, self.REPLY))
        link = sonyhp.Link("AA:BB:CC:DD:EE:FF")
        link.state["name"] = "WH-1000XM4"
        sdp, asked = self.channel_queries({sonyhp.SERVICE_UUID_V2_BYTES: 9})

        def open_channel(channel, timeout):
            if channel == 9:
                # The first UUID answered; the second is not looked up until
                # this candidate has been offered and failed.
                self.assertEqual(asked, [sonyhp.SERVICE_UUID_V2_BYTES])
            return {9: first, 7: second}[channel]

        with mock.patch.object(sonyhp, "sdp_channel", side_effect=sdp), \
                mock.patch.object(sonyhp, "cached_channel", return_value=7), \
                mock.patch.object(sonyhp.Link, "_open", side_effect=open_channel), \
                mock.patch.object(sonyhp, "remember_channel") as remember, \
                mock.patch.object(sonyhp, "forget_channel") as forget, \
                mock.patch.object(sonyhp.Link, "ACK_TIMEOUT", 0.01):
            channel = link.connect()
        self.assertEqual(channel, 7)
        self.assertEqual(
            asked, [sonyhp.SERVICE_UUID_V2_BYTES, sonyhp.SERVICE_UUID_BYTES])
        self.assertTrue(first.closed, "the failed candidate has to be closed")
        remember.assert_called_once_with(link.address, 7)
        forget.assert_not_called()

    def test_a_first_hit_never_queries_the_second_uuid(self):
        stream = FakeStream(sonyhp.encode_message(sonyhp.MSG_COMMAND_1, 1, self.REPLY))
        link = sonyhp.Link("AA:BB:CC:DD:EE:FF")
        link.state["name"] = "WH-1000XM4"
        with mock.patch.object(sonyhp, "sdp_channel", return_value=9) as sdp, \
                mock.patch.object(sonyhp, "cached_channel", return_value=None) as cached, \
                mock.patch.object(sonyhp.Link, "_open", return_value=stream), \
                mock.patch.object(sonyhp, "remember_channel"), \
                mock.patch.object(sonyhp, "forget_channel"):
            channel = link.connect()
        self.assertEqual(channel, 9)
        self.assertEqual(sdp.call_count, 1)
        self.assertEqual(sdp.call_args.kwargs.get("uuid"), sonyhp.SERVICE_UUID_V2_BYTES)
        cached.assert_not_called()

    def test_a_first_uuid_with_no_channel_still_tries_the_second_and_the_cache(self):
        opened = []

        def open_channel(channel, timeout):
            opened.append(channel)
            return FakeStream(b"")

        def settle(channel):
            if channel != 5:
                raise sonyhp.NotConnected("not this one")

        link = sonyhp.Link("AA:BB:CC:DD:EE:FF")
        sdp, asked = self.channel_queries({sonyhp.SERVICE_UUID_BYTES: 7})
        with mock.patch.object(sonyhp, "sdp_channel", side_effect=sdp), \
                mock.patch.object(sonyhp, "cached_channel", return_value=5), \
                mock.patch.object(sonyhp.Link, "_open", side_effect=open_channel), \
                mock.patch.object(sonyhp, "forget_channel") as forget:
            channel = link.transport.open(1.0, attempt=settle)
        self.assertEqual(channel, 5)
        self.assertEqual(opened, [7, 5])
        self.assertEqual(
            asked, [sonyhp.SERVICE_UUID_V2_BYTES, sonyhp.SERVICE_UUID_BYTES])
        forget.assert_not_called()

    def test_a_total_handshake_failure_forgets_the_cached_channel(self):
        streams = {}

        def open_channel(channel, timeout):
            streams[channel] = FakeStream(self.silent)
            return streams[channel]

        link = sonyhp.Link("AA:BB:CC:DD:EE:FF")
        with mock.patch.object(sonyhp, "sdp_channel", side_effect=[9, 7]), \
                mock.patch.object(sonyhp, "cached_channel", return_value=5), \
                mock.patch.object(sonyhp.Link, "_open", side_effect=open_channel), \
                mock.patch.object(sonyhp, "remember_channel"), \
                mock.patch.object(sonyhp, "forget_channel") as forget, \
                mock.patch.object(sonyhp.Link, "ACK_TIMEOUT", 0.01):
            with self.assertRaises(sonyhp.NotConnected):
                link.connect()
        forget.assert_called_with(link.address)
        self.assertFalse(link.state["connected"])
        self.assertEqual(sorted(streams), [5, 7, 9])
        self.assertTrue(all(stream.closed for stream in streams.values()))


class TestSdpChannelBounds(unittest.TestCase):
    """A peer-named RFCOMM channel is only dialled when it is in range."""

    @staticmethod
    def record_with(element):
        return de_seq(de_seq(
            de_uint16(0x0004),
            de_seq(de_seq(de_uuid16(0x0100)), de_seq(de_uuid16(0x0003), element)),
        ))

    def channel_in(self, element):
        tree = sonyhp.parse_sdp_record(self.record_with(element))
        return sonyhp._find_rfcomm_channel(tree)

    def test_the_edges_of_the_range_are_accepted(self):
        self.assertEqual(self.channel_in(de_uint8(1)), 1)
        self.assertEqual(self.channel_in(de_uint8(30)), 30)

    def test_out_of_range_channels_are_ignored(self):
        huge = bytes([0x0B]) + (1 << 40).to_bytes(8, "big")
        for channel, element in ((0, de_uint8(0)), (31, de_uint8(31)), (1 << 40, huge)):
            with self.subTest(channel=channel):
                self.assertIsNone(self.channel_in(element))

    def test_an_ignored_channel_never_reaches_a_connect_call(self):
        link = sonyhp.Link("AA:BB:CC:DD:EE:FF")
        with mock.patch.object(sonyhp, "sdp_channel", return_value=None), \
                mock.patch.object(sonyhp, "cached_channel", return_value=None), \
                mock.patch.object(sonyhp.Link, "_open") as opened:
            with self.assertRaises(sonyhp.NotConnected):
                link.transport.open(1.0)
        opened.assert_not_called()


class TestSubtypeProbeOrder(unittest.TestCase):
    """Each dialect query keeps its deliberate candidate order and fallback.

    The three queries share one implementation, so this pins the per-family
    data that implementation now depends on.
    """

    def negotiate(self, method):
        link = sonyhp.Link("AA:BB:CC:DD:EE:FF")
        sent = []
        with mock.patch.object(
                link, "write",
                side_effect=lambda msg_type, payload: sent.append(payload)), \
                mock.patch.object(link, "pump"):
            result = getattr(link, method)()
        return result, sent

    def test_asc_asks_the_query_subtype_first(self):
        result, sent = self.negotiate("select_asc_subtype")
        self.assertEqual(sent, [bytes([sonyhp.V2_ASC_GET, subtype])
                                for subtype in (0x19, 0x17, 0x15, 0x22)])
        self.assertIsNone(result)

    def test_the_membership_table_and_the_probe_order_are_separate(self):
        self.assertEqual(sonyhp.V2_ASC_SUBTYPES, (0x15, 0x17, 0x19, 0x22))
        self.assertEqual(sonyhp.V2_ASC_PROBE_ORDER, (0x19, 0x17, 0x15, 0x22))
        self.assertEqual(sorted(sonyhp.V2_ASC_SUBTYPES),
                         sorted(sonyhp.V2_ASC_PROBE_ORDER))

    def test_eq_prefers_the_xm6_subtype_and_falls_back_to_it(self):
        result, sent = self.negotiate("select_eq_subtype")
        self.assertEqual(sent, [bytes([sonyhp.V2_EQ_GET, subtype]) for subtype in (0x04, 0x00)])
        self.assertEqual(result, 0x04)

    def test_bgm_asks_its_subtypes_in_order_and_falls_back(self):
        result, sent = self.negotiate("select_bgm_subtype")
        self.assertEqual(sent, [bytes([sonyhp.V2_AUDIO_GET, subtype])
                                for subtype in (sonyhp.V2_AUDIO_SUB_BGM,
                                                sonyhp.V2_AUDIO_SUB_BGM_ALT)])
        self.assertEqual(result, sonyhp.V2_AUDIO_SUB_BGM)


class TestParserBoundary(unittest.TestCase):
    """One unparseable frame must not take the daemon down with it.

    Daemon.run catches only NotConnected and OSError, so a parser exception
    that escaped dispatch would end the process rather than the frame.
    """

    def test_dispatch_ignores_an_unexpected_parser_error(self):
        link = sonyhp.DemoLink()
        link.adapter.apply = mock.Mock(side_effect=IndexError("boom"))
        before = dict(link.state)
        with mock.patch.object(sonyhp, "trace") as traced:
            link.dispatch(sonyhp.MSG_COMMAND_1, bytes([0x11, 0x00, 0x5A, 0x01]))
        self.assertEqual(link.state, before)
        self.assertTrue(link.adapter.apply.called)
        traced.assert_called_once()
        self.assertEqual(traced.call_args[0][0], "rx")

    def test_the_daemons_pump_survives_a_raising_parser(self):
        frame = sonyhp.encode_message(sonyhp.MSG_COMMAND_1, 1, bytes([0x11, 0x00, 0x5A, 0x01]))
        link = sonyhp.Link("AA:BB:CC:DD:EE:FF")
        link.sock = FakeStream(frame)
        link.adapter.apply = mock.Mock(side_effect=IndexError("boom"))
        link.pump(0.05)  # returns instead of propagating up into Daemon.run
        self.assertTrue(link.adapter.apply.called)


if __name__ == "__main__":
    unittest.main(verbosity=2)
