---
name: model-agents
description: Shows which product models the user is working on and which agents (skills such as spec review, test plan, certification, release note, or any team-made agent skill) each model can use, with whether the user/team already used them — as an interactive menu inside the chat — and runs the chosen agent. Use when the user calls the model agents, asks "내 모델", "내가 하는 모델", "에이전트 목록", "MDL-101 에이전트", or wants to continue work on a product model.
allowed-tools: Bash(python3 .agents/skills/model-agents/scripts/view.py:*) Bash(python3 .agents/skills/model-agents/scripts/models.py:*) Bash(python3 .claude/skills/model-agents/scripts/view.py:*) Bash(python3 .claude/skills/model-agents/scripts/models.py:*) Bash(python .agents/skills/model-agents/scripts/view.py:*) Bash(python .agents/skills/model-agents/scripts/models.py:*) Bash(python .claude/skills/model-agents/scripts/view.py:*) Bash(python .claude/skills/model-agents/scripts/models.py:*)
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

`<this skill>` is this skill's folder (e.g. `.agents/skills/model-agents`, `.claude/skills/model-agents`), written
with forward slashes. Interpreter: `python3` on macOS / Linux, `python` on Windows (or `py -3`; the env var
`MODEL_AGENTS_PYTHON` wins). After the first screen just reuse the command lines the views print — they already
use the right interpreter, path and quoting for this OS and shell (bash, PowerShell, cmd).
If the user already named a model (`MDL-101`, `TV-OLED65-X1`) start at step 2; if they named a model and an agent, step 3.

## How to draw a screen — pick by what you can do
1. **You have an "ask the user to pick" tool** (e.g. Claude Code `AskUserQuestion`) — run views with `--format choices`.
   Every question already has 2–4 options; remove the keys the tool does not take (`id`, `field`, `value`,
   `action`, `followup`, `run`) but keep labels/descriptions exactly.
   - **Lists** (home / model): one question. Picked option → run its `action.command` + `--format choices`.
     "더 보기 (n)" is just another option whose command opens the next page.
   - **Agent form** (`view: form`): ask `rounds` in order, one tool call per round (≤ 4 questions each).
     If the picked option has `followup`, ask `followups[<id>]` next (more values of a long select list).
     The answer for a field is the picked option's `value`; if `value` is null or the user typed "Other", use
     the typed text. Then run `submit.command` with one `--set "<name>=<value>"` per field and `--format choices`.
   - **Confirm** (`view: confirm`): shows the applied values. "실행" → run the agent (below) with
     `invoke.by_runner`; "값 다시 고르기" / "취소" → run that option's `action.command`.
2. **Otherwise** (Codex, Copilot CLI, Gemini CLI, any chat): run with the default `--format md` and print the
   output **verbatim**. The user answers with a number (n-th item), a letter (actions), or on the agent screen
   with values like `level=full` / natural language. Next screen: item in step 1 → `view.py model <its key>`;
   agent row in step 2 → `view.py agent <model key> <its /skill name>`; values → the same agent view with
   `--set "<name>=<value>"` for each changed field (shows ✏️ on changed values and the updated request).
   If unsure, run the same view with `--format json` and use the item's `action.command`.
3. **A custom host / portal** calls `view.py … --format json` itself and renders it (schema: `VIEW_SCHEMA.md`).

Invalid values (unknown field, value not among a select's options) are reported as ⚠ lines at the top of
the output — show them and ask again; there is no need to check the exit code.

**Run every `view.py` / `models.py` command on its own, exactly in the form shown** (relative path with `/`, double quotes only, no `;`, `&&`,
pipes, redirections or `echo`). Only that form is pre-approved; anything else stops at a permission prompt.

## Running an agent (step 3)
- Only after the user confirms. Use the request line for your runner from `invoke.by_runner` **of the view that
  was rendered with the user's `--set` values** (Claude `/skill …`, Codex `$skill …`, others "Use the … skill: …"),
  i.e. load that skill and follow its SKILL.md with that request.
- When it is done, run `after.record_use` (fill in a one-line summary; `--status failed` if it failed), then show
  step 2 again so the user sees the agent marked ✅.

## Rules
- Show only what `view.py` returns; never invent models, agents or usage numbers.
- Never run an agent unless the user chose it.
- Register a model when asked: `python3 <this skill>/scripts/models.py add <CODE> --name "…" --line <제품군>`.
- Any skill becomes an agent here by declaring it (see `VIEW_SCHEMA.md` → "Declaring an agent skill").
