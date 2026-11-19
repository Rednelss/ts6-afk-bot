import threading
import unittest
from collections import deque
from unittest.mock import Mock, patch

from paramiko.ssh_exception import AuthenticationException, SSHException

from bot import (
    ServerQueryError,
    StopRequested,
    TS6Query,
    escape,
    parse_response,
    sanitize_for_log,
    unescape,
)


class Clock:
    def __init__(self):
        self.now = 0.0

    def monotonic(self):
        self.now += 0.001
        return self.now

    def sleep(self, seconds):
        self.now += seconds


class Channel:
    def __init__(self, clock, chunks=()):
        self.clock = clock
        self.chunks = deque(chunks)
        self.closed = False
        self.eof_received = False
        self.sent = []

    def recv_ready(self):
        return bool(self.chunks) and self.chunks[0][0] <= self.clock.now

    def recv(self, size):
        return self.chunks.popleft()[1]

    def sendall(self, data):
        self.sent.append(data)

    def settimeout(self, value):
        self.timeout = value

    def close(self):
        self.closed = True


class QueryTests(unittest.TestCase):
    def setUp(self):
        self.clock = Clock()
        self.query = TS6Query("example.com", 10022, "bot", "")
        self.channel = Channel(self.clock)
        self.query._shell = self.channel
        self.monotonic = patch("bot.time.monotonic", self.clock.monotonic)
        self.sleep = patch("bot.time.sleep", self.clock.sleep)
        self.monotonic.start()
        self.sleep.start()
        self.addCleanup(self.monotonic.stop)
        self.addCleanup(self.sleep.stop)

    def test_escape_round_trip_preserves_literal_escape_sequences(self):
        values = [r"name\s\p\n\\", " |/\\\n\r\t\v\a\b\f", "Алиса 💤", r"\unknown"]
        for value in values:
            with self.subTest(value=value):
                self.assertEqual(unescape(escape(value)), value)

    def test_parser_ignores_prompt_echo_and_notifications(self):
        raw = (
            "AFK-Bot@My server(1):Main lobby> clientlist -uid -times\r\n"
            "notifycliententerview clid=8 cid=2 client_type=0\r\n"
            r"clid=1 cid=2 client_nickname=Alice\sSmith\pQA client_unique_identifier=abc="
            "\r\n\x1b[32merror id=0 msg=ok\x1b[0m\r\n"
        )
        self.assertEqual(
            parse_response(raw, "clientlist -uid -times"),
            [
                {
                    "clid": "1",
                    "cid": "2",
                    "client_nickname": "Alice Smith|QA",
                    "client_unique_identifier": "abc=",
                }
            ],
        )

    def test_incomplete_status_is_not_success(self):
        for raw in ("clid=1\n", "error id=0 msg=ok", "error id=0\n"):
            with self.subTest(raw=raw), self.assertRaises(RuntimeError):
                parse_response(raw)

    def test_server_error_retains_code_and_localizes_explanation(self):
        with self.assertRaises(ServerQueryError) as caught:
            parse_response("error id=2568 msg=insufficient\\spermissions\n", language="ru")
        self.assertEqual(caught.exception.code, 2568)
        self.assertEqual(caught.exception.message, "insufficient permissions")
        self.assertIn("Ошибка ServerQuery", str(caught.exception))

    def test_reader_waits_for_the_complete_error_line(self):
        self.channel.chunks.extend(
            [
                (0, b"clid=1\r\nerror id=256"),
                (0.25, b"8 msg=insufficient\\spermissions\r\n"),
            ]
        )
        with self.assertRaises(ServerQueryError) as caught:
            self.query.query("clientlist")
        self.assertEqual(caught.exception.code, 2568)
        self.assertGreaterEqual(self.clock.now, 0.25)

    def test_status_marker_inside_a_data_line_does_not_end_the_response(self):
        self.channel.chunks.extend(
            [
                (0, b"message=contains error id=0 msg=ok\r\n"),
                (0.25, b"error id=2568 msg=denied\r\n"),
            ]
        )
        with self.assertRaises(ServerQueryError):
            self.query.query("clientlist")

    def test_utf8_split_across_packets_is_preserved(self):
        data = f"client_nickname={escape('Алиса 💤')}\r\nerror id=0 msg=ok\r\n".encode()
        split = data.index("💤".encode()) + 2
        self.channel.chunks.extend([(0, data[:split]), (0.1, data[split:])])
        self.assertEqual(self.query.query("clientlist"), [{"client_nickname": "Алиса 💤"}])

    def test_remainder_of_a_packet_is_used_for_the_next_response(self):
        self.channel.chunks.append(
            (0, b"value=one\nerror id=0 msg=ok\nvalue=two\nerror id=0 msg=ok\n")
        )
        self.assertEqual(self.query.query("first"), [{"value": "one"}])
        self.assertEqual(self.query.query("second"), [{"value": "two"}])
        self.assertEqual(self.channel.sent, [b"first\n", b"second\n"])

    def test_timeout_is_bounded_while_data_keeps_arriving(self):
        self.channel.recv_ready = lambda: True
        self.channel.recv = lambda size: b"notifyevent value=1\n"
        with self.assertRaises(TimeoutError):
            self.query._read_until_error(timeout=0.1)
        self.assertLess(self.clock.now, 0.2)

    def test_banner_drain_has_a_hard_deadline(self):
        self.channel.recv_ready = lambda: True
        self.channel.recv = lambda size: b"banner\n"
        self.query._drain(duration=0.1)
        self.assertLess(self.clock.now, 0.2)

    def test_reader_detects_eof_and_closed_channels(self):
        self.channel.chunks.append((0, b""))
        with self.assertRaises(EOFError):
            self.query._read_until_error()
        self.channel.closed = True
        with self.assertRaises(EOFError):
            self.query._read_until_error()
        self.assertLess(self.clock.now, 0.1)

    def test_shutdown_does_not_send_another_command(self):
        stop = threading.Event()
        stop.set()
        self.query._stop_evt = stop
        with self.assertRaises(StopRequested):
            self.query.send("clientmove clid=1 cid=9")
        self.assertEqual(self.channel.sent, [])

    def test_signal_stop_callback_prevents_sending_commands(self):
        self.query._stop_check = lambda: True
        with self.assertRaises(StopRequested):
            self.query.send("clientlist")
        self.assertEqual(self.channel.sent, [])

    def test_multiline_commands_are_rejected(self):
        with self.assertRaises(ValueError):
            self.query.send("clientlist\nclientmove clid=1 cid=9")
        self.assertEqual(self.channel.sent, [])

    def test_connection_failures_close_the_ssh_client(self):
        for error in (
            AuthenticationException("bad password"),
            SSHException("bad banner"),
            OSError("offline"),
        ):
            with self.subTest(error=error):
                ssh = Mock()
                ssh.connect.side_effect = error
                with (
                    patch("bot.paramiko.SSHClient", return_value=ssh),
                    self.assertRaises((RuntimeError, OSError)),
                ):
                    self.query.connect()
                ssh.close.assert_called_once()
                self.assertIsNone(self.query._ssh)

    def test_shell_setup_failure_also_closes_the_ssh_client(self):
        ssh = Mock()
        ssh.invoke_shell.side_effect = SSHException("no shell")
        with patch("bot.paramiko.SSHClient", return_value=ssh), self.assertRaises(RuntimeError):
            self.query.connect()
        ssh.close.assert_called_once()
        options = ssh.connect.call_args.kwargs
        self.assertEqual(options["banner_timeout"], 15)
        self.assertEqual(options["auth_timeout"], 15)
        self.assertEqual(options["channel_timeout"], 15)

    def test_stop_during_connection_closes_the_ssh_client(self):
        stop = threading.Event()
        self.query._stop_evt = stop
        ssh = Mock()
        ssh.connect.side_effect = lambda **kwargs: stop.set()
        with patch("bot.paramiko.SSHClient", return_value=ssh), self.assertRaises(StopRequested):
            self.query.connect()
        ssh.close.assert_called_once()
        ssh.invoke_shell.assert_not_called()

    def test_log_sanitization_removes_terminal_controls(self):
        clean = sanitize_for_log("\x1b[31mAlice\x1b[0m\n\b\x00Bob")
        self.assertNotRegex(clean, r"[\x00-\x1f\x7f]")
        self.assertIn("Alice", clean)

    def test_cleanup_still_closes_ssh_when_the_shell_close_fails(self):
        shell = Mock()
        shell.close.side_effect = RuntimeError("already broken")
        ssh = Mock()
        self.query._shell = shell
        self.query._ssh = ssh
        self.query.close()
        ssh.close.assert_called_once()
        self.assertIsNone(self.query._shell)
        self.assertIsNone(self.query._ssh)


if __name__ == "__main__":
    unittest.main()
