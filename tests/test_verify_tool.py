"""The hardware verify tool, driven against the demo link.

`tools/verify.py` needs a running daemon for `release`/`reclaim`, so these
start a real helper daemon in demo mode inside a throwaway `XDG_RUNTIME_DIR`
and point `XDG_CACHE_HOME` and the tool at the same sandbox. Nothing here
touches the radio, the real socket, or the real cache and log.
"""

import contextlib
import importlib.util
import io
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
HELPER = os.path.join(ROOT, "bin", "sony-headphones")
TOOL = os.path.join(ROOT, "tools", "verify.py")

MAC = "00:00:00:00:00:00"
STATUSES = {"HONOURED", "IGNORED", "FAILED", "REFUSED"}

# The controls the v1 demo (a WH-1000XM4) reports as supported and reported.
V1_DEMO_CONTROLS = {
    "nc", "ambient-level", "focus-on-voice", "eq", "dsee", "speak-to-chat",
    "stc-sensitivity", "stc-timeout", "stc-focus-on-voice",
    "pause-when-taken-off", "voice-notifications", "auto-power-off",
}


def wait_for_socket(path, timeout=15.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if os.path.exists(path):
            return True
        time.sleep(0.05)
    return False


def touched_controls(stdout):
    """The control names on result lines, keyed off the status column."""
    names = set()
    for line in stdout.splitlines():
        parts = line.split()
        if len(parts) >= 2 and parts[1] in STATUSES:
            names.add(parts[0])
    return names


class _DemoDaemonTestCase(unittest.TestCase):
    """One demo daemon serves the whole class; each run restores what it wrote.

    The demo level is a class attribute, so v1 and v2 each get their own
    throwaway sandbox and a real `bin/sony-headphones watch` daemon on the demo
    transport. Every call in a test goes down the same CLI control socket the
    widget uses, never the radio, the real socket or the real cache and log.
    """

    DEMO = "1"

    @classmethod
    def setUpClass(cls):
        cls.runtime = tempfile.mkdtemp()
        cls.cache = tempfile.mkdtemp()
        os.chmod(cls.runtime, 0o700)
        os.chmod(cls.cache, 0o700)
        cls.env = {
            "SONY_HEADPHONES_DEMO": cls.DEMO,
            "XDG_RUNTIME_DIR": cls.runtime,
            "XDG_CACHE_HOME": cls.cache,
            "PATH": os.environ.get("PATH", ""),
            "HOME": os.environ.get("HOME", ""),
        }
        cls.daemon = subprocess.Popen(
            [HELPER, "watch"],
            env=cls.env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        sock = os.path.join(cls.runtime, "omarchy-sony-headphones.sock")
        if not wait_for_socket(sock):
            cls.tearDownClass()
            raise AssertionError("the demo daemon never bound its control socket")

    @classmethod
    def tearDownClass(cls):
        daemon = getattr(cls, "daemon", None)
        if daemon is not None:
            daemon.terminate()
            try:
                daemon.wait(timeout=10)
            except subprocess.TimeoutExpired:
                daemon.kill()
                daemon.wait(timeout=10)
        for path in (getattr(cls, "runtime", None), getattr(cls, "cache", None)):
            if path:
                shutil.rmtree(path, ignore_errors=True)

    def run_tool(self, *args, mac=MAC):
        return subprocess.run(
            [sys.executable, TOOL, mac, *args],
            capture_output=True, text=True, timeout=120, env=self.env,
        )

    def status(self):
        """The demo's own `status --json`, read through the sandboxed daemon."""
        done = subprocess.run(
            [HELPER, "--json", "--address", MAC, "status"],
            capture_output=True, text=True, timeout=60, env=self.env,
        )
        self.assertEqual(done.returncode, 0, done.stderr)
        return json.loads(done.stdout.strip().splitlines()[-1])


class TestVerifyToolDemo(_DemoDaemonTestCase):
    """The v1 demo: a WH-1000XM4, where the earlier control set applies."""

    DEMO = "1"

    def test_the_full_run_reports_every_control_it_touched(self):
        done = self.run_tool()
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual(touched_controls(done.stdout), V1_DEMO_CONTROLS)
        # The demo answers faithfully, keeping values across the session gap the
        # way real hardware does, so every write reads back as the value written.
        for control in V1_DEMO_CONTROLS:
            self.assertRegex(done.stdout, rf"(?m)^{control}\s+HONOURED\b")
        self.assertIn("summary:", done.stdout)
        self.assertIn("every changed value was restored", done.stdout)

    def test_named_controls_restrict_the_run(self):
        done = self.run_tool("dsee", "nc")
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual(touched_controls(done.stdout), {"dsee", "nc"})
        self.assertIn("2 verified", done.stdout)

    def test_an_unknown_control_is_rejected(self):
        done = self.run_tool("nonsense")
        self.assertNotEqual(done.returncode, 0)
        self.assertIn("unknown control", done.stderr)

    def test_an_unsupported_control_is_skipped_with_a_warning(self):
        done = self.run_tool("dsee", "touch-sensor")
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual(touched_controls(done.stdout), {"dsee"})
        self.assertIn("skipping touch-sensor", done.stderr)

    def test_a_non_address_is_rejected(self):
        done = self.run_tool(mac="not-a-mac")
        self.assertNotEqual(done.returncode, 0)
        self.assertIn("not a Bluetooth address", done.stderr)


class TestVerifyToolDemoV2(_DemoDaemonTestCase):
    """The v2 demo, so the v2 code path and the ambient floor run for real.

    The expected control set is derived from the demo's own `status --json`
    through the tool's `is_supported`, not written out by hand, so it tracks
    the v2 feature set as the demo changes.
    """

    DEMO = "2"

    def test_the_full_run_honours_every_control_the_v2_demo_reports(self):
        tool = load_tool()
        state = self.status()
        expected = {
            control for control in tool.ORDER
            if tool.is_supported(control, state, state.get("protocol"))
        }

        done = self.run_tool()
        self.assertEqual(done.returncode, 0, done.stderr)

        touched = touched_controls(done.stdout)
        self.assertTrue(touched, "the demo run touched no control")
        self.assertEqual(touched, expected)
        self.assertIn("ambient-level", touched)
        for control in touched:
            self.assertRegex(done.stdout, rf"(?m)^{control}\s+HONOURED\b")
        self.assertNotRegex(done.stdout, r"(?m)^\S+\s+(IGNORED|FAILED|REFUSED)\b")

        verified = int(re.search(r"summary: (\d+) verified", done.stdout).group(1))
        self.assertEqual(verified, len(touched))
        self.assertIn("every changed value was restored", done.stdout)


class _FakeDevice:
    """A programmable stand-in for the daemon's state, driven through tool.call.

    The real demo (see `TestVerifyToolDemo`) reports HONOURED, so a device that
    ignores writes or refuses them has to be faked: this drives the classifier's
    IGNORED/REFUSED and restore-failure paths off the radio.
    """

    FIELDS = {
        "nc": "nc_mode",
        "ambient-level": "ambient_level",
        "focus-on-voice": "focus_on_voice",
        "eq": "eq_preset",
        "dsee": "dsee",
    }

    def __init__(self, ignore=False, refuse=False, reject_value=None, protocol="v1"):
        self.ignore = ignore
        self.refuse = refuse
        self.reject_value = reject_value
        self.protocol = protocol
        self.state = {
            "connected": True, "protocol": protocol, "name": "Fake",
            "features": ["equalizer", "dsee"],
            "nc_mode": "noise-cancelling", "ambient_level": 12 if protocol == "v2" else 10,
            "focus_on_voice": False, "eq_preset": "off", "dsee": True,
        }

    def __call__(self, mac, *args):
        command = args[0]
        if command == "set":
            if self.refuse:
                return self.reply({"ok": False, "error": "no",
                                   "state": dict(self.state)}, 1)
            # A device that accepts the trial but silently drops the write that
            # puts the original back: the restore confirmation must catch it.
            if args[2] != self.reject_value and not self.ignore:
                self._apply(args[1], args[2])
            return self.reply({"ok": True, "state": dict(self.state)})
        if command == "status":
            return self.reply(dict(self.state))
        return self.reply({"ok": True, "state": dict(self.state)})

    def _apply(self, control, value):
        field = self.FIELDS[control]
        current = self.state.get(field)
        if isinstance(current, bool):
            self.state[field] = value in ("on", "true", "1")
        elif control == "ambient-level":
            level = int(value)
            if self.protocol == "v2" and level < 1:
                # v2 has no ambient level 0; the encoder lifts it to 1.
                level = 1
            self.state[field] = level
            if self.state["nc_mode"] not in ("ambient-sound", "off"):
                self.state["nc_mode"] = "ambient-sound"
        else:
            self.state[field] = value

    @staticmethod
    def reply(payload, code=0):
        return subprocess.CompletedProcess(
            ["fake"], code, stdout=json.dumps(payload) + "\n", stderr="",
        )


def load_tool():
    spec = importlib.util.spec_from_file_location("verifytool", TOOL)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class TestVerifyToolClassification(unittest.TestCase):
    """The HONOURED / IGNORED / REFUSED verdicts, against a faithful device."""

    def run_tool(self, device, controls):
        tool = load_tool()
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            with mock.patch.object(tool, "call", device):
                code = tool.main([MAC, *controls])
        return code, out.getvalue(), err.getvalue()

    def test_a_honoured_write_reads_back_as_the_written_value(self):
        device = _FakeDevice()
        code, out, _ = self.run_tool(device, ["dsee", "eq", "ambient-level"])
        self.assertEqual(code, 0, out)
        for control in ("dsee", "eq", "ambient-level"):
            self.assertRegex(out, rf"(?m)^{control}\s+HONOURED\b")
        self.assertEqual(device.state["dsee"], True)
        self.assertEqual(device.state["eq_preset"], "off")
        self.assertEqual(device.state["ambient_level"], 10)
        self.assertEqual(device.state["nc_mode"], "noise-cancelling")

    def test_a_v2_ambient_floor_does_not_produce_a_false_failure(self):
        # v2 lifts an ambient level of 0 to 1, so a trial value of 0 would read
        # back as 1; the tool must never choose a value the device will move.
        device = _FakeDevice(protocol="v2")
        code, out, _ = self.run_tool(device, ["ambient-level"])
        self.assertEqual(code, 0, out)
        self.assertRegex(out, r"(?m)^ambient-level\s+HONOURED\b")

    def test_a_device_that_ignores_writes_reads_back_as_ignored(self):
        device = _FakeDevice(ignore=True)
        code, out, _ = self.run_tool(device, ["dsee", "eq"])
        self.assertEqual(code, 0, out)
        self.assertRegex(out, r"(?m)^dsee\s+IGNORED\b")
        self.assertRegex(out, r"(?m)^eq\s+IGNORED\b")

    def test_a_refused_write_is_reported_as_refused(self):
        device = _FakeDevice(refuse=True)
        code, out, _ = self.run_tool(device, ["dsee"])
        self.assertEqual(code, 0, out)
        self.assertRegex(out, r"(?m)^dsee\s+REFUSED\b")

    def test_a_restore_that_does_not_take_exits_non_zero(self):
        # The trial write lands but the value stays changed: the tool must
        # notice the restore did not stick and fail the run.
        device = _FakeDevice(reject_value="on")
        code, out, err = self.run_tool(device, ["dsee"])
        self.assertEqual(code, 1, out)
        self.assertRegex(out, r"(?m)^dsee\s+HONOURED\b")
        self.assertIn("not restored: dsee", out)
        self.assertIn("could not restore dsee", err)
        self.assertEqual(device.state["dsee"], False)


class TestVerifyToolWithoutDaemon(unittest.TestCase):
    """No daemon in the sandbox: the tool must refuse, not reach for the radio."""

    def test_it_refuses_when_the_daemon_is_unreachable(self):
        runtime = tempfile.mkdtemp()
        cache = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, runtime, True)
        self.addCleanup(shutil.rmtree, cache, True)
        os.chmod(runtime, 0o700)
        os.chmod(cache, 0o700)
        env = {
            "SONY_HEADPHONES_DEMO": "1",
            "XDG_RUNTIME_DIR": runtime,
            "XDG_CACHE_HOME": cache,
            "PATH": os.environ.get("PATH", ""),
            "HOME": os.environ.get("HOME", ""),
        }
        done = subprocess.run(
            [sys.executable, TOOL, MAC],
            capture_output=True, text=True, timeout=60, env=env,
        )
        self.assertNotEqual(done.returncode, 0)
        self.assertIn("daemon", done.stderr)


if __name__ == "__main__":
    unittest.main(verbosity=2)
