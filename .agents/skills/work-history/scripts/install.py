#!/usr/bin/env python3
"""Install the work-history skill + recording hooks for several AI runners at once.

  python3 install.py --project /path/to/repo [--runners claude,codex,copilot,gemini] [--copy]
  python3 install.py --user [--runners ...]          # skill links in ~/.<runner>/skills only

Project layout produced (canonical copy lives in .agents/skills, which Codex and Copilot read):
  .agents/skills/work-history/            skill (copied unless it is already there)
  .claude/skills/work-history  -> link    Claude Code
  .github/skills/work-history  -> link    Copilot (it also reads .agents/.claude)  [only with --github-link]
  .gemini/skills/work-history  -> link    Gemini CLI
  hooks: .claude/settings.json · .codex/hooks.json · .github/hooks/work-history.json · .gemini/settings.json
Existing JSON settings are merged, never overwritten; re-running is idempotent.
"""
import argparse
import json
import os
import shutil
from pathlib import Path

SKILL_SRC = Path(__file__).resolve().parent.parent
NAME = "work-history"
REL = f".agents/skills/{NAME}/scripts/hook_logger.py"
ALL = ["claude", "codex", "copilot", "gemini"]
MARK = "hook_logger.py"


def _git_root_cmd(runner):
    return f'python3 "$(git rev-parse --show-toplevel 2>/dev/null || pwd)/{REL}" --runner {runner}'


def _merge_event_hooks(cfg, event, entry):
    """Append entry under cfg['hooks'][event] unless a work-history hook is already there."""
    lst = cfg.setdefault("hooks", {}).setdefault(event, [])
    if MARK in json.dumps(lst):
        return False
    lst.append(entry)
    return True


def _rw_json(path, fn):
    path.parent.mkdir(parents=True, exist_ok=True)
    cfg = json.loads(path.read_text(encoding="utf-8")) if path.exists() and path.read_text().strip() else {}
    changed = fn(cfg)
    if changed or not path.exists():
        path.write_text(json.dumps(cfg, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return path


CLAUDE_ALLOW = [f"Bash(python3 {d}/work-history/scripts/{f}:*)"
                for d in (".agents/skills", ".claude/skills") for f in ("view.py", "models.py")]


def hooks_claude(root, allow_menu=True):
    cmd = f'python3 "$CLAUDE_PROJECT_DIR/{REL}" --runner claude'

    def fn(cfg):
        added = []
        if allow_menu:
            # The skill's `allowed-tools` only covers the turn it was invoked in; the menu's follow-up
            # turns run view.py / models.py again, so allow exactly those two scripts for the project.
            allow = cfg.setdefault("permissions", {}).setdefault("allow", [])
            added = [x for x in CLAUDE_ALLOW if x not in allow]
            allow += added
        a = _merge_event_hooks(cfg, "UserPromptSubmit", {"hooks": [{"type": "command", "command": cmd}]})
        b = _merge_event_hooks(cfg, "PostToolUse", {"matcher": "Bash|Edit|Write|Skill",
                                                    "hooks": [{"type": "command", "command": cmd}]})
        return a or b or bool(added)
    return _rw_json(root / ".claude" / "settings.json", fn)


def hooks_codex(root):
    cmd = _git_root_cmd("codex")

    def fn(cfg):
        a = _merge_event_hooks(cfg, "UserPromptSubmit", {"hooks": [{"type": "command", "command": cmd}]})
        b = _merge_event_hooks(cfg, "PostToolUse", {"matcher": ".*",
                                                    "hooks": [{"type": "command", "command": cmd}]})
        return a or b
    return _rw_json(root / ".codex" / "hooks.json", fn)


def hooks_copilot(root):
    def entry(event):
        return {"type": "command", "cwd": ".", "timeoutSec": 10,
                "bash": f"python3 {REL} --runner copilot --event {event}",
                "powershell": f"python {REL.replace('/', chr(92))} --runner copilot --event {event}"}

    def fn(cfg):
        cfg.setdefault("version", 1)
        changed = False
        for ev in ("sessionStart", "userPromptSubmitted", "postToolUse"):
            changed |= _merge_event_hooks(cfg, ev, entry(ev))
        return changed
    return _rw_json(root / ".github" / "hooks" / f"{NAME}.json", fn)


def hooks_gemini(root):
    cmd = _git_root_cmd("gemini")

    def fn(cfg):
        cfg.setdefault("tools", {})["enableHooks"] = True        # hooks are opt-in in Gemini CLI
        cfg.setdefault("hooks", {})["enabled"] = True
        a = _merge_event_hooks(cfg, "BeforeAgent", {"matcher": "*", "hooks": [{"type": "command", "command": cmd}]})
        b = _merge_event_hooks(cfg, "AfterTool", {"matcher": "*", "hooks": [{"type": "command", "command": cmd}]})
        return True if (a or b) else False
    return _rw_json(root / ".gemini" / "settings.json", fn)


HOOKS = {"claude": hooks_claude, "codex": hooks_codex, "copilot": hooks_copilot, "gemini": hooks_gemini}
LINK_DIRS = {"claude": ".claude/skills", "gemini": ".gemini/skills"}  # codex/copilot read .agents/skills
USER_DIRS = {"claude": ".claude/skills", "codex": ".agents/skills", "copilot": ".copilot/skills",
             "gemini": ".gemini/skills"}


def link(target, dest, copy=False):
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.is_symlink() or dest.exists():
        return f"exists  {dest}"
    if not copy:
        try:
            dest.symlink_to(os.path.relpath(target, dest.parent), target_is_directory=True)
            return f"link    {dest} -> {target}"
        except OSError:
            pass  # e.g. Windows without symlink rights
    shutil.copytree(target, dest, ignore=shutil.ignore_patterns("data", "__pycache__"))
    return f"copy    {dest}"


def _is_agent_skill(d):
    if not (d / "SKILL.md").exists():
        return False
    if d.name == "model-agents":                       # the menu itself
        return True
    ui = d / "ui.json"
    if ui.exists() and json.loads(ui.read_text(encoding="utf-8")).get("agent"):
        return True
    return "model-agent:" in (d / "SKILL.md").read_text(encoding="utf-8").split("---", 2)[1]


def bundle():
    """work-history + the model-agents menu + every sibling skill that declares itself a model agent."""
    return [SKILL_SRC] + [d for d in sorted(SKILL_SRC.parent.iterdir()) if d != SKILL_SRC and _is_agent_skill(d)]


def install_project(root, runners, copy=False, github_link=False):
    root = root.resolve()
    log = []
    for src in bundle():
        canonical = root / ".agents" / "skills" / src.name
        if canonical.resolve() != src.resolve():
            if canonical.exists():
                log.append(f"exists  {canonical}")
            else:
                canonical.parent.mkdir(parents=True, exist_ok=True)
                shutil.copytree(src, canonical, ignore=shutil.ignore_patterns("data", "__pycache__"))
                log.append(f"copy    {canonical}")
        for r in runners:
            if r in LINK_DIRS:
                log.append(link(canonical, root / LINK_DIRS[r] / src.name, copy))
            if r == "copilot" and github_link:
                log.append(link(canonical, root / ".github" / "skills" / src.name, copy))
    for r in runners:
        log.append(f"hooks   {HOOKS[r](root)}")
    return log


def install_user(runners, copy=False):
    return [link(src, Path.home() / USER_DIRS[r] / src.name, copy) for src in bundle() for r in runners]


def main():
    ap = argparse.ArgumentParser()
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--project", type=Path)
    g.add_argument("--user", action="store_true")
    ap.add_argument("--runners", default=",".join(ALL))
    ap.add_argument("--copy", action="store_true", help="copy instead of symlink")
    ap.add_argument("--github-link", action="store_true", help="also add .github/skills link for Copilot")
    a = ap.parse_args()
    runners = [r.strip() for r in a.runners.split(",") if r.strip()]
    bad = set(runners) - set(ALL)
    if bad:
        ap.error(f"unknown runners: {', '.join(bad)}")
    log = install_user(runners, a.copy) if a.user else install_project(a.project, runners, a.copy, a.github_link)
    print("\n".join(log))


if __name__ == "__main__":
    main()
