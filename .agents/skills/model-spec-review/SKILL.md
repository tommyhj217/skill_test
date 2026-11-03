---
name: model-spec-review
description: Review the product spec of a Jira-registered model (power, display, connectivity, mechanical) and list risks, open questions and spec gaps. Use when asked to review the spec of a model like MDL-101 or a model code such as TV-OLED65-X1.
---

# 사양 검토 (model-spec-review)

Agent skill generated per Jira model (owner role: `dev`). Works on any runner that reads
Agent Skills (Claude Code, Codex, Copilot CLI, Gemini CLI).

## Inputs
The request names a Jira key (e.g. `MDL-101`) and/or a model code. Get the model's fields with:

```bash
python3 <skills dir>/work-history/scripts/jira_sync.py show <JIRA_KEY> --json
```
(`<skills dir>` = the folder that contains this skill, e.g. `.agents/skills` or `.claude/skills`.)
(fields: jira_key, model_code, model_name, product_line, status, due, spec_url, regions, launch_date, roles).
If a Jira MCP/connector is available you may read the issue directly instead.

## Steps
1. Read the spec document at `spec_url` (wiki/Confluence link) and any files the user points to.
2. Check each area in scope (`focus`): 전원, 디스플레이, 통신, 기구 — values, tolerances, missing items.
3. Compare against previous models of the same `product_line` if available in the repo.
4. Output: summary, a table of findings (영역 | 항목 | 문제 | 심각도 | 제안), open questions for the PM.

## Record usage (always, at the end)
So the model menu (`model-agents` skill / UI) shows this agent as used:
```bash
python3 <skills dir>/work-history/scripts/models.py use <JIRA_KEY or MODEL_CODE> model-spec-review --summary "<one-line result>"
```
Use `--status failed` if you could not finish. (Runs started from the work-history UI are recorded automatically.)

## Output
Markdown. Keep it short enough to paste as a Jira comment. The work-history UI can post it to the issue.
Example: `examples/sample_output.md`.
