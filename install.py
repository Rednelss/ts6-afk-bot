#!/usr/bin/env python3
"""Install or remove ts6-afk-bot on Linux. Requires Python 3.10+ and root."""

from __future__ import annotations

import argparse
import getpass
import grp
import json
import os
import pwd
import re
import shlex
import shutil
import socket
import subprocess
import sys
import tempfile
import time
from dataclasses import asdict
from pathlib import Path

from configuration import Config
from i18n import DEFAULT_LANGUAGE, LANGUAGES, configure_cli_language, tr

REPO_URL = "https://github.com/Rednelss/ts6-afk-bot.git"
SERVICE_NAME = "ts6-afk-bot"
SYSTEM_USER = "ts6afkbot"
SYSTEM_GROUP = SYSTEM_USER
USER_CREATED = False
INSTALL_DIR = Path("/opt/ts6-afk-bot")
CONFIG_DIR = Path("/etc/ts6-afk-bot")
STATE_DIR = Path("/var/lib/ts6-afk-bot")
VENV_DIR = INSTALL_DIR / "venv"
CONFIG_FILE = CONFIG_DIR / "config.json"
ENV_FILE = CONFIG_DIR / "env"
UNIT_FILE = Path(f"/etc/systemd/system/{SERVICE_NAME}.service")
MARKER_NAME = ".ts6-afk-bot-install"
_MISSING = object()

PKG_TABLE = {
    "apt-get": ["python3", "python3-venv", "python3-pip", "git"],
    "dnf": ["python3", "python3-pip", "git"],
    "yum": ["python3", "python3-pip", "git"],
    "pacman": ["python", "python-pip", "git"],
    "zypper": ["python3", "python3-pip", "git"],
}


def _use_color() -> bool:
    return sys.stdout.isatty() and os.environ.get("NO_COLOR") is None


def _paint(code: str, text: str) -> str:
    return f"\033[{code}m{text}\033[0m" if _use_color() else text


def info(msg: str) -> None:
    print(f"{_paint('34', '[*]')} {msg}", flush=True)


def ok(msg: str) -> None:
    print(f"{_paint('32', '[+]')} {msg}", flush=True)


def warn(msg: str) -> None:
    print(f"{_paint('33', '[!]')} {msg}", flush=True)


def err(msg: str) -> None:
    print(f"{_paint('31', '[-]')} {msg}", file=sys.stderr, flush=True)


def die(msg: str, code: int = 1) -> None:
    err(msg)
    raise SystemExit(code)


def run(
    cmd: list[str],
    check: bool = True,
    capture: bool = False,
    cwd: Path | None = None,
    env: dict[str, str] | None = None,
    quiet: bool = False,
    input_text: str | None = None,
) -> subprocess.CompletedProcess[str]:
    if isinstance(cmd, str):
        raise TypeError(tr("Commands must be passed as an argument list, not a shell string."))
    command = [str(part) for part in cmd]
    display = shlex.join(command)
    if not quiet:
        info(f"$ {display}")
    try:
        result = subprocess.run(
            command,
            shell=False,
            check=False,
            cwd=cwd,
            env=env,
            stdout=subprocess.PIPE if capture else None,
            stderr=subprocess.STDOUT if capture else None,
            text=True,
            input=input_text,
        )
    except OSError as exc:
        die(tr("Could not run {command}: {error}", command=display, error=exc))
    if check and result.returncode:
        if capture and result.stdout:
            err(result.stdout.strip())
        die(
            tr(
                "Command exited with code {code}: {command}",
                code=result.returncode,
                command=display,
            )
        )
    return result


def ensure_root() -> None:
    if os.geteuid() != 0:
        die(tr("Run this installer as root:\n    sudo python3 install.py"))


def check_python() -> None:
    if not shutil.which("python3"):
        die(tr("python3 was not found. Install Python 3.10+ and try again."))
    result = run(
        ["python3", "-c", "import sys; print('%d.%d' % sys.version_info[:2])"],
        capture=True,
        quiet=True,
        check=False,
    )
    if result.returncode:
        die(tr("Could not determine the Python version."))
    version = result.stdout.strip()
    try:
        major, minor = (int(part) for part in version.split("."))
    except ValueError:
        die(tr("Could not parse the Python version: {version!r}", version=version))
    if (major, minor) < (3, 10):
        die(tr("Python 3.10+ is required; found {version}.", version=version))
    ok(f"Python {version}")
    result = run(["python3", "-c", "import venv"], capture=True, quiet=True, check=False)
    if result.returncode:
        die(tr("The venv module is unavailable. Install python3-venv on Debian/Ubuntu."))


def detect_package_manager() -> str | None:
    return next((name for name in PKG_TABLE if shutil.which(name)), None)


def install_system_dependencies() -> None:
    manager = detect_package_manager()
    if manager is None:
        warn(tr("No supported package manager found; skipping system packages."))
        return
    info(tr("Package manager: {manager}", manager=manager))
    packages = PKG_TABLE[manager]
    if manager == "apt-get":
        run(["apt-get", "update", "-qq"], check=False, quiet=True)
        command = ["apt-get", "install", "-y", "--no-install-recommends", *packages]
    elif manager in ("dnf", "yum"):
        command = [manager, "install", "-y", *packages]
    elif manager == "pacman":
        # Do not refresh the package database without a full system upgrade.
        command = ["pacman", "-S", "--noconfirm", "--needed", *packages]
    else:
        command = ["zypper", "--non-interactive", "install", *packages]
    if run(command, check=False).returncode:
        warn(tr("System package installation failed; checking available tools next."))


def ensure_system_user() -> None:
    global SYSTEM_GROUP, USER_CREATED
    USER_CREATED = False
    try:
        account = pwd.getpwnam(SYSTEM_USER)
        ok(tr("User '{user}' already exists.", user=SYSTEM_USER))
        try:
            marker = json.loads((INSTALL_DIR / MARKER_NAME).read_text(encoding="utf-8"))
            USER_CREATED = (
                isinstance(marker, dict)
                and marker.get("service") == SERVICE_NAME
                and marker.get("user") == SYSTEM_USER
                and marker.get("created_user") is True
            )
        except (OSError, ValueError):
            pass
    except KeyError:
        shell = "/usr/sbin/nologin" if Path("/usr/sbin/nologin").exists() else "/bin/false"
        info(tr("Creating system user '{user}'...", user=SYSTEM_USER))
        run(
            [
                "useradd",
                "--system",
                "--user-group",
                "--home-dir",
                str(STATE_DIR),
                "--no-create-home",
                "--shell",
                shell,
                SYSTEM_USER,
            ]
        )
        USER_CREATED = True
        account = pwd.getpwnam(SYSTEM_USER)
    if account.pw_uid == 0:
        die(tr("The bot must not run as a user with UID 0."))
    SYSTEM_GROUP = grp.getgrgid(account.pw_gid).gr_name
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    os.chown(STATE_DIR, account.pw_uid, account.pw_gid)
    os.chmod(STATE_DIR, 0o750)


def fetch_source(source: str | None, skip_update: bool) -> None:
    bot_file = INSTALL_DIR / "bot.py"
    if bot_file.is_file() and skip_update:
        ok(tr("Source update skipped (--skip-update)."))
        return

    local = Path(source).expanduser() if source else None
    if local is not None and local.exists():
        local = local.resolve()
        if not local.is_dir():
            die(tr("Local source must be a directory: {path}", path=local))
        if local == INSTALL_DIR.resolve():
            if not bot_file.is_file():
                die(tr("Source file not found: {path}", path=bot_file))
            return
        if INSTALL_DIR.resolve().is_relative_to(local):
            die(tr("The installation directory must be outside the source directory."))
        if INSTALL_DIR.exists() and not bot_file.is_file() and any(INSTALL_DIR.iterdir()):
            die(
                tr(
                    "Directory {path} is not empty and does not contain bot.py. Choose another --install-dir.",
                    path=INSTALL_DIR,
                )
            )
        if not (local / "bot.py").is_file():
            die(tr("Source file not found: {path}", path=local / "bot.py"))
        info(tr("Copying local sources from {path}", path=local))
        shutil.copytree(
            local,
            INSTALL_DIR,
            dirs_exist_ok=True,
            ignore=shutil.ignore_patterns(
                ".git",
                ".venv",
                "venv",
                "__pycache__",
                ".ruff_cache",
                ".pytest_cache",
                ".aws",
                ".codex",
                ".agents",
                "config.json",
                "env",
                ".env",
                MARKER_NAME,
            ),
        )
    elif bot_file.is_file():
        if (INSTALL_DIR / ".git").exists():
            if source:
                origin = run(
                    ["git", "-C", str(INSTALL_DIR), "remote", "get-url", "origin"],
                    capture=True,
                    quiet=True,
                ).stdout.strip()
                if origin.rstrip("/") != source.rstrip("/"):
                    die(
                        tr(
                            "The installed repository has a different origin. Use its checkout or choose another --install-dir."
                        )
                    )
            info(tr("Updating the repository..."))
            # A failed fetch must not reset to a stale FETCH_HEAD or discard local changes.
            run(["git", "-C", str(INSTALL_DIR), "pull", "--ff-only"])
            ok(tr("Repository updated."))
        else:
            if source:
                die(
                    tr(
                        "The installed sources are not a Git checkout. Choose another --install-dir for a source URL."
                    )
                )
            ok(tr("Installed sources are not a Git repository; update skipped."))
        return
    else:
        if INSTALL_DIR.exists() and any(INSTALL_DIR.iterdir()):
            die(
                tr(
                    "Directory {path} is not empty and does not contain bot.py. Choose another --install-dir.",
                    path=INSTALL_DIR,
                )
            )
        if not shutil.which("git"):
            die(tr("git was not found. Install it before using a source URL."))
        INSTALL_DIR.parent.mkdir(parents=True, exist_ok=True)
        url = source or REPO_URL
        info(tr("Cloning {url}", url=url))
        run(["git", "clone", "--depth", "1", "--", url, str(INSTALL_DIR)])
    if not bot_file.is_file():
        die(tr("Source file not found: {path}", path=bot_file))


def setup_venv() -> None:
    py = VENV_DIR / "bin" / "python"
    if not py.is_file():
        info(tr("Creating a virtual environment..."))
        run(["python3", "-m", "venv", str(VENV_DIR)])
    run([str(py), "-m", "pip", "install", "--upgrade", "pip", "wheel"], check=False, quiet=True)
    requirements = INSTALL_DIR / "requirements.txt"
    if requirements.is_file():
        run([str(py), "-m", "pip", "install", "-r", str(requirements)])
    else:
        warn(tr("requirements.txt was not found; installing paramiko directly."))
        run([str(py), "-m", "pip", "install", "paramiko>=3.4"])
    result = run(
        [str(py), "-c", "import paramiko; print(paramiko.__version__)"],
        capture=True,
        quiet=True,
        check=False,
    )
    if result.returncode:
        die(tr("paramiko was not installed. Check the pip output above."))
    ok(f"paramiko {result.stdout.strip()}")


def ask(prompt: str, default=None, required: bool = True, cast=str, validator=None):
    suffix = f" [{default}]" if default not in (None, "", []) else ""
    while True:
        try:
            raw = input(f"{prompt}{suffix}: ").strip()
        except EOFError:
            die(
                tr(
                    "Input ended before configuration was complete. Use --non-interactive with the required options."
                )
            )
        if not raw:
            if default is not None:
                raw = default
            elif not required:
                return None
            else:
                print("  " + tr("A value is required."))
                continue
        try:
            value = cast(raw)
        except (TypeError, ValueError):
            print("  " + tr("Invalid value; try again."))
            continue
        if validator is not None and not validator(value):
            print("  " + tr("Invalid value; try again."))
            continue
        return value


def parse_int_list(value) -> list[int]:
    if value in (None, "", []):
        return []
    tokens = value if isinstance(value, (list, tuple)) else re.split(r"[,\s]+", str(value).strip())
    if any(isinstance(token, bool) for token in tokens):
        raise ValueError
    return [int(token) for token in tokens if str(token).strip()]


def parse_str_list(value) -> list[str]:
    if value in (None, "", []):
        return []
    tokens = value if isinstance(value, (list, tuple)) else re.split(r"[,\s]+", str(value).strip())
    return [str(token).strip() for token in tokens if str(token).strip()]


def build_config(args: argparse.Namespace) -> dict:
    interactive = not args.non_interactive

    def need(attr, prompt, default=_MISSING, cast=str, validator=None):
        value = getattr(args, attr, None)
        if value is not None:
            try:
                value = cast(value)
            except (TypeError, ValueError):
                die(
                    tr(
                        "Invalid value for --{option}: {value!r}",
                        option=attr.replace("_", "-"),
                        value=value,
                    )
                )
            if validator is not None and not validator(value):
                die(
                    tr(
                        "Invalid value for --{option}: {value!r}",
                        option=attr.replace("_", "-"),
                        value=value,
                    )
                )
            return value
        if interactive:
            return ask(
                tr(prompt),
                default=None if default is _MISSING else default,
                required=default is _MISSING,
                cast=cast,
                validator=validator,
            )
        if default is not _MISSING:
            return default
        die(tr("--{option} is required in non-interactive mode.", option=attr.replace("_", "-")))

    if interactive:
        print()
        info(tr("Bot configuration. Press Enter to accept the default."))
        print()
    data = {
        "host": need(
            "host", "TS6 server address (host)", validator=lambda value: bool(value.strip())
        ),
        "query_port": need(
            "query_port", "SSH ServerQuery port", 10022, int, lambda value: 1 <= value <= 65535
        ),
        "username": need(
            "username",
            "Query account username",
            "serveradmin",
            validator=lambda value: bool(value.strip()),
        ),
        "virtual_server_id": need(
            "virtual_server_id", "Virtual server ID (sid)", 1, int, lambda value: value >= 1
        ),
        "afk_channel_id": need(
            "afk_channel_id", "AFK channel ID (cid)", cast=int, validator=lambda value: value >= 1
        ),
        "timeout_seconds": need(
            "timeout_seconds", "Idle limit in seconds", 300, int, lambda value: value >= 10
        ),
        "check_interval": need(
            "check_interval", "Check interval in seconds", 30, int, lambda value: value >= 1
        ),
        "reconnect_delay": need(
            "reconnect_delay", "Reconnect delay in seconds", 10, int, lambda value: value >= 0
        ),
        "poke_message": need(
            "poke_message", "Poke message (Enter: automatic; '-' to disable)", None
        ),
        "exclude_uids": need(
            "exclude_uids", "Excluded UIDs (comma-separated; empty for none)", [], parse_str_list
        ),
        "exclude_channels": need(
            "exclude_channels",
            "Excluded channel IDs (comma-separated; empty for none)",
            [],
            parse_int_list,
            lambda value: all(cid >= 1 for cid in value),
        ),
        "language": getattr(args, "language", None) or DEFAULT_LANGUAGE,
    }
    if interactive and args.poke_message is None and data["poke_message"] == "-":
        data["poke_message"] = ""

    password = args.password
    if password is None:
        password = os.environ.get("TS6_QUERY_PASSWORD")
    if password is None and interactive:
        while True:
            try:
                first = getpass.getpass(tr("Query account password (Enter to leave empty): "))
                if not first:
                    password = ""
                    break
                second = getpass.getpass(tr("Repeat the password: "))
            except EOFError:
                die(
                    tr(
                        "Input ended before configuration was complete. Use --non-interactive with the required options."
                    )
                )
            if first == second:
                password = first
                break
            warn(tr("Passwords do not match; try again."))
    data["password"] = password if password is not None else ""
    try:
        cfg = Config(**data)
        if cfg.password:
            quote_environment_value(cfg.password)
    except ValueError as exc:
        die(str(exc))
    return asdict(cfg)


def check_connection(host: str, port: int) -> bool:
    try:
        with socket.create_connection((host, port), timeout=5):
            ok(tr("{host}:{port} is reachable.", host=host, port=port))
            return True
    except OSError as exc:
        warn(
            tr(
                "Could not connect to {host}:{port} ({error}). The bot will retry when the server becomes available.",
                host=host,
                port=port,
                error=exc,
            )
        )
        return False


def atomic_write(path: Path, content: str, mode: int) -> None:
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            os.fchmod(handle.fileno(), mode)
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


def write_config(cfg: dict) -> None:
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    os.chmod(CONFIG_DIR, 0o750)
    data = dict(cfg)
    data["password"] = ""
    atomic_write(CONFIG_FILE, json.dumps(data, indent=2, ensure_ascii=False) + "\n", 0o640)
    ok(tr("Configuration written: {path}", path=CONFIG_FILE))


def quote_environment_value(value: str) -> str:
    if any(
        ord(char) in (0, 0xFEFF)
        or 0xD800 <= ord(char) <= 0xDFFF
        or 0xFDD0 <= ord(char) <= 0xFDEF
        or ord(char) & 0xFFFF in (0xFFFE, 0xFFFF)
        for char in value
    ):
        raise ValueError(
            tr(
                "The password contains characters that cannot be stored in a systemd environment file."
            )
        )
    # EnvironmentFile uses shell double-quote escapes, not JSON escape sequences.
    escaped = value.translate(
        str.maketrans(
            {
                "\\": "\\\\",
                '"': '\\"',
                "$": "\\$",
                chr(96): "\\" + chr(96),
            }
        )
    )
    return f'"{escaped}"'


def write_env(password: str) -> None:
    if not password:
        if ENV_FILE.exists():
            ENV_FILE.unlink()
            warn(tr("Empty password; removed {path}.", path=ENV_FILE))
        else:
            info(tr("No password supplied; no environment file created."))
        return
    content = (
        "# Generated by install.py. Keep this file out of version control.\n"
        "# Overrides the password field in config.json.\n"
        f"TS6_QUERY_PASSWORD={quote_environment_value(password)}\n"
    )
    atomic_write(ENV_FILE, content, 0o600)
    ok(tr("Password saved to {path} (mode 600).", path=ENV_FILE))


def unit_quote(value: str, command: bool = False) -> str:
    value = value.replace("%", "%%")
    if command:
        value = value.replace("$", "$$")
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


def write_unit(verbose: bool) -> None:
    command = [
        str(VENV_DIR / "bin" / "python"),
        str(INSTALL_DIR / "bot.py"),
        "--config",
        str(CONFIG_FILE),
    ]
    if verbose:
        command.append("--verbose")
    # systemd expands environment variables in arguments, but not the executable path.
    exec_line = (
        unit_quote(command[0])
        + " "
        + " ".join(unit_quote(part, command=True) for part in command[1:])
    )
    unit = f"""# Generated by install.py.
[Unit]
Description={tr("TeamSpeak 6 AFK bot")}
Documentation=https://github.com/Rednelss/ts6-afk-bot
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User={SYSTEM_USER}
Group={SYSTEM_GROUP}
WorkingDirectory={str(INSTALL_DIR).replace("%", "%%")}
Environment=PYTHONUNBUFFERED=1
Environment={unit_quote("HOME=" + str(STATE_DIR))}
EnvironmentFile=-{str(ENV_FILE).replace("%", "%%")}
ExecStart={exec_line}
Restart=always
RestartSec=10
TimeoutStopSec=10
KillSignal=SIGTERM
SyslogIdentifier={SERVICE_NAME}
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=strict
ProtectHome=true
ProtectKernelTunables=true
ProtectKernelModules=true
ProtectControlGroups=true
RestrictSUIDSGID=true
RestrictAddressFamilies=AF_INET AF_INET6 AF_UNIX
LockPersonality=true
CapabilityBoundingSet=
AmbientCapabilities=

[Install]
WantedBy=multi-user.target
"""
    atomic_write(UNIT_FILE, unit, 0o644)
    ok(tr("systemd unit written: {path}", path=UNIT_FILE))


def fix_permissions() -> None:
    run(["chown", "-R", f"root:{SYSTEM_GROUP}", "--", str(INSTALL_DIR)], quiet=True)
    run(["chmod", "-R", "g+rX", "--", str(INSTALL_DIR)], quiet=True)
    run(["chmod", "-R", "go-w", "--", str(INSTALL_DIR)], quiet=True)
    run(["chown", "-R", f"root:{SYSTEM_GROUP}", "--", str(CONFIG_DIR)], quiet=True)
    os.chmod(CONFIG_DIR, 0o750)
    os.chmod(CONFIG_FILE, 0o640)
    if ENV_FILE.exists():
        run(["chown", "root:root", "--", str(ENV_FILE)], quiet=True)
        os.chmod(ENV_FILE, 0o600)


def enable_service() -> None:
    if not shutil.which("systemctl"):
        die(tr("systemctl was not found. Use --no-service for a manual installation."))
    run(["systemctl", "daemon-reload"])
    run(["systemctl", "enable", SERVICE_NAME])
    # enable --now leaves an already running service on the old code and config.
    run(["systemctl", "restart", SERVICE_NAME])
    time.sleep(2)
    result = run(["systemctl", "is-active", SERVICE_NAME], capture=True, quiet=True, check=False)
    state = result.stdout.strip()
    if state == "active":
        ok(tr("Service {service} is active.", service=SERVICE_NAME))
    else:
        warn(tr("Service state: {state}. Check the logs:", state=state))
        warn(f"  journalctl -u {SERVICE_NAME} -n 50 --no-pager")
    run(["systemctl", "status", SERVICE_NAME, "--no-pager", "-l"], check=False)


def configure_paths(args: argparse.Namespace) -> None:
    global INSTALL_DIR, VENV_DIR, CONFIG_DIR, CONFIG_FILE, STATE_DIR, ENV_FILE
    global UNIT_FILE, SERVICE_NAME, SYSTEM_USER, SYSTEM_GROUP
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_-]{0,31}", args.user) or args.user == "root":
        die(tr("Invalid system user name: {user!r}", user=args.user))
    if not re.fullmatch(
        r"[A-Za-z0-9][A-Za-z0-9_.@-]*", args.service_name
    ) or args.service_name.endswith(".service"):
        die(tr("Invalid service name: {service!r}", service=args.service_name))
    if any(ord(char) < 32 or ord(char) == 127 for char in args.install_dir):
        die(tr("Installation paths must not contain control characters."))
    path = Path(args.install_dir).expanduser().resolve()
    if str(path) != str(path).rstrip():
        die(tr("The installation directory must not end with whitespace."))
    protected = {
        Path("/"),
        Path("/opt"),
        Path("/etc"),
        Path("/etc/systemd"),
        Path("/etc/systemd/system"),
        Path("/usr"),
        Path("/usr/local"),
        Path("/var"),
        Path("/var/lib"),
        Path("/var/log"),
        Path("/var/cache"),
        Path("/home"),
        Path("/root"),
        Path("/tmp"),
        Path("/run"),
        Path("/dev"),
        Path("/proc"),
        Path("/sys"),
        Path.home(),
    }
    if path in protected or any(
        path.is_relative_to(root) for root in (Path("/dev"), Path("/proc"), Path("/sys"))
    ):
        die(tr("Unsafe installation directory: {path}", path=path))
    INSTALL_DIR = path
    VENV_DIR = path / "venv"
    SERVICE_NAME = args.service_name
    SYSTEM_USER = args.user
    SYSTEM_GROUP = SYSTEM_USER
    CONFIG_DIR = Path("/etc") / SERVICE_NAME
    CONFIG_FILE = CONFIG_DIR / "config.json"
    ENV_FILE = CONFIG_DIR / "env"
    STATE_DIR = Path("/var/lib") / SERVICE_NAME
    UNIT_FILE = Path("/etc/systemd/system") / f"{SERVICE_NAME}.service"
    managed = (INSTALL_DIR, CONFIG_DIR, STATE_DIR)
    for index, first in enumerate(managed):
        for second in managed[index + 1 :]:
            if first.is_relative_to(second) or second.is_relative_to(first):
                die(tr("Installation, configuration, and state directories must not overlap."))


def check_directories() -> None:
    for path, hint in (
        (INSTALL_DIR, INSTALL_DIR / "bot.py"),
        (CONFIG_DIR, CONFIG_FILE),
        (STATE_DIR, None),
    ):
        if path.is_symlink():
            die(tr("Installation directories must not be symbolic links: {path}", path=path))
        if not path.exists() or not any(path.iterdir()) or (path / MARKER_NAME).is_file():
            continue
        legacy = hint is not None and hint.is_file()
        if path == Path("/var/lib/ts6-afk-bot"):
            legacy = True
        if not legacy:
            die(tr("Directory {path} is not managed by this installer.", path=path))
    check_unit_owner()


def check_unit_owner() -> None:
    if UNIT_FILE.exists():
        content = UNIT_FILE.read_text(encoding="utf-8")
        if (
            "Description=TeamSpeak 6 AFK Bot" not in content
            and "Documentation=https://github.com/Rednelss/ts6-afk-bot" not in content
        ):
            die(tr("The existing unit does not belong to ts6-afk-bot: {path}", path=UNIT_FILE))


def write_markers() -> None:
    content = (
        json.dumps({"service": SERVICE_NAME, "user": SYSTEM_USER, "created_user": USER_CREATED})
        + "\n"
    )
    for path in (INSTALL_DIR, CONFIG_DIR, STATE_DIR):
        atomic_write(path / MARKER_NAME, content, 0o640)


def uninstall(purge: bool) -> None:
    check_unit_owner()
    created_user = False
    if purge:
        for path in (INSTALL_DIR, CONFIG_DIR, STATE_DIR):
            if not path.exists():
                continue
            try:
                marker = json.loads((path / MARKER_NAME).read_text(encoding="utf-8"))
            except (OSError, ValueError):
                marker = None
            if (
                not isinstance(marker, dict)
                or marker.get("service") != SERVICE_NAME
                or marker.get("user") != SYSTEM_USER
            ):
                die(
                    tr(
                        "Refusing to purge a directory without an installation marker: {path}",
                        path=path,
                    )
                )
            if path == INSTALL_DIR:
                created_user = marker.get("created_user") is True
    info(tr("Uninstalling ts6-afk-bot..."))
    if UNIT_FILE.exists():
        if not shutil.which("systemctl"):
            die(tr("systemctl was not found. Use --no-service for a manual installation."))
        run(["systemctl", "disable", "--now", SERVICE_NAME])
        UNIT_FILE.unlink()
        run(["systemctl", "daemon-reload"])
        run(["systemctl", "reset-failed", SERVICE_NAME], check=False)
        ok(tr("Service stopped and removed."))
    if purge:
        for path in (INSTALL_DIR, CONFIG_DIR, STATE_DIR):
            if path.exists():
                shutil.rmtree(path)
                ok(tr("Removed: {path}", path=path))
        if created_user:
            try:
                pwd.getpwnam(SYSTEM_USER)
            except KeyError:
                pass
            else:
                run(["userdel", SYSTEM_USER])
                ok(tr("User {user} removed.", user=SYSTEM_USER))
        else:
            info(tr("Existing user {user} retained.", user=SYSTEM_USER))
    else:
        warn(
            tr(
                "Data kept: {install}, {config}, {state}",
                install=INSTALL_DIR,
                config=CONFIG_DIR,
                state=STATE_DIR,
            )
        )
        warn(tr("To remove the data too, repeat the same options with --uninstall --purge."))
    ok(tr("Done."))


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    argv = sys.argv[1:] if argv is None else argv
    try:
        configure_cli_language(argv)
    except ValueError as exc:
        die(str(exc))
    parser = argparse.ArgumentParser(
        prog="install.py",
        allow_abbrev=False,
        description=tr("Linux installer for ts6-afk-bot (TeamSpeak 6 AFK bot)."),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=tr("Examples:") + "\n"
        "  sudo python3 install.py\n"
        "  sudo python3 install.py --language ru\n"
        "  sudo python3 install.py --non-interactive --host ts.example.com --afk-channel-id 12\n"
        "  sudo python3 install.py --uninstall --purge\n",
    )
    group = parser.add_argument_group(
        tr("Bot options (prompted for unless --non-interactive is set)")
    )
    group.add_argument(
        "--language",
        choices=LANGUAGES,
        help=tr("Interface and bot language (default: en; or TS6_LANGUAGE)"),
    )
    group.add_argument("--host", help=tr("TS6 server address"))
    group.add_argument("--query-port", type=int, help=tr("SSH ServerQuery port (default: 10022)"))
    group.add_argument("--username", help=tr("Query account username"))
    group.add_argument(
        "--password", help=tr("Query account password (or TS6_QUERY_PASSWORD; saved outside JSON)")
    )
    group.add_argument("--virtual-server-id", type=int, help=tr("Virtual server ID (sid)"))
    group.add_argument("--afk-channel-id", type=int, help=tr("AFK channel ID (cid)"))
    group.add_argument("--timeout-seconds", type=int, help=tr("Idle limit in seconds"))
    group.add_argument("--check-interval", type=int, help=tr("Check interval in seconds"))
    group.add_argument("--reconnect-delay", type=int, help=tr("Reconnect delay in seconds"))
    group.add_argument(
        "--poke-message", help=tr("Custom poke text; an empty string disables pokes")
    )
    group.add_argument("--exclude-uids", help=tr("Comma-separated client UIDs to exclude"))
    group.add_argument("--exclude-channels", help=tr("Comma-separated channel IDs to exclude"))

    group = parser.add_argument_group(tr("Installation options"))
    group.add_argument(
        "--source",
        default=str(Path(__file__).resolve().parent),
        help=tr("Source URL or local directory (default: this checkout)"),
    )
    group.add_argument(
        "--install-dir",
        default="/opt/ts6-afk-bot",
        help=tr("Installation directory (default: /opt/ts6-afk-bot)"),
    )
    group.add_argument("--user", default="ts6afkbot", help=tr("System user (default: ts6afkbot)"))
    group.add_argument(
        "--service-name",
        default="ts6-afk-bot",
        help=tr("systemd service name, without .service (default: ts6-afk-bot)"),
    )
    group.add_argument("--skip-update", action="store_true", help=tr("Keep the installed sources"))
    group.add_argument(
        "--no-service", action="store_true", help=tr("Install without creating a systemd service")
    )
    group.add_argument(
        "--bot-verbose", action="store_true", help=tr("Run the bot with debug logging")
    )
    group.add_argument(
        "--non-interactive",
        "--yes",
        dest="non_interactive",
        action="store_true",
        help=tr("Use defaults without prompting; --host and --afk-channel-id are required"),
    )

    group = parser.add_argument_group(tr("Uninstallation"))
    group.add_argument("--uninstall", action="store_true", help=tr("Stop and remove the service"))
    group.add_argument(
        "--purge",
        action="store_true",
        help=tr("With --uninstall: also remove installed files and the system user"),
    )
    args = parser.parse_args(argv)
    if args.purge and not args.uninstall:
        parser.error(tr("--purge requires --uninstall."))
    if args.language is None:
        args.language = os.environ.get("TS6_LANGUAGE", DEFAULT_LANGUAGE)
    return args


def main(argv: list[str] | None = None) -> None:
    args = parse_args(argv)
    configure_paths(args)
    ensure_root()
    if args.uninstall:
        uninstall(args.purge)
        return
    print()
    print(_paint("1;34", "  " + tr("TS6 AFK Bot: Linux installer")))
    print(
        _paint(
            "90",
            "  "
            + tr(
                "directory: {path}   service: {service}.service",
                path=INSTALL_DIR,
                service=SERVICE_NAME,
            ),
        )
    )
    print()

    cfg = build_config(args)
    check_directories()
    if not args.no_service and not shutil.which("systemctl"):
        die(tr("systemctl was not found. Use --no-service for a manual installation."))
    install_system_dependencies()
    check_python()
    ensure_system_user()
    fetch_source(args.source, args.skip_update)
    setup_venv()
    check_connection(cfg["host"], cfg["query_port"])
    write_config(cfg)
    if (
        args.non_interactive
        and args.password is None
        and "TS6_QUERY_PASSWORD" not in os.environ
        and ENV_FILE.exists()
    ):
        info(tr("No password supplied; keeping the existing environment file."))
    else:
        write_env(cfg["password"])
    write_markers()
    fix_permissions()
    if args.no_service:
        warn(tr("--no-service: no systemd unit created."))
        print()
        info(tr("Manual start:"))
        if ENV_FILE.exists():
            info(
                tr(
                    "Set TS6_QUERY_PASSWORD in your shell before starting the bot; sudo must preserve it."
                )
            )
        command = ["sudo"]
        if ENV_FILE.exists():
            command.append("--preserve-env=TS6_QUERY_PASSWORD")
        command += [
            "-u",
            SYSTEM_USER,
            str(VENV_DIR / "bin" / "python"),
            str(INSTALL_DIR / "bot.py"),
            "--config",
            str(CONFIG_FILE),
        ]
        print("    " + shlex.join(command))
        print()
        return

    write_unit(args.bot_verbose)
    enable_service()
    print()
    ok(tr("Installation complete."))
    print()
    print(_paint("1", "  " + tr("Useful commands:")))
    print(f"    systemctl status {SERVICE_NAME}")
    print(f"    systemctl restart {SERVICE_NAME}")
    print(f"    journalctl -u {SERVICE_NAME} -f")
    print()
    print(_paint("1", "  " + tr("Files:")))
    print("    " + tr("config: {path}", path=CONFIG_FILE))
    if ENV_FILE.exists():
        print("    " + tr("password: {path}", path=ENV_FILE))
    print("    " + tr("code: {path}", path=INSTALL_DIR))
    print()
    warn(
        tr("After editing config.json, run: sudo systemctl restart {service}", service=SERVICE_NAME)
    )
    print()


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print()
        die(tr("Interrupted by the user."), code=130)
    except (OSError, ValueError) as exc:
        die(tr("Installation failed: {error}", error=exc))
