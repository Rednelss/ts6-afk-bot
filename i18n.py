"""English and Russian messages shared by the bot and installer."""

from __future__ import annotations

import argparse
import os
from collections.abc import Sequence

DEFAULT_LANGUAGE = "en"
LANGUAGES = ("en", "ru")

_RU = {
    "Unsupported language {language!r}; choose en or ru.": "Неподдерживаемый язык {language!r}; выберите en или ru.",
    "usage: ": "использование: ",
    "positional arguments": "позиционные аргументы",
    "optional arguments": "необязательные параметры",
    "options": "параметры",
    "show this help message and exit": "показать справку и выйти",
    "show program's version number and exit": "показать версию программы и выйти",
    "unrecognized arguments: %s": "неизвестные аргументы: %s",
    "the following arguments are required: %s": "обязательные аргументы: %s",
    "argument %(argument_name)s: %(message)s": "аргумент %(argument_name)s: %(message)s",
    "%(prog)s: error: %(message)s\n": "%(prog)s: ошибка: %(message)s\n",
    "invalid choice: %(value)r (choose from %(choices)s)": "недопустимое значение: %(value)r (выберите из %(choices)s)",
    "invalid %(type)s value: %(value)r": "недопустимое значение типа %(type)s: %(value)r",
    "expected one argument": "ожидается один аргумент",
    "not allowed with argument %s": "нельзя использовать вместе с аргументом %s",
    "Configuration file not found: {path}": "Файл конфигурации не найден: {path}",
    "Configuration must be a JSON object.": "Конфигурация должна быть JSON-объектом.",
    "Missing required configuration fields: {fields}": "В конфигурации отсутствуют обязательные поля: {fields}",
    "Unknown configuration keys (ignored): %s": "Неизвестные ключи конфигурации (проигнорированы): %s",
    "config: '{field}' must be a string.": "config: поле '{field}' должно быть строкой.",
    "config: '{field}' must not be empty.": "config: поле '{field}' не должно быть пустым.",
    "config: 'poke_message' must be a string or null.": "config: поле 'poke_message' должно быть строкой или null.",
    "config: '{field}' must be an integer.": "config: поле '{field}' должно быть целым числом.",
    "config: '{field}' must be at least {minimum}.": "config: поле '{field}' должно быть не меньше {minimum}.",
    "config: 'query_port' must be between 1 and 65535.": "config: поле 'query_port' должно быть в диапазоне от 1 до 65535.",
    "config: 'exclude_uids' must be a list of nonempty strings.": "config: поле 'exclude_uids' должно быть массивом непустых строк.",
    "config: 'exclude_channels' must be a list of positive integers.": "config: поле 'exclude_channels' должно быть массивом положительных целых чисел.",
    "You were moved to the AFK channel after {seconds} seconds of inactivity.": "Вы перемещены в AFK-канал из-за неактивности (порог: {seconds} с).",
    "Connecting to ServerQuery %s:%d...": "Подключение к ServerQuery %s:%d...",
    "ServerQuery authentication failed. Check the query account username and password.": "Не удалось авторизоваться в ServerQuery. Проверьте логин и пароль query-аккаунта.",
    "SSH connection failed: {error}": "Ошибка подключения SSH: {error}",
    "Connected to ServerQuery.": "Соединение с ServerQuery установлено.",
    "ServerQuery error {code}: {message}": "Ошибка ServerQuery {code}: {message}",
    "ServerQuery response has no complete status line.": "Ответ ServerQuery не содержит полной строки статуса.",
    "ServerQuery connection closed.": "Соединение с ServerQuery закрыто.",
    "Could not close a query resource: %s": "Не удалось закрыть ресурс query-соединения: %s",
    "ServerQuery did not respond within {timeout} seconds.": "ServerQuery не ответил за {timeout} с.",
    "Call connect() before sending commands.": "Перед отправкой команд вызовите connect().",
    "A ServerQuery command must be one nonempty line.": "Команда ServerQuery должна состоять из одной непустой строки.",
    "Repeated signal %s; exiting immediately.": "Повторный сигнал %s; немедленный выход.",
    "Received signal %s; stopping (press Ctrl+C again to force exit)...": "Получен сигнал %s; завершение работы (повторный Ctrl+C для немедленного выхода)...",
    "use sid={sid} failed: {error}": "Не удалось выполнить use sid={sid}: {error}",
    "whoami failed: {error}": "Не удалось выполнить whoami: {error}",
    "use sid={sid} did not select the server; virtualserver_id={actual}.": "use sid={sid} не переключил сервер; virtualserver_id={actual}.",
    "Selected virtual server sid=%d.": "Выбран виртуальный сервер sid=%d.",
    "Could not set the query nickname: %s": "Не удалось задать ник query-клиента: %s",
    "clientlist -times is unavailable; using clientinfo for idle times.": "Флаг clientlist -times недоступен; время простоя будет получено через clientinfo.",
    "Skipping a client with invalid clid/cid: %r": "Пропущен клиент с некорректным clid/cid: %r",
    "clientinfo failed for clid=%s: %s": "Ошибка clientinfo для clid=%s: %s",
    "clientinfo has no idle time for clid=%s.": "Ответ clientinfo не содержит времени простоя для clid=%s.",
    "Skipping a client whose identity or channel changed: clid=%s": "Пропущен клиент, у которого сменился UID или канал: clid=%s",
    "Skipping an invalid idle time for clid=%s: %r": "Пропущено некорректное время простоя для clid=%s: %r",
    "Moved to AFK: %s (clid=%s, idle %.1f min).": "Перемещён в AFK: %s (clid=%s, простой %.1f мин).",
    "clientmove failed for clid=%s: %s": "Ошибка clientmove для clid=%s: %s",
    "clientpoke failed for clid=%s: %s": "Ошибка clientpoke для clid=%s: %s",
    "Users moved this tick: %d": "Пользователей перемещено за проход: %d",
    "Bot started. AFK cid=%d, idle limit %d s, check interval %d s.": "Бот запущен. AFK cid=%d, порог простоя %d с, интервал %d с.",
    "Client check failed: %s": "Сбой проверки клиентов: %s",
    "Connection failed: %s": "Не удалось подключиться: %s",
    "Reconnecting in %d seconds...": "Переподключение через %d с...",
    "Bot stopped.": "Бот остановлен.",
    "TeamSpeak 6 AFK bot: move idle clients to a separate channel.": "AFK-бот TeamSpeak 6: перемещение неактивных клиентов в отдельный канал.",
    "Path to the JSON configuration (default: config.json)": "Путь к JSON-конфигурации (по умолчанию: config.json)",
    "Enable debug logging": "Включить подробный лог DEBUG",
    "Output language: en or ru (overrides TS6_LANGUAGE and config)": "Язык вывода: en или ru (приоритет выше TS6_LANGUAGE и конфига)",
    "Configuration error: %s": "Ошибка конфигурации: %s",
    "Interrupted by the user.": "Прервано пользователем.",
    "Commands must be passed as an argument list, not a shell string.": "Команды нужно передавать списком аргументов, а не строкой для shell.",
    "Command exited with code {code}: {command}": "Команда завершилась с кодом {code}: {command}",
    "Could not run {command}: {error}": "Не удалось запустить {command}: {error}",
    "Run this installer as root:\n    sudo python3 install.py": "Запустите установщик от root:\n    sudo python3 install.py",
    "python3 was not found. Install Python 3.10+ and try again.": "python3 не найден. Установите Python 3.10+ и повторите запуск.",
    "Could not determine the Python version.": "Не удалось определить версию Python.",
    "Could not parse the Python version: {version!r}": "Не удалось разобрать версию Python: {version!r}",
    "Python 3.10+ is required; found {version}.": "Требуется Python 3.10+; найден {version}.",
    "The venv module is unavailable. Install python3-venv on Debian/Ubuntu.": "Модуль venv недоступен. На Debian/Ubuntu установите python3-venv.",
    "No supported package manager found; skipping system packages.": "Поддерживаемый пакетный менеджер не найден; системные пакеты пропущены.",
    "Package manager: {manager}": "Пакетный менеджер: {manager}",
    "System package installation failed; checking available tools next.": "Не удалось установить системные пакеты; далее будут проверены доступные инструменты.",
    "User '{user}' already exists.": "Пользователь '{user}' уже существует.",
    "Creating system user '{user}'...": "Создание системного пользователя '{user}'...",
    "The bot must not run as a user with UID 0.": "Бот не должен работать от пользователя с UID 0.",
    "Source update skipped (--skip-update).": "Обновление исходников пропущено (--skip-update).",
    "Updating the repository...": "Обновление репозитория...",
    "Repository updated.": "Репозиторий обновлён.",
    "Installed sources are not a Git repository; update skipped.": "Установленные исходники не являются Git-репозиторием; обновление пропущено.",
    "Directory {path} is not empty and does not contain bot.py. Choose another --install-dir.": "Каталог {path} не пуст и не содержит bot.py. Выберите другой --install-dir.",
    "Local source must be a directory: {path}": "Локальный источник должен быть каталогом: {path}",
    "The installation directory must be outside the source directory.": "Каталог установки должен находиться вне каталога исходников.",
    "Copying local sources from {path}": "Копирование локальных исходников из {path}",
    "Cloning {url}": "Клонирование {url}",
    "Source file not found: {path}": "Файл исходников не найден: {path}",
    "git was not found. Install it before using a source URL.": "git не найден. Установите его для работы с URL исходников.",
    "The installed repository has a different origin. Use its checkout or choose another --install-dir.": "У установленного репозитория другой origin. Используйте его рабочую копию или выберите другой --install-dir.",
    "The installed sources are not a Git checkout. Choose another --install-dir for a source URL.": "Установленные исходники не являются Git-репозиторием. Для URL исходников выберите другой --install-dir.",
    "Creating a virtual environment...": "Создание виртуального окружения...",
    "requirements.txt was not found; installing paramiko directly.": "requirements.txt не найден; paramiko будет установлен напрямую.",
    "paramiko was not installed. Check the pip output above.": "paramiko не установлен. Проверьте вывод pip выше.",
    "A value is required.": "Нужно указать значение.",
    "Invalid value; try again.": "Некорректное значение; попробуйте снова.",
    "Input ended before configuration was complete. Use --non-interactive with the required options.": "Ввод завершился до окончания настройки. Используйте --non-interactive с обязательными параметрами.",
    "Invalid value for --{option}: {value!r}": "Некорректное значение --{option}: {value!r}",
    "--{option} is required in non-interactive mode.": "В неинтерактивном режиме нужен параметр --{option}.",
    "Bot configuration. Press Enter to accept the default.": "Настройка бота. Нажмите Enter, чтобы принять значение по умолчанию.",
    "TS6 server address (host)": "Адрес сервера TS6 (host)",
    "SSH ServerQuery port": "Порт SSH ServerQuery",
    "Query account username": "Логин query-аккаунта",
    "Virtual server ID (sid)": "ID виртуального сервера (sid)",
    "AFK channel ID (cid)": "ID AFK-канала (cid)",
    "Idle limit in seconds": "Порог простоя в секундах",
    "Check interval in seconds": "Интервал проверки в секундах",
    "Reconnect delay in seconds": "Пауза перед переподключением в секундах",
    "Poke message (Enter: automatic; '-' to disable)": "Poke-сообщение (Enter: стандартное; '-' для отключения)",
    "Excluded UIDs (comma-separated; empty for none)": "Исключённые UID (через запятую; пусто, если нет)",
    "Excluded channel IDs (comma-separated; empty for none)": "Исключённые ID каналов (через запятую; пусто, если нет)",
    "Query account password (Enter to leave empty): ": "Пароль query-аккаунта (Enter, чтобы оставить пустым): ",
    "Repeat the password: ": "Повторите пароль: ",
    "Passwords do not match; try again.": "Пароли не совпадают; попробуйте снова.",
    "{host}:{port} is reachable.": "{host}:{port} доступен.",
    "Could not connect to {host}:{port} ({error}). The bot will retry when the server becomes available.": "Не удалось подключиться к {host}:{port} ({error}). Бот повторит попытку, когда сервер станет доступен.",
    "Configuration written: {path}": "Конфигурация записана: {path}",
    "Empty password; removed {path}.": "Пароль пуст; файл {path} удалён.",
    "No password supplied; no environment file created.": "Пароль не задан; файл окружения не создан.",
    "The password contains characters that cannot be stored in a systemd environment file.": "Пароль содержит символы, которые нельзя сохранить в файле окружения systemd.",
    "Password saved to {path} (mode 600).": "Пароль сохранён в {path} (права 600).",
    "TeamSpeak 6 AFK bot": "AFK-бот TeamSpeak 6",
    "systemd unit written: {path}": "Юнит systemd записан: {path}",
    "systemctl was not found. Use --no-service for a manual installation.": "systemctl не найден. Для ручной установки используйте --no-service.",
    "Service {service} is active.": "Сервис {service} активен.",
    "Service state: {state}. Check the logs:": "Состояние сервиса: {state}. Проверьте логи:",
    "Uninstalling ts6-afk-bot...": "Удаление ts6-afk-bot...",
    "Service stopped and removed.": "Сервис остановлен и удалён.",
    "Refusing to purge a directory without an installation marker: {path}": "Отказ от удаления каталога без метки установки: {path}",
    "Removed: {path}": "Удалено: {path}",
    "User {user} removed.": "Пользователь {user} удалён.",
    "Existing user {user} retained.": "Существующий пользователь {user} сохранён.",
    "Data kept: {install}, {config}, {state}": "Данные сохранены: {install}, {config}, {state}",
    "To remove the data too, repeat the same options with --uninstall --purge.": "Для удаления данных повторите те же параметры с --uninstall --purge.",
    "Done.": "Готово.",
    "Linux installer for ts6-afk-bot (TeamSpeak 6 AFK bot).": "Установщик ts6-afk-bot (AFK-бот TeamSpeak 6) для Linux.",
    "Examples:": "Примеры:",
    "Bot options (prompted for unless --non-interactive is set)": "Параметры бота (запрашиваются, если нет --non-interactive)",
    "TS6 server address": "Адрес сервера TS6",
    "SSH ServerQuery port (default: 10022)": "Порт SSH ServerQuery (по умолчанию: 10022)",
    "Query account password (or TS6_QUERY_PASSWORD; saved outside JSON)": "Пароль query-аккаунта (или TS6_QUERY_PASSWORD; сохраняется вне JSON)",
    "Interface and bot language (default: en; or TS6_LANGUAGE)": "Язык установщика и бота (по умолчанию: en; или TS6_LANGUAGE)",
    "Custom poke text; an empty string disables pokes": "Текст poke-сообщения; пустая строка отключает poke",
    "Comma-separated client UIDs to exclude": "Исключённые UID клиентов через запятую",
    "Comma-separated channel IDs to exclude": "Исключённые ID каналов через запятую",
    "Installation options": "Параметры установки",
    "Source URL or local directory (default: this checkout)": "URL или каталог исходников (по умолчанию: текущая рабочая копия)",
    "Installation directory (default: /opt/ts6-afk-bot)": "Каталог установки (по умолчанию: /opt/ts6-afk-bot)",
    "System user (default: ts6afkbot)": "Системный пользователь (по умолчанию: ts6afkbot)",
    "systemd service name, without .service (default: ts6-afk-bot)": "Имя сервиса systemd без .service (по умолчанию: ts6-afk-bot)",
    "Keep the installed sources": "Сохранить установленные исходники",
    "Install without creating a systemd service": "Установить без создания сервиса systemd",
    "Run the bot with debug logging": "Запускать бота с подробным логом",
    "Use defaults without prompting; --host and --afk-channel-id are required": "Использовать значения по умолчанию без вопросов; нужны --host и --afk-channel-id",
    "Uninstallation": "Удаление",
    "Stop and remove the service": "Остановить и удалить сервис",
    "With --uninstall: also remove installed files and the system user": "Вместе с --uninstall: также удалить файлы установки и системного пользователя",
    "--purge requires --uninstall.": "Для --purge нужен --uninstall.",
    "Invalid system user name: {user!r}": "Недопустимое имя системного пользователя: {user!r}",
    "Invalid service name: {service!r}": "Недопустимое имя сервиса: {service!r}",
    "Unsafe installation directory: {path}": "Небезопасный каталог установки: {path}",
    "Directory {path} is not managed by this installer.": "Каталог {path} не принадлежит этой установке.",
    "The existing unit does not belong to ts6-afk-bot: {path}": "Существующий юнит не принадлежит ts6-afk-bot: {path}",
    "Installation paths must not contain control characters.": "Пути установки не должны содержать управляющие символы.",
    "The installation directory must not end with whitespace.": "Каталог установки не должен заканчиваться пробельным символом.",
    "Installation, configuration, and state directories must not overlap.": "Каталоги установки, конфигурации и состояния не должны пересекаться.",
    "Installation directories must not be symbolic links: {path}": "Каталоги установки не должны быть символическими ссылками: {path}",
    "TS6 AFK Bot: Linux installer": "TS6 AFK Bot: установщик для Linux",
    "directory: {path}   service: {service}.service": "каталог: {path}   сервис: {service}.service",
    "--no-service: no systemd unit created.": "--no-service: юнит systemd не создан.",
    "Manual start:": "Ручной запуск:",
    "Set TS6_QUERY_PASSWORD in your shell before starting the bot; sudo must preserve it.": "Перед запуском задайте TS6_QUERY_PASSWORD в оболочке; sudo должен сохранить эту переменную.",
    "Installation complete.": "Установка завершена.",
    "Installation failed: {error}": "Ошибка установки: {error}",
    "No password supplied; keeping the existing environment file.": "Пароль не задан; существующий файл окружения сохранён.",
    "Useful commands:": "Полезные команды:",
    "Files:": "Файлы:",
    "config: {path}": "конфиг: {path}",
    "password: {path}": "пароль: {path}",
    "code: {path}": "код: {path}",
    "After editing config.json, run: sudo systemctl restart {service}": "После изменения config.json выполните: sudo systemctl restart {service}",
}

_language = DEFAULT_LANGUAGE


class Translator:
    def __init__(self, language: str = DEFAULT_LANGUAGE) -> None:
        if language not in LANGUAGES:
            raise ValueError(
                tr("Unsupported language {language!r}; choose en or ru.", language=language)
            )
        self.language = language

    def __call__(self, source: str, **values: object) -> str:
        text = _RU.get(source, source) if self.language == "ru" else source
        return text.format(**values) if values else text


def tr(source: str, **values: object) -> str:
    text = _RU.get(source, source) if _language == "ru" else source
    return text.format(**values) if values else text


def set_language(language: str) -> None:
    global _language
    _language = Translator(language).language
    # argparse uses gettext even when the application has its own language setting.
    argparse._ = tr


def configure_cli_language(argv: Sequence[str]) -> str | None:
    set_language(DEFAULT_LANGUAGE)
    parser = argparse.ArgumentParser(add_help=False, allow_abbrev=False)
    parser.add_argument("--language", choices=LANGUAGES)
    args, _ = parser.parse_known_args(argv)
    selected = (
        args.language
        if args.language is not None
        else os.environ.get("TS6_LANGUAGE", DEFAULT_LANGUAGE)
    )
    set_language(selected)
    return args.language
