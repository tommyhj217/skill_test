---
name: model-release-note
description: Draft release notes / launch summary for a Jira-registered product model for internal teams or customers. Use when asked to write release notes or a launch summary for a model.
---

# 릴리즈 노트 (model-release-note)

Agent skill generated per Jira model (owner role: `pm`). Works on any runner that reads
Agent Skills (Claude Code, Codex, Copilot CLI, Gemini CLI).

## Inputs
The request names a Jira key (e.g. `MDL-101`) and/or a model code. Get the model's fields with:

```bash
python3 .agents/skills/work-history/scripts/jira_sync.py show <JIRA_KEY> --json
```
(fields: jira_key, model_code, model_name, product_line, status, due, spec_url, regions, launch_date, roles).
If a Jira MCP/connector is available you may read the issue directly instead.

## Steps
1. Get the model's info and current status from Jira.
2. Collect results from the other agents of the same model if available (spec review, test plan, certification).
3. Write for the chosen `audience` and `language`: highlights, specs, availability by region, known issues.

## Record usage (always, at the end)
So the model menu (`model-agents` skill / UI) shows this agent as used:
```bash
python3 .agents/skills/work-history/scripts/models.py use <JIRA_KEY or MODEL_CODE> model-release-note --summary "<one-line result>"
```
Use `--status failed` if you could not finish. (Runs started from the work-history UI are recorded automatically.)

## Output
Markdown. Keep it short enough to paste as a Jira comment. The work-history UI can post it to the issue.
Example: `examples/sample_output.md`.
