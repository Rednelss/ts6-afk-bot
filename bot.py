#!/usr/bin/env python3
"""Move idle TeamSpeak clients to an AFK channel through SSH ServerQuery."""

from __future__ import annotations

import argparse
import logging
import re
import signal
import sys
import threading
import time
from collections.abc import Callable
from pathlib import Path

import paramiko
from paramiko.ssh_exception import AuthenticationException, SSHException

from configuration import Config, ConfigurationError
from i18n import DEFAULT_LANGUAGE, LANGUAGES, Translator, configure_cli_language, set_language, tr

__version__ = "1.1.0"

LOG_FORMAT = "%(asctime)s [%(levelname)s] %(message)s"
logging.basicConfig(level=logging.INFO, format=LOG_FORMAT, datefmt="%Y-%m-%d %H:%M:%S")
log = logging.getLogger("ts6-afk-bot")


class StopRequested(Exception):
    """Interrupt query I/O when a shutdown signal has been received."""


_ANSI_RE = re.compile(
    r"\x1b(?:"
    r"\[[0-9;?]*[A-Za-z]"
    r"|\][^\x07\x1b]*(?:\x07|\x1b\\)"
    r"|[()][0-9A-Za-z]"
    r"|[=>MNOP]"
    r")"
)
_PROMPT_RE = re.compile(r"^.+?@.+?\([0-9]+\):.*?>\s*$")
_STATUS_RE = re.compile(r"error id=([0-9]+) msg=([^ ]*)(?: .*)?")
_CONTROL_RE = re.compile(r"[\x00-\x1f\x7f-\x9f]")
_ESCAPES = {
    "\\": "\\",
    "/": "/",
    "s": " ",
    "p": "|",
    "a": "\a",
    "b": "\b",
    "f": "\f",
    "n": "\n",
    "r": "\r",
    "t": "\t",
    "v": "\v",
}
_UNESCAPE_RE = re.compile(r"\\([\\/spabfnrtv])")
_ESCAPE_TABLE = str.maketrans({value: "\\" + key for key, value in _ESCAPES.items()})


def unescape(value: str) -> str:
    # Decode once so a literal backslash followed by 's' stays literal.
    return _UNESCAPE_RE.sub(lambda match: _ESCAPES[match[1]], value)


def escape(value: str) -> str:
    return value.translate(_ESCAPE_TABLE)


def sanitize_for_log(value: str, limit: int = 64) -> str:
    clean = _CONTROL_RE.sub(" ", _ANSI_RE.sub("", value)).strip()
    if len(clean) <= limit:
        return clean
    return clean[: limit - 3] + "..." if limit > 3 else clean[: max(0, limit)]


class ServerQueryError(RuntimeError):
    def __init__(self, code: int, message: str, language: str = DEFAULT_LANGUAGE) -> None:
        self.code = code
        self.message = message
        super().__init__(
            Translator(language)(
                "ServerQuery error {code}: {message}",
                code=code,
                message=sanitize_for_log(message, limit=200),
            )
        )


def parse_response(
    raw: str, echo: str = "", language: str = DEFAULT_LANGUAGE
) -> list[dict[str, str]]:
    entries: list[dict[str, str]] = []
    echo = echo.strip()
    lines = raw.split("\n")
    for index, line in enumerate(lines):
        clean = _ANSI_RE.sub("", line).strip()
        if not clean or (echo and clean == echo) or clean.startswith("notify"):
            continue
        if "=" not in clean.split(" ", 1)[0]:
            if _PROMPT_RE.fullmatch(clean):
                continue
            if echo and clean.endswith(echo) and _PROMPT_RE.fullmatch(clean[: -len(echo)].strip()):
                continue

        status = _STATUS_RE.fullmatch(clean)
        if status and index < len(lines) - 1:
            code = int(status[1])
            if code:
                raise ServerQueryError(code, unescape(status[2]), language)
            return entries

        for chunk in clean.split("|"):
            pairs = chunk.split(" ")
            if not pairs or "=" not in pairs[0]:
                continue
            row = {
                key: unescape(value)
                for key, value in (pair.split("=", 1) for pair in pairs if "=" in pair)
            }
            if row:
                entries.append(row)

    raise RuntimeError(Translator(language)("ServerQuery response has no complete status line."))


class TS6Query:
    def __init__(
        self,
        host: str,
        port: int,
        username: str,
        password: str,
        stop_evt: threading.Event | None = None,
        connect_timeout: int = 15,
        language: str = DEFAULT_LANGUAGE,
        stop_check: Callable[[], bool] | None = None,
    ) -> None:
        self.host = host
        self.port = port
        self.username = username
        self.password = password
        self.connect_timeout = connect_timeout
        self.language = language
        self.t = Translator(language)
        self._stop_evt = stop_evt
        self._stop_check = stop_check
        self._ssh: paramiko.SSHClient | None = None
        self._shell: paramiko.Channel | None = None
        self._recv_buffer = b""

    def _check_stop(self) -> None:
        if (self._stop_evt is not None and self._stop_evt.is_set()) or (
            self._stop_check is not None and self._stop_check()
        ):
            raise StopRequested()

    def connect(self) -> None:
        self.close()
        self._check_stop()
        log.info(self.t("Connecting to ServerQuery %s:%d..."), self.host, self.port)
        ssh = paramiko.SSHClient()
        self._ssh = ssh
        try:
            ssh.load_system_host_keys()
            ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
            ssh.connect(
                hostname=self.host,
                port=self.port,
                username=self.username,
                password=self.password,
                timeout=self.connect_timeout,
                banner_timeout=self.connect_timeout,
                auth_timeout=self.connect_timeout,
                channel_timeout=self.connect_timeout,
                allow_agent=False,
                look_for_keys=False,
            )
            self._check_stop()
            self._shell = ssh.invoke_shell(term="xterm", width=512, height=512)
            self._shell.settimeout(self.connect_timeout)
            self._drain(duration=0.8)
        except AuthenticationException as exc:
            self.close()
            raise RuntimeError(
                self.t(
                    "ServerQuery authentication failed. Check the query account username and password."
                )
            ) from exc
        except SSHException as exc:
            self.close()
            raise RuntimeError(self.t("SSH connection failed: {error}", error=exc)) from exc
        except BaseException:
            self.close()
            raise
        log.info(self.t("Connected to ServerQuery."))

    def close(self) -> None:
        # Attempt both closes even if one library resource fails during cleanup.
        for resource in (self._shell, self._ssh):
            if resource is not None:
                try:
                    resource.close()
                except Exception as exc:  # noqa: BLE001
                    log.debug(self.t("Could not close a query resource: %s"), exc)
        self._shell = None
        self._ssh = None
        self._recv_buffer = b""

    def _check_eof(self) -> None:
        assert self._shell is not None
        if self._shell.closed or self._shell.eof_received:
            raise EOFError(self.t("ServerQuery connection closed."))

    def _drain(self, duration: float = 0.5) -> str:
        assert self._shell is not None
        chunks: list[bytes] = []
        deadline = time.monotonic() + duration
        quiet_until = deadline
        while time.monotonic() < quiet_until:
            self._check_stop()
            if self._shell.recv_ready():
                chunk = self._shell.recv(65535)
                if not chunk:
                    raise EOFError(self.t("ServerQuery connection closed."))
                chunks.append(chunk)
                quiet_until = min(deadline, time.monotonic() + 0.1)
            else:
                self._check_eof()
                time.sleep(0.02)
        return b"".join(chunks).decode("utf-8", errors="replace")

    def _read_until_error(self, timeout: float = 5.0) -> str:
        assert self._shell is not None
        buf = bytearray(self._recv_buffer)
        self._recv_buffer = b""
        deadline = time.monotonic() + timeout
        processed = 0
        while True:
            self._check_stop()
            while (end := buf.find(b"\n", processed)) >= 0:
                line = _ANSI_RE.sub(
                    "", buf[processed:end].decode("utf-8", errors="replace")
                ).strip()
                processed = end + 1
                if _STATUS_RE.fullmatch(line):
                    # A prompt or the next reply can share the same SSH packet.
                    self._recv_buffer = bytes(buf[processed:])
                    return bytes(buf[:processed]).decode("utf-8", errors="replace")

            if time.monotonic() >= deadline:
                raise TimeoutError(
                    self.t("ServerQuery did not respond within {timeout} seconds.", timeout=timeout)
                )
            if self._shell.recv_ready():
                chunk = self._shell.recv(65535)
                if not chunk:
                    raise EOFError(self.t("ServerQuery connection closed."))
                buf.extend(chunk)
            else:
                self._check_eof()
                time.sleep(min(0.02, max(0, deadline - time.monotonic())))

    def send(self, command: str, timeout: float = 5.0) -> str:
        self._check_stop()
        if self._shell is None:
            raise RuntimeError(self.t("Call connect() before sending commands."))
        cmd = command.strip()
        if not cmd or "\n" in cmd or "\r" in cmd:
            raise ValueError(self.t("A ServerQuery command must be one nonempty line."))
        self._shell.settimeout(timeout)
        self._shell.sendall((cmd + "\n").encode("utf-8"))
        raw = self._read_until_error(timeout)
        if log.isEnabledFor(logging.DEBUG):
            if len(raw) > 800:
                log.debug("CMD=%r RAW[%d]=%r...%r", cmd, len(raw), raw[:400], raw[-200:])
            else:
                log.debug("CMD=%r RAW=%r", cmd, raw)
        return raw

    def query(self, command: str, timeout: float = 5.0) -> list[dict[str, str]]:
        return parse_response(
            self.send(command, timeout), echo=command.strip(), language=self.language
        )


def _positive_id(value: str) -> bool:
    if not isinstance(value, str) or not re.fullmatch(r"[0-9]+", value):
        return False
    try:
        return int(value) > 0
    except ValueError:
        return False


class AFKBot:
    def __init__(self, cfg: Config) -> None:
        self.cfg = cfg
        self.t = Translator(cfg.language)
        self.query: TS6Query | None = None
        self._stop_evt = threading.Event()
        # A signal handler cannot safely acquire the lock inside Event.set().
        self._stop_requested = False
        self._clientlist_times = True

    def install_signal_handlers(self) -> None:
        def handler(signum: int, _frame: object) -> None:
            if self._should_stop():
                log.warning(self.t("Repeated signal %s; exiting immediately."), signum)
                raise KeyboardInterrupt
            self._stop_requested = True
            log.info(
                self.t("Received signal %s; stopping (press Ctrl+C again to force exit)..."), signum
            )

        signal.signal(signal.SIGINT, handler)
        signal.signal(signal.SIGTERM, handler)

    def _should_stop(self) -> bool:
        return self._stop_requested or self._stop_evt.is_set()

    def _wait(self, seconds: float) -> None:
        end = time.monotonic() + seconds
        while not self._should_stop():
            remaining = end - time.monotonic()
            if remaining <= 0:
                return
            self._stop_evt.wait(timeout=min(0.5, remaining))

    def connect(self) -> None:
        if self.query is not None:
            self.query.close()
            self.query = None
        q = TS6Query(
            host=self.cfg.host,
            port=self.cfg.query_port,
            username=self.cfg.username,
            password=self.cfg.password,
            stop_evt=self._stop_evt,
            stop_check=self._should_stop,
            language=self.cfg.language,
        )
        try:
            q.connect()
            try:
                q.query(f"use sid={self.cfg.virtual_server_id}")
            except ServerQueryError as exc:
                raise RuntimeError(
                    self.t(
                        "use sid={sid} failed: {error}", sid=self.cfg.virtual_server_id, error=exc
                    )
                ) from exc
            try:
                who = q.query("whoami")
            except ServerQueryError as exc:
                raise RuntimeError(self.t("whoami failed: {error}", error=exc)) from exc
            actual_sid = who[0].get("virtualserver_id") if who else None
            if actual_sid != str(self.cfg.virtual_server_id):
                raise RuntimeError(
                    self.t(
                        "use sid={sid} did not select the server; virtualserver_id={actual}.",
                        sid=self.cfg.virtual_server_id,
                        actual=actual_sid,
                    )
                )
            log.info(self.t("Selected virtual server sid=%d."), self.cfg.virtual_server_id)
            try:
                q.query(f"clientupdate client_nickname={escape('AFK-Bot')}")
            except ServerQueryError as exc:
                log.warning(self.t("Could not set the query nickname: %s"), exc)
        except BaseException:
            q.close()
            raise
        self.query = q
        self._clientlist_times = True

    def tick(self) -> None:
        assert self.query is not None
        cfg = self.cfg
        command = "clientlist -uid -times" if self._clientlist_times else "clientlist -uid"
        try:
            clients = self.query.query(command)
        except ServerQueryError as exc:
            # These errors identify unsupported parameters, not permission or flood failures.
            if not self._clientlist_times or exc.code not in (1536, 1538, 1540):
                raise
            self._clientlist_times = False
            log.warning(
                self.t("clientlist -times is unavailable; using clientinfo for idle times.")
            )
            clients = self.query.query("clientlist -uid")

        moved = 0
        for client in clients:
            self._check_client_stop()
            if client.get("client_type") != "0":
                continue
            clid, cid = client.get("clid", ""), client.get("cid", "")
            uid = client.get("client_unique_identifier", "")
            if not _positive_id(clid) or not _positive_id(cid):
                log.debug(self.t("Skipping a client with invalid clid/cid: %r"), client)
                continue
            if int(cid) == cfg.afk_channel_id or int(cid) in cfg.exclude_channels:
                continue
            if uid in cfg.exclude_uids:
                continue

            idle = client.get("client_idle_time")
            if idle is None or (cfg.exclude_uids and not uid):
                try:
                    info = self.query.query(f"clientinfo clid={clid}")
                except ServerQueryError as exc:
                    log.warning(self.t("clientinfo failed for clid=%s: %s"), clid, exc)
                    continue
                entry = next((row for row in info if "client_idle_time" in row), None)
                if entry is None:
                    log.debug(self.t("clientinfo has no idle time for clid=%s."), clid)
                    continue
                actual_uid = entry.get("client_unique_identifier", uid)
                actual_cid = entry.get("client_channel_id", entry.get("cid", cid))
                if (uid and actual_uid != uid) or actual_cid != cid:
                    log.debug(
                        self.t("Skipping a client whose identity or channel changed: clid=%s"), clid
                    )
                    continue
                if cfg.exclude_uids and (not actual_uid or actual_uid in cfg.exclude_uids):
                    continue
                idle = entry["client_idle_time"]

            try:
                if not isinstance(idle, str) or not re.fullmatch(r"[0-9]+", idle):
                    raise ValueError
                idle_ms = int(idle)
            except ValueError:
                log.debug(self.t("Skipping an invalid idle time for clid=%s: %r"), clid, idle)
                continue
            if idle_ms < cfg.timeout_seconds * 1000:
                continue

            self._check_client_stop()
            try:
                self.query.query(f"clientmove clid={clid} cid={cfg.afk_channel_id}")
            except ServerQueryError as exc:
                log.warning(self.t("clientmove failed for clid=%s: %s"), clid, exc)
                continue
            nick = sanitize_for_log(client.get("client_nickname", f"clid={clid}"))
            log.info(
                self.t("Moved to AFK: %s (clid=%s, idle %.1f min)."), nick, clid, idle_ms / 60000
            )
            moved += 1
            message = cfg.effective_poke_message()
            if message:
                try:
                    self.query.query(f"clientpoke clid={clid} msg={escape(message)}")
                except ServerQueryError as exc:
                    log.debug(self.t("clientpoke failed for clid=%s: %s"), clid, exc)
        if moved:
            log.info(self.t("Users moved this tick: %d"), moved)

    def _check_client_stop(self) -> None:
        if self._should_stop():
            raise StopRequested()

    def run(self) -> None:
        self.install_signal_handlers()
        while not self._should_stop():
            try:
                self.connect()
                log.info(
                    self.t("Bot started. AFK cid=%d, idle limit %d s, check interval %d s."),
                    self.cfg.afk_channel_id,
                    self.cfg.timeout_seconds,
                    self.cfg.check_interval,
                )
                while not self._should_stop():
                    try:
                        self.tick()
                    except StopRequested:
                        break
                    except (SSHException, OSError, EOFError, RuntimeError) as exc:
                        log.error(self.t("Client check failed: %s"), exc)
                        break
                    self._wait(self.cfg.check_interval)
            except StopRequested:
                pass
            except (SSHException, OSError, EOFError, RuntimeError) as exc:
                log.error(self.t("Connection failed: %s"), exc)
            finally:
                if self.query is not None:
                    self.query.close()
                    self.query = None
            if self._should_stop():
                break
            log.info(self.t("Reconnecting in %d seconds..."), self.cfg.reconnect_delay)
            self._wait(self.cfg.reconnect_delay)
        log.info(self.t("Bot stopped."))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="ts6-afk-bot",
        allow_abbrev=False,
        description=tr("TeamSpeak 6 AFK bot: move idle clients to a separate channel."),
    )
    parser.add_argument(
        "-c",
        "--config",
        default="config.json",
        help=tr("Path to the JSON configuration (default: config.json)"),
    )
    parser.add_argument("-v", "--verbose", action="store_true", help=tr("Enable debug logging"))
    parser.add_argument(
        "--language",
        choices=LANGUAGES,
        help=tr("Output language: en or ru (overrides TS6_LANGUAGE and config)"),
    )
    parser.add_argument("-V", "--version", action="version", version=f"%(prog)s {__version__}")
    return parser


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    try:
        language = configure_cli_language(argv)
    except ValueError as exc:
        log.error(tr("Configuration error: %s"), exc)
        return 1
    args = build_parser().parse_args(argv)
    if args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)
    try:
        cfg = Config.load(Path(args.config), language=language)
    except (OSError, ValueError) as exc:
        if isinstance(exc, ConfigurationError):
            set_language(exc.language)
        log.error(tr("Configuration error: %s"), exc)
        return 1
    set_language(cfg.language)
    try:
        AFKBot(cfg).run()
    except KeyboardInterrupt:
        log.info(tr("Interrupted by the user."))
        return 130
    return 0


if __name__ == "__main__":
    sys.exit(main())
