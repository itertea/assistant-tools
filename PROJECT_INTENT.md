# assistant-tools (kit) — Project Intent

## What
CLI tool for AI agents to interact with Telegram (and other services: STT, TTS, search, video).

## Architecture: Daemon

ALL Telegram operations go through a single persistent daemon process.

**Why daemon exists:**
1. Single Telegram session — no conflicts when multiple scripts/agents use kit simultaneously
2. Persistent connection — no reconnect overhead on every command (fast, no flood-wait)
3. Session file locking — only one process touches the .session file

**Daemon contract:**
- Auto-starts on first kit tg command
- Auto-restarts when kit binary changes (version hash check)
- Auto-shuts down after 10 min idle
- Unix socket at /tmp/kit-tg-daemon.sock
- ALL tg commands proxy through daemon — no direct Telethon connections from CLI

**Commands that MUST go through daemon (no exceptions):**
- history, send, send-file, send-photo, send-voice, send-album
- ask, watch, wait-next
- forward, edit, delete, react
- find-dialog, get, media-info, media-download

## Config

`~/.config/assistant-tools/config.toml` — auto-created on first run with defaults.

```toml
[tg]
api_id = 2040          # Telegram Desktop public credentials (default)
api_hash = "b18441a1ff607e10a989891a5462e627"

[stt]
url = "https://api.groq.com/openai/v1/audio/transcriptions"  # default: Groq
api_key = ""           # GROQ_API_KEY env var as fallback
model = "whisper-large-v3"
# For OmniRoute: url = "http://HOST:20128/v1/audio/transcriptions", model = "groq/whisper-large-v3"
```

## Output Format

All commands return JSON: `{ok, command, provider, data, error, meta}`.
No --format text. Agents parse JSON natively.

## Video Handling

- send-photo/send-media: auto-detects video by extension
- Videos without audio: kit adds silent audio track (ffmpeg) to prevent Telegram gif conversion
- --as-gif flag: skip silent audio, send as animation
- ffmpeg validation: corrupted videos rejected before upload (imageio-ffmpeg bundled)

## Ask Protocol

- Collects ALL pending messages since last ask (baseline = last ask message ID)
- Puts 👀 reaction on every collected message
- If no pending: waits for new message (infinite by default)
- Session isolation via KIT_SESSION_ID or tty/cwd hash
