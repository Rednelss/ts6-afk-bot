import subprocess
import sys
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from bot import AFKBot, ServerQueryError, StopRequested, escape
from configuration import Config


def client(clid=1, cid=2, idle="300000", **extra):
    data = {
        "clid": str(clid),
        "cid": str(cid),
        "client_type": "0",
        "client_unique_identifier": f"uid{clid}",
        "client_nickname": "Alice",
    }
    if idle is not None:
        data["client_idle_time"] = idle
    data.update(extra)
    return data


class Query:
    def __init__(self, clients=(), info=None, errors=None):
        self.clients = list(clients)
        self.info = info or {}
        self.errors = errors or {}
        self.commands = []
        self.closed = False

    def query(self, command):
        self.commands.append(command)
        if command in self.errors:
            raise self.errors[command]
        if command.startswith("clientlist"):
            return self.clients
        if command.startswith("clientinfo"):
            return self.info.get(command, [])
        return []

    def close(self):
        self.closed = True


class BotTests(unittest.TestCase):
    def bot(self, clients=(), **config):
        instance = AFKBot(Config(host="example.com", afk_channel_id=9, poke_message="", **config))
        instance.query = Query(clients)
        return instance

    def test_only_eligible_idle_clients_are_moved(self):
        clients = [
            client(1, idle="299999"),
            client(2, cid=9),
            client(3, cid="09"),
            client(4, cid=5),
            client(5, client_unique_identifier="protected"),
            client(6, client_type="1"),
            client(7, cid="²"),
            client(8, cid=0),
            client(9, idle="invalid"),
            client(10, idle="-1"),
            client(11),
        ]
        instance = self.bot(clients, exclude_uids=["protected"], exclude_channels=[5])
        instance.tick()
        self.assertEqual(
            instance.query.commands, ["clientlist -uid -times", "clientmove clid=11 cid=9"]
        )

    def test_missing_idle_time_uses_clientinfo(self):
        instance = self.bot([client(idle=None)])
        instance.query.info = {
            "clientinfo clid=1": [{"client_idle_time": "300000", "client_channel_id": "2"}]
        }
        instance.tick()
        self.assertIn("clientmove clid=1 cid=9", instance.query.commands)

    def test_unsupported_times_flag_falls_back_without_reconnecting(self):
        instance = self.bot([client(idle=None)])
        instance.query.errors = {"clientlist -uid -times": ServerQueryError(1540, "invalid flag")}
        instance.query.info = {"clientinfo clid=1": [{"client_idle_time": "300000"}]}
        instance.tick()
        instance.tick()
        self.assertEqual(instance.query.commands.count("clientlist -uid -times"), 1)
        self.assertEqual(instance.query.commands.count("clientlist -uid"), 2)
        self.assertIn("clientmove clid=1 cid=9", instance.query.commands)

    def test_permission_errors_do_not_trigger_the_flag_fallback(self):
        instance = self.bot()
        instance.query.errors = {"clientlist -uid -times": ServerQueryError(2568, "denied")}
        with self.assertRaises(ServerQueryError):
            instance.tick()
        self.assertEqual(instance.query.commands, ["clientlist -uid -times"])

    def test_clientinfo_rechecks_uid_and_channel_changes(self):
        for info in (
            {"client_unique_identifier": "different", "client_idle_time": "300000"},
            {"client_channel_id": "5", "client_idle_time": "300000"},
        ):
            with self.subTest(info=info):
                instance = self.bot([client(idle=None)])
                instance.query.info = {"clientinfo clid=1": [info]}
                instance.tick()
                self.assertFalse(
                    any(command.startswith("clientmove") for command in instance.query.commands)
                )

    def test_missing_uid_does_not_bypass_uid_exclusions(self):
        instance = self.bot([client(client_unique_identifier="")], exclude_uids=["protected"])
        instance.query.info = {
            "clientinfo clid=1": [
                {"client_unique_identifier": "protected", "client_idle_time": "300000"}
            ]
        }
        instance.tick()
        self.assertEqual(instance.query.commands, ["clientlist -uid -times", "clientinfo clid=1"])

    def test_failed_move_does_not_send_a_poke_and_does_not_skip_other_clients(self):
        instance = AFKBot(Config(host="example.com", afk_channel_id=9))
        instance.query = Query(
            [client(1), client(2)],
            errors={"clientmove clid=1 cid=9": ServerQueryError(2568, "denied")},
        )
        instance.tick()
        self.assertFalse(
            any(command.startswith("clientpoke clid=1 ") for command in instance.query.commands)
        )
        self.assertTrue(
            any(command.startswith("clientpoke clid=2 ") for command in instance.query.commands)
        )

    def test_automatic_poke_and_logs_use_the_selected_language(self):
        for language, phrase in (("en", "Moved to AFK"), ("ru", "Перемещён в AFK")):
            with self.subTest(language=language):
                cfg = Config(
                    host="example.com", afk_channel_id=9, language=language, timeout_seconds=120
                )
                instance = AFKBot(cfg)
                instance.query = Query([client()])
                with self.assertLogs("ts6-afk-bot", level="INFO") as logs:
                    instance.tick()
                self.assertIn(phrase, "\n".join(logs.output))
                self.assertIn(
                    f"clientpoke clid=1 msg={escape(cfg.effective_poke_message())}",
                    instance.query.commands,
                )
                self.assertIn("120", cfg.effective_poke_message())

    def test_shutdown_stops_processing_clients(self):
        instance = self.bot([client()])
        instance._stop_evt.set()
        with self.assertRaises(StopRequested):
            instance.tick()
        self.assertFalse(
            any(command.startswith("clientmove") for command in instance.query.commands)
        )

    def test_failed_server_selection_closes_the_new_query_session(self):
        for error in (OSError("offline"), ServerQueryError(2568, "denied")):
            with self.subTest(error=error):
                query = Mock()
                query.query.side_effect = error
                instance = self.bot()
                old = instance.query
                with (
                    patch("bot.TS6Query", return_value=query),
                    self.assertRaises((OSError, RuntimeError)),
                ):
                    instance.connect()
                self.assertTrue(old.closed)
                query.close.assert_called_once()
                self.assertIsNone(instance.query)

    def test_wrong_virtual_server_is_not_accepted(self):
        query = Mock()
        query.query.side_effect = [[], [{"virtualserver_id": "2"}]]
        instance = self.bot()
        with patch("bot.TS6Query", return_value=query), self.assertRaises(RuntimeError):
            instance.connect()
        query.close.assert_called_once()

    def test_query_nickname_permission_is_optional(self):
        query = Mock()
        query.query.side_effect = [
            [],
            [{"virtualserver_id": "1"}],
            ServerQueryError(2568, "denied"),
        ]
        instance = self.bot()
        with patch("bot.TS6Query", return_value=query):
            instance.connect()
        self.assertIs(instance.query, query)
        query.close.assert_not_called()

    def test_reconnection_closes_old_sessions_and_honors_shutdown(self):
        instance = self.bot()
        first = Query(errors={"clientlist -uid -times": EOFError("closed")})
        second = Query()
        sessions = iter([first, second])

        def connect():
            instance.query = next(sessions)

        def stop_after_poll(command):
            instance._stop_evt.set()
            return []

        second.query = stop_after_poll
        with (
            patch.object(instance, "install_signal_handlers"),
            patch.object(instance, "connect", side_effect=connect) as connections,
            patch.object(instance, "_wait") as wait,
        ):
            instance.run()
        self.assertEqual(connections.call_count, 2)
        self.assertTrue(first.closed)
        self.assertTrue(second.closed)
        self.assertIsNone(instance.query)
        wait.assert_any_call(instance.cfg.reconnect_delay)

    def test_signal_handler_does_not_deadlock_on_the_event_lock(self):
        code = """
import signal
from bot import AFKBot
from configuration import Config
instance = AFKBot(Config(host='example.com', afk_channel_id=9))
instance.install_signal_handlers()
with instance._stop_evt._cond:
    signal.raise_signal(signal.SIGTERM)
assert instance._should_stop()
"""
        result = subprocess.run(
            [sys.executable, "-c", code],
            cwd=Path(__file__).resolve().parents[1],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == "__main__":
    unittest.main()
