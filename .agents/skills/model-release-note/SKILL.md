---
name: model-release-note
description: Draft release notes / launch summary for a product model for internal teams or customers. Use when asked to write release notes or a launch summary for a model.
---

# 릴리즈 노트 (model-release-note)

Agent skill offered per product model (owner role: `pm`). Works on any runner that reads
Agent Skills (Claude Code, Codex, Copilot CLI, Gemini CLI).

## Inputs
The request names a model key (e.g. `MDL-101`) and/or a model code. Get the model's fields with:

```bash
python3 <skills dir>/model-agents/scripts/models.py show <MODEL> --json
```
(`<skills dir>` = the folder that contains this skill, e.g. `.agents/skills` or `.claude/skills`.)
(fields: key, model_code, name, product_line, status, due, spec_url, regions, launch_date, roles).
If an in-house connector for the model data is available you may use it instead.

## Steps
1. Get the model's info and current status .
2. Collect results from the other agents of the same model if available (spec review, test plan, certification).
3. Write for the chosen `audience` and `language`: highlights, specs, availability by region, known issues.

## Record usage (always, at the end)
So the model menu (`model-agents` skill) shows this agent as used:
```bash
python3 <skills dir>/model-agents/scripts/models.py use <MODEL> model-release-note --summary "<one-line result>"
```
Use `--status failed` if you could not finish.

## Output
Markdown. Keep it short.
Example: `examples/sample_output.md`.
