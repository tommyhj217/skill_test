---
name: model-agents
description: Entry point for the model agents (spec review, test plan, certification, release note …). Shows the models the user worked on most recently first; for a chosen model lists the available agents and whether the user/team already used each, then runs the chosen agent skill. Use when the user calls the model agents, asks "내 모델", "에이전트 목록", "MDL-101 에이전트", or wants to continue work on a product model.
---

# Model agents (entry)

Replaces the old Jira-triggered agents: every agent is a skill (`model-*`), and this skill is the menu.
Scripts live in `.agents/skills/work-history/scripts` (run from the repository root).

## 1. Models — the ones I ran first
```bash
python3 .agents/skills/work-history/scripts/models.py mine
```
Show the list as-is: "최근 실행한 모델" (most recent first) then "다른 모델". Ask which model to open
unless the user already named one (Jira key like `MDL-101` or a model code like `TV-OLED65-X1`).

## 2. Agents of the chosen model, with usage
```bash
python3 .agents/skills/work-history/scripts/models.py show <MODEL>
```
Each line: agent, skill, ✅ 사용함 (my count, last run, result) / ⚪ 미사용, team usage, owner, status.
Present it as a short table and suggest unused agents first. Ask which one to run.

## 3. Run the chosen agent
Invoke that agent skill (e.g. `model-test-plan`) with the model key. Each agent skill records its own
usage at the end (`models.py use …`), so the next `mine` / `show` reflects it.

## Other actions
- New model (no Jira needed): `models.py add <CODE> --name "…" --line <제품군> [--regions "KR, US"] [--due YYYY-MM-DD]`
- Import/refresh from Jira (optional): `jira_sync.py pull`
- Visual version of this menu: `python3 .agents/skills/work-history/scripts/server.py` → http://127.0.0.1:8765
  (left: models, mine first → click a model → agents with usage and a run button). `--demo` loads sample data.
