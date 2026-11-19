# TS6 AFK Bot

English | [Русский](README.ru.md)

Move idle TeamSpeak clients to an AFK channel through SSH ServerQuery. The bot runs on the server side; clients do not need a plugin.

It supports configurable idle limits, UID and channel exclusions, optional poke messages, reconnection after SSH failures, and systemd installation on Linux.

## Requirements

- Python 3.10+ and `paramiko >= 3.4`.
- SSH ServerQuery enabled on the TeamSpeak server, normally on port `10022`.
- A query account with enough `i_client_move_power` and channel access to move clients. Pokes also require enough `i_client_poke_power`.

## Install on Linux

Clone the repository and run the installer:

```bash
git clone https://github.com/Rednelss/ts6-afk-bot.git
cd ts6-afk-bot
sudo python3 install.py
```

The installer asks for the server address, query credentials, virtual server ID and AFK channel ID. Press Enter to accept a default. It installs the current checkout, creates a virtual environment and service account, writes the configuration, and starts a systemd service. Root privileges are required for installation.

For an unattended installation, only the host and AFK channel are required; other options use their defaults:

```bash
sudo python3 install.py --non-interactive \
  --host ts.example.com \
  --afk-channel-id 12
```

Supply a password through the interactive prompt, `--password`, or `TS6_QUERY_PASSWORD`. To read it without putting the value in a command line:

```bash
read -rsp 'Query password: ' TS6_QUERY_PASSWORD
printf '\n'
export TS6_QUERY_PASSWORD
sudo --preserve-env=TS6_QUERY_PASSWORD python3 install.py --non-interactive \
  --host ts.example.com --afk-channel-id 12
unset TS6_QUERY_PASSWORD
```

The installer saves the password in a root-owned environment file with mode `600`. The JSON configuration keeps an empty password. An unattended reinstall keeps an existing environment file when no password is supplied; pass `--password ''` to clear it.

### Installer options

| Option | Purpose |
|--------|---------|
| `--language en\|ru` | Installer and bot language; English by default |
| `--host` | TeamSpeak server address |
| `--query-port` | SSH ServerQuery port, default `10022` |
| `--username` | Query username, default `serveradmin` |
| `--password` | Query password; `TS6_QUERY_PASSWORD` is also supported |
| `--virtual-server-id` | Virtual server ID, default `1` |
| `--afk-channel-id` | Destination AFK channel ID |
| `--timeout-seconds` | Idle limit, default `300`, minimum `10` |
| `--check-interval` | Polling interval, default `30`, minimum `1` |
| `--reconnect-delay` | Reconnection delay, default `10`, minimum `0` |
| `--poke-message` | Custom poke text; `''` disables pokes |
| `--exclude-uids` | Comma-separated client UIDs |
| `--exclude-channels` | Comma-separated positive channel IDs |
| `--source` | Local source directory or Git URL; default: this checkout |
| `--install-dir` | Code and virtual environment directory, default `/opt/ts6-afk-bot` |
| `--user` | Service account, default `ts6afkbot` |
| `--service-name` | Service name without `.service`, default `ts6-afk-bot` |
| `--skip-update` | Keep the installed source files |
| `--no-service` | Install files without creating a systemd unit |
| `--bot-verbose` | Start the bot with debug logging |
| `--non-interactive`, `--yes` | Use defaults without prompting |
| `--uninstall` | Stop and remove the service, keeping files |
| `--purge` | With `--uninstall`, remove marked installation directories too |

Existing services are restarted after configuration changes. Git source updates use `pull --ff-only`; a failed update stops the installation. Local copying excludes virtual environments, Git metadata and local credential files.

Default installed paths:

```text
/opt/ts6-afk-bot/                     code and venv
/etc/ts6-afk-bot/config.json          configuration, mode 640
/etc/ts6-afk-bot/env                  password, mode 600
/var/lib/ts6-afk-bot/                 service account HOME
/etc/systemd/system/ts6-afk-bot.service
```

A custom `--service-name NAME` also uses `/etc/NAME` and `/var/lib/NAME`. Use a separate installation directory and service account for each instance.

## Language

English is the default for CLI help, logs, installer prompts and automatic poke messages, regardless of the operating system locale.

Use Russian for a single run:

```bash
python3 bot.py --config config.json --language ru
sudo python3 install.py --language ru
```

For a persistent choice, set `"language": "ru"` in `config.json`. With the installed service, restart it after editing:

```bash
sudo systemctl restart ts6-afk-bot
```

Switch back with `"language": "en"` or `--language en`. The bot resolves language in this order: `--language`, `TS6_LANGUAGE`, JSON `language`, then `en`. Installer language comes from its flag or `TS6_LANGUAGE` and is saved to JSON. Use `--language ru --help` for Russian help.

`"poke_message": null` selects an automatic message in the chosen language and uses the configured idle limit. An empty string disables pokes. Custom strings are sent exactly as written and are not translated.

## Manual installation

```bash
python3 -m venv venv
source venv/bin/activate
python -m pip install -r requirements.txt
cp config.example.json config.json
```

Edit `config.json` with your host, credentials and AFK channel, then start:

```bash
python bot.py --config config.json
python bot.py --config config.json --verbose
```

For a manually provisioned systemd service, adapt [ts6-afk-bot.service](ts6-afk-bot.service). Its paths match the default installer layout. Create the account and state directory, make the configuration readable by the service group, and keep the password environment file readable only by root before enabling it.

With `--no-service`, export `TS6_QUERY_PASSWORD` before a manual start if authentication needs a password:

```bash
sudo --preserve-env=TS6_QUERY_PASSWORD -u ts6afkbot \
  /opt/ts6-afk-bot/venv/bin/python /opt/ts6-afk-bot/bot.py \
  --config /etc/ts6-afk-bot/config.json
```

The service account cannot read the root-only environment file directly. systemd loads it when starting the service.

## Configuration

| Field | Type | Default | Meaning |
|-------|------|---------|---------|
| `host` | string | required | TeamSpeak server address |
| `afk_channel_id` | integer | required | Positive AFK channel ID |
| `query_port` | integer | `10022` | SSH port, `1` to `65535` |
| `username` | string | `serveradmin` | Query account name |
| `password` | string | `""` | Query password; overridden by `TS6_QUERY_PASSWORD`, including an empty value |
| `virtual_server_id` | integer | `1` | Positive virtual server ID |
| `timeout_seconds` | integer | `300` | Idle limit, at least `10` seconds |
| `check_interval` | integer | `30` | Polling interval, at least `1` second |
| `reconnect_delay` | integer | `10` | Delay after a connection failure, zero or greater |
| `language` | string | `"en"` | `en` or `ru` |
| `poke_message` | string or null | `null` | Automatic message, custom text, or `""` to disable |
| `exclude_uids` | string[] | `[]` | Client UIDs to leave in place |
| `exclude_channels` | integer[] | `[]` | Positive channel IDs to leave in place |

The example uses `CHANGE_ME` for credentials that you must replace. `serverlist` in an SSH ServerQuery session lists virtual servers. The TeamSpeak client's channel menu can show or copy a channel ID.

## Operation and troubleshooting

```bash
systemctl status ts6-afk-bot
sudo systemctl restart ts6-afk-bot
journalctl -u ts6-afk-bot -f
```

The bot selects a virtual server with `use sid=...` and verifies it with `whoami`. Each poll requests `clientlist -uid -times`. Idle times from ServerQuery are milliseconds; `timeout_seconds` is converted to milliseconds before comparison. Clients at or above the threshold are moved unless they are query clients, already in AFK, or excluded.

If the server rejects `-times`, the bot retries without that flag. If a client list entry omits its idle time, it requests `clientinfo` for that client. These cases require extra queries. Move and poke commands also add queries; the whole tick is not necessarily one command.

Authentication errors usually need a corrected query username/password or SSH ServerQuery configuration. Move permission errors need enough move power and channel access. If the bot moves no clients, check the idle values in `--verbose` logs, exclusions and the threshold.

Ctrl+C or SIGTERM requests shutdown. Polling and query reads check the shutdown flag. SSH connection setup has timeouts and can take longer to finish; a second signal forces interruption.

If systemd is unavailable, use `--no-service`.

## Remove an installation

```bash
sudo python3 install.py --uninstall
sudo python3 install.py --uninstall --purge
```

Purge requires matching `.ts6-afk-bot-install` markers. It removes an account only when the installer recorded creating it. Pre-existing accounts are kept. Install with this version before purging an older installation that has no markers. Repeat custom `--service-name`, `--install-dir` and `--user` options when uninstalling a custom instance.

## Development

```bash
python -m unittest discover -s tests -v
```

Tests use simulated query channels and temporary directories. They do not need a TeamSpeak server or a root installation. See [REVIEW.md](REVIEW.md) for the fixed defects and verification limits.

## License

MIT. See [LICENSE](LICENSE).
