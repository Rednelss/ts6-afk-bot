#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
install.py — автоматическая установка и настройка ts6-afk-bot на Linux.

Что делает скрипт:
  1. Проверяет права root и наличие Python 3.10+ / git / venv.
  2. Ставит системные зависимости через родной пакетный менеджер (apt/dnf/yum/pacman/zypper).
  3. Создаёт изолированного системного пользователя (по умолчанию ts6afkbot).
  4. Клонирует https://github.com/Rednelss/ts6-afk-bot в /opt/ts6-afk-bot
     (или берёт локальную копию через --source).
  5. Создаёт venv и ставит зависимости (paramiko >= 3.4).
  6. Интерактивно (или через флаги) собирает config.json в /etc/ts6-afk-bot/.
  7. Пароль ServerQuery кладёт в /etc/ts6-afk-bot/env (chmod 600), а не в JSON.
  8. Генерирует hardened systemd-юнит, включает и запускает сервис.

Примеры:
  sudo python3 install.py
  sudo python3 install.py --host ts.example.com --afk-channel-id 12 --username bot --password 'secret'
  sudo python3 install.py --non-interactive --host ts.example.com --afk-channel-id 12 --virtual-server-id 1
  sudo python3 install.py --uninstall --purge

Лицензия: MIT
"""

from __future__ import annotations

import argparse
import getpass
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import time
from pathlib import Path

# --------------------------------------------------------------------------- #
#                                Константы                                    #
# --------------------------------------------------------------------------- #

REPO_URL = "https://github.com/Rednelss/ts6-afk-bot.git"

SERVICE_NAME = "ts6-afk-bot"
SYSTEM_USER = "ts6afkbot"

INSTALL_DIR = Path("/opt/ts6-afk-bot")
CONFIG_DIR = Path("/etc/ts6-afk-bot")
STATE_DIR = Path("/var/lib/ts6-afk-bot")

DEFAULTS = {
    "query_port": 10022,
    "virtual_server_id": 1,
    "timeout_seconds": 300,
    "check_interval": 30,
    "reconnect_delay": 10,
    "poke_message": "Вы были перемещены в канал AFK из-за неактивности в течении 5 минут.",
}

# Эти пути пересчитываются в main() при переопределении через CLI
VENV_DIR = INSTALL_DIR / "venv"
CONFIG_FILE = CONFIG_DIR / "config.json"
ENV_FILE = CONFIG_DIR / "env"
UNIT_FILE = Path(f"/etc/systemd/system/{SERVICE_NAME}.service")

# --------------------------------------------------------------------------- #
#                             Вывод в консоль                                 #
# --------------------------------------------------------------------------- #

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

def die(msg: str, code: int = 1) -> "None":
    err(msg)
    sys.exit(code)

# --------------------------------------------------------------------------- #
#                          Обёртка над subprocess                             #
# --------------------------------------------------------------------------- #

def run(cmd, check: bool = True, capture: bool = False, cwd=None,
        env=None, quiet: bool = False, input_text: str | None = None):
    """Запустить команду. cmd — список аргументов (shell не используется)."""
    display = cmd if isinstance(cmd, str) else " ".join(str(c) for c in cmd)
    if not quiet:
        info(f"$ {display}")

    result = subprocess.run(
        cmd,
        shell=isinstance(cmd, str),
        check=False,
        cwd=cwd,
        env=env,
        stdout=subprocess.PIPE if capture else None,
        stderr=subprocess.STDOUT if capture else None,
        text=True,
        input=input_text,
    )

    if check and result.returncode != 0:
        if capture and result.stdout:
            err(result.stdout.strip())
        die(f"Команда завершилась с кодом {result.returncode}: {display}")

    return result

# --------------------------------------------------------------------------- #
#                            Базовые проверки                                 #
# --------------------------------------------------------------------------- #

def ensure_root() -> None:
    if os.geteuid() != 0:
        die("Скрипт нужно запускать от root:\n    sudo python3 install.py")

def check_python() -> None:
    if not shutil.which("python3"):
        die("python3 не найден. Установите Python 3.10+ и повторите запуск.")

    res = run(
        ["python3", "-c", "import sys; print('%d.%d' % sys.version_info[:2])"],
        capture=True, quiet=True, check=False,
    )
    if res.returncode != 0:
        die("Не удалось определить версию Python.")

    version = res.stdout.strip()
    try:
        major, minor = (int(x) for x in version.split("."))
    except ValueError:
        die(f"Не удалось разобрать версию Python: {version!r}")

    if (major, minor) < (3, 10):
        die(f"Требуется Python 3.10+, найден {version}.")

    ok(f"Python {version}")

    venv_check = run(["python3", "-c", "import venv"], capture=True, quiet=True, check=False)
    if venv_check.returncode != 0:
        die("Модуль 'venv' недоступен. Установите пакет python3-venv (Debian/Ubuntu).")

# --------------------------------------------------------------------------- #
#                        Системные зависимости                                #
# --------------------------------------------------------------------------- #

PKG_TABLE = {
    "apt-get": ["python3", "python3-venv", "python3-pip", "git"],
    "dnf":     ["python3", "python3-pip", "git"],
    "yum":     ["python3", "python3-pip", "git"],
    "pacman":  ["python", "python-pip", "git"],
    "zypper":  ["python3", "python3-pip", "git"],
}

def detect_package_manager() -> str | None:
    for pm in ("apt-get", "dnf", "yum", "pacman", "zypper"):
        if shutil.which(pm):
            return pm
    return None

def install_system_dependencies() -> None:
    pm = detect_package_manager()

    if pm is None:
        warn("Пакетный менеджер не определён — установку зависимостей пропускаю.")
        return

    info(f"Пакетный менеджер: {pm}")
    packages = PKG_TABLE.get(pm, [])

    try:
        if pm == "apt-get":
            run(["apt-get", "update", "-qq"], check=False, quiet=True)
            run(["apt-get", "install", "-y", "--no-install-recommends", *packages], check=False)
        elif pm in ("dnf", "yum"):
            run([pm, "install", "-y", *packages], check=False)
        elif pm == "pacman":
            run(["pacman", "-Sy", "--noconfirm", "--needed", *packages], check=False)
        elif pm == "zypper":
            run(["zypper", "--non-interactive", "install", *packages], check=False)
    except Exception as exc:  # noqa: BLE001 — не валим установку из-за пакетов
        warn(f"Не удалось установить часть пакетов: {exc}")

# --------------------------------------------------------------------------- #
#                       Системный пользователь                                #
# --------------------------------------------------------------------------- #

def ensure_system_user() -> None:
    import pwd  # локальный импорт: модуль есть только на Unix

    try:
        pwd.getpwnam(SYSTEM_USER)
        ok(f"Пользователь '{SYSTEM_USER}' уже существует")
    except KeyError:
        shell = "/usr/sbin/nologin"
        if not Path(shell).exists():
            shell = "/bin/false"
        info(f"Создаю системного пользователя '{SYSTEM_USER}'...")
        run([
            "useradd", "--system",
            "--home-dir", str(STATE_DIR),
            "--no-create-home",
            "--shell", shell,
            SYSTEM_USER,
        ], check=False)

    STATE_DIR.mkdir(parents=True, exist_ok=True)
    run(["chown", "-R", f"{SYSTEM_USER}:{SYSTEM_USER}", str(STATE_DIR)], check=False, quiet=True)
    run(["chmod", "750", str(STATE_DIR)], check=False, quiet=True)

# --------------------------------------------------------------------------- #
#                        Получение исходников                                #
# --------------------------------------------------------------------------- #

def fetch_source(source: str | None, skip_update: bool) -> None:
    bot_file = INSTALL_DIR / "bot.py"

    if bot_file.exists():
        if skip_update:
            ok("Исходники уже на месте, обновление пропущено (--skip-update)")
            return
        if (INSTALL_DIR / ".git").exists():
            info("Обновляю репозиторий...")
            run(["git", "-C", str(INSTALL_DIR), "fetch", "--depth", "1", "origin"], check=False)
            run(["git", "-C", str(INSTALL_DIR), "reset", "--hard", "FETCH_HEAD"], check=False)
            ok("Репозиторий обновлён")
        else:
            ok("Исходники уже на месте (не git-репозиторий) — обновление пропущено")
        return

    if INSTALL_DIR.exists() and any(INSTALL_DIR.iterdir()):
        die(f"Каталог {INSTALL_DIR} существует и не пуст, но не содержит bot.py.\n"
            f"    Удалите его вручную или укажите другой путь через --install-dir.")

    INSTALL_DIR.parent.mkdir(parents=True, exist_ok=True)

    if source and Path(source).expanduser().exists():
        local = Path(source).expanduser().resolve()
        info(f"Копирую локальные исходники из {local}")
        shutil.copytree(local, INSTALL_DIR, dirs_exist_ok=True)
    else:
        url = source or REPO_URL
        info(f"Клонирую {url}")
        run(["git", "clone", "--depth", "1", url, str(INSTALL_DIR)])

    if not bot_file.exists():
        die(f"После получения исходников файл {bot_file} не найден. "
            f"Проверьте содержимое репозитория.")

# --------------------------------------------------------------------------- #
#                              venv + pip                                     #
# --------------------------------------------------------------------------- #

def setup_venv() -> None:
    pip = VENV_DIR / "bin" / "pip"
    py = VENV_DIR / "bin" / "python"

    if not py.exists():
        info("Создаю виртуальное окружение...")
        run(["python3", "-m", "venv", str(VENV_DIR)])

    run([str(pip), "install", "--upgrade", "pip", "wheel"], check=False, quiet=True)

    requirements = INSTALL_DIR / "requirements.txt"
    if requirements.exists():
        run([str(pip), "install", "-r", str(requirements)])
    else:
        warn("requirements.txt не найден — ставлю paramiko напрямую")
        run([str(pip), "install", "paramiko>=3.4"])

    check = run([str(py), "-c", "import paramiko; print(paramiko.__version__)"],
                capture=True, quiet=True, check=False)
    if check.returncode != 0:
        die("paramiko не установился в venv. Проверьте вывод pip выше.")
    ok(f"paramiko {check.stdout.strip()}")

# --------------------------------------------------------------------------- #
#                        Интерактивный ввод                                   #
# --------------------------------------------------------------------------- #

def ask(prompt: str, default=None, required: bool = True, cast=str, validator=None):
    suffix = f" [{default}]" if default not in (None, "") else ""
    while True:
        try:
            raw = input(f"{prompt}{suffix}: ").strip()
        except EOFError:
            raw = ""

        if raw == "":
            if default is not None:
                raw = str(default)
            elif not required:
                return None
            else:
                print("  Значение обязательно.")
                continue

        try:
            value = cast(raw)
        except (TypeError, ValueError):
            print("  Некорректное значение, попробуйте снова.")
            continue

        if validator and not validator(value):
            print("  Некорректное значение, попробуйте снова.")
            continue

        return value

def parse_int_list(value) -> list:
    if value in (None, "", []):
        return []
    if isinstance(value, (list, tuple)):
        return [int(v) for v in value]
    return [int(x) for x in re.split(r"[,\s]+", str(value).strip()) if x]

def parse_str_list(value) -> list:
    if value in (None, "", []):
        return []
    if isinstance(value, (list, tuple)):
        return [str(v).strip() for v in value if str(v).strip()]
    return [x for x in re.split(r"[,\s]+", str(value).strip()) if x]

def build_config(args) -> dict:
    interactive = not args.non_interactive

    def need(attr, prompt, default=None, cast=str, validator=None, required=True):
        raw = getattr(args, attr, None)
        if raw not in (None, "", []):
            try:
                return cast(raw) if cast is not str else raw
            except (TypeError, ValueError):
                die(f"Некорректное значение --{attr.replace('_', '-')}: {raw!r}")
        if not interactive:
            if not required and default is not None:
                return default
            die(f"В неинтерактивном режиме обязательно указать --{attr.replace('_', '-')}")
        return ask(prompt, default=default, required=required, cast=cast, validator=validator)

    print()
    info("Настройка бота. Enter — принять значение по умолчанию.")
    print()

    host = need("host", "Адрес TS6-сервера (host)",
                validator=lambda v: bool(str(v).strip()))
    query_port = need("query_port", "Порт SSH ServerQuery",
                      default=DEFAULTS["query_port"], cast=int,
                      validator=lambda v: 1 <= v <= 65535)
    username = need("username", "Логин query-аккаунта", default="serveradmin")
    virtual_server_id = need("virtual_server_id", "ID виртуального сервера (sid)",
                             default=DEFAULTS["virtual_server_id"], cast=int,
                             validator=lambda v: v >= 1)
    afk_channel_id = need("afk_channel_id", "ID AFK-канала (cid)", cast=int,
                          validator=lambda v: v >= 1)
    timeout_seconds = need("timeout_seconds", "Порог простоя, сек",
                           default=DEFAULTS["timeout_seconds"], cast=int,
                           validator=lambda v: v > 0)
    check_interval = need("check_interval", "Период опроса, сек",
                          default=DEFAULTS["check_interval"], cast=int,
                          validator=lambda v: v > 0)
    reconnect_delay = need("reconnect_delay", "Пауза перед переподключением, сек",
                           default=DEFAULTS["reconnect_delay"], cast=int,
                           validator=lambda v: v >= 0)
    poke_message = need("poke_message", "Текст poke-сообщения (пусто — не отправлять)",
                        default=DEFAULTS["poke_message"], required=False)
    exclude_uids = need("exclude_uids", "Исключённые UID (через запятую, пусто — нет)",
                        default="", cast=parse_str_list, required=False)
    exclude_channels = need("exclude_channels", "Исключённые cid (через запятую, пусто — нет)",
                            default="", cast=parse_int_list, required=False)

    # --- пароль ------------------------------------------------------------
    password = args.password
    if password is None:
        if interactive:
            print()
            while True:
                try:
                    p1 = getpass.getpass("Пароль query-аккаунта (Enter — оставить пустым): ")
                except (EOFError, KeyboardInterrupt):
                    p1 = ""
                if not p1:
                    password = ""
                    break
                try:
                    p2 = getpass.getpass("Повторите пароль: ")
                except (EOFError, KeyboardInterrupt):
                    p2 = ""
                if p1 == p2:
                    password = p1
                    break
                warn("Пароли не совпадают, попробуйте снова.")
        else:
            password = ""

    return {
        "host": host,
        "query_port": query_port,
        "username": username,
        "password": password,
        "virtual_server_id": virtual_server_id,
        "afk_channel_id": afk_channel_id,
        "timeout_seconds": timeout_seconds,
        "check_interval": check_interval,
        "reconnect_delay": reconnect_delay,
        "poke_message": poke_message or "",
        "exclude_uids": exclude_uids or [],
        "exclude_channels": exclude_channels or [],
    }

# --------------------------------------------------------------------------- #
#                        Запись конфигов и юнита                              #
# --------------------------------------------------------------------------- #

def check_connection(host: str, port: int) -> bool:
    try:
        with socket.create_connection((host, port), timeout=5):
            ok(f"{host}:{port} доступен")
            return True
    except OSError as exc:
        warn(f"Не удалось подключиться к {host}:{port} ({exc}). "
             f"Бот переподключится сам, когда сервер станет доступен.")
        return False

def write_config(cfg: dict) -> None:
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)

    data = {
        "host": cfg["host"],
        "query_port": cfg["query_port"],
        "username": cfg["username"],
        # Пароль НЕ пишем в JSON — он передаётся через переменную окружения.
        "password": "",
        "virtual_server_id": cfg["virtual_server_id"],
        "afk_channel_id": cfg["afk_channel_id"],
        "timeout_seconds": cfg["timeout_seconds"],
        "check_interval": cfg["check_interval"],
        "reconnect_delay": cfg["reconnect_delay"],
        "poke_message": cfg["poke_message"],
        "exclude_uids": cfg["exclude_uids"],
        "exclude_channels": cfg["exclude_channels"],
    }

    CONFIG_FILE.write_text(
        json.dumps(data, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    os.chmod(CONFIG_FILE, 0o640)
    os.chmod(CONFIG_DIR, 0o750)
    ok(f"Конфиг записан: {CONFIG_FILE}")

def write_env(password: str) -> None:
    if not password:
        if ENV_FILE.exists():
            ENV_FILE.unlink()
            warn(f"Пустой пароль — удалил {ENV_FILE}. "
                 f"Если нужен пароль, задайте его в config.json или в {ENV_FILE}.")
        else:
            info("Пароль не задан — файл окружения не создаётся")
        return

    content = (
        "# Создано install.py. Не добавляйте этот файл в систему контроля версий.\n"
        "# Переменная переопределяет поле \"password\" в config.json.\n"
        f"TS6_QUERY_PASSWORD={json.dumps(password, ensure_ascii=False)}\n"
    )
    ENV_FILE.write_text(content, encoding="utf-8")
    os.chmod(ENV_FILE, 0o600)
    ok(f"Пароль сохранён в {ENV_FILE} (chmod 600)")

def write_unit(verbose: bool) -> None:
    exec_line = f"{VENV_DIR}/bin/python {INSTALL_DIR}/bot.py --config {CONFIG_FILE}"
    if verbose:
        exec_line += " --verbose"

    unit = f"""# Сгенерировано install.py — правьте осторожно.
[Unit]
Description=TeamSpeak 6 AFK Bot
Documentation=https://github.com/Rednelss/ts6-afk-bot
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User={SYSTEM_USER}
Group={SYSTEM_USER}
WorkingDirectory={INSTALL_DIR}
Environment=PYTHONUNBUFFERED=1
Environment=HOME={STATE_DIR}
EnvironmentFile=-{ENV_FILE}
ExecStart={exec_line}
Restart=always
RestartSec=10
TimeoutStopSec=10
KillSignal=SIGTERM
SyslogIdentifier={SERVICE_NAME}

# --- Hardening ---
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
    UNIT_FILE.write_text(unit, encoding="utf-8")
    os.chmod(UNIT_FILE, 0o644)
    ok(f"systemd-юнит записан: {UNIT_FILE}")

def fix_permissions() -> None:
    subprocess.run(["chown", "-R", f"root:{SYSTEM_USER}", str(INSTALL_DIR)], check=False)
    subprocess.run(["chmod", "-R", "g+rX", str(INSTALL_DIR)], check=False)
    subprocess.run(["chown", "-R", f"root:{SYSTEM_USER}", str(CONFIG_DIR)], check=False)
    subprocess.run(["chmod", "750", str(CONFIG_DIR)], check=False)
    if CONFIG_FILE.exists():
        subprocess.run(["chmod", "640", str(CONFIG_FILE)], check=False)
    if ENV_FILE.exists():
        subprocess.run(["chown", "root:root", str(ENV_FILE)], check=False)
        subprocess.run(["chmod", "600", str(ENV_FILE)], check=False)

# --------------------------------------------------------------------------- #
#                          Управление сервисом                                #
# --------------------------------------------------------------------------- #

def enable_service() -> None:
    if not shutil.which("systemctl"):
        warn("systemctl не найден (контейнер/WSL?). Сервис не будет запущен.")
        return

    run(["systemctl", "daemon-reload"])
    run(["systemctl", "enable", "--now", SERVICE_NAME])

    time.sleep(2)
    status = run(["systemctl", "is-active", SERVICE_NAME], capture=True, quiet=True, check=False)
    state = status.stdout.strip()

    if state == "active":
        ok(f"Сервис {SERVICE_NAME} запущен и активен")
    else:
        warn(f"Состояние сервиса: {state}. Смотрите логи:")
        warn(f"  journalctl -u {SERVICE_NAME} -n 50 --no-pager")

    run(["systemctl", "status", SERVICE_NAME, "--no-pager", "-l"], check=False)

def uninstall(purge: bool) -> None:
    info("Удаление ts6-afk-bot...")

    if shutil.which("systemctl") and UNIT_FILE.exists():
        run(["systemctl", "disable", "--now", SERVICE_NAME], check=False)
        UNIT_FILE.unlink(missing_ok=True)
        run(["systemctl", "daemon-reload"], check=False)
        run(["systemctl", "reset-failed", SERVICE_NAME], check=False)
        ok("Сервис остановлен и удалён")

    if purge:
        for path in (INSTALL_DIR, CONFIG_DIR, STATE_DIR):
            if path.exists():
                shutil.rmtree(path, ignore_errors=True)
                ok(f"Удалено: {path}")
        run(["userdel", SYSTEM_USER], check=False)
        ok(f"Пользователь {SYSTEM_USER} удалён (если существовал)")
    else:
        warn(f"Данные сохранены: {INSTALL_DIR}, {CONFIG_DIR}, {STATE_DIR}")
        warn("Для полного удаления запустите: sudo python3 install.py --uninstall --purge")

    ok("Готово.")

# --------------------------------------------------------------------------- #
#                                  main                                       #
# --------------------------------------------------------------------------- #

def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="install.py",
        description="Установщик ts6-afk-bot (TeamSpeak 6 AFK Bot) для Linux.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Примеры:\n"
            "  sudo python3 install.py\n"
            "  sudo python3 install.py --host ts.example.com --afk-channel-id 12\n"
            "  sudo python3 install.py --non-interactive --host ts.example.com "
            "--afk-channel-id 12 --username bot --password secret\n"
            "  sudo python3 install.py --uninstall --purge\n"
        ),
    )

    # --- конфигурация бота ---
    g = parser.add_argument_group("Параметры бота (иначе спросит интерактивно)")
    g.add_argument("--host", help="Адрес TS6-сервера")
    g.add_argument("--query-port", dest="query_port", type=int,
                   help=f"Порт SSH ServerQuery (по умолчанию {DEFAULTS['query_port']})")
    g.add_argument("--username", help="Логин query-аккаунта")
    g.add_argument("--password", help="Пароль query-аккаунта (попадёт в /etc/ts6-afk-bot/env)")
    g.add_argument("--virtual-server-id", dest="virtual_server_id", type=int,
                   help="ID виртуального сервера (sid)")
    g.add_argument("--afk-channel-id", dest="afk_channel_id", type=int,
                   help="ID AFK-канала (cid)")
    g.add_argument("--timeout-seconds", dest="timeout_seconds", type=int,
                   help="Порог простоя в секундах")
    g.add_argument("--check-interval", dest="check_interval", type=int,
                   help="Период опроса в секундах")
    g.add_argument("--reconnect-delay", dest="reconnect_delay", type=int,
                   help="Пауза перед переподключением в секундах")
    g.add_argument("--poke-message", dest="poke_message",
                   help="Текст poke-сообщения (пусто — не отправлять)")
    g.add_argument("--exclude-uids", dest="exclude_uids",
                   help="UID через запятую, которых не трогать")
    g.add_argument("--exclude-channels", dest="exclude_channels",
                   help="cid каналов через запятую, где не трогать")

    # --- установка ---
    g2 = parser.add_argument_group("Параметры установки")
    g2.add_argument("--source", default=None,
                    help="URL или локальный путь к исходникам вместо GitHub")
    g2.add_argument("--install-dir", default=str(INSTALL_DIR),
                    help=f"Каталог установки (по умолчанию {INSTALL_DIR})")
    g2.add_argument("--user", default=SYSTEM_USER,
                    help=f"Системный пользователь (по умолчанию {SYSTEM_USER})")
    g2.add_argument("--service-name", default=SERVICE_NAME,
                    help=f"Имя systemd-юнита (по умолчанию {SERVICE_NAME})")
    g2.add_argument("--skip-update", action="store_true",
                    help="Не обновлять исходники, если они уже есть")
    g2.add_argument("--no-service", action="store_true",
                    help="Установить без создания systemd-юнита")
    g2.add_argument("--bot-verbose", action="store_true",
                    help="Запускать бота с флагом --verbose")
    g2.add_argument("--non-interactive", "--yes", dest="non_interactive",
                    action="store_true",
                    help="Не задавать вопросов (все значения должны быть в флагах)")

    # --- удаление ---
    g3 = parser.add_argument_group("Удаление")
    g3.add_argument("--uninstall", action="store_true", help="Удалить бота и сервис")
    g3.add_argument("--purge", action="store_true",
                    help="Вместе с --uninstall: удалить также все данные и пользователя")

    return parser.parse_args()

def main() -> None:
    global INSTALL_DIR, VENV_DIR, CONFIG_FILE, ENV_FILE, UNIT_FILE, SERVICE_NAME, SYSTEM_USER

    args = parse_args()
    ensure_root()

    # --- применяем CLI-переопределения путей -------------------------------
    INSTALL_DIR = Path(args.install_dir).expanduser()
    VENV_DIR = INSTALL_DIR / "venv"
    SERVICE_NAME = args.service_name
    SYSTEM_USER = args.user
    UNIT_FILE = Path(f"/etc/systemd/system/{SERVICE_NAME}.service")

    if args.uninstall:
        uninstall(args.purge)
        return

    print()
    print(_paint("1;34", "  TS6 AFK Bot — установщик для Linux"))
    print(_paint("90", f"  каталог: {INSTALL_DIR}   сервис: {SERVICE_NAME}.service"))
    print()

    install_system_dependencies()
    check_python()

    if not shutil.which("git"):
        die("git не найден. Установите его вручную и повторите запуск.")

    cfg = build_config(args)

    print()
    ensure_system_user()
    fetch_source(args.source, args.skip_update)
    setup_venv()

    check_connection(cfg["host"], cfg["query_port"])

    write_config(cfg)
    write_env(cfg["password"])
    fix_permissions()

    if args.no_service:
        warn("Флаг --no-service: systemd-юнит не создан.")
        print()
        info("Ручной запуск:")
        print(f"    sudo -u {SYSTEM_USER} {VENV_DIR}/bin/python "
              f"{INSTALL_DIR}/bot.py --config {CONFIG_FILE}")
        print()
        return

    write_unit(args.bot_verbose)
    enable_service()

    print()
    ok("Установка завершена.")
    print()
    print(_paint("1", "  Полезные команды:"))
    print(f"    systemctl status {SERVICE_NAME}")
    print(f"    systemctl restart {SERVICE_NAME}")
    print(f"    journalctl -u {SERVICE_NAME} -f")
    print()
    print(_paint("1", "  Файлы:"))
    print(f"    конфиг : {CONFIG_FILE}")
    if ENV_FILE.exists():
        print(f"    пароль : {ENV_FILE}")
    print(f"    код    : {INSTALL_DIR}")
    print()
    warn("После правки config.json выполните: "
         f"sudo systemctl restart {SERVICE_NAME}")
    print()

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print()
        die("Прервано пользователем.", code=130)
