---
name: model-spec-review
description: Review the product spec of a product model (power, display, connectivity, mechanical) and list risks, open questions and spec gaps. Use when asked to review the spec of a model like MDL-101 or a model code such as TV-OLED65-X1.
---

# 사양 검토 (model-spec-review)

Agent skill offered per product model (owner role: `dev`). Works on any runner that reads
Agent Skills (Claude Code, Codex, Copilot CLI, Gemini CLI).

## Inputs
The request names a model key (e.g. `MDL-101`) and/or a model code. Get the model's fields with:

```bash
python3 <skills dir>/model-agents/scripts/models.py show <MODEL> --json
```
(`<skills dir>` = the folder that contains this skill, e.g. `.agents/skills` or `.claude/skills`.
On Windows use `python` (or `py -3`) instead of `python3`, forward slashes and double quotes.)
(fields: key, model_code, name, product_line, status, due, spec_url, regions, launch_date, roles).
If an in-house connector for the model data is available you may use it instead.

## Steps
1. Read the spec document at `spec_url` (wiki/Confluence link) and any files the user points to.
2. Check each area in scope (`focus`): 전원, 디스플레이, 통신, 기구 — values, tolerances, missing items.
3. Compare against previous models of the same `product_line` if available in the repo.
4. Output: summary, a table of findings (영역 | 항목 | 문제 | 심각도 | 제안), open questions for the PM.

## Record usage (always, at the end)
So the model menu (`model-agents` skill) shows this agent as used:
```bash
python3 <skills dir>/model-agents/scripts/models.py use <MODEL> model-spec-review --summary "<one-line result>"
```
Use `--status failed` if you could not finish.

## Output
Markdown. Keep it short.
Example: `examples/sample_output.md`.
