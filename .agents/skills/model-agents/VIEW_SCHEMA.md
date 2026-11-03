# Dynamic view schema (model agents)

`scripts/view.py` (in this skill) produces **one view document** per screen. Every host draws it in its own way:

| Host | How it draws | Command |
|---|---|---|
| Claude Code (or any host with a "pick an option" tool) | choice questions (`AskUserQuestion`) | `view.py <view> … --format choices` |
| Codex, Copilot CLI, Gemini CLI, any chat | markdown + numbered menu | `view.py <view> …` (`--format md`) |
| Your own host / portal / bot | your components | `view.py <view> … --format json` |

No host needs to know about models or agents — only this schema.

## View document (`--format json`)
```jsonc
{
  "view": "home | models | model | agent | error",
  "user": "alice",
  "title": "alice 님이 진행 중인 모델",
  "subtitle": "…",
  "card": [ { "label": "제품군", "value": "TV", "link": "https://…" } ],          // optional key/value box
  "sections": [
    { "title": "최근 실행한 모델",
      "items": [
        { "id": "MDL-101",
          "title": "TV-OLED65-X1 · 65인치 OLED TV X1",
          "subtitle": "MDL-101 · TV",
          "used": true,                                                          // model view only
          "badges": [ { "text": "에이전트 2/5 사용", "tone": "ok | warn | info | muted" } ],
          "action": { "view": "model", "args": ["MDL-101"], "command": "python3 …/view.py model MDL-101 --user alice" } } ] } ],
  "form": {                                                                      // agent view only
    "fields": [ { "name": "level", "label": "테스트 수준", "type": "select | text | textarea",
                  "options": ["smoke", "regression", "full"], "default": "regression" } ],
    "values": { "level": "regression", … },  "submitted": false,  "errors": [],
    "submit": { "command": "python3 …/view.py agent MDL-101 model-test-plan --user alice", "arg": "--set", "format": "{name}={value}" },
    "prompt_template": "/model-test-plan {key} ({model_code}) {level} …",
    "context": { "key": "MDL-101", "model_code": "TV-OLED65-X1", … } },
  "invoke": { "skill": "model-test-plan", "prompt": "… (with --set values applied)",
              "by_runner": { "claude": "/model-test-plan …", "codex": "$model-test-plan …", "copilot": "Use the …", "gemini": "Use the …" } },
  "after": { "record_use": "python3 …/model-agents/scripts/models.py use MDL-101 model-test-plan --summary \"…\"" },
  "actions": [ { "id": "home", "label": "내 모델 목록", "action": { "view": "home", "command": "…" } } ],
  "prompt": "어떤 모델을 열까요?",
  "note": "…",  "empty": "…"                                                    // optional
}
```
Navigation: run `action.command` of the item/action the user picked. Each command prints the next view
(add `--format json|choices` as needed). The flow is home → model → agent → (run the skill) → model.

## Choices (`--format choices`)
Shaped for `AskUserQuestion` (2–4 options per question, ≤ 4 questions per call). Nothing is cut off:

**Lists** (`home`, `models`, `model`) — items and actions form one option list, paged as 3 + "더 보기 (n)":
```jsonc
{ "view": "model", "page": 1, "pages": 2,
  "questions": [ { "id": "model", "question": "어떤 에이전트를 실행할까요?", "header": "model",
                   "options": [ { "label": "📐 사양 검토", "description": "✅ 사용함 2회 · …", "action": { "command": "…" } },
                                …,
                                { "label": "더 보기 (3)", "action": { "command": "… --format choices --page 2" } } ] } ],
  "context": { "title", "subtitle", "card" } }
```
**Agent form** (`view: form`) — one question per field, split into `rounds` of ≤ 4; a select with > 4 options
shows 3 values + "다른 값 보기 (n)" whose `followup` id names the next question in `followups`:
```jsonc
{ "view": "form",
  "rounds": [ [ { "id": "focus", "field": "focus", "question": "검토 범위?", "header": "검토 범위",
                  "options": [ { "label": "전체", "description": "현재 값", "value": "전체" }, { "label": "전원", "value": "전원" },
                               { "label": "디스플레이", "value": "디스플레이" },
                               { "label": "다른 값 보기 (2)", "followup": "focus#2" } ] },
                { "id": "notes", "field": "notes", "options": [ { "label": "비워 두기", "value": "" },
                                                               { "label": "직접 입력", "value": null } ] } ] ],
  "followups": { "focus#2": { "id": "focus#2", "field": "focus", "options": [ { "label": "통신", "value": "통신" }, { "label": "기구", "value": "기구" } ] } },
  "submit": { "command": "python3 …/view.py agent MDL-101 model-spec-review --user alice", "arg": "--set", "format": "{name}={value}" } }
```
Answer of a field = picked option's `value` (`null` / "Other" → the typed text). Apply them with
`submit.command --set "focus=기구" --set "notes=…" --format choices` (double quotes work in bash, PowerShell and cmd).

**Confirm** (`view: confirm`, after `--set`) — the applied `context.values` (+ `changed`), the final `invoke`
and one question: 실행 (`run: true` → use `invoke.by_runner`) / 값 다시 고르기 / 취소.
Invalid values (unknown field, value not among a select's options) → the view lists them in `form.errors`
(md: ⚠ lines) and the process exits with code 1.

Remove the extra keys (`id`, `field`, `value`, `action`, `followup`, `run`) before passing `questions` to a tool
that does not accept them.

## Declaring an agent skill
A skill shows up as an agent of matching models when it declares itself. No central list; any runner's
skill folder works (`.agents/skills`, `.claude/skills`, `.github/skills`, `.gemini/skills`, user-level dirs,
or `MODEL_AGENTS_SKILL_ROOTS`).

**A. Frontmatter only** (standard Agent Skills `metadata`; other runners ignore it)
```yaml
---
name: team-bom-check
description: …
metadata:
  model-agent: "true"
  agent-role: dev                    # who owns it on a model (dev | qa | cert | pm | …)
  agent-product-lines: "TV, 모니터"  # or "*"
  agent-exclude-product-lines: ""
  agent-label: BOM 점검
  agent-icon: 🧾
  agent-order: "50"                  # position in the menu (default 100)
---
```
The form then has one free-text field and the request is `/<skill> {key} ({model_code}) {args}`.

**B. `ui.json` next to SKILL.md** (custom form fields and request template)
```json
{ "label": "테스트 계획", "icon": "🧪",
  "agent": { "role": "qa", "product_lines": ["*"], "exclude_product_lines": ["액세서리"], "order": 20 },
  "inputs": [ { "name": "level", "label": "테스트 수준", "type": "select", "options": ["smoke","regression","full"], "default": "regression" },
              { "name": "regions", "label": "대상 지역", "type": "text", "default": "{regions}" } ],
  "prompt": "/model-test-plan {key} ({model_code}) {level} 테스트 계획 — 지역: {regions}" }
```
`{…}` in defaults and `prompt` are filled from the model: `key, model_code, model_name, product_line, due,
status` plus every extra field of the model (sample: `regions, spec_url, launch_date`) and `role, assignee`.
The leading `/skill` is rewritten per runner (`$skill` for Codex, …) — see `SKILL_SYNTAX` in `scripts/registry.py`.

To make the menu count a run, the skill's last step should be
`python3 <skills dir>/model-agents/scripts/models.py use <MODEL> <skill> --summary "…"`.
