import contextlib
import io
import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import install
from configuration import Config
from i18n import set_language


class InstallerTests(unittest.TestCase):
    def setUp(self):
        self.stack = contextlib.ExitStack()
        self.addCleanup(self.stack.close)
        self.temp = self.stack.enter_context(tempfile.TemporaryDirectory())
        self.root = Path(self.temp)
        self.install_dir = self.root / "installed"
        self.config_dir = self.root / "config"
        self.state_dir = self.root / "state"
        for path in (self.install_dir, self.config_dir, self.state_dir):
            path.mkdir()
        values = {
            "INSTALL_DIR": self.install_dir,
            "CONFIG_DIR": self.config_dir,
            "STATE_DIR": self.state_dir,
            "VENV_DIR": self.install_dir / "venv",
            "CONFIG_FILE": self.config_dir / "config.json",
            "ENV_FILE": self.config_dir / "env",
            "UNIT_FILE": self.root / "ts6-afk-bot.service",
            "SYSTEM_GROUP": "ts6afkbot",
            "USER_CREATED": False,
            "SERVICE_NAME": "ts6-afk-bot",
            "SYSTEM_USER": "ts6afkbot",
        }
        for name, value in values.items():
            self.stack.enter_context(patch.object(install, name, value))
        self.stack.enter_context(patch.dict(os.environ))
        os.environ.pop("TS6_LANGUAGE", None)
        os.environ.pop("TS6_QUERY_PASSWORD", None)
        self.output = io.StringIO()
        self.stack.enter_context(contextlib.redirect_stdout(self.output))
        self.stack.enter_context(contextlib.redirect_stderr(self.output))
        set_language("en")
        self.addCleanup(set_language, "en")

    def args(self, *extra):
        return install.parse_args(
            ["--non-interactive", "--host", "example.com", "--afk-channel-id", "9", *extra]
        )

    def test_noninteractive_install_accepts_defaults_without_input(self):
        with (
            patch("builtins.input", side_effect=AssertionError("unexpected prompt")),
            patch(
                "install.getpass.getpass", side_effect=AssertionError("unexpected password prompt")
            ),
        ):
            cfg = install.build_config(self.args())
        self.assertEqual(cfg["query_port"], 10022)
        self.assertEqual(cfg["check_interval"], 30)
        self.assertEqual(cfg["username"], "serveradmin")
        self.assertEqual(cfg["exclude_uids"], [])
        self.assertEqual(cfg["language"], "en")

    def test_cli_values_use_the_same_validation_as_prompts(self):
        for option, value in (
            ("--query-port", "0"),
            ("--query-port", "-1"),
            ("--query-port", "65536"),
            ("--virtual-server-id", "0"),
            ("--timeout-seconds", "5"),
            ("--check-interval", "0"),
            ("--reconnect-delay", "-1"),
            ("--exclude-channels", "1,-2"),
            ("--username", ""),
            ("--host", ""),
        ):
            with self.subTest(option=option, value=value), self.assertRaises(SystemExit):
                install.build_config(self.args(option, value))

    def test_missing_required_option_fails_without_prompting(self):
        args = install.parse_args(["--non-interactive", "--host", "example.com"])
        with (
            patch("builtins.input", side_effect=AssertionError("unexpected prompt")),
            self.assertRaises(SystemExit),
        ):
            install.build_config(args)

    def test_empty_poke_flag_disables_pokes(self):
        cfg = Config(**install.build_config(self.args("--poke-message", "")))
        self.assertEqual(cfg.effective_poke_message(), "")

    def test_russian_is_persisted_with_an_automatic_poke(self):
        cfg = Config(**install.build_config(self.args("--language", "ru")))
        self.assertEqual(cfg.language, "ru")
        self.assertIn("Вы перемещены", cfg.effective_poke_message())

    def test_environment_password_is_used_without_printing_it(self):
        os.environ["TS6_QUERY_PASSWORD"] = "test-secret"
        cfg = install.build_config(self.args())
        self.assertEqual(cfg["password"], "test-secret")
        self.assertNotIn("test-secret", self.output.getvalue())

    def test_eof_does_not_loop_forever(self):
        with patch("builtins.input", side_effect=EOFError) as prompt, self.assertRaises(SystemExit):
            install.ask("Host")
        prompt.assert_called_once()

    def test_password_interrupt_is_not_treated_as_an_empty_password(self):
        args = self.args("--poke-message", "", "--exclude-uids", "", "--exclude-channels", "")
        args.non_interactive = False
        with (
            patch("builtins.input", return_value=""),
            patch("install.getpass.getpass", side_effect=KeyboardInterrupt),
            self.assertRaises(KeyboardInterrupt),
        ):
            install.build_config(args)

    def test_environment_file_preserves_special_characters(self):
        secret = (
            "p'\"\\s\\n\n\r\t$VALUE$(printf changed)" + chr(96) + "printf changed" + chr(96) + " 💤"
        )
        install.write_env(secret)
        result = subprocess.run(
            [
                "/bin/sh",
                "-c",
                '. "$1"; printf "%s" "$TS6_QUERY_PASSWORD"',
                "sh",
                str(install.ENV_FILE),
            ],
            capture_output=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, secret.encode())
        self.assertEqual(install.ENV_FILE.stat().st_mode & 0o777, 0o600)
        self.assertNotIn(secret, self.output.getvalue())

    def test_invalid_environment_characters_are_rejected_before_writing(self):
        for value in ("secret\x00", "secret\ufeff", "secret\uffff"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                install.write_env(value)
        self.assertFalse(install.ENV_FILE.exists())

    def test_config_is_atomic_and_does_not_store_the_password(self):
        cfg = install.build_config(self.args("--language", "ru", "--password", "test-secret"))
        install.write_config(cfg)
        data = json.loads(install.CONFIG_FILE.read_text())
        self.assertEqual(data["password"], "")
        self.assertEqual(data["language"], "ru")
        self.assertIsNone(data["poke_message"])
        self.assertEqual(install.CONFIG_FILE.stat().st_mode & 0o777, 0o640)
        original = install.CONFIG_FILE.read_text()
        with (
            patch("install.os.replace", side_effect=OSError("disk failure")),
            self.assertRaises(OSError),
        ):
            install.atomic_write(install.CONFIG_FILE, "replacement", 0o640)
        self.assertEqual(install.CONFIG_FILE.read_text(), original)
        self.assertEqual(list(self.config_dir.glob(".config.json.*")), [])

    def test_local_source_does_not_copy_secrets_or_virtual_environments(self):
        source = self.root / "source"
        source.mkdir()
        (source / "bot.py").write_text("new bot")
        (source / "config.json").write_text("secret config")
        (source / "env").write_text("secret password")
        (source / "venv").mkdir()
        (source / "venv" / "private").write_text("venv")
        (source / ".git").mkdir()
        install.fetch_source(str(source), False)
        self.assertEqual((self.install_dir / "bot.py").read_text(), "new bot")
        for name in ("config.json", "env", "venv", ".git"):
            self.assertFalse((self.install_dir / name).exists())
        (source / "bot.py").write_text("updated bot")
        install.fetch_source(str(source), False)
        self.assertEqual((self.install_dir / "bot.py").read_text(), "updated bot")

    def test_source_destination_recursion_is_rejected(self):
        source = self.root / "source"
        source.mkdir()
        (source / "bot.py").write_text("bot")
        with (
            patch.object(install, "INSTALL_DIR", source / "installed"),
            self.assertRaises(SystemExit),
        ):
            install.fetch_source(str(source), False)

    def test_failed_git_update_does_not_claim_success_or_reset_files(self):
        (self.install_dir / "bot.py").write_text("local bot")
        (self.install_dir / ".git").mkdir()
        with (
            patch("install.run", side_effect=SystemExit(1)) as run,
            patch("install.ok") as ok,
            self.assertRaises(SystemExit),
        ):
            install.fetch_source(None, False)
        self.assertEqual(run.call_args.args[0][-2:], ["pull", "--ff-only"])
        ok.assert_not_called()
        self.assertEqual((self.install_dir / "bot.py").read_text(), "local bot")

    def test_existing_service_is_restarted_after_reconfiguration(self):
        def run(command, **kwargs):
            return subprocess.CompletedProcess(command, 0, stdout="active\n")

        with (
            patch("install.shutil.which", return_value="/bin/systemctl"),
            patch("install.time.sleep"),
            patch("install.run", side_effect=run) as commands,
        ):
            install.enable_service()
        expected = [
            ["systemctl", "daemon-reload"],
            ["systemctl", "enable", install.SERVICE_NAME],
            ["systemctl", "restart", install.SERVICE_NAME],
        ]
        self.assertEqual([call.args[0] for call in commands.call_args_list[:3]], expected)

    def test_existing_account_uses_its_actual_primary_group(self):
        account = SimpleNamespace(pw_uid=1234, pw_gid=4567)
        with (
            patch("install.pwd.getpwnam", return_value=account),
            patch("install.grp.getgrgid", return_value=SimpleNamespace(gr_name="actualgroup")),
            patch("install.os.chown") as chown,
        ):
            install.ensure_system_user()
        self.assertEqual(install.SYSTEM_GROUP, "actualgroup")
        chown.assert_called_once_with(self.state_dir, 1234, 4567)
        self.assertFalse(install.USER_CREATED)

    def test_shell_strings_and_failed_permission_commands_abort(self):
        with self.assertRaises(TypeError):
            install.run("echo unsafe")
        with (
            patch("install.run", side_effect=SystemExit(1)) as commands,
            self.assertRaises(SystemExit),
        ):
            install.fix_permissions()
        self.assertEqual(commands.call_count, 1)

    def test_unsafe_paths_and_unit_names_are_rejected(self):
        for flag, value in (
            ("--install-dir", "/"),
            ("--install-dir", "/etc/ts6-afk-bot"),
            ("--install-dir", "/tmp/ts6-afk-space "),
            ("--service-name", "../other"),
            ("--user", "root"),
        ):
            with self.subTest(flag=flag, value=value), self.assertRaises(SystemExit):
                install.configure_paths(self.args(flag, value))

    def test_custom_service_uses_separate_config_and_state_directories(self):
        install.configure_paths(
            self.args("--service-name", "ts6-afk-bot-two", "--install-dir", str(self.root / "two"))
        )
        self.assertEqual(install.CONFIG_DIR, Path("/etc/ts6-afk-bot-two"))
        self.assertEqual(install.STATE_DIR, Path("/var/lib/ts6-afk-bot-two"))
        self.assertEqual(install.UNIT_FILE.name, "ts6-afk-bot-two.service")

    def test_purge_refuses_unmarked_directories(self):
        file = self.install_dir / "keep.txt"
        file.write_text("keep")
        with patch("install.run") as commands, self.assertRaises(SystemExit):
            install.uninstall(True)
        self.assertTrue(file.exists())
        commands.assert_not_called()

    def test_uninstall_refuses_an_unrelated_unit(self):
        install.UNIT_FILE.write_text("[Unit]\nDescription=Another service\n")
        with patch("install.run") as commands, self.assertRaises(SystemExit):
            install.uninstall(False)
        self.assertTrue(install.UNIT_FILE.exists())
        commands.assert_not_called()

    def test_purge_preserves_an_account_not_created_by_the_installer(self):
        install.write_markers()
        with patch("install.run") as commands:
            install.uninstall(True)
        commands.assert_not_called()
        self.assertFalse(self.install_dir.exists())

    def test_purge_removes_a_created_account(self):
        install.USER_CREATED = True
        install.write_markers()
        with (
            patch("install.pwd.getpwnam", return_value=SimpleNamespace(pw_uid=1234)),
            patch("install.run") as commands,
        ):
            install.uninstall(True)
        commands.assert_called_once_with(["userdel", install.SYSTEM_USER])

    def test_invalid_config_does_not_start_installation_commands(self):
        with (
            patch("install.configure_paths"),
            patch("install.ensure_root"),
            patch("install.install_system_dependencies") as packages,
            self.assertRaises(SystemExit),
        ):
            install.main(
                [
                    "--non-interactive",
                    "--host",
                    "example.com",
                    "--afk-channel-id",
                    "9",
                    "--query-port",
                    "-1",
                ]
            )
        packages.assert_not_called()

    def test_manual_install_flow_uses_the_language_and_preserves_the_password_in_sudo(self):
        def fetch(source, skip_update):
            (self.install_dir / "bot.py").write_text("bot")

        for language in ("en", "ru"):
            with self.subTest(language=language), contextlib.ExitStack() as stack:
                for name in (
                    "configure_paths",
                    "ensure_root",
                    "install_system_dependencies",
                    "check_python",
                    "ensure_system_user",
                    "setup_venv",
                    "check_connection",
                    "fix_permissions",
                ):
                    stack.enter_context(patch.object(install, name))
                stack.enter_context(patch.object(install, "fetch_source", side_effect=fetch))
                unit = stack.enter_context(patch.object(install, "write_unit"))
                service = stack.enter_context(patch.object(install, "enable_service"))
                self.output.seek(0)
                self.output.truncate()
                install.main(
                    [
                        "--non-interactive",
                        "--no-service",
                        "--host",
                        "example.com",
                        "--afk-channel-id",
                        "9",
                        "--language",
                        language,
                        "--password",
                        "test-secret",
                    ]
                )
                self.assertEqual(json.loads(install.CONFIG_FILE.read_text())["language"], language)
                self.assertIn(
                    "Manual start:" if language == "en" else "Ручной запуск:",
                    self.output.getvalue(),
                )
                self.assertIn("--preserve-env=TS6_QUERY_PASSWORD", self.output.getvalue())
                self.assertNotIn("test-secret", self.output.getvalue())
                if language == "en":
                    self.assertNotRegex(self.output.getvalue(), r"[А-Яа-яЁё]")
                unit.assert_not_called()
                service.assert_not_called()

    def test_unattended_reinstall_keeps_an_unspecified_existing_password(self):
        (self.install_dir / "bot.py").write_text("bot")
        install.CONFIG_FILE.write_text(json.dumps({"host": "example.com", "afk_channel_id": 9}))
        install.ENV_FILE.write_text('TS6_QUERY_PASSWORD="existing-secret"\n')
        original = install.ENV_FILE.read_bytes()
        with contextlib.ExitStack() as stack:
            for name in (
                "configure_paths",
                "ensure_root",
                "install_system_dependencies",
                "check_python",
                "ensure_system_user",
                "fetch_source",
                "setup_venv",
                "check_connection",
                "fix_permissions",
            ):
                stack.enter_context(patch.object(install, name))
            install.main(
                [
                    "--non-interactive",
                    "--no-service",
                    "--host",
                    "example.com",
                    "--afk-channel-id",
                    "9",
                ]
            )
        self.assertEqual(install.ENV_FILE.read_bytes(), original)


if __name__ == "__main__":
    unittest.main()
