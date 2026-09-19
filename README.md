# TS6 AFK Bot

Небольшой Python-бот для **TeamSpeak 6**, который автоматически переносит
неактивных пользователей в AFK-канал.

Подключается к ServerQuery через **SSH** (порт по умолчанию `10022`) и раз в
N секунд проверяет `client_idle_time` каждого клиента. Если простой превышает
порог (по умолчанию **5 минут**), бот переносит клиента в указанный AFK-канал
и (опционально) отправляет ему poke-сообщение.

---

## Возможности

- Работает с **TeamSpeak 6** (SSH ServerQuery).
- Настраиваемый порог неактивности и интервал опроса.
- Список исключений по UID и по каналам (например, чтобы не трогать ботов
  или AFK-зону).
- Опциональное poke-сообщение при перемещении.
- Автоматическое переподключение при обрыве SSH.
- Корректная обработка `SIGINT` / `SIGTERM` (systemd-friendly).
- Единственная зависимость — `paramiko`.

---

## Требования

- Python **3.10+**
- `paramiko >= 3.4`
- Включённый **SSH ServerQuery** на сервере TeamSpeak 6.
- Query-аккаунт с правами:
  - `b_client_move_power` — для перемещения клиентов.
  - `i_client_poke_power` — только если используете `poke_message`.
  - `b_client_query_list` — для чтения списка клиентов.

> По умолчанию SSH ServerQuery в TeamSpeak 6 слушает порт **10022**.
> Если у вас TS3 — порт тот же, логика та же (этот бот должен работать
> и на TS3-серверах, где включён SSH Query).

---

## Установка

```bash
git clone Rednelss/ts6-afk-bot
cd ts6-afk-bot

python3 -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate

pip install -r requirements.txt
```

## Настройка

1. Скопируйте пример конфига:

```
cp config.example.json config.json
```
2. Откройте `config.json` и заполните:

| Ключ | Обязательный | Описание |
|---|---|---|
| `host` | да | Хост/IP вашего TeamSpeak-сервера. |
| `query_port` | да | Порт SSH ServerQuery. Обычно `10022`. |
| `username` | да | Логин query-аккаунта. |
| `password` | да | Пароль query-аккаунта. |
| `virtual_server_id` | да | ID виртуального сервера (обычно `1`). Можно посмотреть в клиенте. |
| `afk_channel_id` | да | ID канала, куда перемещать неактивных. |
| `timeout_seconds` | нет | Порог неактивности в секундах. По умолчанию `300` (5 минут). |
| `check_interval` | нет | Интервал проверки в секундах. По умолчанию `30`. |
| `poke_message` | нет | Текст poke при перемещении. Пустая строка — не отправлять. |
| `exclude_uids` | нет | Список `client_unique_identifier`, которых не трогать. |
| `exclude_channels` | нет | Список `cid` каналов, в которых бот не работает. |
| `reconnect_delay` | нет | Пауза перед переподключением. По умолчанию `10`. |

### Как узнать `afk_channel_id` и `virtual_server_id`

Проще всего — через SSH ServerQuery вручную:

```
ssh afkbot@ts.example.com -p 10022
```

После авторизации:

```
use sid=1
channellist
```

Найдите нужный канал в выводе и запомните его `cid`.

### Как узнать UID клиента для исключения

```
clientlist -uid
```

Скопируйте `client_unique_identifier` нужного клиента в `exclude_uids`.

---

## Запуск

```
python bot.py --config config.json
```

С флагом `-v` — подробный лог:

```
python bot.py -c config.json -v
```

Остановить — `Ctrl+C`. Бот корректно закроет SSH-сессию.

### Пример вывода

```
2026-09-18 09:01:00 [INFO] Подключаюсь к ServerQuery ts.example.com:10022 ...
2026-09-18 09:01:01 [INFO] Соединение с ServerQuery установлено.
2026-09-18 09:01:01 [INFO] Бот запущен. AFK-канал cid=42, порог 300 сек, интервал 30 сек.
2026-09-18 09:06:31 [INFO] Перемещён в AFK: Vasya (clid=12, простой 5.1 мин)
```

---

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

## Как это работает внутри

1. `paramiko.SSHClient` открывает интерактивную SSH-сессию к ServerQuery.
2. Бот отправляет `use sid=<virtual_server_id>`, чтобы выбрать нужный
виртуальный сервер.
3. В цикле отправляет `clientlist -uid`, парсит ответ ServerQuery
(поля разделены пробелами, записи — символом `|`).
4. Для каждого не-query клиента (у которого `client_type=0`) делает
`clientinfo clid=<clid>` и читает `client_idle_time` (в миллисекундах).
5. Если простой ≥ `timeout_seconds * 1000`, отправляет
`clientmove clid=<clid> cid=<afk_channel_id>`.
6. При обрыве соединения бот засыпает на `reconnect_delay` секунд
и переподключается.

---

## Частые проблемы

**`Authentication failed`**
Логин/пароль query-аккаунта неверные, либо на сервере не включён SSH Query.
Проверьте в конфиге TeamSpeak: `query_ssh_port` в `ts3server.ini`.

**`Could not request channel move`**
У query-аккаунта нет `b_client_move_power` нужного уровня. Выдайте право
через клиент TeamSpeak (Permissions → Server Groups → нужная группа).

**Клиент уже неактивен 5 минут, но его не перемещает**
Проверьте, что его `cid` не в `exclude_channels` и его UID не в
`exclude_uids`. А также что он не в самом AFK-канале (бот не трогает тех,
кто уже там).

**Не парсится ответ ServerQuery**
Включите `-v`, посмотрите сырой ответ в логе. Формат команд может немного
отличаться в зависимости от сборки сервера.

---

## Лицензия

MIT. Используйте и меняйте как хотите.

