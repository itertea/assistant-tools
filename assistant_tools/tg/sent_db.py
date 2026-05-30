"""Track messages sent by kit for edit/delete ownership checks."""
from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

from assistant_tools.tg.config import ResolvedTgConfig


def _db_path(config: ResolvedTgConfig) -> Path:
    return config.session_file.parent / f"{config.profile}_sent.db"


def _get_conn(config: ResolvedTgConfig) -> sqlite3.Connection:
    path = _db_path(config)
    conn = sqlite3.connect(str(path))
    conn.execute(
        "CREATE TABLE IF NOT EXISTS sent ("
        "  peer_id INTEGER, message_id INTEGER, ts REAL DEFAULT (unixepoch()),"
        "  PRIMARY KEY (peer_id, message_id)"
        ")"
    )
    return conn


def record_sent(config: ResolvedTgConfig, peer_id: int, message_id: int) -> None:
    conn = _get_conn(config)
    conn.execute(
        "INSERT OR IGNORE INTO sent (peer_id, message_id) VALUES (?, ?)",
        (peer_id, message_id),
    )
    conn.commit()
    conn.close()


def is_own_message(config: ResolvedTgConfig, peer_id: int, message_id: int) -> bool:
    conn = _get_conn(config)
    row = conn.execute(
        "SELECT 1 FROM sent WHERE peer_id = ? AND message_id = ?",
        (peer_id, message_id),
    ).fetchone()
    conn.close()
    return row is not None
