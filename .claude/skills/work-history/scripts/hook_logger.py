#!/usr/bin/env python3
"""Claude Code hook -> work-history DB.

Reads the hook JSON payload from stdin (UserPromptSubmit / PostToolUse / SessionStart / Stop)
and records an activity for the current user. Never blocks the runner: always exits 0.
Other runners can call it the same way by piping a JSON object with the same keys.
"""
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from db import add_activity, connect, resolve_username  # noqa: E402


def summarize_tool(name, inp):
    inp = inp or {}
    if name == "Bash":
        return f"$ {inp.get('command', '')}"[:200]
    if name in ("Edit", "Write", "Read", "NotebookEdit"):
        return f"{name} {inp.get('file_path', '')}"
    if name == "Skill":
        return f"/{inp.get('skill', '')} 스킬 실행"
    return f"{name} {json.dumps(inp, ensure_ascii=False)[:150]}"


def main():
    try:
        payload = json.load(sys.stdin)
    except Exception:
        return
    event = payload.get("hook_event_name", "")
    common = dict(session_id=payload.get("session_id"),
                  runner=os.environ.get("WORK_HISTORY_RUNNER", "claude-code"),
                  project=Path(payload.get("cwd") or os.getcwd()).name)
    if event == "UserPromptSubmit":
        kind, summary, extra = "prompt", payload.get("prompt", "")[:300], {}
    elif event == "PostToolUse":
        name, inp = payload.get("tool_name", ""), payload.get("tool_input")
        resp = payload.get("tool_response")
        failed = isinstance(resp, dict) and (resp.get("is_error") or resp.get("interrupted"))
        kind = "skill" if name == "Skill" else "tool"
        extra = {"tool_name": name, "status": "failed" if failed else "success",
                 "skill_name": (inp or {}).get("skill") if name == "Skill" else None}
        summary = summarize_tool(name, inp)
    elif event == "SessionStart":
        kind, summary, extra = "note", f"세션 시작 ({payload.get('source', 'startup')})", {}
    elif event == "Stop":
        kind, summary, extra = "note", "응답 완료", {}
    else:
        return
    conn = connect()
    add_activity(conn, resolve_username(), kind, summary, **common, **extra)


if __name__ == "__main__":
    try:
        main()
    except Exception as e:  # never break the runner
        print(f"[work-history] {e}", file=sys.stderr)
    sys.exit(0)
