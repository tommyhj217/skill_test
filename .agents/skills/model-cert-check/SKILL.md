---
name: model-cert-check
description: Build the certification checklist (KC, FCC, CE, PSE, energy labels…) for a Jira-registered model by target region, with required documents and lead times versus the launch date. Use when asked about certification for a model.
---

# 인증 체크리스트 (model-cert-check)

Agent skill generated per Jira model (owner role: `cert`). Works on any runner that reads
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
1. Get the model's regions and launch date.
2. For each region list required certifications (safety, EMC, radio, energy), documents, test lab lead time.
3. Flag items whose lead time does not fit before `launch_date`.
4. Output a table (지역 | 인증 | 필요 서류 | 소요 | 위험).

## Record usage (always, at the end)
So the model menu (`model-agents` skill / UI) shows this agent as used:
```bash
python3 <skills dir>/work-history/scripts/models.py use <JIRA_KEY or MODEL_CODE> model-cert-check --summary "<one-line result>"
```
Use `--status failed` if you could not finish. (Runs started from the work-history UI are recorded automatically.)

## Output
Markdown. Keep it short enough to paste as a Jira comment. The work-history UI can post it to the issue.
Example: `examples/sample_output.md`.
