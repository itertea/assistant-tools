# pyright: reportMissingTypeStubs=false

from __future__ import annotations

from datetime import datetime
from typing import Any

from telethon.tl.custom.dialog import Dialog
from telethon.tl.custom.message import Message
from telethon.tl.types import Channel
from telethon.tl.types import Chat
from telethon.tl.types import DocumentAttributeAudio
from telethon.tl.types import DocumentAttributeFilename
from telethon.tl.types import DocumentAttributeSticker
from telethon.tl.types import DocumentAttributeVideo
from telethon.tl.types import User
from telethon.tl.types import MessageActionChatAddUser
from telethon.tl.types import MessageActionChatCreate
from telethon.tl.types import MessageActionChatDeleteUser
from telethon.tl.types import MessageActionChatJoinedByLink
from telethon.tl.types import MessageActionChatJoinedByRequest
from telethon.tl.types import MessageActionChatEditTitle
from telethon.tl.types import MessageActionChatEditPhoto
from telethon.tl.types import MessageActionChatDeletePhoto
from telethon.tl.types import MessageActionPinMessage
from telethon.tl.types import MessageActionChannelCreate

from assistant_tools.tg.client import build_message_link


def _action_type(message: Message) -> str | None:
    """Return a short action type string if the message is a service/action message."""
    action: Any = getattr(message, "action", None)
    if action is None:
        return None
    if isinstance(action, (MessageActionChatAddUser, MessageActionChatJoinedByLink, MessageActionChatJoinedByRequest)):
        return "join"
    if isinstance(action, MessageActionChatDeleteUser):
        return "leave"
    if isinstance(action, (MessageActionChatCreate, MessageActionChannelCreate)):
        return "create_chat"
    if isinstance(action, MessageActionChatEditTitle):
        return "edit_title"
    if isinstance(action, (MessageActionChatEditPhoto, MessageActionChatDeletePhoto)):
        return "edit_photo"
    if isinstance(action, MessageActionPinMessage):
        return "pin_message"
    # Fallback: return class name without prefix
    cls_name: str = type(action).__name__
    if cls_name.startswith("MessageAction"):
        return cls_name[len("MessageAction"):].lower()
    return "unknown_action"


def _action_text(message: Message) -> str | None:
    """Generate a human-readable text for action messages."""
    action: Any = getattr(message, "action", None)
    if action is None:
        return None
    action_type = _action_type(message)
    sender: Any = getattr(message, "sender", None)
    sender_name: str = ""
    if sender:
        first = getattr(sender, "first_name", "") or ""
        last = getattr(sender, "last_name", "") or ""
        sender_name = " ".join(p for p in [first, last] if p).strip() or getattr(sender, "username", "") or "?"

    if action_type == "join":
        if isinstance(action, MessageActionChatAddUser):
            return f"{sender_name} added users to the chat"
        return f"{sender_name} joined the chat"
    if action_type == "leave":
        return f"{sender_name} left the chat"
    if action_type == "create_chat":
        return f"{sender_name} created the chat"
    if action_type == "edit_title":
        new_title = getattr(action, "title", "")
        return f"{sender_name} changed the chat title to \"{new_title}\""
    if action_type == "pin_message":
        return f"{sender_name} pinned a message"
    if action_type == "edit_photo":
        return f"{sender_name} changed the chat photo"
    return f"[action: {action_type}]"


def iso_datetime(value: datetime | None) -> str | None:
    if value is None:
        return None
    return value.astimezone().isoformat()


def _chat_type(entity: Any) -> str | None:
    if isinstance(entity, User):
        return "bot" if entity.bot else "private"
    if isinstance(entity, Channel):
        return "supergroup" if entity.megagroup else "channel"
    if isinstance(entity, Chat):
        return "group"
    return None


def _chat_title(entity: Any) -> str | None:
    if isinstance(entity, User):
        first_name: str = entity.first_name or ""
        last_name: str = entity.last_name or ""
        title: str = " ".join(part for part in [first_name, last_name] if part).strip()
        return title or entity.username
    return getattr(entity, "title", None)


def normalize_chat(entity: Any) -> dict[str, Any]:
    if entity is None:
        return {}
    return {
        "id": getattr(entity, "id", None),
        "type": _chat_type(entity),
        "title": _chat_title(entity),
        "username": getattr(entity, "username", None),
        "is_forum": bool(getattr(entity, "forum", False)),
    }


def normalize_user(user: Any) -> dict[str, Any] | None:
    if user is None:
        return None
    first_name: str = str(getattr(user, "first_name", "") or "")
    last_name: str = str(getattr(user, "last_name", "") or "")
    display_name: str = " ".join(part for part in [first_name, last_name] if part).strip()
    return {
        "id": getattr(user, "id", None),
        "username": getattr(user, "username", None),
        "first_name": first_name or None,
        "last_name": last_name or None,
        "display_name": display_name or getattr(user, "username", None),
        "is_bot": bool(getattr(user, "bot", False)),
    }


def compact_user(user: Any) -> dict[str, Any] | None:
    normalized: dict[str, Any] | None = normalize_user(user)
    if normalized is None:
        return None
    return {
        "id": normalized["id"],
        "username": normalized["username"],
        "display_name": normalized["display_name"],
    }


def _document_file_name(document: Any) -> str | None:
    attributes: list[Any] = list(getattr(document, "attributes", []) or [])
    for attr in attributes:
        if isinstance(attr, DocumentAttributeFilename):
            return attr.file_name
    return None


def _media_kind(message: Message) -> str | None:
    if message.photo is not None:
        return "photo"
    if message.video is not None:
        return "video"
    if message.document is not None:
        attributes: list[Any] = list(getattr(message.document, "attributes", []) or [])
        for attr in attributes:
            if isinstance(attr, DocumentAttributeSticker):
                return "sticker"
            if isinstance(attr, DocumentAttributeAudio):
                return "voice" if getattr(attr, "voice", False) else "audio"
            if isinstance(attr, DocumentAttributeVideo):
                return "video_note" if getattr(attr, "round_message", False) else "video"
        mime_type: str | None = getattr(message.document, "mime_type", None)
        if mime_type == "image/gif":
            return "animation"
        return "document"
    return None


def normalize_media(message: Message) -> dict[str, Any] | None:
    kind: str | None = _media_kind(message)
    if kind is None:
        return None

    media_obj: Any = (
        message.photo or message.video or message.document or message.audio or message.voice
    )
    if media_obj is None:
        media_obj = message.file

    document: Any = message.document
    file_name: str | None = _document_file_name(document) if document is not None else None
    width: int | None = None
    height: int | None = None
    if message.photo is not None and getattr(message.photo, "sizes", None):
        for size in reversed(list(getattr(message.photo, "sizes", []) or [])):
            size_width: Any = getattr(size, "w", None)
            size_height: Any = getattr(size, "h", None)
            if size_width is not None and size_height is not None:
                width = int(size_width)
                height = int(size_height)
                break
    duration: int | None = None
    if document is not None:
        for attr in list(getattr(document, "attributes", []) or []):
            if isinstance(attr, DocumentAttributeVideo):
                duration = int(getattr(attr, "duration", 0) or 0) or None
                width = int(getattr(attr, "w", 0) or 0) or width
                height = int(getattr(attr, "h", 0) or 0) or height
            if isinstance(attr, DocumentAttributeAudio):
                duration = int(getattr(attr, "duration", 0) or 0) or duration

    return {
        "kind": kind,
        "file_id": None,
        "file_unique_id": None,
        "file_name": file_name,
        "mime_type": getattr(document, "mime_type", None) if document is not None else None,
        "file_size": getattr(media_obj, "size", None),
        "width": width,
        "height": height,
        "duration": duration,
        "has_spoiler": False,
        "has_protected_content": bool(getattr(message, "noforwards", False)),
    }


def _excerpt_text(text: str | None, max_chars: int = 220) -> str | None:
    if text is None:
        return None
    collapsed: str = " ".join(text.split())
    if len(collapsed) <= max_chars:
        return collapsed
    return collapsed[: max_chars - 1] + "…"


def _extract_reactions(message: Message) -> list[dict[str, Any]] | None:
    """Extract reactions with who reacted."""
    reactions = getattr(message, "reactions", None)
    if not reactions:
        return None
    results = getattr(reactions, "results", None)
    if not results:
        return None
    items: list[dict[str, Any]] = []
    recent = {getattr(getattr(r, "reaction", None), "emoticon", ""): getattr(getattr(r, "peer_id", None), "user_id", None) for r in (getattr(reactions, "recent_reactions", None) or [])}
    for r in results:
        reaction = getattr(r, "reaction", None)
        emoticon = getattr(reaction, "emoticon", None)
        if emoticon:
            entry: dict[str, Any] = {"emoji": emoticon, "count": getattr(r, "count", 1)}
            if recent.get(emoticon):
                entry["user_id"] = recent[emoticon]
            items.append(entry)
    return items or None


def normalize_message(
    message: Message, *, chat_entity: Any | None = None, full: bool = False
) -> dict[str, Any]:
    chat: Any = chat_entity or getattr(message, "chat", None)
    sender: Any = getattr(message, "sender", None)
    chat_id: int | None = getattr(chat, "id", None)
    username: str | None = getattr(chat, "username", None)
    message_id: int | None = getattr(message, "id", None)
    action_type: str | None = _action_type(message)
    action_text: str | None = _action_text(message) if action_type else None
    text: str | None = getattr(message, "text", None) or action_text

    if not full:
        # For history/get/search we keep full text by default.
        # Truncation/excerpts should happen in higher-level renderers (e.g. Pi tools),
        # or via explicit flags.
        reply_to_message_id: int | None = getattr(
            getattr(message, "reply_to", None), "reply_to_msg_id", None
        )
        result: dict[str, Any] = {
            "message_id": message_id,
            "date": iso_datetime(getattr(message, "date", None)),
            "from": compact_user(sender),
            "text": text,
            "media_type": _media_kind(message),
            "reply_to_message_id": reply_to_message_id,
        }
        if action_type:
            result["action"] = action_type
        reactions = _extract_reactions(message)
        if reactions:
            result["reactions"] = reactions
        return result
    result = {
        "chat": normalize_chat(chat),
        "message_id": message_id,
        "date": iso_datetime(getattr(message, "date", None)),
        "from": normalize_user(sender),
        "text": text,
        "caption": getattr(message, "text", None),
        "media_type": _media_kind(message),
        "media_group_id": getattr(message, "grouped_id", None),
        "reply_to_message_id": getattr(getattr(message, "reply_to", None), "reply_to_msg_id", None),
        "outgoing": bool(getattr(message, "out", False)),
        "mentioned": bool(getattr(message, "mentioned", False)),
        "has_protected_content": bool(getattr(message, "noforwards", False)),
        "link": build_message_link(chat_id, username, message_id),
        "media": normalize_media(message),
    }
    if action_type:
        result["action"] = action_type
    return result


def normalize_dialog(dialog: Dialog, *, full: bool = False) -> dict[str, Any]:
    top_message: Any = getattr(dialog, "message", None)
    chat_entity: Any = getattr(dialog, "entity", None)
    if full:
        return {
            "chat": normalize_chat(chat_entity),
            "top_message": normalize_message(top_message, chat_entity=chat_entity, full=True)
            if top_message is not None
            else None,
            "unread_count": getattr(dialog, "unread_count", None),
            "unread_mentions_count": getattr(dialog, "unread_mentions_count", None),
        }
    last_message_text: str | None = None
    last_message_media_type: str | None = None
    last_message_date: str | None = None
    last_message_id: int | None = None
    if top_message is not None:
        last_message_text = _excerpt_text(getattr(top_message, "text", None))
        last_message_media_type = _media_kind(top_message)
        last_message_date = iso_datetime(getattr(top_message, "date", None))
        last_message_id = getattr(top_message, "id", None)
    return {
        "chat": normalize_chat(chat_entity),
        "last_message": {
            "message_id": last_message_id,
            "date": last_message_date,
            "text": last_message_text,
            "media_type": last_message_media_type,
        }
        if top_message is not None
        else None,
        "unread_count": getattr(dialog, "unread_count", None),
        "unread_mentions_count": getattr(dialog, "unread_mentions_count", None),
    }
