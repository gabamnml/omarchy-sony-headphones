"""The CLI, the no-daemon path and how the shell launches the helper.

Service.qml is read as text — there is no QML engine here — to pin the
interpreter, the -I flag and the minimal environment. The rest runs the
helper's direct path, the demo selection and the helper as a subprocess
the way the widget runs it.
"""

import contextlib
import io
import json
import logging
import os
import re
import shutil
import subprocess
import tempfile
import unittest
from unittest import mock

import support
from support import FakeClock, HELPER

sonyhp = support.sonyhp


def assert_one_spawn_refreshes_only_on_failure(test, source, start, end):
    """Pin a --json Process to one refresh(), and only in its failure branch.

    The reply is authoritative, so a success must not spawn a follow-up. The
    count asserts absence: the failure-branch regex alone would still pass if
    an unconditional refresh() were added before the if.
    """
    process = source[source.index(start):source.index(end)]
    test.assertRegex(process,
                     r"stdout:\s*SplitParser\s*\{\s*onRead:\s*function\s*\(line\)\s*\{\s*"
                     r"root\s*\.\s*applyLine\s*\(\s*line\s*\)")
    test.assertEqual(len(re.findall(r"refresh\s*\(\s*\)", process)), 1,
                     "a successful action must not spawn a follow-up refresh")
    test.assertRegex(process,
                     r'if\s*\(\s*exitCode\s*===\s*0\s*\)\s*root\s*\.\s*lastError\s*=\s*""'
                     r"\s*else\s+root\s*\.\s*refresh\s*\(\s*\)")


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
        self.assertEqual(processes, 5)
        self.assertEqual(self.source.count("clearEnvironment: true"), processes)
        self.assertEqual(self.source.count("environment: root.helperEnvironment"), processes)

    def test_the_environment_carries_only_what_the_helper_reads(self):
        self.assertIn('var env = { PATH: "/usr/bin:/bin" }', self.source)
        self.assertIn('["HOME", "XDG_RUNTIME_DIR", "XDG_CACHE_HOME", "SONY_HEADPHONES_DEMO"]', self.source)
        # The log level and the session policy come from plugin settings, not
        # the shell's environment, so a fresh daemon starts where the panel
        # put them.
        self.assertIn("env.SONY_HEADPHONES_LOG = root.logging", self.source)
        self.assertIn("env.SONY_HEADPHONES_SESSION = root.sessionPolicy", self.source)
        self.assertIn("env.SONY_HEADPHONES_SESSION_IDLE = String(root.idleSeconds)", self.source)

    def test_the_helper_itself_names_the_system_interpreter(self):
        with open(HELPER, encoding="utf-8") as handle:
            self.assertEqual(handle.readline().strip(), "#!/usr/bin/python3")

    def test_a_set_action_runs_one_json_process(self):
        # A click runs one helper: the --json set reply is authoritative, so
        # the command carries --json rather than relying on a follow-up status.
        self.assertRegex(
            self.source,
            r'argv\(\s*\[\s*"--json"\s*,\s*"set"\s*,\s*key\s*,\s*String\s*\(\s*value\s*\)\s*\]\s*\)',
        )

    def test_the_set_process_is_one_spawn_and_refreshes_only_on_failure(self):
        # The parser folds the reply into state; only a failure resyncs, so a
        # successful toggle never spawns a second helper.
        assert_one_spawn_refreshes_only_on_failure(
            self, self.source, "id: setProcess", "id: loggingProcess")


class PanelSourceMixin:
    """Read Panel.qml once per test, as text; there is no QML engine here.

    The panel assertions are text-level, so every class that reads Panel.qml
    shares the read instead of re-declaring the path and reopening the file.
    """

    PANEL = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "Panel.qml")

    def setUp(self):
        super().setUp()
        with open(self.PANEL, encoding="utf-8") as handle:
            self.source = handle.read()


class TestPanelLoggingLink(PanelSourceMixin, unittest.TestCase):
    """The logging link is the daemon's, so a dead connection must not hide it.

    There is no QML engine here, so these read Panel.qml as text: enough to
    keep the link out of the connected-only footer it used to share with the
    codec and firmware caption.
    """

    def test_the_footer_is_not_gated_on_a_connection(self):
        # The nearest enclosing RowLayout is the footer. Its own properties
        # come before its first child, so a connected-only gate would sit
        # between the two.
        link = self.source.index("Model.loggingLabel(")
        row = self.source.rindex("RowLayout {", 0, link)
        first_child = self.source.index("Item {", row)
        self.assertNotIn("visible: sony.connected", self.source[row:first_child],
                         "the logging footer must stay reachable while disconnected")

    def test_the_codec_caption_is_still_gated_on_displayed_state(self):
        self.assertRegex(self.source, r"id:\s*caption[\s\S]{0,400}visible:\s*sony\.showing")

    def test_the_panel_renders_and_cycles_the_logging_label(self):
        self.assertRegex(self.source,
                         r"Model\s*\.\s*loggingLabel\s*\(\s*sony\.state\.logging\s*\)")
        self.assertRegex(self.source,
                         r"sony\s*\.\s*setLogging\s*\(\s*Model\s*\.\s*nextLogging\s*\(\s*sony\.state\.logging\s*\)\s*\)")


class TestPanelOpen(PanelSourceMixin, unittest.TestCase):
    """Opening the panel is not a resync point, so it must not spawn a helper.

    The watch subscription carries the current state on subscribe and every
    change after it, so the open handler only resets the cursor, scrolls to the
    top and focuses the key catcher. There is no QML engine here, so Panel.qml
    is read as text and the handler body is pulled out, following PanelSourceMixin.
    """

    def setUp(self):
        super().setUp()
        # The slice runs from the handler's name to the next top-level block,
        # so it spans just the onOpenedChanged body; the panel's other
        # refresh() calls (middle-click and `r`) fall outside it.
        self.handler = self.source[
            self.source.index("onOpenedChanged"):self.source.index("IpcHandler {")
        ]

    def test_opening_the_panel_does_not_refresh(self):
        # The pre-change handler called sony.refresh() between the scroll reset
        # and the focus call; that redundant Python startup is what this pins
        # out.
        self.assertNotRegex(self.handler, r"refresh\s*\(\s*\)",
                            "opening the panel must not spawn a helper")

    def test_opening_the_panel_keeps_its_other_three_jobs(self):
        self.assertRegex(self.handler, r"cursorActive\s*=\s*false")
        self.assertRegex(self.handler, r"cursorIndex\s*=\s*0")
        self.assertRegex(self.handler, r"panelFlick\s*\.\s*contentY\s*=\s*0")
        self.assertRegex(self.handler,
                         r"Qt\s*\.\s*callLater\s*\(\s*function\s*\(\s*\)\s*\{\s*"
                         r"keyCatcher\s*\.\s*forceActiveFocus\s*\(\s*\)")

    def test_middle_click_still_refreshes(self):
        self.assertRegex(self.source,
                         r"buttonCode\s*===\s*Qt\s*\.\s*MiddleButton\s*\)\s*"
                         r"sony\s*\.\s*refresh\s*\(\s*\)")

    def test_the_r_key_still_refreshes(self):
        self.assertRegex(self.source,
                         r'key\s*===\s*"r"\s*\)\s*sony\s*\.\s*refresh\s*\(\s*\)')


class TestServiceLoggingWiring(unittest.TestCase):
    """A logging click has to reach the daemon with the plugin's boot level."""

    def setUp(self):
        with open(TestShellLaunch.SERVICE, encoding="utf-8") as handle:
            self.source = handle.read()

    def test_the_logging_process_sends_the_level_as_json(self):
        # Copy of set(): one --json process whose reply is the new state.
        self.assertRegex(self.source,
                         r'argv\(\s*\[\s*"--json"\s*,\s*"logging"\s*,\s*String\(level\)\s*\]\s*\)')

    def test_the_logging_process_is_one_spawn_and_refreshes_only_on_failure(self):
        # Same one-spawn rule as set(): the --json reply is authoritative, and
        # only a failure falls back to a status refresh.
        assert_one_spawn_refreshes_only_on_failure(
            self, self.source, "id: loggingProcess", "id: sessionProcess")

    def test_the_log_level_boots_from_the_plugin_setting(self):
        self.assertRegex(self.source, r"env\.SONY_HEADPHONES_LOG\s*=\s*root\.logging")


class TestPresenceWiring(unittest.TestCase):
    """Bluetooth presence drives release/reclaim as events, not a poll.

    There is no QML engine here, so Service.qml is read as text, following
    TestShellLaunch: enough to catch the Bluetooth import going missing,
    `present` disappearing, or an on-demand release that a presence gain would
    not reclaim — or one that would reclaim a session the user released by
    hand.
    """

    def setUp(self):
        with open(TestShellLaunch.SERVICE, encoding="utf-8") as handle:
            self.source = handle.read()

    def presence_handler(self):
        # The handler body only, so the other Service.qml text cannot satisfy
        # the assertions below.
        return self.source[
            self.source.index("onPresentChanged"):self.source.index("function refresh")]

    def test_the_service_imports_the_bluetooth_module(self):
        self.assertRegex(self.source,
                         r"(?m)^import\s+Quickshell\.Bluetooth\s*$")

    def test_present_reads_the_bluetooth_service(self):
        self.assertRegex(self.source, r"readonly\s+property\s+bool\s+present\s*:")
        self.assertRegex(self.source, r"Bluetooth\s*\.\s*devices")
        # The presence decision is the pure Model.present, so the QML only
        # flattens the Bluetooth devices into the list the rule takes.
        self.assertRegex(self.source, r"Model\s*\.\s*present\s*\(")
        # Only connected devices count; the mapping reads each device's fields
        # so the binding re-evaluates on a connect or disconnect.
        self.assertRegex(self.source, r"device\s*\.\s*connected")
        self.assertRegex(self.source, r"device\s*\.\s*address")
        self.assertRegex(self.source, r"device\s*\.\s*deviceName")

    def test_presence_only_acts_under_on_demand(self):
        # The guard's `return` is what makes `hold` a no-op; the condition alone
        # would fall through and release. Pin the early exit itself.
        self.assertRegex(self.presence_handler(),
                         r'if\s*\(\s*sessionPolicy\s*!==\s*"on-demand"\s*\)\s*return\b')

    def test_presence_loss_releases_and_gain_reclaims(self):
        handler = self.presence_handler()
        self.assertRegex(handler, r"!\s*present[\s\S]{0,200}?release\s*\(\s*\)")
        self.assertRegex(handler, r"presenceReleased\s*=\s*true")
        # The reclaim is gated on the presence reason, not run unconditionally.
        self.assertRegex(handler,
                         r"else\s+if\s*\(\s*presenceReleased\s*\)\s*\{?\s*reclaim\s*\(\s*\)")

    def test_a_manual_release_is_never_reclaimed(self):
        # release() clears the presence reason, so a later presence gain leaves
        # a session the user released by hand alone.
        release = self.source[
            self.source.index("function release"):self.source.index("function reclaim")]
        self.assertRegex(release, r"presenceReleased\s*=\s*false")
        # The loss branch also skips an already-released session.
        self.assertRegex(self.presence_handler(), r'session\s*===\s*"released"')

    def test_presence_adds_no_timer(self):
        # Presence is a property change, so the watcher's restart timer stays
        # the only timer in the file.
        self.assertEqual(self.source.count("Timer {"), 1)
        self.assertRegex(self.source, r"id:\s*restartTimer[\s\S]{0,80}interval:\s*5000")
        self.assertNotRegex(self.presence_handler(), r"Timer\s*\{")


class TestMprisWiring(unittest.TestCase):
    """Switching the source away pauses playback on this machine, once.

    There is no QML engine here, so Service.qml is read as text, following
    TestPresenceWiring: enough to catch the MPRIS import going missing, the
    pause decision moving off the pure Model rule, or a phone-side switch
    (a state line through applyLine) pausing local playback.
    """

    def setUp(self):
        with open(TestShellLaunch.SERVICE, encoding="utf-8") as handle:
            self.source = handle.read()

    def chooser(self):
        # The chooser body only, so the other Service.qml text cannot satisfy
        # the assertions below.
        return self.source[
            self.source.index("function choosePlaybackSource"):self.source.index("function refresh")]

    def pauser(self):
        # The pauser body only, so the chooser's call cannot satisfy the
        # assertions below.
        return self.source[
            self.source.index("function pauseLocalPlayers"):self.source.index("function choosePlaybackSource")]

    def test_the_service_imports_the_mpris_module(self):
        self.assertRegex(self.source,
                         r"(?m)^import\s+Quickshell\.Services\.Mpris\s*$")

    def test_the_pause_decision_is_the_pure_model_rule(self):
        self.assertRegex(self.chooser(), r"Model\s*\.\s*pausesLocalPlayback\s*\(")
        self.assertRegex(self.chooser(), r"Model\s*\.\s*playbackSource\s*\(\s*state\s*\)")

    def test_the_pause_asks_players_that_can_pause(self):
        pauser = self.pauser()
        self.assertRegex(pauser, r"Mpris\s*\.\s*players")
        self.assertRegex(pauser, r"canPause")
        self.assertRegex(pauser, r"pause\s*\(\s*\)")
        # The chooser reaches the pauser through the pure rule, so picking
        # another peer pauses and re-picking the current one does not.
        self.assertRegex(self.chooser(), r"pauseLocalPlayers\s*\(\s*\)")

    def test_the_pause_is_selection_only(self):
        # The pause lives in the chooser the panel calls, so a state line the
        # phone's switch arrives on never touches local playback.
        self.assertRegex(self.source, r"function\s+choosePlaybackSource\s*\(")
        apply = self.source[
            self.source.index("function applyLine"):self.source.index("onHelperPathChanged")]
        self.assertNotRegex(apply, r"pause\s*\(\s*\)")
        self.assertNotRegex(apply, r"pausesLocalPlayback")


class TestSessionWiring(PanelSourceMixin, unittest.TestCase):
    """The session policy and manual toggle cross the QML text verbatim.

    There is no QML engine here, so Panel.qml and Service.qml are read as text,
    following TestShellLaunch: enough to catch a setting that never leaves the
    panel, a runtime session command that lost its fields, or a row that does
    not reach release/reclaim.
    """

    def setUp(self):
        super().setUp()
        base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        with open(os.path.join(base, "Service.qml"), encoding="utf-8") as handle:
            self.service = handle.read()

    def test_the_panel_reads_both_settings_and_passes_them_to_the_service(self):
        self.assertRegex(self.source, r'setting\(\s*"sessionPolicy"')
        self.assertRegex(self.source, r'setting\(\s*"idleSeconds"')
        self.assertRegex(self.source, r"sessionPolicy\s*:\s*root\.sessionPolicy")
        self.assertRegex(self.source, r"idleSeconds\s*:\s*root\.idleSeconds")

    def test_the_panel_clamps_idle_seconds_to_the_helper_bounds(self):
        # A hand-edited 0 or 200000 must not reach the daemon, which refuses an
        # idle outside 1..86400; the panel clamps through the mirrored Model.
        self.assertRegex(self.source,
                         r"idleSeconds\s*:\s*Model\s*\.\s*clampSessionIdle\s*\(\s*setting\(\s*\"idleSeconds\"")

    def test_the_service_forwards_both_environment_variables(self):
        self.assertRegex(self.service,
                         r"env\s*\.\s*SONY_HEADPHONES_SESSION\s*=\s*root\s*\.\s*sessionPolicy")
        self.assertRegex(self.service,
                         r"env\s*\.\s*SONY_HEADPHONES_SESSION_IDLE\s*=\s*String\s*\(\s*root\s*\.\s*idleSeconds\s*\)")

    def test_the_service_can_send_a_runtime_session_command(self):
        self.assertRegex(self.service, r'\[\s*"--json"\s*,\s*"session"\s*,')
        self.assertRegex(self.service, r'"--idle"')

    def test_the_service_spawns_release_and_reclaim(self):
        self.assertRegex(self.service, r"function\s+release\s*\(\s*\)")
        self.assertRegex(self.service, r"function\s+reclaim\s*\(\s*\)")
        self.assertRegex(self.service, r'argv\(\s*\[\s*"--json"\s*,\s*"release"\s*\]\s*\)')
        self.assertRegex(self.service, r'argv\(\s*\[\s*"--json"\s*,\s*"reclaim"\s*\]\s*\)')

    def test_the_session_action_is_one_helper_and_refreshes_only_on_failure(self):
        # The helper's --json reply is authoritative, so a successful action
        # must not spawn a follow-up status; only the failure path recovers.
        assert_one_spawn_refreshes_only_on_failure(
            self, self.service, "id: sessionProcess", "id: refreshProcess")

    def test_the_panel_row_labels_the_session_and_flips_it(self):
        self.assertRegex(self.source, r"Model\s*\.\s*sessionLabel\s*\(\s*sony\s*\.\s*state\s*\)")
        self.assertRegex(self.source, r"sony\s*\.\s*release\s*\(\s*\)")
        self.assertRegex(self.source, r"sony\s*\.\s*reclaim\s*\(\s*\)")

    def test_activating_a_connecting_session_is_a_no_op(self):
        # A held session releases and a released one reclaims; a connecting
        # session is mid-hand-off, so it must not release.
        self.assertRegex(self.source,
                         r'sony\s*\.\s*session\s*===\s*"held"\s*\)\s*sony\s*\.\s*release\s*\(\s*\)')

    def test_ipc_exposes_release_and_reclaim(self):
        self.assertRegex(self.source, r"function\s+release\s*\(\s*\)\s*:\s*string")
        self.assertRegex(self.source, r"function\s+reclaim\s*\(\s*\)\s*:\s*string")


class TestWriteThenVerifyWiring(PanelSourceMixin, unittest.TestCase):
    """A refused write has to reach the panel, not just the helper's state.

    There is no QML engine here, so Panel.qml and Service.qml are read as text,
    following PanelSourceMixin: enough to pin the refusal line, the per-row
    pending/refused markers and the optimistic pending patch that starts it.
    """

    def setUp(self):
        super().setUp()
        base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        with open(os.path.join(base, "Service.qml"), encoding="utf-8") as handle:
            self.service = handle.read()

    def test_the_panel_draws_a_refusal_line(self):
        self.assertRegex(self.source,
                         r"Model\s*\.\s*refusedSummary\s*\(\s*sony\s*\.\s*state\s*\)")

    def test_the_rows_mark_pending_and_refused(self):
        self.assertRegex(self.source,
                         r"Model\s*\.\s*isPending\s*\(\s*sony\s*\.\s*state\s*,")
        self.assertRegex(self.source,
                         r"Model\s*\.\s*refusedReason\s*\(\s*sony\s*\.\s*state\s*,")

    def test_the_service_marks_the_written_key_pending(self):
        self.assertRegex(self.service, r"function\s+pendingPatch\s*\(\s*key\s*\)")
        self.assertRegex(self.service, r"pending\s*\.\s*push\s*\(\s*key\s*\)")
        self.assertRegex(self.service, r"optimistic\s*\(\s*next\s*\)")


class TestAvailabilityWiring(PanelSourceMixin, unittest.TestCase):
    """A blocked setting greys out with a reason instead of hiding.

    There is no QML engine here, so Panel.qml is read as text, following
    PanelSourceMixin: enough to pin the per-row availability read, the
    tooltip carrying the reason, the dimmed control and the early-return
    guard that makes activating a greyed row a no-op.
    """

    def test_the_rows_read_availability(self):
        self.assertRegex(self.source,
                         r"Model\s*\.\s*availabilityFor\s*\(\s*sony\s*\.\s*state\s*,")

    def test_the_blocked_reason_rides_in_a_tooltip(self):
        # The tooltip is the shell's own surface, so it takes the theme's
        # [tooltip] colors, radius and shared delay. Qt's attached ToolTip is
        # the unstyled default, so its return is the regression this pins.
        self.assertNotIn("ToolTip.text", self.source)
        self.assertRegex(self.source, r"PanelToolTip\s*\{")
        self.assertRegex(self.source, r"visible:\s*\w+\s*\.\s*containsMouse")
        self.assertRegex(self.source, r"availability\s*\.\s*reason")

    def test_the_control_dims_while_staying_enabled_otherwise(self):
        self.assertRegex(self.source, r"enabled:\s*\w+\.available")
        self.assertRegex(self.source, r"opacity:\s*\w+\.available\s*\?\s*1\.0\s*:\s*0\.5")

    def test_activating_a_greyed_row_is_a_no_op(self):
        # The click/pick handlers bail before any write, and the shared
        # keyboard paths (activateRow, adjustCursorRow) do the same.
        self.assertRegex(self.source, r"if\s*\(\s*!\w+\.available\s*\)\s*return")
        self.assertRegex(
            self.source,
            r"if\s*\(\s*!Model\s*\.\s*availabilityFor\s*\(\s*sony\s*\.\s*state\s*,"
            r"\s*\w+\s*\)\s*\.\s*available\s*\)\s*return")


class TestDirectRefresh(unittest.TestCase):
    """The no-daemon path has to answer the commands the daemon does."""

    def test_refresh_asks_the_device_again(self):
        class StubLink:
            protocol = "v1"

            def __init__(self, address):
                self.state = sonyhp.initial_state()
                self.state["address"] = address
                self.refreshes = 0

            def connect(self, protocol=None):
                return 0

            def refresh(self):
                self.refreshes += 1
                self.state["firmware"] = f"pass-{self.refreshes}"
                return self.state

            def close(self):
                pass

        device = {"address": "AA:BB:CC:DD:EE:FF", "name": "WH-1000XM4", "protocol": "v1"}
        # The ambient environment may have SONY_HEADPHONES_DEMO set; "0" is the
        # documented off value, so the test takes the hardware path either way.
        with mock.patch.dict(os.environ, {"SONY_HEADPHONES_DEMO": "0"}), \
                mock.patch.object(sonyhp, "find_device", return_value=device), \
                mock.patch.object(sonyhp, "Link", StubLink):
            response = sonyhp.direct({"cmd": "refresh"}, None)
        self.assertTrue(response["ok"])
        self.assertEqual(response["state"]["firmware"], "pass-2",
                         "refresh has to reach the device, not reuse the connect snapshot")

    def test_demo_mode_reports_a_failing_set_like_the_hardware_path(self):
        with mock.patch.dict(os.environ, {"SONY_HEADPHONES_DEMO": "1"}):
            response = sonyhp.direct(
                {"cmd": "set", "key": "connection-quality", "value": "stable"}, None)
        self.assertFalse(response["ok"])
        self.assertIn("connection-quality", response["error"])
        self.assertIsInstance(response["state"], dict)
        self.assertTrue(response["state"]["connected"])

    def test_demo_mode_carries_out_every_direct_command(self):
        with mock.patch.dict(os.environ, {"SONY_HEADPHONES_DEMO": "1"}):
            for request in (
                {"cmd": "status"},
                {"cmd": "refresh"},
                {"cmd": "cycle"},
                {"cmd": "power-off"},
                {"cmd": "set", "key": "dsee", "value": "off"},
            ):
                with self.subTest(request=request):
                    self.assertTrue(sonyhp.direct(request, None)["ok"])
            refusal = sonyhp.direct({"cmd": "logging"}, None)
        self.assertFalse(refusal["ok"])
        self.assertEqual(refusal["error"], "no sony-headphones daemon is running")

    def test_the_v2_demo_reports_an_unsupported_power_off(self):
        with mock.patch.dict(os.environ, {"SONY_HEADPHONES_DEMO": "v2"}):
            response = sonyhp.direct({"cmd": "power-off"}, None)
        self.assertFalse(response["ok"])
        self.assertIn("not supported", response["error"])
        self.assertEqual(response["state"]["protocol"], "v2")


class TestDeviceNameIngest(unittest.TestCase):
    """The Bluetooth name is attacker-controlled and is cleaned at discovery."""

    def setUp(self):
        # Each test gets its own discovery cache, so nothing one test scanned
        # can satisfy the next one's lookup.
        patcher = mock.patch.object(sonyhp, "_DISCOVERY", sonyhp._DiscoveryCache())
        patcher.start()
        self.addCleanup(patcher.stop)

    def connected_devices_with_name(self, hostile_name):
        def fake_bluetoothctl(*args, **kwargs):
            if tuple(args[:2]) == ("devices", "Connected"):
                return "Device AA:BB:CC:DD:EE:FF %s\n" % hostile_name
            if args[:1] == ("info",):
                return "UUID: Vendor specific (%s)" % sonyhp.SERVICE_UUID
            return ""

        with mock.patch.object(sonyhp, "bluetoothctl", fake_bluetoothctl):
            return sonyhp.connected_devices()

    def test_escape_sequences_are_cleaned_before_the_name_enters_state(self):
        devices = self.connected_devices_with_name("Evil\x1b[31m\tName\x07")
        name = devices[0]["name"]
        self.assertEqual(name, "Evil[31m Name")
        self.assertFalse(any(ord(char) < 0x20 or 0x7F <= ord(char) <= 0x9F for char in name))

    def test_a_control_only_name_falls_back_to_the_address(self):
        devices = self.connected_devices_with_name("\x1b\x07\x00")
        self.assertEqual(devices[0]["name"], "AA:BB:CC:DD:EE:FF")


class TestDiscoveryCache(unittest.TestCase):
    """Discovery caches in memory: a short-lived list and a lasting MAC hint.

    The cache exists to stop the daemon's retry loop from spawning
    `bluetoothctl` continuously, but it stays a hint: Link's init handshake
    still decides the protocol. Only the list is time-limited; a device that is
    present keeps its protocol hint for the life of the process.
    """

    def setUp(self):
        self.clock = FakeClock()
        cache = mock.patch.object(
            sonyhp, "_DISCOVERY", sonyhp._DiscoveryCache(clock=self.clock))
        cache.start()
        self.addCleanup(cache.stop)
        self.calls = []
        self.devices_output = "Device AA:BB:CC:DD:EE:FF WH-1000XM4\n"
        self.info_output = "UUID: Vendor specific (%s)" % sonyhp.SERVICE_UUID
        bluetooth = mock.patch.object(sonyhp, "bluetoothctl", self.fake_bluetoothctl)
        bluetooth.start()
        self.addCleanup(bluetooth.stop)

    def fake_bluetoothctl(self, *args, **kwargs):
        self.calls.append(args)
        if tuple(args[:2]) == ("devices", "Connected"):
            return self.devices_output
        if args[:1] == ("info",):
            return self.info_output
        return ""

    def scans(self):
        return [call for call in self.calls if tuple(call[:2]) == ("devices", "Connected")]

    def infos(self):
        return [call for call in self.calls if call[:1] == ("info",)]

    def test_two_calls_inside_the_ttl_scan_once_with_one_info_per_device(self):
        self.devices_output = (
            "Device AA:BB:CC:DD:EE:FF WH-1000XM4\n"
            "Device 11:22:33:44:55:66 WH-1000XM6\n"
        )
        sonyhp.find_device()
        self.clock.now += 1.0
        sonyhp.find_device()
        self.assertEqual(len(self.scans()), 1)
        self.assertEqual(len(self.infos()), 2)

    def test_the_list_is_scanned_again_after_the_ttl(self):
        sonyhp.find_device()
        self.clock.now += sonyhp.DISCOVERY_TTL + 0.1
        sonyhp.find_device()
        self.assertEqual(len(self.scans()), 2)

    def test_a_repeated_info_is_served_from_the_hint_cache(self):
        sonyhp.find_device()
        self.clock.now += sonyhp.DISCOVERY_TTL + 0.1
        sonyhp.find_device()
        self.assertEqual(len(self.scans()), 2)
        self.assertEqual(len(self.infos()), 1, "the per-MAC hint outlives the list")

    def test_a_device_with_no_service_is_not_cached_as_a_negative_hint(self):
        self.info_output = "UUID: Audio Sink (0000110b-0000-1000-8000-00805f9b34fb)"
        self.assertIsNone(sonyhp.find_device())
        self.clock.now += sonyhp.DISCOVERY_TTL + 0.1
        self.info_output = "UUID: Vendor specific (%s)" % sonyhp.SERVICE_UUID
        self.assertIsNotNone(sonyhp.find_device())
        self.assertEqual(len(self.infos()), 2)

    def test_the_cached_names_are_the_cleaned_forms(self):
        self.devices_output = "Device AA:BB:CC:DD:EE:FF Evil\x1b[31m\tName\x07\n"
        sonyhp.find_device()
        self.clock.now += 1.0
        self.assertEqual(sonyhp.connected_devices()[0]["name"], "Evil[31m Name")

    def test_a_live_scan_bypasses_the_cache(self):
        sonyhp.connected_devices()
        sonyhp.connected_devices(live=True)
        self.assertEqual(len(self.scans()), 2)
        self.assertEqual(len(self.infos()), 2)

    def test_no_cache_file_is_written(self):
        cache_dir = tempfile.mkdtemp()
        os.chmod(cache_dir, 0o700)
        self.addCleanup(shutil.rmtree, cache_dir, True)
        with mock.patch.object(sonyhp, "CACHE_DIR", cache_dir):
            sonyhp.find_device()
            self.clock.now += sonyhp.DISCOVERY_TTL + 0.1
            sonyhp.find_device()
        self.assertEqual(os.listdir(cache_dir), [])


class MainRunnerMixin:
    """Run sonyhp.main against a throwaway runtime and cache, capturing output.

    Every main() call builds a log handler against the import-time CACHE_DIR and
    binds the socket under XDG_RUNTIME_DIR, so both are redirected to throwaway
    directories and the module logger is reset afterwards.
    """

    def run_main(self, *argv, demo="1"):
        runtime = tempfile.mkdtemp()
        os.chmod(runtime, 0o700)
        self.addCleanup(shutil.rmtree, runtime, True)
        # main() calls configure_logging(), which builds its file handler
        # against the import-time CACHE_DIR; without a redirect the run
        # appends to and rotates the user's real log.
        cache = tempfile.mkdtemp()
        os.chmod(cache, 0o700)
        self.addCleanup(shutil.rmtree, cache, True)
        cache_patch = mock.patch.object(sonyhp, "CACHE_DIR", cache)
        cache_patch.start()
        self.addCleanup(cache_patch.stop)
        self.addCleanup(self.reset_logging)
        self.reset_logging()
        env = {"SONY_HEADPHONES_DEMO": demo, "XDG_RUNTIME_DIR": runtime}
        out, err = io.StringIO(), io.StringIO()
        with mock.patch.dict(os.environ, env), \
                contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = sonyhp.main(list(argv))
        return code, out.getvalue(), err.getvalue()

    def reset_logging(self):
        """Put the module logger back to its pre-configured, silent state."""
        for handler in list(sonyhp.LOG.handlers):
            sonyhp.LOG.removeHandler(handler)
            handler.close()
        sonyhp._LOG_HANDLER = None
        sonyhp.LOG.addHandler(logging.NullHandler())


class TestJsonErrorPreservation(MainRunnerMixin, unittest.TestCase):
    """A refusal has to travel in the JSON the widget reads as state.error.

    Service.applyLine takes lastError from state.error on every parsed line,
    so a --json reply whose state drops response.error silently clears it.
    """

    def test_a_refused_json_set_carries_its_reason_in_the_state(self):
        code, out, _ = self.run_main("--json", "set", "connection-quality", "sound")
        self.assertNotEqual(code, 0)
        lines = out.splitlines()
        self.assertEqual(len(lines), 1, out)
        payload = json.loads(lines[0])
        self.assertTrue(payload["error"], "the refusal reason must ride in state.error")
        self.assertIn("connection-quality", payload["error"])

    def test_the_v1_demo_names_the_cross_protocol_refusal(self):
        code, out, _ = self.run_main("--json", "set", "connection-quality", "sound")
        self.assertNotEqual(code, 0)
        payload = json.loads(out)
        self.assertEqual(
            payload["error"],
            "'connection-quality' is a v2 setting and cannot be sent over v1",
        )

    def test_a_successful_json_set_keeps_error_null_and_the_new_state(self):
        code, out, _ = self.run_main("--json", "set", "dsee", "off")
        self.assertEqual(code, 0, out)
        payload = json.loads(out)
        self.assertIsNone(payload["error"])
        self.assertFalse(payload["dsee"])

    def test_a_refused_json_set_keeps_its_stderr_and_exit_code(self):
        code, out, err = self.run_main("set", "connection-quality", "sound")
        self.assertEqual(code, 1)
        self.assertEqual(out, "")
        self.assertIn("connection-quality", err)

    def test_json_logging_refusal_carries_the_reason(self):
        code, out, _ = self.run_main("--json", "logging", "all")
        self.assertNotEqual(code, 0)
        payload = json.loads(out)
        self.assertEqual(payload["error"], "no sony-headphones daemon is running")

    def test_json_logging_success_keeps_error_null_and_the_new_level(self):
        state = sonyhp.initial_state()
        state["logging"] = "all"
        response = {"ok": True, "state": state, "logging": "all"}
        with mock.patch.object(sonyhp, "run_request", return_value=response):
            code, out, _ = self.run_main("--json", "logging", "all")
        self.assertEqual(code, 0, out)
        payload = json.loads(out)
        self.assertIsNone(payload["error"])
        self.assertEqual(payload["logging"], "all")

    def test_json_logging_merges_a_daemon_refusal(self):
        state = sonyhp.initial_state()
        response = {"ok": False, "error": "log level locked", "state": state}
        with mock.patch.object(sonyhp, "run_request", return_value=response):
            code, out, _ = self.run_main("--json", "logging", "all")
        self.assertEqual(code, 1)
        payload = json.loads(out)
        self.assertEqual(payload["error"], "log level locked")


class TestSessionCli(MainRunnerMixin, unittest.TestCase):
    """release, reclaim and session belong to the daemon that owns the policy.

    With no daemon running they answer a clear error rather than dialling the
    headphones for a one-shot that releases its link the moment it exits.
    """

    def test_the_parser_registers_the_session_subcommands(self):
        parser = sonyhp.build_parser()
        self.assertIs(parser.parse_args(["release"]).func, sonyhp.cmd_release)
        self.assertIs(parser.parse_args(["reclaim"]).func, sonyhp.cmd_reclaim)
        args = parser.parse_args(["session", "on-demand", "--idle", "45"])
        self.assertIs(args.func, sonyhp.cmd_session)
        self.assertEqual(args.value, "on-demand")
        self.assertEqual(args.idle, 45)
        self.assertIsNone(parser.parse_args(["session"]).value)

    def test_release_without_a_daemon_is_a_clear_error(self):
        code, out, err = self.run_main("release")
        self.assertEqual(code, 1)
        self.assertEqual(out, "")
        self.assertIn("no sony-headphones daemon is running", err)

    def test_reclaim_without_a_daemon_is_a_clear_error(self):
        code, _, err = self.run_main("reclaim")
        self.assertEqual(code, 1)
        self.assertIn("no sony-headphones daemon is running", err)

    def test_session_without_a_daemon_is_a_clear_error(self):
        code, out, _ = self.run_main("--json", "session", "on-demand")
        self.assertEqual(code, 1)
        payload = json.loads(out)
        self.assertEqual(payload["error"], "no sony-headphones daemon is running")
        self.assertFalse(payload["connected"])

    def test_status_still_works_without_a_daemon(self):
        code, out, _ = self.run_main("--json", "status")
        self.assertEqual(code, 0, out)
        self.assertTrue(json.loads(out)["connected"])

    def test_cmd_session_reports_the_daemons_answer(self):
        args = sonyhp.build_parser().parse_args(["session"])
        state = sonyhp.initial_state()
        state["session_policy"] = "on-demand"
        state["session_idle"] = 45
        response = {"ok": True, "state": state}
        with mock.patch.object(sonyhp, "run_request", return_value=response):
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                self.assertEqual(sonyhp.cmd_session(args), 0)
        self.assertIn("on-demand", output.getvalue())
        self.assertIn("45", output.getvalue())

    def test_cmd_release_reports_a_daemon_refusal(self):
        args = sonyhp.build_parser().parse_args(["release"])
        response = {"ok": False, "error": "headphones not connected",
                    "state": sonyhp.initial_state()}
        with mock.patch.object(sonyhp, "run_request", return_value=response):
            with contextlib.redirect_stderr(io.StringIO()) as err:
                self.assertEqual(sonyhp.cmd_release(args), 1)
        self.assertIn("headphones not connected", err.getvalue())


class TestDemoSelection(unittest.TestCase):
    """SONY_HEADPHONES_DEMO picks which stand-in the helper carries."""

    def test_the_environment_maps_to_a_protocol_or_nothing(self):
        cases = {
            "": None, "0": None,
            "1": "v1", "true": "v1", "yes": "v1",
            "v2": "v2", "2": "v2",
        }
        for setting, expected in cases.items():
            with self.subTest(setting=setting):
                with mock.patch.dict(os.environ, {"SONY_HEADPHONES_DEMO": setting}):
                    self.assertEqual(sonyhp.demo_mode(), expected)
                    self.assertEqual(bool(sonyhp.demo_mode()), expected is not None)

    def test_each_setting_picks_its_stand_in(self):
        cases = (("1", sonyhp.DemoLink), ("v2", sonyhp.DemoLinkV2), ("2", sonyhp.DemoLinkV2))
        for setting, expected in cases:
            with self.subTest(setting=setting):
                with mock.patch.dict(os.environ, {"SONY_HEADPHONES_DEMO": setting}):
                    self.assertIs(type(sonyhp.demo_link()), expected)

    def test_direct_reports_the_v2_state(self):
        with mock.patch.dict(os.environ, {"SONY_HEADPHONES_DEMO": "v2"}):
            response = sonyhp.direct({"cmd": "refresh"}, None)
        self.assertTrue(response["ok"])
        self.assertEqual(response["state"]["protocol"], "v2")
        for key in ("dsee", "connection_quality", "listening_mode"):
            self.assertIsNotNone(response["state"][key], key)

    def test_the_daemon_connects_the_named_stand_in(self):
        with mock.patch.dict(os.environ, {"SONY_HEADPHONES_DEMO": "v2"}):
            daemon = sonyhp.Daemon()
            daemon.publish = mock.Mock()
            daemon.try_connect()
        self.assertIsInstance(daemon.link, sonyhp.DemoLinkV2)
        self.assertEqual(daemon.link.state["protocol"], "v2")


class TestDemoSubprocess(unittest.TestCase):
    """The whole helper, run the way the widget runs it, against the v2 demo."""

    def test_v2_demo_status_reports_the_v2_state(self):
        runtime = tempfile.mkdtemp()
        os.chmod(runtime, 0o700)
        self.addCleanup(shutil.rmtree, runtime, True)
        env = {"SONY_HEADPHONES_DEMO": "v2", "XDG_RUNTIME_DIR": runtime}
        done = subprocess.run(
            [HELPER, "--json", "status"],
            capture_output=True, text=True, timeout=30, env=env,
        )
        self.assertEqual(done.returncode, 0, done.stderr)
        state = json.loads(done.stdout)
        self.assertEqual(state["protocol"], "v2")
        for key in ("dsee", "connection_quality", "listening_mode"):
            self.assertIsNotNone(state[key], key)


if __name__ == "__main__":
    unittest.main(verbosity=2)
