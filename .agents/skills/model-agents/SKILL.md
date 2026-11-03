---
name: model-agents
description: Shows which product models the user is working on and which agents (skills such as spec review, test plan, certification, release note, or any team-made agent skill) each model can use, with whether the user/team already used them — as an interactive menu inside the chat — and runs the chosen agent. Use when the user calls the model agents, asks "내 모델", "내가 하는 모델", "에이전트 목록", "MDL-101 에이전트", or wants to continue work on a product model.
allowed-tools: Bash(python3 .agents/skills/model-agents/scripts/view.py:*) Bash(python3 .agents/skills/model-agents/scripts/models.py:*) Bash(python3 .claude/skills/model-agents/scripts/view.py:*) Bash(python3 .claude/skills/model-agents/scripts/models.py:*)
metadata:
  ui: dynamic-view
  ui-schema: VIEW_SCHEMA.md
---

# Model agents (menu)

Purpose: show the user **which models they are working on** and **which agents each model can use**
(and whether they already used them). No separate app — the menu is drawn inside this chat from a
view document that `scripts/view.py` (in this skill's folder) produces.

## Screens
| Step | Command | Shows |
|---|---|---|
| 1 | `python3 <this skill>/scripts/view.py home` | my models: recently run first, then ones I own a role on |
| 2 | `… view.py model <KEY or MODEL_CODE>` | agents available for that model: ✅ used (count, last) / ⚪ unused, team usage, owner, status |
| 3 | `… view.py agent <KEY> <skill>` | the agent's inputs (pre-filled from the model) and how to invoke it on each runner |
| – | `… view.py models` | all models (when the user wants models they are not on) |

`<this skill>` is this skill's folder (e.g. `.agents/skills/model-agents`, `.claude/skills/model-agents`).
If the user already named a model (`MDL-101`, `TV-OLED65-X1`) start at step 2; if they named a model and an agent, step 3.

## How to draw a screen — pick by what you can do
1. **You have an "ask the user to pick" tool** (e.g. Claude Code `AskUserQuestion`):
   run the view with `--format choices`. If it has `context.card`, print that as a 2-column table first.
   Pass `questions` to the tool unchanged except for removing each option's `action` key (keep labels and
   descriptions exactly). When the user picks an option, run that option's `action.command` with
   `--format choices` appended to get the next screen. "더 보기" options already contain the next page command.
2. **Otherwise** (Codex, Copilot CLI, Gemini CLI, any chat): run with the default `--format md` and print the
   output **verbatim** as your message. The user answers with a number (n-th item) or a letter (actions).
   Next screen: item in step 1 → `view.py model <its key>`; agent row in step 2 → `view.py agent <model key> <its /skill name>`.
   If unsure, run the same view with `--format json` and use the item's `action.command`.
3. **A custom host / portal** calls `view.py … --format json` itself and renders it (schema: `VIEW_SCHEMA.md`).

## Running an agent (step 3)
- Show the inputs and defaults; ask only for what the user wants to change (with option 1, `--format choices`
  turns select fields into questions).
- Then run the agent skill: use the line for your runner from `invoke.by_runner`
  (Claude `/skill …`, Codex `$skill …`, others "Use the … skill: …"), i.e. load that skill and follow its SKILL.md
  with that request.
- When it is done, run `after.record_use` (fill in a one-line summary; `--status failed` if it failed), then show
  step 2 again so the user sees the agent marked ✅.

## Rules
- Show only what `view.py` returns; never invent models, agents or usage numbers.
- Never run an agent unless the user chose it.
- Register a model when asked: `python3 <this skill>/scripts/models.py add <CODE> --name "…" --line <제품군>`.
- Any skill becomes an agent here by declaring it (see `VIEW_SCHEMA.md` → "Declaring an agent skill").
