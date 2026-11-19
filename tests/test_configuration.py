import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import bot
from configuration import Config
from i18n import set_language

ROOT = Path(__file__).resolve().parents[1]


class ConfigurationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "config.json"
        self.environment = patch.dict(os.environ)
        self.environment.start()
        self.addCleanup(self.environment.stop)
        os.environ.pop("TS6_LANGUAGE", None)
        os.environ.pop("TS6_QUERY_PASSWORD", None)
        set_language("en")
        self.addCleanup(set_language, "en")

    def write(self, data):
        self.path.write_text(json.dumps(data), encoding="utf-8")

    def test_minimal_config_uses_english_and_numeric_defaults(self):
        os.environ["LANG"] = "ru_RU.UTF-8"
        self.write({"host": "example.com", "afk_channel_id": 9})
        cfg = Config.load(self.path)
        self.assertEqual(cfg.language, "en")
        self.assertEqual(cfg.query_port, 10022)
        self.assertEqual(cfg.timeout_seconds, 300)
        self.assertTrue(cfg.effective_poke_message().startswith("You were moved"))

    def test_nonobject_json_is_rejected(self):
        for data in ([], None, "text", 3):
            with self.subTest(data=data):
                self.write(data)
                with self.assertRaises(ValueError):
                    Config.load(self.path)

    def test_missing_required_fields_have_a_clear_error(self):
        self.write({"host": "example.com"})
        with self.assertRaisesRegex(ValueError, "afk_channel_id"):
            Config.load(self.path)

    def test_bad_types_ranges_and_exclusion_elements_are_rejected(self):
        cases = [
            ("host", 1),
            ("host", " "),
            ("username", ""),
            ("password", None),
            ("query_port", True),
            ("query_port", 0),
            ("query_port", 65536),
            ("virtual_server_id", 0),
            ("afk_channel_id", -1),
            ("timeout_seconds", 9),
            ("check_interval", 0),
            ("reconnect_delay", -1),
            ("exclude_uids", [1]),
            ("exclude_uids", [""]),
            ("exclude_uids", "uid"),
            ("exclude_channels", [True]),
            ("exclude_channels", ["2"]),
            ("exclude_channels", [0]),
            ("poke_message", 3),
            ("language", "de"),
            ("language", []),
        ]
        for name, value in cases:
            with self.subTest(name=name, value=value), self.assertRaises(ValueError):
                Config(**({"host": "example.com", "afk_channel_id": 9} | {name: value}))

    def test_language_precedence_is_cli_then_environment_then_json(self):
        self.write({"host": "example.com", "afk_channel_id": 9, "language": "ru"})
        self.assertEqual(Config.load(self.path).language, "ru")
        os.environ["TS6_LANGUAGE"] = "en"
        self.assertEqual(Config.load(self.path).language, "en")
        self.assertEqual(Config.load(self.path, language="ru").language, "ru")

    def test_password_environment_override_can_also_be_empty(self):
        self.write({"host": "example.com", "afk_channel_id": 9, "password": "json-password"})
        os.environ["TS6_QUERY_PASSWORD"] = "environment-password"
        self.assertEqual(Config.load(self.path).password, "environment-password")
        os.environ["TS6_QUERY_PASSWORD"] = ""
        self.assertEqual(Config.load(self.path).password, "")

    def test_custom_and_disabled_pokes_are_preserved(self):
        for text in ("", "Custom user text"):
            for language in ("en", "ru"):
                with self.subTest(text=text, language=language):
                    cfg = Config(
                        host="example.com", afk_channel_id=9, language=language, poke_message=text
                    )
                    self.assertEqual(cfg.effective_poke_message(), text)

    def test_automatic_poke_uses_the_actual_timeout_and_language(self):
        cfg = Config(host="example.com", afk_channel_id=9, language="ru", timeout_seconds=120)
        self.assertIn("120", cfg.effective_poke_message())
        self.assertIn("Вы перемещены", cfg.effective_poke_message())
        self.assertNotIn("300", cfg.effective_poke_message())

    def test_main_reports_file_errors_without_a_traceback(self):
        with (
            patch("bot.Config.load", side_effect=PermissionError("denied")),
            self.assertLogs("ts6-afk-bot", level="ERROR") as logs,
        ):
            self.assertEqual(bot.main([]), 1)
        self.assertIn("Configuration error", "\n".join(logs.output))

    def test_config_language_is_used_for_validation_error_logs(self):
        self.write(
            {"host": "example.com", "afk_channel_id": 9, "language": "ru", "check_interval": 0}
        )
        with self.assertLogs("ts6-afk-bot", level="ERROR") as logs:
            self.assertEqual(bot.main(["--config", str(self.path)]), 1)
        output = "\n".join(logs.output)
        self.assertIn("Ошибка конфигурации", output)
        self.assertNotIn("Configuration error", output)

    def test_cli_language_reaches_the_bot(self):
        self.write({"host": "example.com", "afk_channel_id": 9, "language": "ru"})
        with patch("bot.AFKBot") as afk:
            self.assertEqual(bot.main(["--config", str(self.path), "--language", "en"]), 0)
        self.assertEqual(afk.call_args.args[0].language, "en")

    def test_help_and_errors_are_localized_for_both_entry_points(self):
        environment = os.environ.copy()
        environment["LANG"] = "ru_RU.UTF-8"
        for script in ("bot.py", "install.py"):
            for language in ("en", "ru"):
                with self.subTest(script=script, language=language):
                    result = subprocess.run(
                        [sys.executable, str(ROOT / script), "--help", "--language", language],
                        cwd=ROOT,
                        env=environment,
                        capture_output=True,
                        text=True,
                        check=False,
                    )
                    self.assertEqual(result.returncode, 0, result.stderr)
                    self.assertIn("usage:" if language == "en" else "использование:", result.stdout)
                    if language == "en":
                        self.assertNotRegex(result.stdout, r"[А-Яа-яЁё]")
        result = subprocess.run(
            [sys.executable, str(ROOT / "install.py"), "--language", "ru", "--query-port", "bad"],
            cwd=ROOT,
            env=environment,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(result.returncode, 2)
        self.assertIn("ошибка:", result.stderr)
        self.assertIn("недопустимое", result.stderr)


if __name__ == "__main__":
    unittest.main()
