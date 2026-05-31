# Kit (assistant-tools) — Agent Rules

## Why Daemon

Daemon exists because Telegram session (.session file) is a SINGLE-WRITER resource. If two processes open it simultaneously — corruption, auth conflicts, flood-wait. Daemon serializes all access through one persistent connection.

## Daemon First Rule

ALL Telegram commands go through the daemon. Never create direct Telethon connections from CLI dispatch. If a command isn't in daemon yet — add it there, don't bypass.

## No Python Pipe Rule

Never pipe kit JSON output through python/jq. Read JSON directly — it's the intended format for agents.

## Test After Install Rule

After changing daemon code, ALWAYS:
1. `pkill -9 -f daemon; rm -f /tmp/kit-tg-daemon.sock`
2. `uv tool install --force ...`
3. Then test

The daemon runs from the INSTALLED binary, not from `uv run`.

## Session ID

Session isolation uses KIT_SESSION_ID env var → tty name → cwd hash. One agent = one session ID.
