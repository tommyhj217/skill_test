# Model agents — a menu skill with a dynamic UI

**Purpose:** show each user **which models they are working on** and **which agents (skills) each model can use**, plus whether they have already used them. Users then run the agent they need from there.

- **No extra app.** The skill defines its screens, and each runner draws them in its own way:
  - **Claude Code** shows them as choice prompts.
  - **Codex, Copilot and Gemini** show them as a markdown numbered menu.
  - **Your own host** renders the JSON.
- **Any skill becomes an agent with a declaration.** Add `metadata` to a SKILL.md, or a `ui.json`. There is no central list to edit, and team-made skills join the menu the same way.

## How it looks (real Claude Code, user bob)
```
/model-agents
## bob 님이 진행 중인 모델
**최근 실행한 모델**
1. **TV-OLED65-X1 · 65인치 OLED TV X1** (MDL-101 · TV) — 최근 실행 … · 에이전트 1/5 사용 · 내 담당: QA
2. **MON-27Q-PRO · 27인치 QHD 모니터 Pro** (MDL-102 · 모니터) — 최근 실행 … · 에이전트 1/5 사용 · 내 담당: QA
3. …
**내 담당 (아직 실행 안 함)**
4. **SPK-BAR-500 · 사운드바 500** (MDL-103 · 오디오) — 에이전트 4개 · 아직 사용 안 함 · 내 담당: 개발
[a] 다른 모델 보기 (1) · [b] 모델 등록

> 2
| # | 에이전트 | 내 사용 | 팀 | 담당 | 상태 |
| 1 | 📐 사양 검토 `/model-spec-review` | ⚪ 미사용 | 팀 0회 (0명) | 담당 alice | 할 일 |
| 2 | 🧪 테스트 계획 `/model-test-plan` | ✅ 사용함 1회 · 최근 … | 팀 1회 (1명) | 담당 bob (나) | 진행 중 |
| 3 | ✅ 인증 체크리스트 `/model-cert-check` | ⚪ 미사용 | 팀 1회 (1명) | 담당 jiwoo | 진행 중 |
| 4 | 📝 릴리즈 노트 `/model-release-note` | ⚪ 미사용 | … |
| 5 | 🧾 BOM 점검 `/team-bom-check` | ⚪ 미사용 | … |        ← a team-made skill (metadata only)

> 5
→ input fields (pre-filled from the model) + how to run it on each runner: /team-bom-check · $team-bom-check · …
```

## Try it
```bash
export WORK_HISTORY_USER=bob                                     # by default, the local part of your git email
python3 .agents/skills/work-history/scripts/db.py seed --reset   # sample: 5 models, 4 users, usage history
```
- **Claude Code / Codex / Copilot / Gemini:** type `/model-agents` (in Codex, `$model-agents`), or ask "내 모델 보여줘". Answer with a number to move to the next screen.
- **Without a runner** (to see the screens directly):
  ```bash
  V=.agents/skills/work-history/scripts/view.py
  python3 $V home                                   # my models
  python3 $V model MDL-101                          # agents of a model + usage
  python3 $V agent MDL-101 model-test-plan          # agent inputs + how to invoke on each runner
  python3 $V model MDL-101 --format choices         # Claude Code choice-prompt format
  python3 $V model MDL-101 --format json            # for your own host
  ```
- **Tests:** `cd .agents/skills/work-history && for t in test_view test_jira_agents test_multi_runner; do python3 tests/$t.py; done` (45 tests)

## How it works
```
.agents/skills/
├── model-agents/        Menu skill. SKILL.md tells the runner how to draw each screen from view.py
├── model-spec-review/   ┐
├── model-test-plan/     │ Agent skills: SKILL.md + ui.json (declaration, input form, request template)
├── model-cert-check/    │
├── model-release-note/  ┘
├── team-bom-check/      Example of a team-made agent: declared only via frontmatter metadata (no ui.json)
└── work-history/
    ├── scripts/view.py      Dynamic UI: view document → md / choices / json
    ├── scripts/models.py    Model CLI: mine / show / add / use (record usage)
    ├── ui/VIEW_SCHEMA.md    View document schema + how to declare an agent
    ├── scripts/jira_sync.py (optional) import models from Jira, webhook, comment write-back
    └── …                    DB, runner adapters, hooks, tests
```
`.claude/skills/*` and `.gemini/skills/*` are links to `.agents/skills`. Codex and Copilot read `.agents/skills` directly.

**Drawing a screen:** `view.py` creates one view document. The `model-agents` SKILL.md tells the runner which format to use:

| Host | Format | Behavior |
|---|---|---|
| Has a choice tool (Claude Code `AskUserQuestion`) | `--format choices` | Up to 4 options per question, "더 보기" for paging. On the input screen, each field becomes a question. |
| Others (Codex, Copilot, Gemini, any chat) | `--format md` | Printed as-is. The user answers with a number or letter. |
| Your own host / portal / bot | `--format json` | Each item's `action.command` is the command for the next screen. |

## Turning a skill into an agent (including team-made skills)
Put a skill in any skill folder (`.agents/skills`, `.claude/skills`, `~/.claude/skills`, … or `WORK_HISTORY_SKILL_ROOTS`) and add this frontmatter. That's all it takes to join the menu:
```yaml
metadata:
  model-agent: "true"
  agent-role: dev
  agent-product-lines: "TV, 모니터"   # or "*"
  agent-label: BOM 점검
  agent-icon: 🧾
```
- For custom input fields, add a `ui.json`. See `.agents/skills/work-history/ui/VIEW_SCHEMA.md`.
- To make the menu count a run, end the skill with `models.py use <MODEL> <skill> --summary "…"`.
- Claude Code, Codex and others ignore `metadata` and `ui.json`, so the skill still works normally on its own.

## Installing into another project
```bash
python3 .agents/skills/work-history/scripts/install.py --project <path> [--runners claude,codex,copilot,gemini]
```
This copies or links the menu, the agent skills and work-history. For Claude Code it also adds:
- automatic recording hooks,
- **permissions for `view.py` and `models.py`**, so the scripts don't trigger a prompt on every screen.

## Sample data
| Model | Product line | Agents | Sample usage |
|---|---|---|---|
| MDL-101 TV-OLED65-X1 | TV | 5 (4 + BOM 점검) | alice: spec review ×2, cert · bob: test plan |
| MDL-102 MON-27Q-PRO | 모니터 (Monitor) | 5 | bob: test plan · jiwoo: cert |
| MDL-103 SPK-BAR-500 | 오디오 (Audio) | 4 | alice: release note |
| MDL-104 ACC-REMOTE-V2 | 액세서리 (Accessory) | 2 (spec review and release note only) | none |
| MDL-105 TV-QLED55-S3 | TV | 5 | alice, bob, jiwoo |

Owners per model come from the role fields in the sample Jira data. Without Jira, register a model with `models.py add`.

## (Optional) Jira, web UI, work history
- **Jira:** `jira/jira_mapping.json` (field mapping) + `JIRA_BASE_URL`/`JIRA_EMAIL`/`JIRA_API_TOKEN` → `jira_sync.py pull`, webhook at `/api/jira/webhook`. Optional: the menu works without it.
- **Web UI** (`server.py`): an earlier, separate UI. The menu doesn't need it. Delete it if you don't use it.
- **Work history / multi-runner:** `hook_logger.py` and `runners.json` record prompts and tool runs from each runner, and can run skills headless.

## Verification status
- **Tested in real Claude Code (`claude -p`):**
  - `/model-agents` → "2" → "5", over three turns: menu → model agents and usage → custom-skill input screen.
  - The skill's `allowed-tools` covers only the first turn. Later turns need project permissions (added by `install.py`).
  - The project must be trusted by opening it interactively in Claude Code once.
- **Tested by unit tests (45):**
  - choice format (≤4 options, paging),
  - md and json formats,
  - following the action commands through the full loop,
  - agent declarations in a skill folder outside the repo.
- **Not tested:**
  - Codex, Copilot and Gemini (not installed here). The md menu path is plain text output, but whether those runners follow the SKILL.md instructions well is unconfirmed.
  - The interactive choice prompt (`AskUserQuestion`) in Claude Code. It can't be checked in headless mode.
  - The actual work of the agents (spec review and so on). Those are examples.
- **Codex:** `view.py` writes to the DB, so it can't run in the read-only sandbox. It needs the `workspace-write` mode.
