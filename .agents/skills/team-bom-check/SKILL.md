---
name: team-bom-check
description: Check the BOM (bill of materials) of a product model for end-of-life parts, single-source parts and cost outliers. Use when asked to check the BOM or parts of a model (e.g. MDL-101, TV-OLED65-X1).
metadata:
  model-agent: "true"
  agent-role: dev
  agent-product-lines: "TV, 모니터"
  agent-label: BOM 점검
  agent-icon: 🧾
  agent-order: "50"
---

# BOM 점검 (team-bom-check)

Example of a **team-made skill** that joins the model agent menu without any change to the
menu itself: the `metadata` block above (Agent Skills frontmatter) is the whole integration.
Claude Code, Codex and other runners ignore it; the `model-agents` menu reads it.

## Steps
1. Get the model: `python3 <skills dir>/model-agents/scripts/models.py show <MODEL> --json`
   (`<skills dir>` = the folder that contains this skill; on Windows use `python` instead of `python3`).
2. Read the BOM file the user points to (CSV/XLSX), or ask for it.
3. List: EOL / NRND parts, single-source parts, parts whose cost is > 2× the median of the same category.
4. Output a short table (부품 | 문제 | 영향 | 제안).
5. Record usage: `python3 <skills dir>/model-agents/scripts/models.py use <MODEL> team-bom-check --summary "<one line>"`
