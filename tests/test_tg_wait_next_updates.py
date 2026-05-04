from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from assistant_tools.tg import commands
from assistant_tools.tg.config import ResolvedTgConfig


class _FakeMessage:
    def __init__(self, message_id: int, text: str, *, out: bool = False) -> None:
        self.id = message_id
        self.date = None
        self.sender = None
        self.text = text
        self.photo = None
        self.video = None
        self.document = None
        self.audio = None
        self.voice = None
        self.grouped_id = None
        self.reply_to = None
        self.out = out
        self.mentioned = False
        self.noforwards = False


class _FakeClient:
    def __init__(self) -> None:
        self.calls: int = 0

    async def get_me(self) -> Any:
        return SimpleNamespace(id=1)

    async def get_messages(self, entity: Any, limit: int) -> list[_FakeMessage]:
        self.calls += 1
        if self.calls == 1:
            return [_FakeMessage(122, "baseline")]
        return [_FakeMessage(123, "probe")]


def _config() -> ResolvedTgConfig:
    return ResolvedTgConfig(
        profile="default",
        api_id=1,
        api_hash="hash",
        session_file=Path("/tmp/test.session"),
        download_dir=Path("/tmp/downloads"),
        cache_dir=Path("/tmp/cache"),
        session_string=None,
        proxy=None,
        takeout=False,
        sleep_threshold=60,
        hide_password=False,
    )


async def _run_wait_next() -> None:
    fake_client = _FakeClient()
    commands_module: Any = commands

    @asynccontextmanager
    async def fake_telegram_client(config: ResolvedTgConfig):
        yield fake_client

    async def fake_resolve_peer_entity(client: Any, peer: str) -> Any:
        return SimpleNamespace(id=1, username="me")

    original_telegram_client = commands_module.telegram_client
    original_resolve_peer_entity = getattr(commands_module, "_resolve_peer_entity")
    try:
        setattr(commands_module, "telegram_client", fake_telegram_client)
        setattr(commands_module, "_resolve_peer_entity", fake_resolve_peer_entity)
        result = await commands.wait_next_message(_config(), "me", 1.0, False)
    finally:
        setattr(commands_module, "telegram_client", original_telegram_client)
        setattr(commands_module, "_resolve_peer_entity", original_resolve_peer_entity)

    assert result.ok is True
    assert result.data is not None
    assert result.data["message"]["message_id"] == 123
    assert result.data["message"]["text"] == "probe"


def test_wait_next_polls_for_messages_after_baseline() -> None:
    asyncio.run(_run_wait_next())
