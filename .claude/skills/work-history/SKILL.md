---
name: work-history
description: Manage the connected user's AI work history stored in a local SQLite DB — show recent prompts/tool/skill runs, search or summarize them, add notes, and launch a web UI to browse, pin, edit and delete entries. Use when the user asks about "내 작업 이력", "what did I do yesterday", history, past sessions, or wants the work-history UI/dashboard.
---

# Work History

Records what users do with an AI runner (Claude Code, Codex, Cursor, …) into a SQLite DB
and lets the **connected user** browse and manage *their own* history.

All scripts are Python 3 stdlib only. Paths below are relative to this skill directory
(`.claude/skills/work-history`).

## Who is the connected user?
Resolved in this order: `WORK_HISTORY_USER` env → `git config user.email` (local part) → OS user.
Check with `python3 scripts/db.py whoami`. Members only see their own rows; users with
`role='admin'` can view others (UI dropdown / `?as=<username>`).

## DB
Default `data/work_history.db` (override with `WORK_HISTORY_DB`). Tables:
`users`, `sessions` (runner session id, runner, project), `activities`
(kind = prompt | tool | skill | note, status = success | failed | in_progress, tags, pinned).

## Tasks

- **Show / search history** (answer in chat):
  `python3 scripts/db.py list --limit 20 [--kind skill] [--q 검색어]`
- **Stats / summary**: `python3 scripts/db.py stats` → summarize the JSON for the user.
- **Add a note**: `python3 scripts/db.py log --kind note --summary "..." [--tags a,b]`
- **Launch the UI**: run in background
  `python3 scripts/server.py --port 8765` and tell the user to open http://127.0.0.1:8765.
  (`--user <name>` to impersonate for testing.)
- **Continue from history in the UI**: clicking a row shows suggested next actions
  (resume the original Claude Code session, related skills, free prompt). Choosing one shows
  a form generated from that skill's `ui.json`; running it calls `claude -p` (`--resume` when
  continuing) in the background and streams the result into a pane. Panes can be split
  left/right or top/bottom (◫ / ⊟) and resized. Use `--runner mock` for a demo without API calls,
  `--workdir DIR` to choose where runs execute, `WORK_HISTORY_CLAUDE_ARGS` for extra CLI flags.
- **Make another skill appear in the UI**: add `ui.json` next to its SKILL.md
  (or an entry in `catalog.json` for skills outside this repo) – see `scripts/skills_registry.py`.
- **Load sample data** (demo/test only): `python3 scripts/db.py seed --reset`

## Automatic recording
`scripts/hook_logger.py` reads a Claude Code hook payload on stdin and logs it.
Register it in `.claude/settings.json` for `UserPromptSubmit` and `PostToolUse`
(see README). Other runners can pipe a JSON object with the same keys
(`hook_event_name`, `session_id`, `cwd`, `prompt` / `tool_name`, `tool_input`).
