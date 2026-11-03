#!/usr/bin/env python3
"""Install the model-agents menu + the agent skills next to it into another project (or for the user).

  python3 install.py --project /path/to/repo [--runners claude,codex,copilot,gemini] [--copy] [--update] [--no-permissions]
  python3 install.py --user [--runners …]
  (Windows: `python install.py …` or `py -3 install.py …`)

Project layout:
  .agents/skills/<skill>                  canonical copy (Codex and Copilot read this folder)
  .claude/skills/<skill>  -> link         Claude Code
  .gemini/skills/<skill>  -> link         Gemini CLI
  .claude/settings.json   permissions.allow for this skill's view.py / models.py (merged, not overwritten),
                          because a skill's `allowed-tools` only covers the turn it was invoked in.
Re-running is idempotent.

Windows: links are made as symlink → directory junction (no admin / developer mode needed) → copy.
A git checkout with core.symlinks=false turns the repo's .claude/skills/* links into small text files;
those are detected and replaced. `--update` refreshes copies after the skills changed.
"""
import argparse
import json
import os
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import compat  # noqa: E402

SKILL_SRC = Path(__file__).resolve().parent.parent          # …/skills/model-agents
ALL = ["claude", "codex", "copilot", "gemini"]
LINK_DIRS = {"claude": ".claude/skills", "gemini": ".gemini/skills"}   # codex / copilot read .agents/skills
USER_DIRS = {"claude": ".claude/skills", "codex": ".agents/skills", "copilot": ".copilot/skills",
             "gemini": ".gemini/skills"}
# python3 (macOS / Linux) and python (Windows, Git Bash) — the menu prints whichever fits the OS
CLAUDE_ALLOW = [f"Bash({py} {d}/model-agents/scripts/{f}:*)"
                for py in ("python3", "python") for d in (".agents/skills", ".claude/skills")
                for f in ("view.py", "models.py")]
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


def _git_link_stub(p):
    """A symlink checked out as a plain file (core.symlinks=false, typical on Windows)."""
    try:
        return p.is_file() and not p.is_symlink() and p.stat().st_size < 512 and \
            p.read_text(encoding="utf-8", errors="replace").strip().startswith("..")
    except OSError:
        return False


def _junction(target, dest):
    if not compat.IS_WINDOWS:
        return False
    try:
        import _winapi
        _winapi.CreateJunction(str(Path(target).resolve()), str(dest))
        return True
    except (ImportError, OSError):
        return False


def link(target, dest, copy=False, update=False):
    dest.parent.mkdir(parents=True, exist_ok=True)
    if _git_link_stub(dest):
        dest.unlink()
        note = " (replaced git symlink stub)"
    else:
        note = ""
    is_link = dest.is_symlink() or (dest.exists() and os.path.realpath(dest) !=
                                    os.path.join(os.path.realpath(dest.parent), dest.name))   # junction
    if is_link:
        return f"exists  {dest} (link)"
    if dest.is_dir():
        if not update:
            return f"exists  {dest} (copy; --update to refresh)"
        shutil.rmtree(dest)
        shutil.copytree(target, dest, ignore=IGNORE)
        return f"updated {dest}"
    if not copy:
        try:
            dest.symlink_to(os.path.relpath(target, dest.parent), target_is_directory=True)
            return f"link    {dest}{note}"
        except (OSError, ValueError):
            if _junction(target, dest):
                return f"junction {dest}{note}"
    shutil.copytree(target, dest, ignore=IGNORE)
    return f"copy    {dest}{note}"


def allow_claude(root):
    path = root / ".claude" / "settings.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    text = path.read_text(encoding="utf-8") if path.exists() else ""
    cfg = json.loads(text) if text.strip() else {}
    allow = cfg.setdefault("permissions", {}).setdefault("allow", [])
    added = [x for x in CLAUDE_ALLOW if x not in allow]
    if added or not path.exists():
        allow += added
        path.write_text(json.dumps(cfg, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return f"allow   {path} (+{len(added)})"


def install_project(root, runners, copy=False, permissions=True, update=False):
    root, log = root.resolve(), []
    for src in bundle():
        canonical = root / ".agents" / "skills" / src.name
        if canonical.resolve() != src.resolve():
            if canonical.exists() and not update:
                log.append(f"exists  {canonical}")
            else:
                if canonical.exists():
                    shutil.rmtree(canonical)
                canonical.parent.mkdir(parents=True, exist_ok=True)
                shutil.copytree(src, canonical, ignore=IGNORE)
                log.append(f"copy    {canonical}")
        log += [link(canonical, root / LINK_DIRS[r] / src.name, copy, update) for r in runners if r in LINK_DIRS]
    if permissions and "claude" in runners:
        log.append(allow_claude(root))
    return log


def main():
    compat.utf8_stdio()
    ap = argparse.ArgumentParser()
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--project", type=Path)
    g.add_argument("--user", action="store_true")
    ap.add_argument("--runners", default=",".join(ALL))
    ap.add_argument("--copy", action="store_true", help="copy instead of symlink")
    ap.add_argument("--update", action="store_true", help="refresh copied skills (after editing the source)")
    ap.add_argument("--no-permissions", action="store_true", help="do not touch .claude/settings.json")
    a = ap.parse_args()
    runners = [r.strip() for r in a.runners.split(",") if r.strip()]
    if set(runners) - set(ALL):
        ap.error(f"unknown runners: {set(runners) - set(ALL)}")
    if a.user:
        log = [link(s, Path.home() / USER_DIRS[r] / s.name, a.copy, a.update) for s in bundle() for r in runners]
    else:
        log = install_project(a.project, runners, a.copy, not a.no_permissions, a.update)
    print("\n".join(log))


if __name__ == "__main__":
    main()
