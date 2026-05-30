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
from telethon.tl.types import InputPeerSelf

from assistant_tools.tg.client import make_client, telegram_client
from assistant_tools.tg.commands import (
    _get_peer_id,
    _resolve_peer_entity,
    normalize_message,
)
from assistant_tools.tg.config import ResolvedTgConfig
from assistant_tools.tg.normalize import normalize_chat


SOCKET_PATH = Path(tempfile.gettempdir()) / "kit-tg-daemon.sock"
IDLE_TIMEOUT = 600  # 10 minutes without requests → shutdown
_last_activity: float = 0.0

# Version = hash of this file, changes on every update
import hashlib as _hashlib
_DAEMON_VERSION = _hashlib.md5(Path(__file__).read_bytes()).hexdigest()[:8]


async def handle_client(reader: asyncio.StreamReader, writer: asyncio.StreamWriter, client: TelegramClient, config: ResolvedTgConfig) -> None:
    global _last_activity
    import time
    _last_activity = time.time()
    try:
        data = await reader.readline()
        request = json.loads(data.decode())
        cmd = request.get("cmd")
        result: dict[str, Any] = {"ok": False, "error": "unknown command"}

        if cmd == "ping":
            result = {"ok": True, "data": "pong", "version": _DAEMON_VERSION}

        elif cmd == "shutdown":
            # Graceful shutdown
            import signal
            os.kill(os.getpid(), signal.SIGTERM)
            result = {"ok": True, "data": "shutting down"}

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

        elif cmd == "ask":
            from assistant_tools.tg.sent_db import record_ask, get_last_ask, record_sent, is_own_message
            peer = request["peer"]
            text = request.get("text")
            timeout = request.get("timeout", 0)  # 0 = infinite
            session_id = request.get("session_id", "default")
            entity = await _resolve_peer_entity(client, peer)
            peer_id = await _get_peer_id(client, entity)

            # Check for unreads from previous ask in this session
            last_ask_id = get_last_ask(config, peer_id, session_id)
            baseline_id = last_ask_id

            # Send question if provided
            if text:
                # Format ask message with session tag and visual distinction
                session_tag = session_id.replace("/dev/pts/", "pts").replace("/", "_").replace("-", "_")
                formatted = f"❓ **#ask_{session_tag}**\n\n{text}"
                kwargs_ask: dict[str, Any] = {"parse_mode": "md"}
                sent_msg = await client.send_message(entity, formatted, **kwargs_ask)
                msg_id = int(getattr(sent_msg, "id", 0) or 0)
                if peer_id and msg_id:
                    record_sent(config, peer_id, msg_id)
                    record_ask(config, peer_id, msg_id, session_id)
                    baseline_id = msg_id

            # If no text and no previous ask — nothing to wait for
            if not text and baseline_id == 0:
                result = {"ok": True, "data": {"responses": []}}
                writer.write(json.dumps(result, ensure_ascii=False).encode())
                await writer.drain()
                writer.close()
                await writer.wait_closed()
                return

            def _is_user_reply(msg: Any, mid: int) -> bool:
                """Check if message is a user reply (not sent by kit)."""
                if mid <= baseline_id:
                    return False
                # Skip messages sent by kit
                if is_own_message(config, peer_id, mid):
                    return False
                return True

            # Collect any messages already there (sent between asks)
            responses: list[dict[str, Any]] = []
            messages_raw = await client.get_messages(entity, limit=50)
            for msg in reversed(list(messages_raw or [])):
                mid = int(getattr(msg, "id", 0) or 0)
                if _is_user_reply(msg, mid):
                    responses.append(normalize_message(msg, chat_entity=entity))

            if responses:
                # Auto-react to show user the agent read the message
                from telethon.tl.functions.messages import SendReactionRequest
                from telethon.tl.types import ReactionEmoji, InputPeerUser
                react_peer = await client.get_input_entity(await client.get_me())
                for resp in responses:
                    try:
                        mid = resp.get("message_id")
                        if mid:
                            await client(SendReactionRequest(
                                peer=react_peer,
                                msg_id=mid,
                                reaction=[ReactionEmoji(emoticon="👀")],
                            ))
                    except Exception as react_err:
                        import traceback
                        print(f"REACT ERROR: {react_err}", file=sys.stderr)
                        traceback.print_exc(file=sys.stderr)
                result = {"ok": True, "data": {"responses": responses}}
            else:
                # Wait for response (infinite if timeout=0)
                import time
                deadline = (time.time() + timeout) if timeout > 0 else None
                while True:
                    if deadline is not None and time.time() >= deadline:
                        break
                    await asyncio.sleep(1.5)
                    messages_raw = await client.get_messages(entity, limit=10)
                    for msg in reversed(list(messages_raw or [])):
                        mid = int(getattr(msg, "id", 0) or 0)
                        if _is_user_reply(msg, mid):
                            responses.append(normalize_message(msg, chat_entity=entity))
                            baseline_id = max(baseline_id, mid)
                    if responses:
                        break

                    # Auto-react
                    from telethon.tl.functions.messages import SendReactionRequest
                    from telethon.tl.types import ReactionEmoji, InputPeerUser
                    react_peer2 = await client.get_input_entity(await client.get_me())
                    for resp in responses:
                        try:
                            mid = resp.get("message_id")
                            if mid:
                                await client(SendReactionRequest(
                                    peer=react_peer2,
                                    msg_id=mid,
                                    reaction=[ReactionEmoji(emoticon="👀")],
                                ))
                        except Exception:
                            pass
                if responses:
                    result = {"ok": True, "data": {"responses": responses}}
                else:
                    result = {"ok": False, "error": f"No response within {timeout}s"}

        writer.write(json.dumps(result, ensure_ascii=False).encode())
        await writer.drain()
        writer.close()
        await writer.wait_closed()
        return
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

        async def _idle_watchdog() -> None:
            global _last_activity
            import time
            _last_activity = time.time()
            while True:
                await asyncio.sleep(30)
                if time.time() - _last_activity > IDLE_TIMEOUT:
                    server.close()
                    return

        async with server:
            watchdog = asyncio.create_task(_idle_watchdog())
            try:
                await server.serve_forever()
            except asyncio.CancelledError:
                pass
            finally:
                watchdog.cancel()
                SOCKET_PATH.unlink(missing_ok=True)


async def daemon_request(request: dict[str, Any]) -> dict[str, Any]:
    """Send a request to the running daemon and return the response."""
    if not SOCKET_PATH.exists():
        return {"ok": False, "error": "daemon not running (no socket)"}
    try:
        reader, writer = await asyncio.open_unix_connection(str(SOCKET_PATH))
        payload = json.dumps(request, ensure_ascii=False).encode() + b"\n"
        writer.write(payload)
        await writer.drain()
        # Read until daemon sends response (may take long for ask)
        data = await reader.read(1 << 20)
        writer.close()
        await writer.wait_closed()
        return json.loads(data.decode())
    except (ConnectionRefusedError, ConnectionResetError, OSError):
        SOCKET_PATH.unlink(missing_ok=True)
        return {"ok": False, "error": "daemon not running (no socket)"}


async def ensure_daemon(config: ResolvedTgConfig) -> None:
    """Ensure daemon is running. Start it as a background subprocess if not."""
    import subprocess
    import time

    # Check if socket exists and daemon responds
    if SOCKET_PATH.exists():
        try:
            reader, writer = await asyncio.open_unix_connection(str(SOCKET_PATH))
            writer.write(json.dumps({"cmd": "ping"}).encode() + b"\n")
            await writer.drain()
            writer.write_eof()
            data = await reader.read(4096)
            writer.close()
            await writer.wait_closed()
            resp = json.loads(data.decode())
            if resp.get("ok"):
                # Check version — restart if outdated
                if resp.get("version") == _DAEMON_VERSION:
                    return  # Daemon is alive and up-to-date
                # Kill outdated daemon
                try:
                    r2, w2 = await asyncio.open_unix_connection(str(SOCKET_PATH))
                    w2.write(json.dumps({"cmd": "shutdown"}).encode() + b"\n")
                    await w2.drain()
                    w2.close()
                    await w2.wait_closed()
                except Exception:
                    pass
                await asyncio.sleep(2)
                SOCKET_PATH.unlink(missing_ok=True)
        except (ConnectionRefusedError, ConnectionResetError, OSError):
            SOCKET_PATH.unlink(missing_ok=True)

    # Start daemon as background process
    env = os.environ.copy()
    subprocess.Popen(
        [sys.executable, "-m", "assistant_tools.tg.daemon_runner"],
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )

    # Wait for socket to appear
    for _ in range(30):  # 3 seconds max
        await asyncio.sleep(0.1)
        if SOCKET_PATH.exists():
            # Verify it responds
            try:
                reader, writer = await asyncio.open_unix_connection(str(SOCKET_PATH))
                writer.write(json.dumps({"cmd": "ping"}).encode() + b"\n")
                await writer.drain()
                writer.write_eof()
                data = await reader.read(4096)
                writer.close()
                await writer.wait_closed()
                if json.loads(data.decode()).get("ok"):
                    return
            except (ConnectionRefusedError, ConnectionResetError, OSError):
                continue

    # Daemon failed to start
    print("warning: kit tg daemon failed to start within 3s. Falling back to direct connection.", file=sys.stderr)
    print(f"  hint: try running 'TELEGRAM_API_ID=... kit tg daemon-start' manually to see errors", file=sys.stderr)
