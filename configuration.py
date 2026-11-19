"""Configuration validation shared by the runtime and installer."""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass, field, fields
from pathlib import Path

from i18n import DEFAULT_LANGUAGE, Translator

log = logging.getLogger("ts6-afk-bot")


class ConfigurationError(ValueError):
    def __init__(self, message: str, language: str) -> None:
        self.language = language
        super().__init__(message)


@dataclass
class Config:
    host: str
    afk_channel_id: int
    query_port: int = 10022
    username: str = "serveradmin"
    password: str = ""
    virtual_server_id: int = 1
    timeout_seconds: int = 300
    check_interval: int = 30
    poke_message: str | None = None
    exclude_uids: list[str] = field(default_factory=list)
    exclude_channels: list[int] = field(default_factory=list)
    reconnect_delay: int = 10
    language: str = DEFAULT_LANGUAGE

    def __post_init__(self) -> None:
        t = Translator(self.language)

        def fail(message: str, **values: object) -> None:
            raise ConfigurationError(t(message, **values), self.language)

        for name in ("host", "username", "password"):
            value = getattr(self, name)
            if not isinstance(value, str):
                fail("config: '{field}' must be a string.", field=name)
            if name != "password" and not value.strip():
                fail("config: '{field}' must not be empty.", field=name)

        self.host = self.host.strip()
        self.username = self.username.strip()
        if self.poke_message is not None and not isinstance(self.poke_message, str):
            fail("config: 'poke_message' must be a string or null.")

        minimums = {
            "query_port": 1,
            "virtual_server_id": 1,
            "afk_channel_id": 1,
            "timeout_seconds": 10,
            "check_interval": 1,
            "reconnect_delay": 0,
        }
        for name, minimum in minimums.items():
            value = getattr(self, name)
            if type(value) is not int:
                fail("config: '{field}' must be an integer.", field=name)
            if value < minimum:
                fail("config: '{field}' must be at least {minimum}.", field=name, minimum=minimum)
        if self.query_port > 65535:
            fail("config: 'query_port' must be between 1 and 65535.")

        if not isinstance(self.exclude_uids, list) or any(
            not isinstance(uid, str) or not uid.strip() for uid in self.exclude_uids
        ):
            fail("config: 'exclude_uids' must be a list of nonempty strings.")
        if not isinstance(self.exclude_channels, list) or any(
            type(cid) is not int or cid < 1 for cid in self.exclude_channels
        ):
            fail("config: 'exclude_channels' must be a list of positive integers.")

    @classmethod
    def load(cls, path: Path, language: str | None = None) -> Config:
        selected = language if language is not None else os.environ.get("TS6_LANGUAGE")
        t = Translator(selected if selected is not None else DEFAULT_LANGUAGE)
        if not path.is_file():
            raise FileNotFoundError(t("Configuration file not found: {path}", path=path))
        with path.open(encoding="utf-8") as handle:
            data = json.load(handle)
        if not isinstance(data, dict):
            raise ConfigurationError(t("Configuration must be a JSON object."), t.language)

        if selected is None:
            selected = data.get("language", DEFAULT_LANGUAGE)
        t = Translator(selected)
        data["language"] = selected
        # An explicitly empty environment variable also overrides a JSON password.
        env_password = os.environ.get("TS6_QUERY_PASSWORD")
        if env_password is not None:
            data["password"] = env_password

        known = {item.name for item in fields(cls)}
        unknown = set(data) - known
        if unknown:
            log.warning(t("Unknown configuration keys (ignored): %s"), ", ".join(sorted(unknown)))
        missing = {"host", "afk_channel_id"} - set(data)
        if missing:
            raise ConfigurationError(
                t(
                    "Missing required configuration fields: {fields}",
                    fields=", ".join(sorted(missing)),
                ),
                selected,
            )
        return cls(**{name: value for name, value in data.items() if name in known})

    def effective_poke_message(self) -> str:
        if self.poke_message is None:
            return Translator(self.language)(
                "You were moved to the AFK channel after {seconds} seconds of inactivity.",
                seconds=self.timeout_seconds,
            )
        return self.poke_message
