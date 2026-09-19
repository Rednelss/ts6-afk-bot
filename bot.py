#!/usr/bin/env python3
"""
TS6 AFK Bot
===========

Небольшой бот для TeamSpeak 6, который автоматически перемещает
неактивных пользователей в AFK-канал.

Как это работает:
  1. Бот подключается к ServerQuery TeamSpeak 6 через SSH (порт по умолчанию 10022).
  2. Каждые N секунд он запрашивает список клиентов на виртуальном сервере.
  3. Для каждого клиента читает client_idle_time (мс без активности).
  4. Если время простоя превышает заданный порог (по умолчанию 300 сек = 5 мин),
     бот перемещает клиента в AFK-канал и (опционально) отправляет poke-сообщение.

Требования:
  - Python 3.10+
  - paramiko
  - Включённый SSH ServerQuery на сервере TS6
  - Query-аккаунт с правами b_client_move_power и i_client_poke_power

Запуск:
  python bot.py --config config.json
"""

from __future__ import annotations

import argparse
import json
import logging
import re
import signal
import sys
import time
from dataclasses import dataclass, field, fields
from pathlib import Path
from typing import Optional

import paramiko
from paramiko.ssh_exception import AuthenticationException, SSHException

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
#  Хелперы для ServerQuery
# --------------------------------------------------------------------------- #

# ANSI escape-последовательности, которые возвращает SSH-шелл (TTY).
# Пример: \x1b[29G \x1b[J \x1b[46G
_ANSI_RE = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]")

# Приглашение ServerQuery:  nick@server(id):channel>
_PROMPT_RE = re.compile(r"^\S+@\S+\(\d+\):\S+>\s*$")

def unescape(value: str) -> str:
    """Раскодировать экранированные символы ServerQuery."""
    return (
        value.replace("\\p", "|")
        .replace("\\/", "/")
        .replace("\\s", " ")
        .replace("\\\\", "\\")
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
    )

def parse_response(raw: str, echo: str = "") -> list[dict[str, str]]:
    """
    Разобрать ответ ServerQuery в список словарей.

    Реальный формат ответа через invoke_shell():

        <эхо команды с ANSI-последовательностями>\r\n
        <данные, записи через '|', поля через ' '>\r\n
        error id=... msg=...\r\n
        <приглашение>

    Что делаем:
      * вырезаем ANSI-последовательности;
      * отбрасываем строку-эхо (она начинается с отправленной команды);
      * отбрасываем приглашение;
      * проверяем error id.

    Параметр echo — команда, которую мы только что отправили. Нужен, чтобы
    надёжно отличить эхо от данных (эхо может содержать '=', например
    'clientinfo clid=1', и без этого фильтра попадает в результат).
    """
    entries: list[dict[str, str]] = []

    lines = raw.replace("\r\n", "\n").replace("\r", "\n").split("\n")

    for idx, raw_line in enumerate(lines):
        clean = _ANSI_RE.sub("", raw_line).strip()
        if not clean:
            continue

        # Приглашение ServerQuery
        if _PROMPT_RE.match(clean):
            continue

        # Эхо отправленной команды — оно всегда идёт первой строкой
        if idx == 0 and echo and clean.startswith(echo):
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
        connect_timeout: int = 15,
    ) -> None:
        self.host = host
        self.port = port
        self.username = username
        self.password = password
        self.connect_timeout = connect_timeout
        self._ssh: Optional[paramiko.SSHClient] = None
        self._shell = None

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
        self._shell = ssh.invoke_shell()
        # Считываем приветственный баннер
        self._read_all(wait=0.8)
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

    def _read_all(self, wait: float = 0.5) -> str:
        """
        Считывает всё, что успело прийти от сервера за указанное окно.

        ServerQuery не присылает явных маркеров конца ответа, поэтому
        мы используем небольшие паузы тишины.
        """
        assert self._shell is not None
        buf = ""
        deadline = time.time() + wait
        while time.time() < deadline:
            if self._shell.recv_ready():
                buf += self._shell.recv(65535).decode("utf-8", errors="replace")
                deadline = time.time() + 0.2  # продлеваем окно, пока есть данные
            else:
                time.sleep(0.05)
        return buf

    def send(self, command: str, wait: float = 0.5) -> str:
        """Отправить команду и вернуть сырой ответ сервера."""
        assert self._shell is not None, "сначала вызовите connect()"
        self._shell.send(command.strip() + "\n")
        return self._read_all(wait=wait)

    def query(self, command: str, wait: float = 0.5) -> list[dict[str, str]]:
        """Отправить команду и вернуть распарсенный ответ."""
        raw = self.send(command, wait=wait)
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

        known = {f.name for f in fields(cls)}
        unknown = set(data) - known
        if unknown:
            log.warning("Неизвестные ключи в конфиге (проигнорированы): %s",
                        ", ".join(sorted(unknown)))

        filtered = {k: v for k, v in data.items() if k in known}
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
        self.query: Optional[TS6Query] = None
        self._stop = False

    # -- сигналы ------------------------------------------------------------ #

    def install_signal_handlers(self) -> None:
        def handler(signum, _frame):
            log.info("Получен сигнал %s — завершаю работу...", signum)
            self._stop = True

        signal.signal(signal.SIGINT, handler)
        signal.signal(signal.SIGTERM, handler)

    # -- подключение -------------------------------------------------------- #

    def connect(self) -> None:
        q = TS6Query(
            host=self.cfg.host,
            port=self.cfg.query_port,
            username=self.cfg.username,
            password=self.cfg.password,
        )
        q.connect()

        # Выбираем виртуальный сервер
        resp = q.query(f"use sid={self.cfg.virtual_server_id}")
        if not resp:
            log.info("Выбран виртуальный сервер sid=%d", self.cfg.virtual_server_id)

        # Устанавливаем читаемый ник query-клиента
        q.send("clientupdate client_nickname=AFK-Bot", wait=0.3)

        self.query = q

    # -- один проход проверки ---------------------------------------------- #

    def tick(self) -> None:
        assert self.query is not None
        cfg = self.cfg

        try:
            clients = self.query.query("clientlist -uid", wait=0.8)
        except RuntimeError as exc:
            log.error("Не удалось получить clientlist: %s", exc)
            raise

        moved = 0
        for c in clients:
            if self._stop:
                return

            # Пропускаем query-клиентов (client_type=1)
            if c.get("client_type") == "1":
                continue

            clid = c.get("clid")
            uid = c.get("client_unique_identifier", "")
            cid = c.get("cid", "")
            nick = c.get("client_nickname", f"clid={clid}")

            if clid is None or cid == "":
                continue

            # Уже в AFK-канале — не трогаем
            if cid == str(cfg.afk_channel_id):
                continue

            # Исключения по каналу
            if cid.isdigit() and int(cid) in cfg.exclude_channels:
                continue

            # Исключения по UID
            if uid and uid in cfg.exclude_uids:
                continue

            # Читаем время простоя
            try:
                info = self.query.query(f"clientinfo clid={clid}", wait=0.5)
            except RuntimeError as exc:
                log.warning("clientinfo для clid=%s не удался: %s", clid, exc)
                continue

            # Ищем запись с client_idle_time, а не доверяем info[0]:
            # в info может попасть эхо команды или промежуточные строки.
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

            # Перемещаем
            try:
                self.query.query(
                    f"clientmove clid={clid} cid={cfg.afk_channel_id}",
                    wait=0.4,
                )
                log.info(
                    "Перемещён в AFK: %s (clid=%s, простой %.1f мин)",
                    nick, clid, idle_ms / 60000,
                )
                moved += 1
            except RuntimeError as exc:
                log.warning("clientmove для clid=%s не удался: %s", clid, exc)
                continue

            # Опциональное оповещение
            if cfg.poke_message:
                try:
                    self.query.query(
                        f"clientpoke clid={clid} msg={escape(cfg.poke_message)}",
                        wait=0.3,
                    )
                except RuntimeError as exc:
                    log.debug("clientpoke для clid=%s не удался: %s", clid, exc)

        if moved:
            log.info("За проход перемещено пользователей: %d", moved)

    # -- основной цикл ------------------------------------------------------ #

    def run(self) -> None:
        self.install_signal_handlers()

        while not self._stop:
            try:
                self.connect()
                log.info(
                    "Бот запущен. AFK-канал cid=%d, порог %d сек, интервал %d сек.",
                    self.cfg.afk_channel_id,
                    self.cfg.timeout_seconds,
                    self.cfg.check_interval,
                )

                while not self._stop:
                    try:
                        self.tick()
                    except (SSHException, OSError, EOFError, RuntimeError) as exc:
                        log.error("Сбой во время проверки: %s", exc)
                        break  # выходим во внешний цикл для переподключения

                    # Спим, но с реакцией на сигнал
                    slept = 0
                    while slept < self.cfg.check_interval and not self._stop:
                        time.sleep(1)
                        slept += 1

            except Exception as exc:  # noqa: BLE001
                log.error("Не удалось подключиться: %s", exc)
            finally:
                if self.query is not None:
                    self.query.close()
                    self.query = None

            if self._stop:
                break

            log.info("Переподключение через %d сек...", self.cfg.reconnect_delay)
            slept = 0
            while slept < self.cfg.reconnect_delay and not self._stop:
                time.sleep(1)
                slept += 1

        log.info("Бот остановлен.")

# --------------------------------------------------------------------------- #
#  Точка входа
# --------------------------------------------------------------------------- #

def main() -> int:
    parser = argparse.ArgumentParser(description="TS6 AFK Bot")
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
    args = parser.parse_args()

    if args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)

    try:
        cfg = Config.load(Path(args.config))
    except (FileNotFoundError, ValueError, json.JSONDecodeError) as exc:
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
    return 0

if __name__ == "__main__":
    sys.exit(main())
