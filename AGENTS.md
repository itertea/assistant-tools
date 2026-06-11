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

## Kit-dev Session Lessons (2026-05-30)

Errors from this session that must not repeat:
- Never use `kit tg wait-next` for user interaction — always `ask`
- Never use python wrappers around kit output — read JSON directly
- Never report completion without verifying (test everything)
- When daemon code changes, daemon must auto-restart (version check)
- React code must be AFTER the break statement, not inside unreachable code
- `InputPeerSelf` doesn't work for reactions — use `get_input_entity(me)`
- Don't modify production services without testing on test first
- Don't ask user questions you can answer yourself (check programmatically)
- Don't use hardcoded exclusion lists — use single source of truth functions
- STT config belongs in kit config, not in AGENTS.md rules

## Kit-dev Session Lessons (2026-05-31)

Errors from this session that must not repeat:
- Never open PRs without explicit "да, создавай" from user
- Never use python/jq pipes on kit output — read JSON directly
- Never test daemon code via `uv run` while installed daemon is running (different binary!)
- Always `pkill daemon + rm socket + uv tool install` before testing daemon changes
- Session ID must not depend on cwd — use "default" when no tty
- Don't claim "timing issue" or "API lag" without proof — investigate the real bug
- Don't trust subagent claims without verifying (e.g. "copy = forward duplicate")
- Don't invent problems that don't exist (e.g. "grace period needed", "duplicate bug")
- When user asks a direct question — answer it directly, don't deflect
- Squash means per-feature commits, not one giant commit
- NEVER use `git merge --squash` to collapse existing per-feature commits into one. Add new commits ON TOP of existing ones. If existing commits need updating — amend or rebase, don't destroy them.

## Kit Ask Duplicate Bug (Active)

Ask sometimes returns the same messages twice. Root cause: baseline update race condition when multiple asks fire in quick succession or when daemon restarts between asks. The baseline (stored in sent_db ask table) must be updated AFTER responses are returned, not just after sending the ask message. Investigation ongoing.

## Kit PR Squash Task (Active)

Need to rebuild pr-ready branch with 15 separate commits (one per feature). Current state:
- Branch `pr-ready` exists with 1 squash commit (wrong, needs 15)
- Branch `main` has 68 commits ahead of `origin/main`
- All code is correct and working on `main`
- Files are interrelated (daemon.py has daemon+ask+react+send-file)

Approach: rebuild from origin/main, adding features incrementally. Each commit must compile.
Order (dependencies first):
1. Null stripping + action messages + multi-peer wait-next
2. Daemon architecture (daemon.py, daemon_runner.py, middleware)
3. Sent_db tracking
4. Ask + session isolation
5. Watch
6. Forward/edit/delete
7. React
8. Find-dialog
9. Send-album + send-media alias
10. Send through daemon (send-file/photo/voice)
11. Video handling (ffmpeg, silent audio, --as-gif)
12. STT config + tg stt
13. Config commands + auto-create + defaults
14. Duration in output + misc fixes
15. Docs (PROJECT_INTENT.md, AGENTS.md, README)
