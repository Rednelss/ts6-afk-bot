# TS6 AFK Bot

[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![Python 3.10+](https://img.shields.io/badge/python-3.10%2B-blue.svg)](https://www.python.org/downloads/)

Небольшой бот для **TeamSpeak 6**, который автоматически перемещает неактивных
пользователей в отдельный AFK-канал. Работает через SSH ServerQuery, не требует
плагинов на стороне клиента.

---

## Возможности

- 🔌 Подключение к ServerQuery TeamSpeak 6 по SSH (порт `10022`).
- ⏱️ Автоматический перенос в AFK-канал после настраиваемого простоя.
- 📢 Опциональное poke-сообщение при переносе.
- 🚫 Гибкие исключения по `client_unique_identifier` и по каналу.
- ♻️ Автоматическое переподключение при разрыве SSH.
- 🐳 Docker-образ и `docker-compose.yml` для быстрого деплоя.
- 🧩 systemd-unit для продакшена.
- 🛑 Корректная остановка по `Ctrl+C` / `SIGTERM` за ≤ 0.5 секунды.
- 📊 Один запрос `clientlist -uid -times` на тик вместо N+1 запросов.

---

## Требования

- Python **3.10+**
- [`paramiko`](https://www.paramiko.org/) ≥ 3.4
- На сервере TeamSpeak 6:
  - включён **SSH ServerQuery** (порт `10022` по умолчанию);
  - query-аккаунт с правами `b_client_move_power` и `i_client_poke_power`.

---

## Установка

### Рекомендуемый способ: автоматический установщик `install.py`

Скрипт `install.py` полностью автоматизирует развёртывание на Linux:
проверяет зависимости, создаёт системного пользователя, клонирует репозиторий,
настраивает venv, генерирует `config.json` и systemd-юнит, запускает сервис.

**Требуется root.** Запустите:

```bash
sudo python3 install.py
```

Скрипт задаст несколько вопросов (адрес TS6-сервера, порт ServerQuery, логин
и пароль query-аккаунта, `sid`, `cid` AFK-канала и т.д.). Для большинства
параметров есть значения по умолчанию — просто нажмите Enter, чтобы принять их.

#### Неинтерактивная установка

Если нужно развернуть бота без вопросов (например, в скрипте автоматизации),
передайте все параметры флагами:

```bash
sudo python3 install.py --non-interactive \
  --host ts.example.com \
  --query-port 10022 \
  --username bot \
  --password 'SuperSecret' \
  --virtual-server-id 1 \
  --afk-channel-id 12 \
  --timeout-seconds 300 \
  --check-interval 30 \
  --reconnect-delay 10 \
  --poke-message 'Вы были перемещены в канал AFK из-за неактивности.' \
  --exclude-uids 'uid1,uid2' \
  --exclude-channels '5,7'
```

#### Все флаги `install.py`

**Параметры бота** (иначе спросит интерактивно):

| Флаг | Описание |
|------|----------|
| `--host` | Адрес TS6-сервера |
| `--query-port` | Порт SSH ServerQuery (по умолчанию `10022`) |
| `--username` | Логин query-аккаунта |
| `--password` | Пароль query-аккаунта (сохраняется в `/etc/ts6-afk-bot/env`) |
| `--virtual-server-id` | ID виртуального сервера (`sid`) |
| `--afk-channel-id` | ID AFK-канала (`cid`) |
| `--timeout-seconds` | Порог простоя в секундах (по умолчанию `300`) |
| `--check-interval` | Период опроса в секундах (по умолчанию `30`) |
| `--reconnect-delay` | Пауза перед переподключением в секундах (по умолчанию `10`) |
| `--poke-message` | Текст poke-сообщения (пусто — не отправлять) |
| `--exclude-uids` | UID через запятую, которых не трогать |
| `--exclude-channels` | `cid` каналов через запятую, где не трогать |

**Параметры установки:**

| Флаг | Описание |
|------|----------|
| `--source` | URL или локальный путь к исходникам вместо GitHub |
| `--install-dir` | Каталог установки (по умолчанию `/opt/ts6-afk-bot`) |
| `--user` | Системный пользователь (по умолчанию `ts6afkbot`) |
| `--service-name` | Имя systemd-юнита (по умолчанию `ts6-afk-bot`) |
| `--skip-update` | Не обновлять исходники, если они уже есть |
| `--no-service` | Установить без создания systemd-юнита |
| `--bot-verbose` | Запускать бота с флагом `--verbose` |
| `--non-interactive` (`--yes`) | Не задавать вопросов (все значения должны быть в флагах) |

**Удаление:**

| Флаг | Описание |
|------|----------|
| `--uninstall` | Удалить бота и сервис |
| `--purge` | Вместе с `--uninstall`: удалить также все данные и пользователя |

#### Что именно делает `install.py`

1. Проверяет права root, наличие Python 3.10+, модуля `venv` и `git`.
2. Устанавливает системные зависимости через `apt`, `dnf`, `yum`, `pacman` или `zypper`.
3. Создаёт изолированного системного пользователя `ts6afkbot` (без shell).
4. Клонирует репозиторий в `/opt/ts6-afk-bot` (или копирует локальные исходники через `--source`).
5. Создаёт venv в `/opt/ts6-afk-bot/venv` и устанавливает `paramiko ≥ 3.4`.
6. Собирает `config.json` и сохраняет его в `/etc/ts6-afk-bot/config.json` (chmod 640).
7. Пароль ServerQuery кладёт в `/etc/ts6-afk-bot/env` как `TS6_QUERY_PASSWORD` (chmod 600), а не в JSON.
8. Генерирует hardened systemd-юнит `/etc/systemd/system/ts6-afk-bot.service`, включает и запускает сервис.

#### Где что лежит после установки

```
/opt/ts6-afk-bot/                 код + venv
/etc/ts6-afk-bot/config.json      настройки (chmod 640)
/etc/ts6-afk-bot/env              TS6_QUERY_PASSWORD (chmod 600)
/var/lib/ts6-afk-bot/             HOME сервисного пользователя
/etc/systemd/system/ts6-afk-bot.service
```

---

### Ручная установка (venv)

Если вы предпочитаете установить бота вручную:

```bash
git clone https://github.com/Rednelss/ts6-afk-bot.git
cd ts6-afk-bot

python3 -m venv venv
source venv/bin/activate          # Windows: venv\Scripts\activate

pip install -r requirements.txt
```

### Запуск как systemd-сервис (ручной)

1. Скопируйте бота в `/opt/ts6-afk-bot`, создайте venv, установите зависимости.
2. Скопируйте unit:

   ```bash
   sudo cp ts6-afk-bot.service /etc/systemd/system/ts6-afk-bot.service
   ```

3. Откройте unit и при необходимости поправьте `User`, `WorkingDirectory`
   и `ExecStart`.
4. Запустите:

   ```bash
   sudo systemctl daemon-reload
   sudo systemctl enable --now ts6-afk-bot
   sudo journalctl -u ts6-afk-bot -f
   ```

---

## Настройка

При автоматической установке `config.json` создаётся в `/etc/ts6-afk-bot/config.json`.
При ручной установке скопируйте пример:

```bash
cp config.example.json config.json
```

| Поле | Тип | По умолчанию | Описание |
|------|-----|--------------|----------|
| `host` | string | — | Адрес TS6-сервера |
| `query_port` | int | — | Порт SSH ServerQuery (обычно `10022`) |
| `username` | string | — | Логин query-аккаунта |
| `password` | string | — | Пароль query-аккаунта (при автоматической установке не используется — см. ниже) |
| `virtual_server_id` | int | — | `sid` виртуального сервера (обычно `1`) |
| `afk_channel_id` | int | — | `cid` AFK-канала |
| `timeout_seconds` | int | `300` | Порог простоя в секундах |
| `check_interval` | int | `30` | Период опроса в секундах |
| `reconnect_delay` | int | `10` | Пауза перед переподключением |
| `poke_message` | string | `""` | Текст poke; пустая строка — не отправлять |
| `exclude_uids` | string[] | `[]` | UID, которых не трогать |
| `exclude_channels` | int[] | `[]` | `cid` каналов, где не трогать |

> **Пароль через переменную окружения.** Если задать `TS6_QUERY_PASSWORD`,
> она переопределит `password` из JSON. Автоматический установщик использует
> именно этот способ: пароль сохраняется в `/etc/ts6-afk-bot/env`, а в
> `config.json` остаётся пустая строка.

**Как узнать `virtual_server_id` и `afk_channel_id`:**

```bash
# sid
ssh -p 10022 username@ts.example.com
> serverlist
> quit

# cid — через клиент TeamSpeak: правый клик по каналу → "Copy channel ID"
```

---

## Запуск

### Обычный

```bash
python bot.py --config config.json
```

С подробным логом:

```bash
python bot.py --config config.json --verbose
```

При автоматической установке бот запускается как systemd-сервис и стартует
автоматически при загрузке системы.

---

## Управление сервисом (systemd)

```bash
systemctl status ts6-afk-bot
systemctl restart ts6-afk-bot
journalctl -u ts6-afk-bot -f
```

---

## Удаление

Автоматический установщик умеет удалять бота:

```bash
# Остановить и убрать юнит, файлы оставить
sudo python3 install.py --uninstall

# Снести всё, включая данные и системного пользователя
sudo python3 install.py --uninstall --purge
```

---

## Как это работает

1. При старте бот подключается к ServerQuery по SSH и выполняет `use sid=X`.
2. Проверяет результат через `whoami` — если контекст не переключился,
   перезапускает цикл.
3. Каждые `check_interval` секунд выполняет `clientlist -uid -times` и
   получает список клиентов с их `client_idle_time` **одной командой**.
4. Для клиентов с простоем `> timeout_seconds`:
   - пропускает query-клиентов, исключённые UID, исключённые каналы;
   - выполняет `clientmove clid=N cid=AFK`;
   - опционально `clientpoke`.
5. При разрыве SSH — переподключается через `reconnect_delay` секунд.

### Почему `clientlist -times`, а не `clientinfo` для каждого

Наивный подход — N+1 запросов: один `clientlist` + по одному `clientinfo`
на клиента. При 30 юзерах это 31 команда за тик. Флаг `-times` добавляет
`client_idle_time` прямо в `clientlist`, и на тик уходит **одна** команда.

Если ваш сервер не поддерживает `-times`, бот автоматически падает обратно
на `clientinfo` — просто будет медленнее.

---

## Устранение неполадок

### `AuthenticationException`

Проверьте логин/пароль query-аккаунта. Убедитесь, что SSH ServerQuery
включён в конфиге TeamSpeak (`query_ssh_port=10022`).

### `use sid=X не сработал: virtualserver_id=…`

Указан неверный `virtual_server_id`. Узнайте его командой `serverlist` в
ServerQuery-шелле.

### Бот никого не перемещает

1. Скорее всего, `client_idle_time` возвращается **в секундах**, а не
   миллисекундах — проверьте лог с `--verbose` и посмотрите `RAW=`.
2. Или пользователи в `exclude_uids` / `exclude_channels`.
3. Или порог `timeout_seconds` слишком велик.

### `clientmove … error id=516` (нет прав)

У query-аккаунта нет `b_client_move_power`. Выдайте права через ServerQuery.

### `systemctl` не найден

Если вы запускаете установщик в контейнере или WSL, systemd может
отсутствовать. В этом случае используйте флаг `--no-service` — установщик
настроит всё, но не будет создавать юнит. Бота можно запустить вручную:

```bash
sudo -u ts6afkbot /opt/ts6-afk-bot/venv/bin/python /opt/ts6-afk-bot/bot.py --config /etc/ts6-afk-bot/config.json
```

---

## Разработка

```bash
pip install ruff
ruff check .
ruff format .
```

---

## Лицензия

MIT — см. [LICENSE](LICENSE).
