# Kit (assistant-tools) — Agent Rules

## Daemon First Rule

ALL Telegram commands go through the daemon. Never create direct Telethon connections from CLI dispatch. If a command isn't in daemon yet — add it there, don't bypass.

## No Python Pipe Rule

Never pipe kit JSON output through python/jq. Read JSON directly — it's the intended format for agents.

## Test After Install Rule

After changing daemon code, ALWAYS:
1. `pkill -9 -f daemon; rm -f /tmp/kit-tg-daemon.sock`
2. `uv tool install --force ...`
3. Then test

The daemon runs from the INSTALLED binary, not from `uv run`. If you test with `uv run` while installed daemon is running — you're testing against old code.

## Video Upload Rule

Videos without audio get silent audio track added automatically. This prevents Telegram from converting them to GIF. Don't disable this unless --as-gif is explicitly passed.

## Ask Baseline Rule

Ask baseline = max(last_ask_message_id, last_returned_response_id). This prevents:
- Re-reading already-processed messages
- Skipping messages sent between asks

## Session ID

Session isolation uses KIT_SESSION_ID env var, or falls back to tty name, or cwd hash. Different session IDs = independent baselines. One agent session = one session ID.
