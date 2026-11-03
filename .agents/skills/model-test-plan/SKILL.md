---
name: model-test-plan
description: Create a QA test plan for a Jira-registered product model: scope, test cases by area, environments per region and schedule up to the due date. Use when asked for a test plan for a model (e.g. MDL-102, MON-27Q-PRO).
---

# 테스트 계획 (model-test-plan)

Agent skill generated per Jira model (owner role: `qa`). Works on any runner that reads
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
1. Get the model's info (code, product line, regions, due date).
2. Pick the test level (`level`): smoke / regression / full.
3. List test cases by area with priority, and environments per region (power voltage, broadcast standard, language).
4. Output a table (영역 | 케이스 | 우선순위 | 지역 | 담당) and a schedule ending before `due`.

## Record usage (always, at the end)
So the model menu (`model-agents` skill / UI) shows this agent as used:
```bash
python3 <skills dir>/work-history/scripts/models.py use <JIRA_KEY or MODEL_CODE> model-test-plan --summary "<one-line result>"
```
Use `--status failed` if you could not finish. (Runs started from the work-history UI are recorded automatically.)

## Output
Markdown. Keep it short enough to paste as a Jira comment. The work-history UI can post it to the issue.
Example: `examples/sample_output.md`.
