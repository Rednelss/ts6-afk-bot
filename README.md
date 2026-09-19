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

### Локально (venv)

```bash
git clone https://github.com/Rednelss/ts6-afk-bot.git
cd ts6-afk-bot

python3 -m venv venv
source venv/bin/activate          # Windows: venv\Scripts\activate

pip install -r requirements.txt
```

## Запуск как systemd-сервис (Linux)

1. Скопируйте бота в `/opt/ts6-afk-bot`, создайте venv, установите зависимости.
2. Скопируйте unit:

```
sudo cp ts6-afk-bot.service /etc/systemd/system/ts6-afk-bot.service
```
3. Откройте unit и при необходимости поправьте `User`, `WorkingDirectory`
и `ExecStart`.
4. Запустите:

```
sudo systemctl daemon-reload
sudo systemctl enable --now ts6-afk-bot
sudo journalctl -u ts6-afk-bot -f
```

---

## Настройка

Скопируйте пример и отредактируйте:

```bash
cp config.example.json config.json
```

| Поле | Тип | По умолчанию | Описание |
|------|-----|--------------|----------|
| `host` | string | — | Адрес TS6-сервера |
| `query_port` | int | — | Порт SSH ServerQuery (обычно `10022`) |
| `username` | string | — | Логин query-аккаунта |
| `password` | string | — | Пароль query-аккаунта |
| `virtual_server_id` | int | — | `sid` виртуального сервера (обычно `1`) |
| `afk_channel_id` | int | — | `cid` AFK-канала |
| `timeout_seconds` | int | `300` | Порог простоя в секундах |
| `check_interval` | int | `30` | Период опроса в секундах |
| `reconnect_delay` | int | `10` | Пауза перед переподключением |
| `poke_message` | string | `""` | Текст poke; пустая строка — не отправлять |
| `exclude_uids` | string[] | `[]` | UID, которых не трогать |
| `exclude_channels` | int[] | `[]` | `cid` каналов, где не трогать |

> **Пароль через переменную окружения.** Если задать `TS6_QUERY_PASSWORD`,
> она переопределит `password` из JSON. Удобно для Docker/CI.

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
