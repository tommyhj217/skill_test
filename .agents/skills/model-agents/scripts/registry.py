#!/usr/bin/env python3
"""Find agent skills in every runner's skill folders. No central list: a skill is an agent when it says so.

Declaration — either in SKILL.md frontmatter (Agent Skills `metadata`, ignored by the runners themselves):
    metadata:
      model-agent: "true"
      agent-role: dev                     # owner role on a model (dev | qa | cert | pm | …)
      agent-product-lines: "TV, 모니터"   # or "*"
      agent-exclude-product-lines: ""
      agent-label: BOM 점검
      agent-icon: 🧾
      agent-order: "50"                   # menu position (default 100)
or in ui.json next to SKILL.md (adds a custom input form and request template):
    {"label", "icon", "agent": {"role", "product_lines", "exclude_product_lines", "order"},
     "inputs": [{"name", "label", "type": "select|text|textarea", "options", "default"}],
     "prompt": "/skill {key} ({model_code}) {field} …"}
"""
import json
import os
import re
from pathlib import Path

SKILL_DIR = Path(__file__).resolve().parent.parent
PROJECT_DIRS = [".agents/skills", ".claude/skills", ".github/skills", ".gemini/skills"]
USER_DIRS = [".agents/skills", ".claude/skills", ".copilot/skills", ".codex/skills", ".gemini/skills"]
# how each runner invokes a skill inside a prompt
SKILL_SYNTAX = {"claude": "/{skill}", "codex": "${skill}", "copilot": "Use the {skill} skill:",
                "gemini": "Use the {skill} skill:"}


def project_root():
    return Path(os.environ.get("MODEL_AGENTS_PROJECT") or SKILL_DIR.parent.parent.parent)


def skill_roots():
    roots = [Path(p) for p in os.environ.get("MODEL_AGENTS_SKILL_ROOTS", "").split(os.pathsep) if p]
    roots += [project_root() / d for d in PROJECT_DIRS] + [Path.home() / d for d in USER_DIRS]
    seen, out = set(), []
    for r in roots:
        if r.is_dir() and r.resolve() not in seen:
            seen.add(r.resolve())
            out.append(r)
    return out


def frontmatter(text):
    """YAML subset: top-level `key: value` plus one level of nested maps (e.g. `metadata:`)."""
    m = re.match(r"^---\s*\n(.*?)\n---", text, re.S)
    out, parent = {}, None
    for line in (m.group(1).splitlines() if m else []):
        if not line.strip() or line.lstrip().startswith("#") or ":" not in line:
            continue
        k, v = line.split(":", 1)
        v = v.split(" #")[0].strip().strip('"\'')
        if line[:1] in (" ", "\t") and parent:
            out[parent][k.strip()] = v
        elif v == "":
            parent = k.strip()
            out[parent] = {}
        else:
            parent = None
            out[k.strip()] = v
    return out


def _csv(v):
    return [x.strip() for x in (v if isinstance(v, list) else str(v or "").split(",")) if str(x).strip()]


def _decl(ui, meta):
    a = ui.get("agent")
    if isinstance(a, dict):
        d = dict(a)
    elif str(meta.get("model-agent", "")).lower() in ("true", "yes", "1"):
        d = {"role": meta.get("agent-role"), "product_lines": meta.get("agent-product-lines"),
             "exclude_product_lines": meta.get("agent-exclude-product-lines"), "order": meta.get("agent-order")}
    else:
        return None
    d["product_lines"] = _csv(d.get("product_lines")) or ["*"]
    d["exclude_product_lines"] = _csv(d.get("exclude_product_lines"))
    try:
        d["order"] = int(d.get("order") or 100)
    except ValueError:
        d["order"] = 100
    return d


def load_agents():
    """{skill_name: {name, label, icon, description, agent, inputs, prompt, source}} for declared agent skills."""
    found = {}
    for root in skill_roots():
        for md in sorted(root.glob("*/SKILL.md")):
            fm = frontmatter(md.read_text(encoding="utf-8"))
            name = fm.get("name") or md.parent.name
            if name in found:
                continue                       # same skill reachable through another runner's folder
            uip = md.parent / "ui.json"
            ui = json.loads(uip.read_text(encoding="utf-8")) if uip.exists() else {}
            meta = fm.get("metadata") if isinstance(fm.get("metadata"), dict) else {}
            decl = _decl(ui, meta)
            if not decl:
                continue
            found[name] = {
                "name": name, "source": str(md.parent), "agent": decl,
                "label": ui.get("label") or meta.get("agent-label") or name,
                "icon": ui.get("icon") or meta.get("agent-icon") or "✨",
                "description": fm.get("description", ""),
                "inputs": ui.get("inputs") or [{"name": "args", "label": "추가 지시", "type": "textarea", "default": ""}],
                "prompt": ui.get("prompt") or f"/{name} {{key}} ({{model_code}}) {{args}}"}
    return found


def applies(agent, product_line):
    d = agent["agent"]
    if product_line in d["exclude_product_lines"]:
        return False
    return "*" in d["product_lines"] or product_line in d["product_lines"]


def agents_for(product_line, agents=None):
    """[(skill, role)] of the agents that apply to a product line, in menu order."""
    agents = agents if agents is not None else load_agents()
    hits = [a for a in agents.values() if applies(a, product_line)]
    return [(a["name"], a["agent"].get("role")) for a in sorted(hits, key=lambda a: (a["agent"]["order"], a["name"]))]


def fill(template, ctx):
    """Fill {placeholders} from ctx (missing → empty) and collapse spaces."""
    out = template
    for _ in range(2):                         # defaults may themselves contain {placeholders}
        out = re.sub(r"\{(\w+)\}", lambda m: str(ctx.get(m.group(1), "") if ctx.get(m.group(1)) is not None else ""), out)
    return re.sub(r"[ \t]+", " ", out).strip()


def for_runner(runner, prompt, skill):
    """'/skill …' (catalog notation) → the runner's own way of invoking a skill."""
    return re.sub(rf"^/{re.escape(skill)}\b", SKILL_SYNTAX[runner].replace("{skill}", skill), prompt)
