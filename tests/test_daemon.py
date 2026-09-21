"""The daemon: private runtime files, socket bounds, discovery, logging.

Anything that can write the control socket can drive the headphones, so
the gate on its parent directory, the lock, the socket replacement rule
and the log file's ownership checks are tested here, along with the
bluetoothctl invocation and the publish fan-out.
"""

import contextlib
import io
import json
import logging
import os
import shutil
import socket
import stat
import subprocess
import tempfile
import time
import unittest
from unittest import mock

import support
from support import FakeClock, FakeStream, HELPER

sonyhp = support.sonyhp


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

    def test_binds_the_socket_inside_the_verified_directory(self):
        # The socket is created relative to the descriptor for the directory
        # whose permissions were checked, not the path that was checked
        # earlier. Point the path somewhere else and the socket must still
        # land next to the descriptor, mode 0600 and owned by us.
        elsewhere = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, elsewhere, True)
        self.daemon._socket_path = os.path.join(elsewhere, sonyhp.SOCKET_NAME)
        listener = self.daemon.bind_socket(self.dir_fd)
        self.addCleanup(listener.close)

        info = os.lstat(sonyhp.SOCKET_NAME, dir_fd=self.dir_fd)
        self.assertTrue(stat.S_ISSOCK(info.st_mode))
        self.assertEqual(info.st_uid, os.getuid())
        self.assertEqual(stat.S_IMODE(info.st_mode), 0o600)
        self.assertFalse(os.path.lexists(os.path.join(elsewhere, sonyhp.SOCKET_NAME)))

    def test_bind_replaces_a_stale_socket_in_the_verified_directory(self):
        path = os.path.join(self.tmp, sonyhp.SOCKET_NAME)
        stale = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.addCleanup(stale.close)
        stale.bind(path)
        elsewhere = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, elsewhere, True)
        self.daemon._socket_path = os.path.join(elsewhere, sonyhp.SOCKET_NAME)

        listener = self.daemon.bind_socket(self.dir_fd)
        self.addCleanup(listener.close)

        self.assertTrue(stat.S_ISSOCK(os.lstat(sonyhp.SOCKET_NAME, dir_fd=self.dir_fd).st_mode))
        self.assertFalse(os.path.lexists(os.path.join(elsewhere, sonyhp.SOCKET_NAME)))

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


class TestMalformedRequests(unittest.TestCase):
    """A malformed request is refused, never fatal to the daemon.

    The widget reads a traceback on the daemon's stderr as an error message,
    so a request that is not the expected object, or a set whose value cannot
    be parsed, has to come back as a structured failure and leave the daemon
    serving the next client.
    """

    def answer(self, daemon, line: bytes) -> dict:
        client = FakeStream(line)
        sent = []
        client.sendall = sent.append
        listener = mock.Mock()
        listener.accept.return_value = (client, None)
        with mock.patch.dict(os.environ, {"SONY_HEADPHONES_DEMO": "1"}), \
                contextlib.redirect_stdout(io.StringIO()):
            daemon.accept(listener)
        self.assertTrue(client.closed)
        self.assertTrue(sent, "the daemon answered nothing")
        return json.loads(b"".join(sent).decode())

    def test_a_non_object_request_is_answered_with_an_error(self):
        daemon = sonyhp.Daemon()
        for request in ([], "x", None, 3, True):
            with self.subTest(request=request):
                response = self.answer(daemon, (json.dumps(request) + "\n").encode())
                self.assertFalse(response["ok"])
                self.assertIn("error", response)

    def test_a_set_with_a_missing_or_unusable_value_is_answered_with_an_error(self):
        daemon = sonyhp.Daemon()
        requests = (
            {"cmd": "set", "key": "ambient-level"},
            {"cmd": "set", "key": "ambient-level", "value": None},
            {"cmd": "set", "key": "ambient-level", "value": []},
            {"cmd": "set", "key": "eq-bands", "value": None},
        )
        for request in requests:
            with self.subTest(request=request):
                response = self.answer(daemon, (json.dumps(request) + "\n").encode())
                self.assertFalse(response["ok"])
                self.assertIn("error", response)

    def test_the_daemon_survives_every_malformed_request(self):
        daemon = sonyhp.Daemon()
        lines = [
            b"[]\n", b'"x"\n', b"null\n",
            b'{"cmd": "set", "key": "ambient-level"}\n',
            b'{"cmd": "set", "key": "ambient-level", "value": null}\n',
            b'{"cmd": "set", "key": "ambient-level", "value": []}\n',
        ]
        for line in lines:
            with self.subTest(line=line):
                self.assertFalse(self.answer(daemon, line)["ok"])
        self.assertTrue(self.answer(daemon, b'{"cmd": "status"}\n')["ok"])

    def test_handle_command_answers_an_unusable_value_without_raising(self):
        daemon = sonyhp.Daemon()
        with mock.patch.dict(os.environ, {"SONY_HEADPHONES_DEMO": "1"}), \
                contextlib.redirect_stdout(io.StringIO()):
            for request in (
                {"cmd": "set", "key": "ambient-level"},
                {"cmd": "set", "key": "ambient-level", "value": None},
                {"cmd": "set", "key": "ambient-level", "value": []},
            ):
                with self.subTest(request=request):
                    response = daemon.handle_command(request)
                    self.assertFalse(response["ok"])
                    self.assertIn("error", response)
            self.assertTrue(daemon.handle_command({"cmd": "status"})["ok"])

    def test_a_non_finite_number_is_refused_and_the_daemon_survives(self):
        # json accepts Infinity and NaN by default, and int(inf) raises
        # OverflowError -- an ArithmeticError, not the ValueError the command
        # boundary used to promise -- so this request is the one that killed
        # the daemon. It has to come back as a structured refusal.
        daemon = sonyhp.Daemon()
        for literal in (b"1e999", b"-1e999", b"Infinity", b"-Infinity", b"NaN"):
            with self.subTest(literal=literal):
                line = b'{"cmd": "set", "key": "ambient-level", "value": ' + literal + b"}\n"
                response = self.answer(daemon, line)
                self.assertFalse(response["ok"])
                self.assertIn("error", response)
        self.assertTrue(self.answer(daemon, b'{"cmd": "status"}\n')["ok"])

    def test_a_deeply_nested_array_within_the_line_cap_is_answered(self):
        daemon = sonyhp.Daemon()
        depth = 2000
        line = (b"[" * depth) + b"0" + (b"]" * depth) + b"\n"
        self.assertLessEqual(len(line), sonyhp.MAX_LINE_BYTES)
        response = self.answer(daemon, line)
        self.assertFalse(response["ok"])
        self.assertIn("error", response)

    def test_the_daemon_keeps_serving_after_a_hostile_sequence(self):
        daemon = sonyhp.Daemon()
        depth = 2000
        lines = [
            b'{"cmd": "set", "key": "ambient-level", "value": 1e999}\n',
            b'{"cmd": "set", "key": "ambient-level", "value": -1e999}\n',
            b'{"cmd": "set", "key": "ambient-level", "value": NaN}\n',
            (b"[" * depth) + b"0" + (b"]" * depth) + b"\n",
            b'{"cmd": "set", "key": "ambient-level"}\n',
            b'{"cmd": "set", "key": "ambient-level", "value": null}\n',
            b'{"cmd": "set", "key": "ambient-level", "value": []}\n',
            b"[]\n", b'"x"\n', b"null\n",
        ]
        for line in lines:
            with self.subTest(line=line[:40]):
                self.assertFalse(self.answer(daemon, line)["ok"])
        response = self.answer(daemon, b'{"cmd": "status"}\n')
        self.assertTrue(response["ok"])

    def test_direct_refuses_a_non_finite_number(self):
        request = {"cmd": "set", "key": "ambient-level", "value": float("inf")}
        with mock.patch.dict(os.environ, {"SONY_HEADPHONES_DEMO": "1"}), \
                contextlib.redirect_stdout(io.StringIO()):
            response = sonyhp.direct(request, None)
        self.assertFalse(response["ok"])
        self.assertIn("error", response)


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


class TestPublish(unittest.TestCase):
    def test_one_json_line_per_change_and_one_serialisation(self):
        daemon = sonyhp.Daemon()
        client = FakeStream(b"")
        client.sent = []
        client.sendall = client.sent.append
        daemon.subscribers.append(client)
        state = sonyhp.initial_state()
        output = io.StringIO()
        with mock.patch.object(sonyhp.json, "dumps", wraps=json.dumps) as dumps, \
                contextlib.redirect_stdout(output):
            daemon.publish(state)
            daemon.publish(dict(state))
            state["battery"] = 50
            daemon.publish(state)
        lines = output.getvalue().splitlines()
        self.assertEqual(len(lines), 2)
        self.assertEqual(dumps.call_count, 2)
        self.assertEqual(client.sent, [(lines[0] + "\n").encode(), (lines[1] + "\n").encode()])


class TestSubscriberBackPressure(unittest.TestCase):
    """A stuck or surplus subscriber must not stall or grow the daemon.

    The daemon is single-threaded, so `publish` gives every send a short
    timeout and drops the client on failure, and `accept` refuses to grow the
    subscriber list past its cap. These use fakes rather than real sockets so
    the failure modes and the timeout are exercised without waiting.
    """

    def answer(self, daemon, line: bytes) -> dict:
        client = FakeStream(line)
        sent = []
        client.sendall = sent.append
        listener = mock.Mock()
        listener.accept.return_value = (client, None)
        with contextlib.redirect_stdout(io.StringIO()):
            daemon.accept(listener)
        self.assertTrue(client.closed)
        self.assertTrue(sent, "the daemon answered nothing")
        return json.loads(b"".join(sent).decode())

    def test_a_failing_subscriber_is_dropped_and_the_rest_still_receive(self):
        daemon = sonyhp.Daemon()
        broken = FakeStream(b"")
        broken.sendall = mock.Mock(side_effect=socket.timeout("the peer never read"))
        healthy = FakeStream(b"")
        healthy.sent = []
        healthy.sendall = healthy.sent.append
        daemon.subscribers.extend([broken, healthy])
        with contextlib.redirect_stdout(io.StringIO()):
            daemon.publish(sonyhp.initial_state())
        self.assertNotIn(broken, daemon.subscribers)
        self.assertIn(healthy, daemon.subscribers)
        self.assertEqual(len(healthy.sent), 1)
        self.assertTrue(broken.closed)

    def test_each_send_is_given_the_short_subscriber_timeout(self):
        daemon = sonyhp.Daemon()
        client = FakeStream(b"")
        client.timeouts = []
        client.settimeout = client.timeouts.append
        daemon.subscribers.append(client)
        with contextlib.redirect_stdout(io.StringIO()):
            daemon.publish(sonyhp.initial_state())
        self.assertIn(daemon.SUBSCRIBER_TIMEOUT, client.timeouts)

    def test_a_stuck_subscriber_does_not_block_a_later_status_client(self):
        daemon = sonyhp.Daemon()
        stuck = FakeStream(b"")
        stuck.sendall = mock.Mock(side_effect=socket.timeout("the peer never read"))
        daemon.subscribers.append(stuck)
        with contextlib.redirect_stdout(io.StringIO()):
            # Enough distinct states that a real unread socket would have
            # filled its buffer long before the last one.
            for battery in range(64):
                state = sonyhp.initial_state()
                state["battery"] = battery
                daemon.publish(state)
        self.assertEqual(daemon.subscribers, [])
        self.assertTrue(self.answer(daemon, b'{"cmd": "status"}\n')["ok"])

    def test_a_seventeenth_subscriber_is_refused_and_the_daemon_stays_up(self):
        daemon = sonyhp.Daemon()
        for _ in range(daemon.MAX_SUBSCRIBERS):
            daemon.subscribers.append(FakeStream(b""))
        response = self.answer(daemon, b'{"cmd": "subscribe"}\n')
        self.assertEqual(len(daemon.subscribers), daemon.MAX_SUBSCRIBERS)
        self.assertFalse(response["ok"])
        self.assertIn("error", response)
        self.assertTrue(self.answer(daemon, b'{"cmd": "status"}\n')["ok"])

    def test_the_subscriber_list_never_grows_across_connect_drop_cycles(self):
        daemon = sonyhp.Daemon()
        for _ in range(daemon.MAX_SUBSCRIBERS * 3):
            client = FakeStream(b'{"cmd": "subscribe"}\n')
            sent = []
            client.sendall = sent.append
            listener = mock.Mock()
            listener.accept.return_value = (client, None)
            with contextlib.redirect_stdout(io.StringIO()):
                daemon.accept(listener)
            self.assertLessEqual(len(daemon.subscribers), daemon.MAX_SUBSCRIBERS)
            if daemon.subscribers:
                daemon.drop(daemon.subscribers[0])
        self.assertEqual(daemon.subscribers, [])


class LoggingResetMixin:
    """Put the module logger back to its pre-configured, silent state.

    configure_logging() installs a handler and sets `_LOG_HANDLER` process-wide,
    so any test that may touch it resets both before and after the test.
    """

    def reset_logging(self):
        for handler in list(sonyhp.LOG.handlers):
            sonyhp.LOG.removeHandler(handler)
            handler.close()
        sonyhp._LOG_HANDLER = None
        sonyhp.LOG.addHandler(logging.NullHandler())


class DemoDaemonMixin(LoggingResetMixin):
    """A daemon on the demo transport, with the socket scaffolding to drive it.

    TestSessionPolicy and TestWriteThenVerify both connect a demo link and
    answer client requests through accept(); the second also subscribes. One
    mixin rather than two copies of the same connect/answer pair.
    """

    def setUp(self):
        self.clock = FakeClock()
        self.daemon = sonyhp.Daemon(clock=self.clock)
        cache = tempfile.mkdtemp()
        os.chmod(cache, 0o700)
        self.addCleanup(shutil.rmtree, cache, True)
        cache_patch = mock.patch.object(sonyhp, "CACHE_DIR", cache)
        cache_patch.start()
        self.addCleanup(cache_patch.stop)
        self.addCleanup(self.reset_logging)
        self.reset_logging()

    def connect(self):
        with mock.patch.dict(os.environ, {"SONY_HEADPHONES_DEMO": "1"}), \
                contextlib.redirect_stdout(io.StringIO()):
            self.daemon.try_connect()

    def answer(self, request: dict) -> dict:
        client = FakeStream((json.dumps(request) + "\n").encode())
        sent = []
        client.sendall = sent.append
        listener = mock.Mock()
        listener.accept.return_value = (client, None)
        with mock.patch.dict(os.environ, {"SONY_HEADPHONES_DEMO": "1"}), \
                contextlib.redirect_stdout(io.StringIO()):
            self.daemon.accept(listener)
        self.assertTrue(client.closed)
        self.assertTrue(sent, "the daemon answered nothing")
        return json.loads(b"".join(sent).decode())

    def subscribe(self) -> list:
        """Attach a live subscriber and return the lines it is sent."""
        client = FakeStream((json.dumps({"cmd": "subscribe"}) + "\n").encode())
        lines = []
        client.sendall = lines.append
        listener = mock.Mock()
        listener.accept.return_value = (client, None)
        with contextlib.redirect_stdout(io.StringIO()):
            self.daemon.accept(listener)
        self.assertIn(client, self.daemon.subscribers)
        return lines

    @staticmethod
    def payloads(lines):
        return [json.loads(line) for line in lines]


class TestSessionPolicy(DemoDaemonMixin, unittest.TestCase):
    """The control session can be released and reclaimed over the socket.

    A Sony headset allows one control session at a time, so the daemon needs a
    deliberate way to hand it over (release) and take it back (reclaim), plus
    an on-demand policy that releases it after a quiet spell. Every run stays on
    the demo transport with a fake clock, so no test waits and none touches the
    radio or the real cache.
    """

    def test_release_keeps_the_last_values_and_clears_the_error(self):
        self.connect()
        before = self.daemon.state()
        response = self.answer({"cmd": "release"})
        self.assertTrue(response["ok"])
        state = response["state"]
        self.assertFalse(state["connected"])
        self.assertEqual(state["session"], "released")
        self.assertIsNone(state["error"])
        self.assertEqual(state["battery"], before["battery"])
        self.assertEqual(state["name"], before["name"])

    def test_release_after_a_lost_link_still_keeps_the_last_values(self):
        self.connect()
        before = self.daemon.state()
        with contextlib.redirect_stdout(io.StringIO()):
            self.daemon.lose_link("dropped")
        # The link is already gone, so release has no live state to snapshot and
        # must fall back to what the device last reported.
        response = self.answer({"cmd": "release"})
        self.assertTrue(response["ok"])
        state = response["state"]
        self.assertFalse(state["connected"])
        self.assertEqual(state["session"], "released")
        self.assertIsNone(state["error"])
        self.assertEqual(state["battery"], before["battery"])
        self.assertEqual(state["name"], before["name"])

    def test_a_set_while_released_reclaims_and_applies(self):
        self.connect()
        self.answer({"cmd": "release"})
        response = self.answer({"cmd": "set", "key": "dsee", "value": "off"})
        self.assertTrue(response["ok"])
        self.assertTrue(response["state"]["connected"])
        self.assertEqual(response["state"]["session"], "held")
        self.assertFalse(response["state"]["dsee"])

    def test_a_write_survives_release_and_reclaim_in_demo(self):
        # A real headset keeps its settings in flash across the session gap, so
        # the demo stand-in is reused rather than rebuilt; verify relies on it.
        self.connect()
        device = self.daemon.link.device
        self.answer({"cmd": "set", "key": "dsee", "value": "off"})
        self.answer({"cmd": "release"})
        response = self.answer({"cmd": "reclaim"})
        self.assertTrue(response["ok"])
        self.assertIs(self.daemon.link.device, device)
        self.assertFalse(response["state"]["dsee"])

    def test_reclaim_on_a_released_link_reconnects(self):
        self.connect()
        self.answer({"cmd": "release"})
        response = self.answer({"cmd": "reclaim"})
        self.assertTrue(response["ok"])
        self.assertTrue(response["state"]["connected"])
        self.assertEqual(response["state"]["session"], "held")
        self.assertIsNotNone(response["state"]["battery"])

    def test_reclaim_on_a_held_link_is_a_noop_success(self):
        self.connect()
        held = self.daemon.link
        response = self.answer({"cmd": "reclaim"})
        self.assertTrue(response["ok"])
        self.assertEqual(response["state"]["session"], "held")
        self.assertIs(self.daemon.link, held)

    def test_a_failed_reclaim_reports_the_connection_error(self):
        self.daemon.try_connect = mock.Mock()  # leaves the link unset
        response = self.answer({"cmd": "reclaim"})
        self.assertFalse(response["ok"])
        self.assertIn("not connected", response["error"])

    def test_on_demand_releases_after_the_idle(self):
        self.answer({"cmd": "session", "value": "on-demand", "idle": 5})
        self.connect()
        self.clock.now += 4.0
        self.daemon.release_if_idle()
        self.assertEqual(self.daemon.session, "held")
        self.clock.now += 1.0
        self.daemon.release_if_idle()
        self.assertEqual(self.daemon.session, "released")
        self.assertFalse(self.daemon.state()["connected"])

    def test_a_poll_and_a_subscriber_do_not_reset_the_idle_timer(self):
        self.answer({"cmd": "session", "value": "on-demand", "idle": 5})
        self.connect()
        self.daemon.subscribers.append(FakeStream(b""))
        with contextlib.redirect_stdout(io.StringIO()):
            self.daemon.publish(self.daemon.state())
            self.daemon.link.request(self.daemon.link.poll_requests(), settle=0.0)
        self.clock.now += 5.0
        self.daemon.release_if_idle()
        self.assertEqual(self.daemon.session, "released")

    def test_the_run_loop_poll_hook_does_not_reset_the_idle_timer(self):
        self.answer({"cmd": "session", "value": "on-demand", "idle": 5})
        self.connect()
        activity = self.daemon.last_activity
        self.clock.now += 5.0
        # Take the demo off so poll_link performs the real request, then drive
        # the run loop's own hook. The poll is link traffic, not client
        # activity, so the idle timer must not move and release_if_idle must
        # still fire on the same tick.
        with mock.patch.object(sonyhp, "demo_mode", return_value=None), \
                mock.patch.object(self.daemon.link, "request",
                                  wraps=self.daemon.link.request) as request, \
                contextlib.redirect_stdout(io.StringIO()):
            self.daemon.poll_link()
            request.assert_called_once()
            self.assertEqual(self.daemon.last_activity, activity)
            self.assertEqual(self.daemon.session, "held")
            self.daemon.release_if_idle()
        self.assertEqual(self.daemon.session, "released")

    def test_session_refuses_a_bool_a_float_and_a_string_idle(self):
        for idle in (True, 5.9, "45"):
            with self.subTest(idle=idle):
                response = self.answer({"cmd": "session", "idle": idle})
                self.assertFalse(response["ok"])
                self.assertIn("whole number", response["error"])
                self.assertEqual(response["state"]["session_idle"], 30)

    def test_a_client_command_resets_the_idle_timer(self):
        self.answer({"cmd": "session", "value": "on-demand", "idle": 5})
        self.connect()
        self.clock.now += 4.0
        self.answer({"cmd": "refresh"})
        self.clock.now += 4.0
        self.daemon.release_if_idle()
        self.assertEqual(self.daemon.session, "held")
        self.clock.now += 1.0
        self.daemon.release_if_idle()
        self.assertEqual(self.daemon.session, "released")

    def test_hold_never_auto_releases(self):
        self.connect()
        self.clock.now += 100000.0
        self.daemon.release_if_idle()
        self.assertEqual(self.daemon.session, "held")
        self.assertTrue(self.daemon.state()["connected"])

    def test_session_read_reports_the_policy_without_a_link(self):
        response = self.answer({"cmd": "session"})
        self.assertTrue(response["ok"])
        self.assertEqual(response["state"]["session_policy"], "hold")
        self.assertEqual(response["state"]["session_idle"], 30)

    def test_the_policy_boots_from_the_environment(self):
        with mock.patch.dict(os.environ, {
                "SONY_HEADPHONES_SESSION": "on-demand",
                "SONY_HEADPHONES_SESSION_IDLE": "45"}):
            daemon = sonyhp.Daemon(clock=self.clock)
        self.assertEqual(daemon.session_policy, "on-demand")
        self.assertEqual(daemon.session_idle, 45)

    def test_an_invalid_environment_falls_back_to_the_defaults(self):
        for setting in ("nonsense", "", " "):
            with self.subTest(policy=setting):
                with mock.patch.dict(os.environ, {"SONY_HEADPHONES_SESSION": setting}):
                    self.assertEqual(sonyhp.Daemon().session_policy, "hold")
        for setting in ("nonsense", "0", "-4", "1e9", "999999999"):
            with self.subTest(idle=setting):
                with mock.patch.dict(os.environ, {"SONY_HEADPHONES_SESSION_IDLE": setting}):
                    self.assertEqual(sonyhp.Daemon().session_idle, 30)

    def test_session_refuses_an_unknown_policy_and_out_of_range_idle(self):
        for request in (
            {"cmd": "session", "value": "sometimes"},
            {"cmd": "session", "idle": 0},
            {"cmd": "session", "idle": -1},
            {"cmd": "session", "idle": 10 ** 9},
        ):
            with self.subTest(request=request):
                response = self.answer(request)
                self.assertFalse(response["ok"])
                self.assertIn("error", response)
        # An explicit null is a read, not a refusal.
        self.assertTrue(self.answer({"cmd": "session", "idle": None})["ok"])

    def test_a_refused_session_change_leaves_the_policy_alone(self):
        response = self.answer({"cmd": "session", "value": "on-demand", "idle": 0})
        self.assertFalse(response["ok"])
        self.assertEqual(response["state"]["session_policy"], "hold")
        self.assertEqual(response["state"]["session_idle"], 30)

    def test_reclaim_resets_the_idle_timer(self):
        self.answer({"cmd": "session", "value": "on-demand", "idle": 5})
        self.connect()
        self.clock.now += 4.0
        self.answer({"cmd": "reclaim"})
        self.clock.now += 4.0
        self.daemon.release_if_idle()
        self.assertEqual(self.daemon.session, "held")

    def test_subscribers_see_the_released_and_reclaimed_lines(self):
        self.connect()
        client = FakeStream(b"")
        client.sent = []
        client.sendall = client.sent.append
        self.daemon.subscribers.append(client)
        with contextlib.redirect_stdout(io.StringIO()):
            self.answer({"cmd": "release"})
            self.answer({"cmd": "reclaim"})
        sessions = [json.loads(line)["session"] for line in client.sent]
        self.assertIn("released", sessions)
        self.assertIn("held", sessions)


class TestWriteThenVerify(DemoDaemonMixin, unittest.TestCase):
    """A write the device does not confirm is refused, and the daemon says so.

    Every run stays on the demo transport. The stand-in is put in stubborn mode
    so a write is acknowledged but never applied, and the daemon's own state
    and published lines carry the pending then refused transition.
    """

    def test_a_confirmed_set_is_pending_then_neither_pending_nor_refused(self):
        self.connect()
        lines = self.subscribe()
        response = self.answer({"cmd": "set", "key": "dsee", "value": "off"})
        self.assertTrue(response["ok"])
        self.assertTrue(any(payload.get("pending") == ["dsee"]
                            for payload in self.payloads(lines)),
                        "the intermediate pending state must be published")
        self.assertEqual(response["state"]["pending"], [])
        self.assertEqual(response["state"]["refused"], {})
        self.assertIs(response["state"]["dsee"], False)

    def test_a_stubborn_set_is_refused_and_reported(self):
        self.connect()
        self.daemon.link.device.stubborn = True
        lines = self.subscribe()
        response = self.answer({"cmd": "set", "key": "dsee", "value": "off"})
        self.assertTrue(response["ok"])
        self.assertEqual(response["state"]["pending"], [])
        self.assertIn("the headphones did not change dsee",
                      response["state"]["refused"]["dsee"])
        self.assertIs(response["state"]["dsee"], True)
        self.assertTrue(any((payload.get("refused") or {}).get("dsee")
                            for payload in self.payloads(lines)),
                        "the refusal must be published")

    def test_a_later_success_clears_the_refusal(self):
        self.connect()
        self.daemon.link.device.stubborn = True
        refused = self.answer({"cmd": "set", "key": "dsee", "value": "off"})
        self.assertIn("dsee", refused["state"]["refused"])
        self.daemon.link.device.stubborn = False
        response = self.answer({"cmd": "set", "key": "dsee", "value": "off"})
        self.assertEqual(response["state"]["refused"], {})
        self.assertEqual(response["state"]["pending"], [])
        self.assertIs(response["state"]["dsee"], False)

    def test_json_status_emits_both_fields(self):
        # No hand-built state: a stubborn set through the daemon produces a
        # real refused entry, and --json status must carry both fields from
        # the live state (only the socket hop to run_request is stubbed).
        self.connect()
        self.daemon.link.device.stubborn = True
        refused = self.answer({"cmd": "set", "key": "dsee", "value": "off"})
        self.assertIn("dsee", refused["state"]["refused"])
        args = sonyhp.build_parser().parse_args(["--json", "status"])
        live = {"ok": True, "state": self.daemon.state()}
        with mock.patch.object(sonyhp, "run_request", return_value=live):
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                self.assertEqual(sonyhp.cmd_status(args), 0)
        payload = json.loads(output.getvalue())
        self.assertEqual(payload["pending"], [])
        self.assertEqual(payload["refused"],
                         {"dsee": "the headphones did not change dsee"})


class TestLogging(LoggingResetMixin, unittest.TestCase):
    """The local log: level names, the private file and runtime changes."""

    def setUp(self):
        self.cache = tempfile.mkdtemp()
        os.chmod(self.cache, 0o700)
        self.addCleanup(shutil.rmtree, self.cache, True)
        patch = mock.patch.object(sonyhp, "CACHE_DIR", self.cache)
        patch.start()
        self.addCleanup(patch.stop)
        self.addCleanup(self.reset_logging)
        self.reset_logging()

    def log_path(self):
        return os.path.join(self.cache, sonyhp.LOG_FILE_NAME)

    def read_log(self):
        try:
            with open(self.log_path(), encoding="utf-8") as handle:
                return handle.read()
        except FileNotFoundError:
            return ""

    def test_the_level_name_parses_case_insensitively_with_a_safe_default(self):
        cases = {
            None: "errors", "": "errors", "bogus": "errors", "warning": "errors",
            "off": "off", "errors": "errors", "all": "all",
            "ALL": "all", " Errors ": "errors", "OFF": "off",
        }
        for setting, expected in cases.items():
            with self.subTest(setting=setting):
                self.assertEqual(sonyhp._log_level_name(setting), expected)

    def test_the_default_level_is_errors(self):
        with mock.patch.dict(os.environ):
            os.environ.pop("SONY_HEADPHONES_LOG", None)
            self.assertEqual(sonyhp.configure_logging(), "errors")

    def test_startup_reads_the_environment(self):
        with mock.patch.dict(os.environ, {"SONY_HEADPHONES_LOG": "ALL"}):
            self.assertEqual(sonyhp.configure_logging(), "all")
        with mock.patch.dict(os.environ, {"SONY_HEADPHONES_LOG": "nonsense"}):
            self.assertEqual(sonyhp.configure_logging(), "errors")

    def test_off_writes_nothing(self):
        self.assertEqual(sonyhp.configure_logging("off"), "off")
        sonyhp.LOG.error("an error that must not appear")
        sonyhp.LOG.info("an info that must not appear")
        self.assertEqual(self.read_log(), "")

    def test_errors_keeps_failures_and_drops_detail(self):
        sonyhp.configure_logging("errors")
        sonyhp.LOG.info("connection detail")
        sonyhp.LOG.error("connection failure")
        text = self.read_log()
        self.assertIn("connection failure", text)
        self.assertNotIn("connection detail", text)

    def test_all_keeps_detail(self):
        sonyhp.configure_logging("all")
        sonyhp.LOG.info("connection detail")
        self.assertIn("connection detail", self.read_log())

    def test_a_runtime_level_change_takes_effect(self):
        sonyhp.configure_logging("errors")
        sonyhp.LOG.info("before the change")
        sonyhp.LOG.error("always visible")
        sonyhp.configure_logging("all")
        sonyhp.LOG.info("after the change")
        text = self.read_log()
        self.assertNotIn("before the change", text)
        self.assertIn("after the change", text)
        self.assertIn("always visible", text)

    def test_the_daemon_can_change_the_level_at_runtime(self):
        sonyhp.configure_logging("errors")
        daemon = sonyhp.Daemon()
        with contextlib.redirect_stdout(io.StringIO()):
            response = daemon.handle_command({"cmd": "logging", "value": "all"})
            read_back = daemon.handle_command({"cmd": "logging"})
        self.assertTrue(response["ok"])
        self.assertEqual(response["logging"], "all")
        self.assertEqual(response["state"]["logging"], "all")
        self.assertEqual(read_back["logging"], "all")
        sonyhp.LOG.info("visible after the switch")
        self.assertIn("visible after the switch", self.read_log())

    def test_the_log_file_is_private_and_not_a_symlink(self):
        sonyhp.configure_logging("errors")
        sonyhp.LOG.error("first line")
        info = os.lstat(self.log_path())
        self.assertTrue(stat.S_ISREG(info.st_mode))
        self.assertEqual(stat.S_IMODE(info.st_mode), 0o600)
        self.assertFalse(os.path.islink(self.log_path()))

    def test_a_loose_log_file_is_tightened(self):
        with open(self.log_path(), "w", encoding="utf-8") as handle:
            handle.write("old\n")
        os.chmod(self.log_path(), 0o644)
        sonyhp.configure_logging("errors")
        sonyhp.LOG.error("new")
        self.assertEqual(stat.S_IMODE(os.lstat(self.log_path()).st_mode), 0o600)

    def test_a_symlink_on_the_log_path_is_refused(self):
        target = os.path.join(self.cache, "target")
        with open(target, "w", encoding="utf-8") as handle:
            handle.write("untouched\n")
        os.symlink(target, self.log_path())
        sonyhp.configure_logging("errors")
        sonyhp.LOG.error("must not reach the target")
        with open(target, encoding="utf-8") as handle:
            self.assertEqual(handle.read(), "untouched\n")
        self.assertTrue(os.path.islink(self.log_path()))

    def test_bluetooth_addresses_are_redacted(self):
        sonyhp.configure_logging("errors")
        sonyhp.LOG.error("could not open AA:BB:CC:DD:EE:FF")
        text = self.read_log()
        self.assertNotIn("AA:BB:CC:DD:EE:FF", text)
        self.assertIn("<address>", text)

    def test_rotation_is_bounded(self):
        with mock.patch.object(sonyhp, "LOG_MAX_BYTES", 200):
            sonyhp.configure_logging("errors")
        sonyhp.LOG.error("a" * 100)
        sonyhp.LOG.error("b" * 100)
        self.assertTrue(os.path.exists(self.log_path() + ".1"))

    def test_a_failing_command_is_logged(self):
        sonyhp.configure_logging("errors")
        daemon = sonyhp.Daemon()
        with mock.patch.dict(os.environ, {"SONY_HEADPHONES_DEMO": "1"}), \
                contextlib.redirect_stdout(io.StringIO()):
            response = daemon.handle_command(
                {"cmd": "set", "key": "connection-quality", "value": "stable"})
        self.assertFalse(response["ok"])
        text = self.read_log()
        self.assertIn("command failed", text)
        self.assertIn("connection-quality", text)

    def test_an_applied_command_is_logged_with_its_key_and_value(self):
        sonyhp.configure_logging("all")
        daemon = sonyhp.Daemon()
        with mock.patch.dict(os.environ, {"SONY_HEADPHONES_DEMO": "1"}), \
                contextlib.redirect_stdout(io.StringIO()):
            response = daemon.handle_command({"cmd": "set", "key": "dsee", "value": "off"})
        self.assertTrue(response["ok"])
        self.assertIn("command applied: set dsee=off", self.read_log())

    def test_a_newline_in_a_command_value_is_logged_as_one_line(self):
        sonyhp.configure_logging("errors")
        daemon = sonyhp.Daemon()
        for separator in ("\n", "\r"):
            with self.subTest(separator=separator):
                with mock.patch.dict(os.environ, {"SONY_HEADPHONES_DEMO": "1"}), \
                        contextlib.redirect_stdout(io.StringIO()):
                    daemon.handle_command(
                        {"cmd": "set", "key": "dsee",
                         "value": f"off{separator}FORGED-INJECTION"})
                text = self.read_log()
                self.assertIn("FORGED-INJECTION", text)
                self.assertEqual(len(text.splitlines()), 1)
                os.truncate(self.log_path(), 0)

    def test_a_traceback_keeps_its_newlines_while_a_value_stays_one_line(self):
        sonyhp.configure_logging("errors")
        try:
            raise ValueError("boom")
        except ValueError:
            sonyhp.LOG.exception("command failed\nFORGED-INJECTION")
        text = self.read_log()
        self.assertIn("Traceback (most recent call last)", text)
        self.assertIn("ValueError: boom", text)
        lines = text.splitlines()
        self.assertGreater(len(lines), 1)
        self.assertFalse(any(line.startswith("FORGED-INJECTION") for line in lines))

    def test_a_parser_error_swallowed_by_the_boundary_is_logged(self):
        sonyhp.configure_logging("errors")
        link = sonyhp.DemoLink()
        link.adapter.apply = mock.Mock(side_effect=IndexError("boom"))
        link.dispatch(sonyhp.MSG_COMMAND_1, bytes([0x11, 0x00, 0x5A, 0x01]))
        self.assertIn("parser error ignored", self.read_log())

    def test_the_cli_logging_subcommand_reports_the_daemon_answer(self):
        args = sonyhp.build_parser().parse_args(["logging", "all"])
        self.assertEqual(args.level, "all")
        response = {"ok": True, "logging": "all", "state": {"logging": "all"}}
        with mock.patch.object(sonyhp, "run_request", return_value=response):
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                self.assertEqual(sonyhp.cmd_logging(args), 0)
        self.assertIn("all", output.getvalue())


class TestTraceFile(unittest.TestCase):
    """SONY_HEADPHONES_TRACE names a private file, or tracing stays off.

    The trace records raw frame bytes and the path arrives from the
    environment, so it is the one file that must not follow a symlink or
    take over someone else's file. Unlike the log, a failure here disables
    tracing rather than raising: debugging must never be the failure.
    """

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        os.chmod(self.tmp, 0o700)
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.path = os.path.join(self.tmp, "trace")

    def test_creates_a_private_regular_file(self):
        handle = sonyhp._open_trace_file(self.path)
        self.assertIsNotNone(handle)
        self.addCleanup(handle.close)
        info = os.lstat(self.path)
        self.assertTrue(stat.S_ISREG(info.st_mode))
        self.assertEqual(stat.S_IMODE(info.st_mode), 0o600)
        self.assertFalse(os.path.islink(self.path))

    def test_tightens_the_file_even_under_a_loose_umask(self):
        previous = os.umask(0o000)
        self.addCleanup(os.umask, previous)
        handle = sonyhp._open_trace_file(self.path)
        self.assertIsNotNone(handle)
        self.addCleanup(handle.close)
        self.assertEqual(stat.S_IMODE(os.lstat(self.path).st_mode), 0o600)

    def test_refuses_a_symlink_and_leaves_the_target_alone(self):
        target = os.path.join(self.tmp, "target")
        with open(target, "w", encoding="utf-8") as handle:
            handle.write("keep\n")
        os.symlink(target, self.path)
        self.assertIsNone(sonyhp._open_trace_file(self.path))
        with open(target, encoding="utf-8") as handle:
            self.assertEqual(handle.read(), "keep\n")

    def test_refuses_a_file_owned_by_someone_else(self):
        with open(self.path, "w", encoding="utf-8") as handle:
            handle.write("keep\n")
        with mock.patch.object(os, "getuid", return_value=os.getuid() + 1):
            self.assertIsNone(sonyhp._open_trace_file(self.path))
        with open(self.path, encoding="utf-8") as handle:
            self.assertEqual(handle.read(), "keep\n")

    def test_the_environment_variable_is_wired_to_the_private_open(self):
        runtime = tempfile.mkdtemp()
        os.chmod(runtime, 0o700)
        cache = tempfile.mkdtemp()
        os.chmod(cache, 0o700)
        self.addCleanup(shutil.rmtree, runtime, True)
        self.addCleanup(shutil.rmtree, cache, True)
        env = {
            "SONY_HEADPHONES_DEMO": "v2",
            "XDG_RUNTIME_DIR": runtime,
            "XDG_CACHE_HOME": cache,
            "SONY_HEADPHONES_TRACE": self.path,
        }
        done = subprocess.run(
            [HELPER, "--json", "status"],
            capture_output=True, text=True, timeout=30, env=env,
        )
        self.assertEqual(done.returncode, 0, done.stderr)
        info = os.lstat(self.path)
        self.assertTrue(stat.S_ISREG(info.st_mode))
        self.assertEqual(stat.S_IMODE(info.st_mode), 0o600)
        self.assertFalse(os.path.islink(self.path))


if __name__ == "__main__":
    unittest.main(verbosity=2)
