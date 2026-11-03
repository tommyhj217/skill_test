#!/usr/bin/env python3
"""Install the model-agents menu + the agent skills next to it into another project (or for the user).

  python3 install.py --project /path/to/repo [--runners claude,codex,copilot,gemini] [--copy] [--no-permissions]
  python3 install.py --user [--runners …]

Project layout:
  .agents/skills/<skill>                  canonical copy (Codex and Copilot read this folder)
  .claude/skills/<skill>  -> link         Claude Code
  .gemini/skills/<skill>  -> link         Gemini CLI
  .claude/settings.json   permissions.allow for this skill's view.py / models.py (merged, not overwritten),
                          because a skill's `allowed-tools` only covers the turn it was invoked in.
Re-running is idempotent. Symlinks fall back to copies where they are not allowed.
"""
import argparse
import json
import os
import shutil
from pathlib import Path

SKILL_SRC = Path(__file__).resolve().parent.parent          # …/skills/model-agents
ALL = ["claude", "codex", "copilot", "gemini"]
LINK_DIRS = {"claude": ".claude/skills", "gemini": ".gemini/skills"}   # codex / copilot read .agents/skills
USER_DIRS = {"claude": ".claude/skills", "codex": ".agents/skills", "copilot": ".copilot/skills",
             "gemini": ".gemini/skills"}
CLAUDE_ALLOW = [f"Bash(python3 {d}/model-agents/scripts/{f}:*)"
                for d in (".agents/skills", ".claude/skills") for f in ("view.py", "models.py")]
IGNORE = shutil.ignore_patterns("data", "__pycache__", "tests")


def _is_agent(d):
    if (d / "ui.json").exists() and json.loads((d / "ui.json").read_text(encoding="utf-8")).get("agent"):
        return True
    md = d / "SKILL.md"
    return md.exists() and "model-agent:" in md.read_text(encoding="utf-8").split("---", 2)[1]


def bundle():
    """This menu skill + every sibling skill that declares itself a model agent."""
    return [SKILL_SRC] + [d for d in sorted(SKILL_SRC.parent.iterdir())
                          if d != SKILL_SRC and (d / "SKILL.md").exists() and _is_agent(d)]


def link(target, dest, copy=False):
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.is_symlink() or dest.exists():
        return f"exists  {dest}"
    if not copy:
        try:
            dest.symlink_to(os.path.relpath(target, dest.parent), target_is_directory=True)
            return f"link    {dest}"
        except OSError:
            pass
    shutil.copytree(target, dest, ignore=IGNORE)
    return f"copy    {dest}"


def allow_claude(root):
    path = root / ".claude" / "settings.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    cfg = json.loads(path.read_text(encoding="utf-8")) if path.exists() and path.read_text().strip() else {}
    allow = cfg.setdefault("permissions", {}).setdefault("allow", [])
    added = [x for x in CLAUDE_ALLOW if x not in allow]
    if added or not path.exists():
        allow += added
        path.write_text(json.dumps(cfg, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return f"allow   {path} (+{len(added)})"


def install_project(root, runners, copy=False, permissions=True):
    root, log = root.resolve(), []
    for src in bundle():
        canonical = root / ".agents" / "skills" / src.name
        if canonical.resolve() != src.resolve():
            if canonical.exists():
                log.append(f"exists  {canonical}")
            else:
                canonical.parent.mkdir(parents=True, exist_ok=True)
                shutil.copytree(src, canonical, ignore=IGNORE)
                log.append(f"copy    {canonical}")
        log += [link(canonical, root / LINK_DIRS[r] / src.name, copy) for r in runners if r in LINK_DIRS]
    if permissions and "claude" in runners:
        log.append(allow_claude(root))
    return log


def main():
    ap = argparse.ArgumentParser()
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--project", type=Path)
    g.add_argument("--user", action="store_true")
    ap.add_argument("--runners", default=",".join(ALL))
    ap.add_argument("--copy", action="store_true", help="copy instead of symlink")
    ap.add_argument("--no-permissions", action="store_true", help="do not touch .claude/settings.json")
    a = ap.parse_args()
    runners = [r.strip() for r in a.runners.split(",") if r.strip()]
    if set(runners) - set(ALL):
        ap.error(f"unknown runners: {set(runners) - set(ALL)}")
    if a.user:
        log = [link(s, Path.home() / USER_DIRS[r] / s.name, a.copy) for s in bundle() for r in runners]
    else:
        log = install_project(a.project, runners, a.copy, not a.no_permissions)
    print("\n".join(log))


if __name__ == "__main__":
    main()
