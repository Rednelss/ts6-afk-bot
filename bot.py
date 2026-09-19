#!/usr/bin/env python3
"""
TS6 AFK Bot
===========

Небольшой бот для TeamSpeak 6, который автоматически перемещает
неактивных пользователей в AFK-канал.

Как это работает:
  1. Бот подключается к ServerQuery TeamSpeak 6 через SSH (порт 10022).
  2. Каждые N секунд запрашивает список клиентов одной командой
     (clientlist -uid -times) — без N+1 запросов на каждого юзера.
  3. Читает client_idle_time (мс без активности).
  4. Если простой > порога — перемещает в AFK и (опционально) шлёт poke.

Остановка:
  Первый Ctrl+C — мягкая остановка (в течение ~0.5 сек).
  Второй Ctrl+C — жёсткий выход немедленно.

Требования:
  - Python 3.10+
  - paramiko
  - Включённый SSH ServerQuery на сервере TS6
  - Query-аккаунт с правами b_client_move_power и i_client_poke_power

Запуск:
  python bot.py --config config.json

Переменные окружения:
  TS6_QUERY_PASSWORD — если задана, переопределяет password из config.json.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import re
import signal
import sys
import threading
import time
from dataclasses import dataclass, field, fields
from pathlib import Path

import paramiko
from paramiko.ssh_exception import AuthenticationException, SSHException

__version__ = "1.0.0"

# --------------------------------------------------------------------------- #
#  Логирование
# --------------------------------------------------------------------------- #

LOG_FORMAT = "%(asctime)s [%(levelname)s] %(message)s"
logging.basicConfig(
    level=logging.INFO,
    format=LOG_FORMAT,
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("ts6-afk-bot")

# --------------------------------------------------------------------------- #
#  Внутренний сигнал остановки
# --------------------------------------------------------------------------- #

class StopRequested(Exception):
    """Выбрасывается внутри I/O-циклов, когда пришёл сигнал остановки."""

# --------------------------------------------------------------------------- #
#  Хелперы для ServerQuery
# --------------------------------------------------------------------------- #

# ANSI escape-последовательности, которые возвращает SSH-шелл (TTY).
_ANSI_RE = re.compile(
    r"\x1b(?:"
    r"\[[0-9;?]*[A-Za-z]"                 # CSI
    r"|\][^\x07\x1b]*(?:\x07|\x1b\\)"     # OSC (BEL или ST)
    r"|[()][0-9A-Za-z]"                   # charset select
    r"|[=>MNOP]"                          # одиночные escape
    r")"
)

# Приглашение ServerQuery:  nick@server(id):channel>
_PROMPT_RE = re.compile(r"^\S+@\S+\(\d+\):\S+>\s*$")

# Управляющие символы, которые ломают формат лога одной строкой.
_CONTROL_RE = re.compile(r"[\r\n\t]+")

def unescape(value: str) -> str:
    """
    Раскодировать экранированные символы ServerQuery.

    Порядок замен критичен: сначала разворачиваем двойной бэкслеш,
    иначе последующие replace('\\\\p', '|') сожрут его хвост.
    """
    return (
        value.replace("\\\\", "\\")
        .replace("\\p", "|")
        .replace("\\/", "/")
        .replace("\\s", " ")
        .replace("\\n", "\n")
        .replace("\\r", "\r")
        .replace("\\t", "\t")
        .replace("\\v", "\v")
    )

def escape(value: str) -> str:
    """Закодировать строку для передачи в ServerQuery."""
    return (
        value.replace("\\", "\\\\")
        .replace("/", "\\/")
        .replace("|", "\\p")
        .replace(" ", "\\s")
        .replace("\n", "\\n")
        .replace("\r", "\\r")
        .replace("\t", "\\t")
        .replace("\v", "\\v")
    )

def sanitize_for_log(value: str, limit: int = 64) -> str:
    """Убрать ANSI/переводы строк из пользовательских данных для лога."""
    clean = _ANSI_RE.sub("", value)
    clean = _CONTROL_RE.sub(" ", clean).strip()
    if len(clean) > limit:
        clean = clean[: limit - 1] + "…"
    return clean

def parse_response(raw: str, echo: str = "") -> list[dict[str, str]]:
    """
    Разобрать ответ ServerQuery в список словарей.

    Формат ответа через invoke_shell():

        <эхо команды с ANSI>\\r\\n
        <данные, записи через '|', поля через ' '>\\r\\n
        error id=... msg=...\\r\\n
        <приглашение>

    Строку-эхо убираем точным сравнением (clean == echo), а не по индексу —
    это устойчиво к тому, что буфер мог начать читаться с середины ответа.
    """
    entries: list[dict[str, str]] = []
    echo_norm = echo.strip()

    lines = raw.replace("\r\n", "\n").replace("\r", "\n").split("\n")

    for raw_line in lines:
        clean = _ANSI_RE.sub("", raw_line).strip()
        if not clean:
            continue

        # Приглашение ServerQuery
        if _PROMPT_RE.match(clean):
            continue

        # Эхо отправленной команды — точное совпадение
        if echo_norm and clean == echo_norm:
            continue

        # Строка ошибки
        if clean.startswith("error id="):
            params = dict(
                p.split("=", 1)
                for p in clean[len("error "):].split(" ")
                if "=" in p
            )
            if params.get("id") not in (None, "0"):
                raise RuntimeError(f"ServerQuery вернул ошибку: {clean}")
            continue

        for chunk in clean.split("|"):
            row: dict[str, str] = {}
            for pair in chunk.split(" "):
                if "=" in pair:
                    k, v = pair.split("=", 1)
                    row[k] = unescape(v)
            if row:
                entries.append(row)

    return entries

# --------------------------------------------------------------------------- #
#  Тонкая обёртка над SSH ServerQuery TS6
# --------------------------------------------------------------------------- #

class TS6Query:
    """Минимальный клиент SSH ServerQuery для TeamSpeak 3/6."""

    def __init__(
        self,
        host: str,
        port: int,
        username: str,
        password: str,
        stop_evt: threading.Event | None = None,
        connect_timeout: int = 15,
    ) -> None:
        self.host = host
        self.port = port
        self.username = username
        self.password = password
        self.connect_timeout = connect_timeout
        self._stop_evt = stop_evt
        self._ssh: paramiko.SSHClient | None = None
        self._shell = None

    # -- проверка остановки ------------------------------------------------- #

    def _check_stop(self) -> None:
        if self._stop_evt is not None and self._stop_evt.is_set():
            raise StopRequested()

    # -- соединение --------------------------------------------------------- #

    def connect(self) -> None:
        log.info("Подключаюсь к ServerQuery %s:%d ...", self.host, self.port)
        ssh = paramiko.SSHClient()
        ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        try:
            ssh.connect(
                hostname=self.host,
                port=self.port,
                username=self.username,
                password=self.password,
                timeout=self.connect_timeout,
                allow_agent=False,
                look_for_keys=False,
            )
        except AuthenticationException as exc:
            raise RuntimeError(
                "Не удалось авторизоваться в ServerQuery. "
                "Проверьте логин/пароль query-аккаунта."
            ) from exc
        except SSHException as exc:
            raise RuntimeError(f"Ошибка SSH при подключении: {exc}") from exc

        self._ssh = ssh
        # Большие размеры терминала исключают переносы приглашения и пагинацию.
        self._shell = ssh.invoke_shell(term="xterm", width=512, height=512)
        # Считываем приветственный баннер (там нет error id=).
        self._drain(duration=0.8)
        log.info("Соединение с ServerQuery установлено.")

    def close(self) -> None:
        try:
            if self._shell is not None:
                self._shell.close()
        except Exception:
            pass
        try:
            if self._ssh is not None:
                self._ssh.close()
        except Exception:
            pass
        self._ssh = None
        self._shell = None

    # -- чтение / запись ---------------------------------------------------- #

    def _drain(self, duration: float = 0.5) -> str:
        """Прочитать всё, что успеет прийти за окно тишины (для баннера)."""
        assert self._shell is not None
        chunks: list[bytes] = []
        deadline = time.monotonic() + duration
        while time.monotonic() < deadline:
            self._check_stop()
            if self._shell.recv_ready():
                chunks.append(self._shell.recv(65535))
                deadline = time.monotonic() + 0.1
            else:
                time.sleep(0.02)
        return b"".join(chunks).decode("utf-8", errors="replace")

    def _read_until_error(self, timeout: float = 5.0) -> str:
        """
        Читать ответ, пока не увидим 'error id='. После этого дочитать хвост
        (промпт) коротким окном тишины. Проверяет stop_evt на каждом шаге —
        иначе Ctrl+C не сможет прервать I/O.

        Копим БАЙТЫ, декодируем один раз в конце: иначе многобайтовый UTF-8
        (ник с эмодзи) может разорваться на границе recv и превратиться в '?'.
        """
        assert self._shell is not None
        buf = b""
        deadline = time.monotonic() + timeout

        while time.monotonic() < deadline:
            self._check_stop()
            if self._shell.recv_ready():
                buf += self._shell.recv(65535)
                # Маркер ASCII — ищем в байтах, декодирование не нужно.
                if b"error id=" in buf:
                    quiet_until = time.monotonic() + 0.1
                    while time.monotonic() < quiet_until:
                        self._check_stop()
                        if self._shell.recv_ready():
                            buf += self._shell.recv(65535)
                            quiet_until = time.monotonic() + 0.1
                        else:
                            time.sleep(0.01)
                    return buf.decode("utf-8", errors="replace")
            else:
                time.sleep(0.02)

        raise TimeoutError(f"ServerQuery не ответил за {timeout} сек")

    def send(self, command: str, timeout: float = 5.0) -> str:
        """Отправить команду и вернуть сырой ответ сервера."""
        assert self._shell is not None, "сначала вызовите connect()"
        cmd = command.strip()
        self._shell.send(cmd + "\n")
        raw = self._read_until_error(timeout=timeout)
        if log.isEnabledFor(logging.DEBUG):
            if len(raw) > 800:
                log.debug("CMD=%r RAW[%d]=%r…%r",
                          cmd, len(raw), raw[:400], raw[-200:])
            else:
                log.debug("CMD=%r RAW=%r", cmd, raw)
        return raw

    def query(self, command: str, timeout: float = 5.0) -> list[dict[str, str]]:
        """Отправить команду и вернуть распарсенный ответ."""
        raw = self.send(command, timeout=timeout)
        return parse_response(raw, echo=command.strip())

# --------------------------------------------------------------------------- #
#  Конфигурация
# --------------------------------------------------------------------------- #

@dataclass
class Config:
    host: str
    query_port: int
    username: str
    password: str
    virtual_server_id: int
    afk_channel_id: int
    timeout_seconds: int = 300          # 5 минут
    check_interval: int = 30            # период опроса, сек
    poke_message: str = ""              # "" — не отправлять
    exclude_uids: list[str] = field(default_factory=list)
    exclude_channels: list[int] = field(default_factory=list)
    reconnect_delay: int = 10

    @classmethod
    def load(cls, path: Path) -> "Config":
        if not path.is_file():
            raise FileNotFoundError(f"Файл конфигурации не найден: {path}")

        with path.open(encoding="utf-8") as f:
            data = json.load(f)

        # Пароль можно переопределить переменной окружения.
        env_password = os.environ.get("TS6_QUERY_PASSWORD")
        if env_password:
            data["password"] = env_password

        known = {f.name for f in fields(cls)}
        unknown = set(data) - known
        if unknown:
            log.warning("Неизвестные ключи в конфиге (проигнорированы): %s",
                        ", ".join(sorted(unknown)))

        filtered = {k: v for k, v in data.items() if k in known}

        # Валидация типов — иначе ошибка всплывёт где-то в глубине tick().
        str_fields = ("host", "username", "password", "poke_message")
        int_fields = (
            "query_port", "virtual_server_id", "afk_channel_id",
            "timeout_seconds", "check_interval", "reconnect_delay",
        )
        for key in str_fields:
            if key in filtered and not isinstance(filtered[key], str):
                raise ValueError(f"config.json: поле '{key}' должно быть строкой")
        for key in int_fields:
            if key in filtered and not isinstance(filtered[key], int):
                raise ValueError(f"config.json: поле '{key}' должно быть целым числом")
        if "exclude_uids" in filtered and not isinstance(filtered["exclude_uids"], list):
            raise ValueError("config.json: поле 'exclude_uids' должно быть массивом")
        if "exclude_channels" in filtered and not isinstance(filtered["exclude_channels"], list):
            raise ValueError("config.json: поле 'exclude_channels' должно быть массивом")

        try:
            return cls(**filtered)
        except TypeError as exc:
            raise ValueError(f"Некорректный config.json: {exc}") from exc

# --------------------------------------------------------------------------- #
#  Бот
# --------------------------------------------------------------------------- #

class AFKBot:
    def __init__(self, cfg: Config) -> None:
        self.cfg = cfg
        self.query: TS6Query | None = None
        self._stop_evt = threading.Event()

    # -- сигналы ------------------------------------------------------------ #

    def install_signal_handlers(self) -> None:
        def handler(signum, _frame):
            if self._stop_evt.is_set():
                # Повторный Ctrl+C — выходим жёстко, без ожидания.
                log.warning("Повторный сигнал %s — немедленный выход.", signum)
                raise KeyboardInterrupt
            log.info(
                "Получен сигнал %s — завершаю работу "
                "(Ctrl+C ещё раз = жёсткий выход)...", signum,
            )
            self._stop_evt.set()

        signal.signal(signal.SIGINT, handler)
        signal.signal(signal.SIGTERM, handler)

    # -- ожидание с реакцией на сигнал -------------------------------------- #

    def _wait(self, seconds: float) -> None:
        """
        Ждать seconds или до получения сигнала остановки.

        Event.wait() в Python 3 перезапускается после сигнала (PEP 475), поэтому
        ждём короткими кусками — так максимум ~0.5 сек до реакции на Ctrl+C.
        """
        end = time.monotonic() + seconds
        while not self._stop_evt.is_set():
            remaining = end - time.monotonic()
            if remaining <= 0:
                return
            self._stop_evt.wait(timeout=min(0.5, remaining))

    # -- подключение -------------------------------------------------------- #

    def connect(self) -> None:
        q = TS6Query(
            host=self.cfg.host,
            port=self.cfg.query_port,
            username=self.cfg.username,
            password=self.cfg.password,
            stop_evt=self._stop_evt,
        )
        q.connect()

        # Выбираем виртуальный сервер.
        try:
            q.query(f"use sid={self.cfg.virtual_server_id}")
        except RuntimeError as exc:
            raise RuntimeError(
                f"use sid={self.cfg.virtual_server_id} не удался: {exc}"
            ) from exc

        # Проверяем, что контекст действительно переключился: ServerQuery
        # иногда возвращает error id=0, но оставляет предыдущий сервер.
        try:
            who = q.query("whoami")
        except RuntimeError as exc:
            raise RuntimeError(f"whoami не удался: {exc}") from exc

        actual_sid = who[0].get("virtualserver_id") if who else None
        if actual_sid != str(self.cfg.virtual_server_id):
            raise RuntimeError(
                f"use sid={self.cfg.virtual_server_id} не сработал: "
                f"сервер сообщает virtualserver_id={actual_sid}"
            )
        log.info("Выбран виртуальный сервер sid=%d", self.cfg.virtual_server_id)

        # Читаемый ник query-клиента.
        q.send(f"clientupdate client_nickname={escape('AFK-Bot')}")

        self.query = q

    # -- один проход проверки ---------------------------------------------- #

    def tick(self) -> None:
        assert self.query is not None
        cfg = self.cfg

        # Одна команда вместо N+1 запросов clientinfo:
        # -uid даёт unique_identifier, -times — client_idle_time/connected_time.
        try:
            clients = self.query.query("clientlist -uid -times")
        except RuntimeError as exc:
            log.error("Не удалось получить clientlist: %s", exc)
            raise

        moved = 0
        for c in clients:
            if self._stop_evt.is_set():
                return

            # Пропускаем query-клиентов (client_type=1).
            if c.get("client_type") == "1":
                continue

            clid = c.get("clid", "")
            cid = c.get("cid", "")
            uid = c.get("client_unique_identifier", "")
            nick = c.get("client_nickname", f"clid={clid}")

            # Валидация: без неё в команду ServerQuery может утечь мусор.
            if not clid.isdigit() or not cid.isdigit():
                log.debug("Пропускаю клиента с некорректным clid/cid: %r", c)
                continue

            # Уже в AFK-канале — не трогаем.
            if cid == str(cfg.afk_channel_id):
                continue

            if int(cid) in cfg.exclude_channels:
                continue

            if uid and uid in cfg.exclude_uids:
                continue

            # client_idle_time может прийти из -times, но не все сервера
            # отдают его в clientlist. Тогда падаем обратно на clientinfo.
            idle_ms_str = c.get("client_idle_time")
            if idle_ms_str is None:
                try:
                    info = self.query.query(f"clientinfo clid={clid}")
                except RuntimeError as exc:
                    log.warning("clientinfo для clid=%s не удался: %s", clid, exc)
                    continue
                idle_entry = next(
                    (e for e in info if "client_idle_time" in e),
                    None,
                )
                if idle_entry is None:
                    log.debug(
                        "clientinfo для clid=%s не содержит client_idle_time "
                        "(получено %d записей)", clid, len(info),
                    )
                    continue
                idle_ms_str = idle_entry.get("client_idle_time", "0") or "0"

            try:
                idle_ms = int(idle_ms_str)
            except ValueError:
                idle_ms = 0

            if idle_ms < cfg.timeout_seconds * 1000:
                continue

            safe_nick = sanitize_for_log(nick)

            # Перемещаем.
            try:
                self.query.query(f"clientmove clid={clid} cid={cfg.afk_channel_id}")
                log.info(
                    "Перемещён в AFK: %s (clid=%s, простой %.1f мин)",
                    safe_nick, clid, idle_ms / 60000,
                )
                moved += 1
            except RuntimeError as exc:
                log.warning("clientmove для clid=%s не удался: %s", clid, exc)
                continue

            # Опциональное оповещение.
            if cfg.poke_message:
                try:
                    self.query.query(
                        f"clientpoke clid={clid} msg={escape(cfg.poke_message)}"
                    )
                except RuntimeError as exc:
                    log.debug("clientpoke для clid=%s не удался: %s", clid, exc)

        if moved:
            log.info("За проход перемещено пользователей: %d", moved)

    # -- основной цикл ------------------------------------------------------ #

    def run(self) -> None:
        self.install_signal_handlers()

        while not self._stop_evt.is_set():
            try:
                self.connect()
                log.info(
                    "Бот запущен. AFK-канал cid=%d, порог %d сек, интервал %d сек.",
                    self.cfg.afk_channel_id,
                    self.cfg.timeout_seconds,
                    self.cfg.check_interval,
                )

                while not self._stop_evt.is_set():
                    try:
                        self.tick()
                    except StopRequested:
                        break
                    except (SSHException, OSError, EOFError,
                            RuntimeError, TimeoutError) as exc:
                        log.error("Сбой во время проверки: %s", exc)
                        break  # переподключение

                    # Короткий polling — Ctrl+C отрабатывает за ≤0.5 сек.
                    self._wait(self.cfg.check_interval)

            except StopRequested:
                pass
            except Exception as exc:  # noqa: BLE001
                log.error("Не удалось подключиться: %s", exc)
            finally:
                if self.query is not None:
                    self.query.close()
                    self.query = None

            if self._stop_evt.is_set():
                break

            log.info("Переподключение через %d сек...", self.cfg.reconnect_delay)
            self._wait(self.cfg.reconnect_delay)

        log.info("Бот остановлен.")

# --------------------------------------------------------------------------- #
#  Точка входа
# --------------------------------------------------------------------------- #

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="ts6-afk-bot",
        description="TeamSpeak 6 AFK bot: перемещает неактивных в отдельный канал.",
    )
    parser.add_argument(
        "-c", "--config",
        default="config.json",
        help="Путь к JSON-конфигу (по умолчанию: config.json)",
    )
    parser.add_argument(
        "-v", "--verbose",
        action="store_true",
        help="Подробный вывод (DEBUG)",
    )
    parser.add_argument(
        "-V", "--version",
        action="version",
        version=f"%(prog)s {__version__}",
    )
    return parser

def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    if args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)

    try:
        cfg = Config.load(Path(args.config))
    except (FileNotFoundError, ValueError) as exc:
        # json.JSONDecodeError — подкласс ValueError, отдельно ловить не нужно.
        log.error("Ошибка конфигурации: %s", exc)
        return 1

    if cfg.timeout_seconds < 10:
        log.warning("timeout_seconds=%d слишком мал, ставлю 10.", cfg.timeout_seconds)
        cfg.timeout_seconds = 10

    bot = AFKBot(cfg)
    try:
        bot.run()
    except KeyboardInterrupt:
        log.info("Прервано пользователем.")
        return 130  # 128 + SIGINT — стандарт для обёрток вроде systemd
    return 0

if __name__ == "__main__":
    sys.exit(main())
