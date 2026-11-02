#!/usr/bin/env python3
"""Discover skills and their UI descriptors, and suggest next actions for an activity.

A skill can describe how it should appear in the work-history UI with an optional
`ui.json` next to its SKILL.md:

{
  "label": "코드 리뷰", "icon": "🔍",
  "applies_to": {"kinds": ["tool"], "tools": ["Edit","Write"], "tags": ["bugfix"], "skills": []},
  "inputs": [ {"name": "level", "label": "강도", "type": "select", "options": ["low","high"], "default": "low"},
              {"name": "target", "label": "대상", "type": "text", "default": "{project}"} ],
  "prompt": "/code-review {level} {target}",
  "output": "markdown"            # markdown | log | table
}

Skills that are not in this repo (built-in / plugin skills) can be described in
`catalog.json` in the work-history skill directory with the same shape plus "name".
Skills without any descriptor still show up with a generic one-line prompt form.
"""
import json
import os
import re
from pathlib import Path

import runners

SKILL_DIR = Path(__file__).resolve().parent.parent
CATALOG = SKILL_DIR / "catalog.json"


# Skill folders used by the different runners (Agent Skills / SKILL.md format is shared):
#   .agents/skills  – Codex, Copilot (and the canonical home of this skill)
#   .claude/skills  – Claude Code (also read by Copilot)
#   .github/skills  – GitHub Copilot
#   .gemini/skills  – Gemini CLI
RUNNER_SKILL_DIRS = [".agents/skills", ".claude/skills", ".github/skills", ".gemini/skills"]
USER_SKILL_DIRS = [".agents/skills", ".claude/skills", ".copilot/skills", ".codex/skills", ".gemini/skills"]


def project_root():
    if os.environ.get("WORK_HISTORY_PROJECT"):
        return Path(os.environ["WORK_HISTORY_PROJECT"])
    # <root>/<.runner>/skills/work-history
    return SKILL_DIR.parent.parent.parent


def _skill_roots():
    roots = []
    env = os.environ.get("WORK_HISTORY_SKILL_ROOTS")
    if env:
        roots += [Path(p) for p in env.split(os.pathsep)]
    roots += [project_root() / d for d in RUNNER_SKILL_DIRS]
    roots += [Path.home() / d for d in USER_SKILL_DIRS]
    seen, out = set(), []
    for r in roots:
        if r.is_dir() and r.resolve() not in seen:
            seen.add(r.resolve())
            out.append(r)
    return out


def _frontmatter(text):
    m = re.match(r"^---\s*\n(.*?)\n---", text, re.S)
    out = {}
    if m:
        for line in m.group(1).splitlines():
            if ":" in line and not line.startswith(" "):
                k, v = line.split(":", 1)
                out[k.strip()] = v.strip().strip('"\'')
    return out


def _normalize(d, name):
    d = dict(d)
    d["name"] = name
    d.setdefault("label", name)
    d.setdefault("icon", "✨")
    d.setdefault("applies_to", {})
    d.setdefault("inputs", [{"name": "args", "label": "추가 지시", "type": "textarea", "default": ""}])
    d.setdefault("prompt", f"/{name} {{args}}")
    d.setdefault("output", "markdown")
    return d


def load_skills():
    skills = {}
    if CATALOG.exists():
        for item in json.loads(CATALOG.read_text(encoding="utf-8")):
            skills[item["name"]] = _normalize(item, item["name"]) | {"source": "catalog"}
    for root in _skill_roots():
        for md in sorted(root.glob("*/SKILL.md")):
            fm = _frontmatter(md.read_text(encoding="utf-8"))
            name = fm.get("name") or md.parent.name
            if skills.get(name, {}).get("source", "catalog") != "catalog":
                continue  # same skill already found via another runner's folder
            ui = md.parent / "ui.json"
            desc = json.loads(ui.read_text(encoding="utf-8")) if ui.exists() else {}
            skills[name] = _normalize({**skills.get(name, {}), **desc}, name) | {
                "source": str(md.parent), "description": fm.get("description", "")[:200]}
    return skills


def _score(skill, act):
    a = skill["applies_to"]
    s = 0
    if act.get("skill_name") == skill["name"]:
        s += 10
    if act.get("kind") in a.get("kinds", []):
        s += 2
    if act.get("tool_name") and act["tool_name"] in a.get("tools", []):
        s += 3
    if act.get("skill_name") and act["skill_name"] in a.get("skills", []):
        s += 4
    tags = set((act.get("tags") or "").split(","))
    s += 2 * len(tags & set(a.get("tags", [])))
    if a.get("always"):
        s += 1
    return s


def suggest(act, limit=6):
    """Ordered list of actions for an activity: continue → rerun → related skills → free prompt."""
    skills = load_skills()
    actions = []
    rkey = runners.canonical(act.get("runner"))
    if rkey and runners.ADAPTERS[rkey].get("resume") and _is_real_session(act.get("session_id")):
        label = runners.ADAPTERS[rkey].get("label", rkey)
        actions.append({"id": "continue", "label": f"이 세션 이어서 하기", "icon": "↪", "runner": rkey,
                        "runners": [rkey], "reason": f"원래 {label} 세션을 resume으로 이어감",
                        "inputs": [{"name": "args", "label": "이어서 할 지시", "type": "textarea",
                                    "default": "앞에서 하던 작업을 이어서 진행해줘."}],
                        "prompt": "{args}", "output": "markdown", "resume": True})
    ranked = sorted(skills.values(), key=lambda s: -_score(s, act))
    for sk in ranked:
        sc = _score(sk, act)
        if sc <= 0 or len(actions) >= limit or sk.get("agent"):
            continue
        reason = "같은 스킬 다시 실행" if act.get("skill_name") == sk["name"] else "이 작업과 관련된 스킬"
        actions.append({**sk, "id": f"skill:{sk['name']}", "reason": reason, "score": sc})
    actions.append({"id": "free", "label": "직접 지시하기", "icon": "💬", "reason": "자유 프롬프트",
                    "inputs": [{"name": "args", "label": "지시", "type": "textarea",
                                "default": "다음 작업을 이어서 해줘: {summary}"}],
                    "prompt": "{args}", "output": "markdown"})
    return actions


def _is_real_session(sid):
    """Seed/demo ids (sess_*, mock-*) can't be resumed; anything else came from a real runner."""
    return bool(sid) and not sid.startswith(("sess_", "mock-"))


def render_prompt(template, values, act, keep_newlines=False):
    ctx = {k: v for k, v in (act or {}).items() if v is not None and not isinstance(v, (dict, list))}
    ctx.update({k: v for k, v in values.items() if v is not None})

    def sub(m):
        return str(ctx.get(m.group(1), m.group(0) if keep_newlines else ""))
    out = re.sub(r"\{(\w+)\}", sub, template)
    out = re.sub(r"\{(\w+)\}", sub, out)  # second pass: defaults may contain {summary}
    if keep_newlines:
        return out
    return re.sub(r"[ \t]+", " ", out).strip()


def agent_action(skill_name):
    """The UI action (form) for an agent's skill."""
    sk = load_skills().get(skill_name)
    if not sk:
        return None
    return {**sk, "id": f"skill:{skill_name}", "reason": "Jira 모델 에이전트"}


if __name__ == "__main__":
    print(json.dumps(list(load_skills().keys()), ensure_ascii=False))
