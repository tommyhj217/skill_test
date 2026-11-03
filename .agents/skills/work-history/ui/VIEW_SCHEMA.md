# Dynamic view schema (model agents)

`scripts/view.py` produces **one view document** per screen. Every host draws it in its own way:

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
    "prompt_template": "/model-test-plan {jira_key} ({model_code}) {level} …",
    "context": { "jira_key": "MDL-101", "model_code": "TV-OLED65-X1", … } },
  "invoke": { "skill": "model-test-plan", "default_prompt": "…",
              "by_runner": { "claude": "/model-test-plan …", "codex": "$model-test-plan …", "copilot": "Use the …", "gemini": "Use the …" } },
  "after": { "record_use": "python3 …/models.py use MDL-101 model-test-plan --summary \"…\"" },
  "actions": [ { "id": "home", "label": "내 모델 목록", "action": { "view": "home", "command": "…" } } ],
  "prompt": "어떤 모델을 열까요?",
  "note": "…",  "empty": "…"                                                    // optional
}
```
Navigation: run `action.command` of the item/action the user picked. Each command prints the next view
(add `--format json|choices` as needed). The flow is home → model → agent → (run the skill) → model.

## Choices (`--format choices`)
`{"view", "questions": [{question, header, multiSelect, options: [{label, description, action}]}], "context"}`
— at most 4 options per question (a "더 보기" option carries the next page's command). On the agent view each
form field becomes a question (max 4), and `then` / `after` tell the host what to run next.
Remove the `action` keys before passing `questions` to a tool that does not accept extra keys.

## Declaring an agent skill
A skill shows up as an agent of matching models when it declares itself. No central list; any runner's
skill folder works (`.agents/skills`, `.claude/skills`, `.github/skills`, `.gemini/skills`, user-level dirs,
or `WORK_HISTORY_SKILL_ROOTS`).

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
The form then has one free-text field and the request is `/<skill> {jira_key} ({model_code}) {args}`.

**B. `ui.json` next to SKILL.md** (custom form fields and request template)
```json
{ "label": "테스트 계획", "icon": "🧪",
  "agent": { "role": "qa", "product_lines": ["*"], "exclude_product_lines": ["액세서리"], "order": 20 },
  "inputs": [ { "name": "level", "label": "테스트 수준", "type": "select", "options": ["smoke","regression","full"], "default": "regression" },
              { "name": "regions", "label": "대상 지역", "type": "text", "default": "{regions}" } ],
  "prompt": "/model-test-plan {jira_key} ({model_code}) {level} 테스트 계획 — 지역: {regions}" }
```
`{…}` in defaults and `prompt` are filled from the model: `jira_key, model_code, model_name, product_line, due,
regions, spec_url, launch_date, status, role, assignee`. The leading `/skill` is rewritten per runner
(`$skill` for Codex, …) from `runners.json`.

To make the menu count a run, the skill's last step should be
`python3 <skills dir>/work-history/scripts/models.py use <MODEL> <skill> --summary "…"`.
