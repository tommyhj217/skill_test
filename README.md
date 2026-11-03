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
  python3 $V model MDL-101 --format choices     # Claude Code choice prompts
  python3 $V model MDL-101 --format json        # your own host
  ```
- **Tests:** `python3 .agents/skills/model-agents/tests/test_model_agents.py` (14 tests)

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

## Installing into another project
```bash
python3 .agents/skills/model-agents/scripts/install.py --project <path> [--runners claude,codex,copilot,gemini]
```
This copies the menu and agent skills. For Claude Code it also adds permissions for `view.py` and `models.py`:
- The skill's `allowed-tools` covers only the first turn, so the menu's follow-up turns need these project permissions.
- Claude Code applies them after the project is trusted (open it interactively once).

## Verification status
- **Real Claude Code:** the multi-turn flow worked (`/model-agents` → number → agent list and usage → input screen).
- **Unit tests:** 14 tests cover discovery, the 3 formats, the full loop, a drop-in skill and install.
- **Not tested:**
  - Codex, Copilot and Gemini (not installed here).
  - Claude Code's interactive choice prompt (can't be checked in headless mode).
- **Codex:** the scripts write to the DB, so they need the `workspace-write` sandbox.
