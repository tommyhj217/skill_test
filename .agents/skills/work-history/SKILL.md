---
name: work-history
description: Manage the connected user's AI work history (prompts, tool and skill runs from Claude Code, Codex, Copilot CLI, Gemini CLI, …) stored in a local SQLite DB — show, search or summarize it, add notes, and launch a web UI to browse it, continue past sessions and run other skills. Use when the user asks about "내 작업 이력", "what did I do yesterday", history, past sessions, or wants the work-history UI/dashboard.
---

# Work History (runner-agnostic)

Records what users do with any AI coding runner into one SQLite DB and lets the
**connected user** browse, manage and continue *their own* history.
Python 3 stdlib only. Commands below run from this skill's directory.

## Who is the connected user?
`WORK_HISTORY_USER` env → `git config user.email` (local part) → OS user.
Check with `python3 scripts/db.py whoami`. Members see only their own rows; `role='admin'` users
can view (not modify) others.

## Tasks
- **Show / search history** (answer in chat):
  `python3 scripts/db.py list --limit 20 [--kind skill] [--q 검색어]`
- **Stats / summary**: `python3 scripts/db.py stats` → summarize the JSON for the user.
- **Record something manually** (runners without hooks, or notes):
  `python3 scripts/db.py log --kind note --summary "..." [--tags a,b]`
- **Launch the UI** (run in background, then give the user the URL):
  `python3 scripts/server.py --port 8765 [--runner claude|codex|copilot|gemini|mock] [--workdir DIR]`
  → http://127.0.0.1:8765. Clicking a row suggests next actions: continue the original session
  on *its own* runner (`--resume`), related skills, or a free prompt. Each action can be run on
  any installed runner (dropdown); results stream into split panes (◫ / ⊟).
- **Install into another project / for other runners**:
  `python3 scripts/install.py --project /path/to/repo [--runners claude,codex,copilot,gemini]`
- **Demo with sample Jira data**: `python3 scripts/server.py --demo` (alice, mock runner)
- **Sample data** (demo only): `python3 scripts/db.py seed --reset`
- **Tests**: `python3 tests/test_multi_runner.py -v && python3 tests/test_jira_agents.py -v`

## Model agents menu
The `model-agents` skill draws the menu in the chat from `scripts/view.py` (md / choices / json);
see `ui/VIEW_SCHEMA.md`. The rest of this section is the optional Jira source.

## Jira model agents (optional)
When a model issue is registered in Jira, one agent per role is generated (spec review → dev,
test plan → QA, certification → cert, release note → PM). Each agent is a skill in `.agents/skills/model-*`.
- Import / refresh: `python3 scripts/jira_sync.py pull` (sample data unless `JIRA_BASE_URL` is set)
- List: `python3 scripts/jira_sync.py agents [--user alice]` · model info: `jira_sync.py show MDL-101`
- Auto-create on registration: point a Jira webhook (issue created/updated) at `POST /api/jira/webhook?secret=…`
- In the UI the "내 에이전트" tab lists the connected user's agents grouped by model; running one fills the
  form from the model's Jira fields, and the result can be posted back to the issue as a comment.
- Field ids and role → person mapping: `jira/jira_mapping.json`. Which agents a model gets is declared by each
  agent skill (`metadata.model-agent` in SKILL.md or `agent` in ui.json) — see `ui/VIEW_SCHEMA.md`.

## Extending
- New runner CLI → add an entry to `runners.json` (`run`, `resume`, `format`, `skill_syntax`).
  Unknown output formats can use `"format": "text"`.
- Make a skill appear in the UI → put `ui.json` next to its SKILL.md, or add it to `catalog.json`
  (`"runners": [...]` limits it to runners that have it).
