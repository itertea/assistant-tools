from __future__ import annotations

from pathlib import Path
import json
import os
import sys
from typing import Any

from assistant_tools.models import CommandResult


class AssistantToolsError(Exception):
    def __init__(self, message: str, *, error_type: str, exit_code: int = 1) -> None:
        super().__init__(message)
        self.error_type: str = error_type
        self.exit_code: int = exit_code


def require_env(name: str) -> str:
    value: str | None = os.environ.get(name)
    if not value:
        raise AssistantToolsError(
            f"Missing required environment variable: {name}",
            error_type="missing_env",
            exit_code=3,
        )
    return value


def is_url(value: str) -> bool:
    return value.startswith("http://") or value.startswith("https://")


def ensure_path_exists(value: str) -> Path:
    path: Path = Path(value).expanduser()
    if not path.exists():
        raise AssistantToolsError(
            f"Input file does not exist: {path}",
            error_type="missing_file",
            exit_code=2,
        )
    return path


def emit_result(result: CommandResult) -> None:
    # Fields that are safe to omit when null (optional/noise fields)
    _OMIT_WHEN_NULL = {
        "media_type", "reply_to_message_id", "action", "error",
        "caption", "media_group_id", "link", "media",
        "has_protected_content", "mentioned", "outgoing",
    }

    def _strip_nulls(obj: Any, depth: int = 0) -> Any:
        if isinstance(obj, dict):
            return {
                k: _strip_nulls(v, depth + 1)
                for k, v in obj.items()
                if not (v is None and k in _OMIT_WHEN_NULL)
            }
        if isinstance(obj, list):
            return [_strip_nulls(i, depth + 1) for i in obj]
        return obj

    payload: dict[str, Any] = _strip_nulls({
        "ok": result.ok,
        "command": result.command,
        "provider": result.provider,
        "data": result.data,
        "error": result.error,
        "meta": result.meta,
    })
    json.dump(payload, sys.stdout, ensure_ascii=False)
    sys.stdout.write("\n")


def error_result(
    *,
    command: str,
    provider: str,
    error_type: str,
    message: str,
    meta: dict[str, Any],
) -> CommandResult:
    return CommandResult(
        ok=False,
        command=command,
        provider=provider,
        data=None,
        error={"type": error_type, "message": message},
        meta=meta,
    )
