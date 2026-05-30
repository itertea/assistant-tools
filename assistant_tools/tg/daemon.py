"""Telegram daemon — single persistent connection, serves commands via unix socket."""
from __future__ import annotations

import asyncio
import json
import os
import sys
import tempfile
from pathlib import Path
from typing import Any

from telethon import TelegramClient

from assistant_tools.tg.client import make_client, telegram_client
from assistant_tools.tg.commands import (
    _get_peer_id,
    _resolve_peer_entity,
    normalize_message,
)
from assistant_tools.tg.config import ResolvedTgConfig
from assistant_tools.tg.normalize import normalize_chat


SOCKET_PATH = Path(tempfile.gettempdir()) / "kit-tg-daemon.sock"


async def handle_client(reader: asyncio.StreamReader, writer: asyncio.StreamWriter, client: TelegramClient, config: ResolvedTgConfig) -> None:
    try:
        data = await reader.read(65536)
        request = json.loads(data.decode())
        cmd = request.get("cmd")
        result: dict[str, Any] = {"ok": False, "error": "unknown command"}

        if cmd == "ping":
            result = {"ok": True, "data": "pong"}

        elif cmd == "history":
            peer = request["peer"]
            limit = request.get("limit", 10)
            entity = await _resolve_peer_entity(client, peer)
            items = []
            async for message in client.iter_messages(entity, limit=limit):
                items.append(normalize_message(message, chat_entity=entity, full=request.get("full", False)))
            result = {"ok": True, "data": {"items": items}}

        elif cmd == "send":
            peer = request["peer"]
            text = request["text"]
            entity = await _resolve_peer_entity(client, peer)
            kwargs: dict[str, Any] = {}
            if request.get("reply_to"):
                kwargs["reply_to"] = request["reply_to"]
            if request.get("parse_mode"):
                kwargs["parse_mode"] = request["parse_mode"]
            message = await client.send_message(entity, text, **kwargs)
            # Track sent message
            from assistant_tools.tg.sent_db import record_sent
            peer_id = await _get_peer_id(client, entity)
            msg_id = int(getattr(message, "id", 0) or 0)
            if peer_id and msg_id:
                record_sent(config, peer_id, msg_id)
            result = {"ok": True, "data": {"message": normalize_message(message, chat_entity=entity)}}

        elif cmd == "find_dialog":
            from telethon.tl.functions.contacts import SearchRequest
            query = request["query"]
            limit = request.get("limit", 20)
            r = await client(SearchRequest(q=query, limit=limit))
            matches = []
            for user in (r.users or []):
                matches.append({"type": "user", "chat": normalize_chat(user)})
            for chat in (r.chats or []):
                matches.append({"type": "chat", "chat": normalize_chat(chat)})
            result = {"ok": True, "data": {"matches": matches}}

        elif cmd == "get":
            peer = request["peer"]
            message_ids = request["message_ids"]
            entity = await _resolve_peer_entity(client, peer)
            messages = await client.get_messages(entity, ids=message_ids)
            items = []
            for msg in (messages if isinstance(messages, list) else [messages]):
                if msg:
                    items.append(normalize_message(msg, chat_entity=entity, full=request.get("full", False)))
            result = {"ok": True, "data": {"items": items}}

        elif cmd == "forward":
            from_entity = await _resolve_peer_entity(client, request["from_peer"])
            to_entity = await _resolve_peer_entity(client, request["to_peer"])
            messages = await client.forward_messages(to_entity, request["message_ids"], from_entity)
            items = []
            for msg in (messages if isinstance(messages, list) else [messages]):
                if msg:
                    items.append(normalize_message(msg, chat_entity=to_entity))
            result = {"ok": True, "data": {"messages": items}}

        elif cmd == "edit":
            peer = request["peer"]
            entity = await _resolve_peer_entity(client, peer)
            kwargs = {}
            if request.get("parse_mode"):
                kwargs["parse_mode"] = request["parse_mode"]
            message = await client.edit_message(entity, request["message_id"], request["text"], **kwargs)
            result = {"ok": True, "data": {"message": normalize_message(message, chat_entity=entity)}}

        elif cmd == "delete":
            peer = request["peer"]
            entity = await _resolve_peer_entity(client, peer)
            await client.delete_messages(entity, request["message_ids"])
            result = {"ok": True, "data": {"deleted": request["message_ids"]}}

        writer.write(json.dumps(result, ensure_ascii=False).encode())
        await writer.drain()
    except Exception as e:
        try:
            writer.write(json.dumps({"ok": False, "error": str(e)}, ensure_ascii=False).encode())
            await writer.drain()
        except Exception:
            pass
    finally:
        writer.close()
        await writer.wait_closed()


async def run_daemon(config: ResolvedTgConfig) -> None:
    import shutil

    # Remove stale socket
    if SOCKET_PATH.exists():
        SOCKET_PATH.unlink()

    # Copy session file so daemon doesn't lock the main one
    daemon_session = config.session_file.parent / f"{config.profile}_daemon.session"
    shutil.copy2(str(config.session_file), str(daemon_session))

    # Create a modified config with daemon session
    from dataclasses import replace
    daemon_config = replace(config, session_file=daemon_session)

    async with telegram_client(daemon_config) as client:
        me = await client.get_me()
        print(f"kit tg daemon started (user: {me.first_name}, id: {me.id})", file=sys.stderr)
        print(f"socket: {SOCKET_PATH}", file=sys.stderr)

        server = await asyncio.start_unix_server(
            lambda r, w: handle_client(r, w, client, config),
            path=str(SOCKET_PATH),
        )
        os.chmod(str(SOCKET_PATH), 0o600)

        async with server:
            await server.serve_forever()


async def daemon_request(request: dict[str, Any]) -> dict[str, Any]:
    """Send a request to the running daemon and return the response."""
    if not SOCKET_PATH.exists():
        return {"ok": False, "error": "daemon not running (no socket)"}
    reader, writer = await asyncio.open_unix_connection(str(SOCKET_PATH))
    writer.write(json.dumps(request, ensure_ascii=False).encode())
    await writer.drain()
    writer.write_eof()
    data = await reader.read(1 << 20)
    writer.close()
    await writer.wait_closed()
    return json.loads(data.decode())
