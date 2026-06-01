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

def _compute_version() -> str:
    """Hash all package .py files to detect any code change."""
    pkg_dir = Path(__file__).parent.parent
    h = _hashlib.md5()
    for f in sorted(pkg_dir.rglob("*.py")):
        h.update(f.read_bytes())
    return h.hexdigest()[:8]

_DAEMON_VERSION = _compute_version()


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

        elif cmd == "whoami":
            me = await client.get_me()
            result = {"ok": True, "data": {"id": me.id, "username": me.username, "first_name": me.first_name}}

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

        elif cmd == "send_file":
            from assistant_tools.tg.sent_db import record_sent
            peer = request["peer"]
            path = request["path"]
            entity = await _resolve_peer_entity(client, peer)
            message = await client.send_file(
                entity, path, caption=request.get("caption"),
                reply_to=request.get("reply_to"), force_document=True,
            )
            peer_id = await _get_peer_id(client, entity)
            msg_id = int(getattr(message, "id", 0) or 0)
            if peer_id and msg_id:
                record_sent(config, peer_id, msg_id)
            result = {"ok": True, "data": {"message": normalize_message(message, chat_entity=entity)}}

        elif cmd == "send_photo":
            from assistant_tools.tg.sent_db import record_sent
            peer = request["peer"]
            path = request["path"]
            entity = await _resolve_peer_entity(client, peer)

            # Add silent audio to videos without sound (prevents gif conversion)
            upload_path = path
            tmp_path = None
            if path.lower().endswith((".mp4", ".mkv", ".avi", ".mov", ".webm")):
                import subprocess, tempfile
                from imageio_ffmpeg import get_ffmpeg_exe
                ffmpeg = get_ffmpeg_exe()
                probe = subprocess.run([ffmpeg, "-i", path], capture_output=True, text=True, timeout=30)
                if "Audio:" not in probe.stderr:
                    tmp = tempfile.NamedTemporaryFile(suffix=f"_{os.path.basename(path)}", delete=False)
                    tmp.close()
                    r = subprocess.run(
                        [ffmpeg, "-y", "-i", path, "-f", "lavfi", "-i", "anullsrc=r=44100:cl=mono",
                         "-c:v", "copy", "-c:a", "aac", "-shortest", tmp.name],
                        capture_output=True, timeout=120,
                    )
                    if r.returncode == 0:
                        upload_path = tmp.name
                        tmp_path = tmp.name
                    else:
                        os.unlink(tmp.name)

            message = await client.send_file(
                entity, upload_path, caption=request.get("caption"),
                reply_to=request.get("reply_to"), force_document=False,
                supports_streaming=True,
            )
            if tmp_path:
                os.unlink(tmp_path)
            peer_id = await _get_peer_id(client, entity)
            msg_id = int(getattr(message, "id", 0) or 0)
            if peer_id and msg_id:
                record_sent(config, peer_id, msg_id)
            result = {"ok": True, "data": {"message": normalize_message(message, chat_entity=entity)}}

        elif cmd == "send_album":
            from assistant_tools.tg.sent_db import record_sent
            peer = request["peer"]
            paths = request["paths"]
            entity = await _resolve_peer_entity(client, peer)

            # Add silent audio to videos without sound
            upload_paths: list[str] = []
            temp_files: list[str] = []
            for p in paths:
                if p.lower().endswith((".mp4", ".mkv", ".avi", ".mov", ".webm")):
                    import subprocess, tempfile
                    from imageio_ffmpeg import get_ffmpeg_exe
                    ffmpeg = get_ffmpeg_exe()
                    probe = subprocess.run([ffmpeg, "-i", p], capture_output=True, text=True, timeout=30)
                    if "Audio:" not in probe.stderr:
                        tmp = tempfile.NamedTemporaryFile(suffix=f"_{os.path.basename(p)}", delete=False)
                        tmp.close()
                        r = subprocess.run(
                            [ffmpeg, "-y", "-i", p, "-f", "lavfi", "-i", "anullsrc=r=44100:cl=mono",
                             "-c:v", "copy", "-c:a", "aac", "-shortest", tmp.name],
                            capture_output=True, timeout=120,
                        )
                        if r.returncode == 0:
                            upload_paths.append(tmp.name)
                            temp_files.append(tmp.name)
                        else:
                            os.unlink(tmp.name)
                            upload_paths.append(p)
                        continue
                upload_paths.append(p)

            messages = await client.send_file(
                entity, upload_paths, caption=request.get("caption"),
                reply_to=request.get("reply_to"),
            )
            peer_id = await _get_peer_id(client, entity)
            items = []
            for msg in (messages if isinstance(messages, list) else [messages]):
                msg_id = int(getattr(msg, "id", 0) or 0)
                if peer_id and msg_id:
                    record_sent(config, peer_id, msg_id)
                items.append(normalize_message(msg, chat_entity=entity))
            for tf in temp_files:
                os.unlink(tf)
            result = {"ok": True, "data": {"messages": items}}

        elif cmd == "send_voice":
            from assistant_tools.tg.sent_db import record_sent
            peer = request["peer"]
            path = request["path"]
            entity = await _resolve_peer_entity(client, peer)
            import mimetypes
            message = await client.send_file(
                entity, path, caption=request.get("caption"),
                reply_to=request.get("reply_to"), voice_note=True,
            )
            peer_id = await _get_peer_id(client, entity)
            msg_id = int(getattr(message, "id", 0) or 0)
            if peer_id and msg_id:
                record_sent(config, peer_id, msg_id)
            result = {"ok": True, "data": {"message": normalize_message(message, chat_entity=entity)}}

        elif cmd == "resolve":
            entity = await client.get_entity(request["peer"])
            result = {"ok": True, "data": {"chat": normalize_chat(entity)}}

        elif cmd == "dialogs":
            from assistant_tools.tg.normalize import normalize_dialog
            limit = request.get("limit", 20)
            full = request.get("full", False)
            items = []
            async for dialog in client.iter_dialogs(limit=limit):
                items.append(normalize_dialog(dialog, full=full))
            result = {"ok": True, "data": {"items": items}}

        elif cmd == "participants":
            from assistant_tools.tg.normalize import normalize_user
            peer = request["peer"]
            limit = request.get("limit", 100)
            entity = await _resolve_peer_entity(client, peer)
            participants = await client.get_participants(entity, limit=limit)
            items = [normalize_user(p) for p in participants]
            result = {"ok": True, "data": {"items": items}}

        elif cmd == "react":
            from telethon.tl.functions.messages import SendReactionRequest
            from telethon.tl.types import ReactionEmoji
            peer = request["peer"]
            message_id = request["message_id"]
            emoji = request.get("emoji", "👍")
            entity = await _resolve_peer_entity(client, peer)
            react_peer = await client.get_input_entity(entity)
            await client(SendReactionRequest(
                peer=react_peer, msg_id=message_id,
                reaction=[ReactionEmoji(emoticon=emoji)],
            ))
            result = {"ok": True, "data": {"reacted": True, "emoji": emoji, "message_id": message_id}}

        elif cmd == "search":
            peer = request["peer"]
            query = request["query"]
            limit = request.get("limit", 20)
            full = request.get("full", False)
            entity = await _resolve_peer_entity(client, peer)
            messages = await client.get_messages(entity, search=query, limit=limit)
            items = [normalize_message(m, chat_entity=entity, full=full) for m in (messages or [])]
            result = {"ok": True, "data": {"items": items}}

        elif cmd == "wait_next":
            peer = request["peer"]
            peers = request.get("peers", [peer] if peer else [])
            timeout = request.get("timeout", 0)
            full = request.get("full", False)
            import time
            me = await client.get_me()
            # Resolve peers and baselines
            peer_data = []
            for p in peers:
                entity = await _resolve_peer_entity(client, p)
                is_self = (getattr(entity, "id", None) == getattr(me, "id", None))
                latest = await client.get_messages(entity, limit=1)
                baseline_id = int(getattr(latest[0], "id", 0)) if latest else 0
                peer_data.append({"peer": p, "entity": entity, "is_self": is_self, "baseline_id": baseline_id})
            deadline = (time.time() + timeout) if timeout > 0 else None
            found = None
            while found is None:
                if deadline and time.time() >= deadline:
                    break
                for pd in peer_data:
                    msgs = await client.get_messages(pd["entity"], limit=50)
                    for msg in (msgs or []):
                        mid = int(getattr(msg, "id", 0) or 0)
                        if mid <= pd["baseline_id"]:
                            continue
                        if not pd["is_self"] and getattr(msg, "out", False):
                            continue
                        found = {"message": normalize_message(msg, chat_entity=pd["entity"], full=full)}
                        break
                    if found:
                        break
                if not found:
                    await asyncio.sleep(1.5)
            if found:
                result = {"ok": True, "data": found}
            else:
                result = {"ok": False, "error": f"Timed out waiting for the next incoming message after {timeout} seconds"}

        elif cmd == "media_download":
            peer = request["peer"]
            message_id = request["message_id"]
            output_dir = request.get("output_dir") or str(config.download_dir)
            full = request.get("full", False)
            from pathlib import Path
            target_dir = Path(output_dir).expanduser()
            target_dir.mkdir(parents=True, exist_ok=True)
            entity = await _resolve_peer_entity(client, peer)
            message = await client.get_messages(entity, ids=message_id)
            if not message:
                result = {"ok": False, "error": "Message not found"}
            elif not message.media:
                result = {"ok": False, "error": "Message has no media"}
            else:
                dl_path = await client.download_media(message, file=str(target_dir) + "/")
                result = {"ok": True, "data": {"path": dl_path, "message_id": message_id}}

        elif cmd == "copy":
            source_peer = request["source_peer"]
            message_id = request["message_id"]
            target_peer = request["target_peer"]
            full = request.get("full", False)
            source_entity = await _resolve_peer_entity(client, source_peer)
            target_entity = await _resolve_peer_entity(client, target_peer)
            message = await client.forward_messages(target_entity, message_id, source_entity)
            msg = message[0] if isinstance(message, list) else message
            result = {"ok": True, "data": {"message": normalize_message(msg, chat_entity=target_entity, full=full)}}

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

            def _is_user_reply(msg: Any, mid: int) -> bool:
                """Check if message is a user reply (not sent by kit)."""
                if mid <= baseline_id:
                    return False
                if is_own_message(config, peer_id, mid):
                    return False
                return True

            # FIRST: collect any pending messages BEFORE sending ask
            responses: list[dict[str, Any]] = []
            messages_raw = await client.get_messages(entity, limit=50)
            for msg in reversed(list(messages_raw or [])):
                mid = int(getattr(msg, "id", 0) or 0)
                if _is_user_reply(msg, mid):
                    responses.append(normalize_message(msg, chat_entity=entity))

            # Send question ONLY if no pending messages
            if text and not responses:
                session_tag = session_id.replace("/dev/pts/", "pts").replace("/", "_").replace("-", "_")
                formatted = f"❓ **#ask_{session_tag}**\n\n{text}"
                kwargs_ask: dict[str, Any] = {}
                sent_msg = await client.send_message(entity, formatted, **kwargs_ask)
                msg_id = int(getattr(sent_msg, "id", 0) or 0)
                if peer_id and msg_id:
                    record_sent(config, peer_id, msg_id)
                    record_ask(config, peer_id, msg_id, session_id)
            elif responses and peer_id:
                # Have pending responses — update baseline, don't send question
                max_seen = max(r.get("message_id", 0) for r in responses)
                if max_seen:
                    record_ask(config, peer_id, max_seen, session_id)

            # If no text and no previous ask — nothing to wait for
            if not text and baseline_id == 0:
                result = {"ok": True, "data": {"responses": []}}
            elif responses:
                # Auto-react to show user the agent read the message
                from telethon.tl.functions.messages import SendReactionRequest
                from telethon.tl.types import ReactionEmoji, InputPeerUser
                react_peer = await client.get_input_entity(entity)
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
                    await asyncio.sleep(3)
                    try:
                        messages_raw = await client.get_messages(entity, limit=10)
                    except Exception:
                        await asyncio.sleep(5)
                        continue
                    for msg in reversed(list(messages_raw or [])):
                        mid = int(getattr(msg, "id", 0) or 0)
                        if _is_user_reply(msg, mid):
                            responses.append(normalize_message(msg, chat_entity=entity))
                            baseline_id = max(baseline_id, mid)
                    if responses:
                        break

                # Auto-react after getting responses from wait loop
                if responses:
                    from telethon.tl.functions.messages import SendReactionRequest as SR2
                    from telethon.tl.types import ReactionEmoji as RE2
                    react_peer2 = await client.get_input_entity(entity)
                    for resp in responses:
                        try:
                            mid = resp.get("message_id")
                            if mid:
                                await client(SR2(peer=react_peer2, msg_id=mid, reaction=[RE2(emoticon="👀")]))
                        except Exception:
                            pass

                if responses:
                    # Update baseline so next ask doesn't re-read these
                    max_resp_id = max(r.get("message_id", 0) for r in responses)
                    if peer_id and max_resp_id:
                        record_ask(config, peer_id, max_resp_id, session_id)
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
        if not data:
            return {"ok": False, "error": "daemon closed connection without response"}
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
        stderr=open("/tmp/kit-daemon.log","a"),
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
