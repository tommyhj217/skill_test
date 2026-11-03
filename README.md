# Model agents — a menu skill with a dynamic UI

Shows each user **which models they are working on** and **which agents (skills) each model can use**, with whether they already used each one. Users then run the agent from there.

- **No separate app.** The skill defines its screens:
  - Claude Code shows them as choice prompts.
  - Codex, Copilot and Gemini show them as a markdown numbered menu.
  - Your own host renders the JSON.
- **No central list.** A skill joins the menu when it declares itself with `metadata` or `ui.json`, including team-made skills.
- **Python 3 standard library only.** Data is stored in SQLite.

## Try it (test data)
```bash
export MODEL_AGENTS_USER=bob                                        # default: local part of your git email
python3 .agents/skills/model-agents/scripts/models.py seed --reset  # load test data
```
- **In a runner:** type `/model-agents` (in Codex, `$model-agents`) or "내 모델 보여줘", then answer with a number.
- **Screens directly:**
  ```bash
  V=.agents/skills/model-agents/scripts/view.py
  python3 $V home                               # my models (recently run first → owned but not run yet)
  python3 $V model MDL-101                      # agents of the model + ✅ used / ⚪ unused
  python3 $V agent MDL-101 model-test-plan      # inputs (pre-filled from the model) + how each runner invokes it
  python3 $V model MDL-101 --format choices     # Claude Code choice prompts (pages of 3 + "더 보기")
  python3 $V model MDL-101 --format json        # your own host
  python3 $V agent MDL-101 model-spec-review --format choices                         # input form as questions
  python3 $V agent MDL-101 model-spec-review --set focus=기구 --set "notes=EU 대기전력"  # apply chosen values
  ```

### Choice format and how choices are applied
| Situation | Behavior |
|---|---|
| More than 4 list items (models, agents) | Items and actions go in one list: 3 per page + "더 보기 (n)" → the next page command (`--page N`). Nothing is dropped. |
| More than 4 input fields | Split into `rounds` of ≤4 questions (one AskUserQuestion call per round). |
| A select with more than 4 options | 3 options + "다른 값 보기 (n)" → a follow-up question (`followups`), chained as needed. |
| Applying choices | Each question's chosen `value` (or Other text) → `view.py agent … --set name=value` → the execution request for each runner (`invoke.by_runner`) is rebuilt with those values, followed by a "실행 / 값 다시 고르기 / 취소" confirmation. In md, changed values are marked ✏️. |
| Invalid value | Unknown field, or a value not among the select's options → ⚠ at the top of the output, exit code 1. |
- **Tests:** `python3 .agents/skills/model-agents/tests/test_model_agents.py` (24 tests)

Example (real Claude Code, user jiwoo):
```
/model-agents
1. MON-27Q-PRO · 27인치 QHD 모니터 Pro (MDL-102 · 모니터) — 최근 실행 … · 에이전트 1/5 사용 · 내 담당: 인증
…
> 1
| # | 에이전트 | 내 사용 | 팀 | 담당 | 상태 |
| 1 | 📐 사양 검토 `/model-spec-review` | ⚪ 미사용 | 팀 0회 (0명) | 담당 alice | 할 일 |
| 3 | ✅ 인증 체크리스트 `/model-cert-check` | ✅ 사용함 1회 · 최근 … | 팀 1회 (1명) | 담당 jiwoo (나) | 진행 중 |
| 5 | 🧾 BOM 점검 `/team-bom-check` | ⚪ 미사용 | … |
```

## Structure
```
.agents/skills/
├── model-agents/                 Menu skill (engine included)
│   ├── SKILL.md                  How the runner should draw the menu
│   ├── VIEW_SCHEMA.md            View document schema + how to declare an agent
│   ├── scripts/view.py           Dynamic UI: view document → md / choices / json
│   ├── scripts/registry.py       Agent skill discovery (frontmatter metadata / ui.json), per-runner invoke syntax
│   ├── scripts/store.py          SQLite: models · agents · usage
│   ├── scripts/models.py         show / add / use (record usage) / seed, plus load_models() ← replace in-house
│   ├── scripts/install.py        Install into another project (+ Claude permissions)
│   ├── samples/                  ★ Test data (models.json, usage.json)
│   └── tests/
├── model-spec-review/  ┐
├── model-test-plan/    │ ★ Test agents (SKILL.md + ui.json + examples)
├── model-cert-check/   │
├── model-release-note/ ┘
└── team-bom-check/       ★ Test agent declared only via metadata
```
`.claude/skills/*` and `.gemini/skills/*` are links to `.agents/skills`. Codex and Copilot read `.agents/skills` directly.

## Parts to replace in-house (★)
| What | Where | How |
|---|---|---|
| Model data source | `scripts/models.py` → `load_models()` | Replace with your in-house system (issue tracker, PLM, API, …). Return the same shape as `samples/models.json`: `key, model_code, product_line` are required, `roles: {role: username}` sets owners, and other fields go into templates as-is. Then run `models.py seed` (or call `import_models()` from your own sync). |
| Agents | `.agents/skills/model-*`, `team-bom-check` | Replace with your real agent skills. Add the declaration below and they join the menu. |
| Test usage | `samples/usage.json` | Test only. Real usage is recorded when an agent skill finishes with `models.py use …`. |
| User identification | `store.resolve_username()` | `MODEL_AGENTS_USER` → git email → OS user. Swap in your company account if needed. |

### Agent declaration (frontmatter only)
```yaml
metadata:
  model-agent: "true"
  agent-role: dev                      # owner role (matches a key in the model's roles)
  agent-product-lines: "TV, 모니터"    # or "*"
  agent-label: BOM 점검
  agent-icon: 🧾
```
- For custom input fields, add a `ui.json`. See `VIEW_SCHEMA.md`.
- End the skill with `models.py use <MODEL> <skill>` so the menu shows ✅.

## Windows
- **Interpreter:** use `python` (or `py -3`) instead of `python3`. The commands the menu prints already use `python` on Windows. To use another one, set `MODEL_AGENTS_PYTHON`, e.g. `$env:MODEL_AGENTS_PYTHON="py -3"`.
- **Quoting:** paths always use `/` and arguments use double quotes, so the same commands work in Git Bash (Claude Code), PowerShell (Codex) and cmd. Example:
  ```powershell
  python .agents/skills/model-agents/scripts/view.py agent MDL-101 model-spec-review --set "focus=기구"
  ```
- **Output encoding:** output is UTF-8 even when the console code page is cp949 or cp1252, so emoji and Korean don't break.
- **Skill links:** with git's default setting (`core.symlinks=false`), the links in `.claude/skills/*` arrive as text files. After cloning, run this once:
  ```powershell
  python .agents/skills/model-agents/scripts/install.py --project .
  ```
  It replaces them in this order: symlink → directory junction (no admin rights needed) → copy. If it copied, refresh with `--update` after editing a skill.

## Installing into another project
```bash
python3 .agents/skills/model-agents/scripts/install.py --project <path> [--runners claude,codex,copilot,gemini] [--copy] [--update]
```
This copies the menu and agent skills. For Claude Code it also adds permissions for `view.py` and `models.py`:
- The skill's `allowed-tools` covers only the first turn, so the menu's follow-up turns need these project permissions.
- Claude Code applies them after the project is trusted (open it interactively once).

## Verification status
- **Real Claude Code:**
  - The multi-turn flow worked: `/model-agents` → number → agent list and usage → input screen.
  - A plain-language value change ("범위는 기구로…") was converted to `--set` and applied to the request.
- **Unit tests:** 24 tests cover discovery, the 3 formats, paging, follow-up questions, applying and validating values, the full loop, a drop-in skill, install, and cross-platform handling (quoting, paths, encoding, git symlink stubs).
- **CI** (`.github/workflows/model-agents-tests.yml`): runs the tests on Windows, macOS and Linux, plus menu smoke tests in PowerShell and cmd.
- **Not tested:**
  - Codex, Copilot and Gemini (not installed here).
  - Claude Code's interactive choice prompt (can't be checked in headless mode).
- **Codex:** the scripts write to the DB, so they need the `workspace-write` sandbox.
