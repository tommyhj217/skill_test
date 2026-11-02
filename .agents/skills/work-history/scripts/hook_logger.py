#!/usr/bin/env python3
"""Universal hook → work-history DB.

  hook_logger.py [--runner claude|codex|copilot|gemini|auto] [--event NAME]

Reads one JSON payload on stdin (as hooks of Claude Code, Codex, Copilot CLI and Gemini CLI
send it), normalizes it and records an activity for the current user. Never blocks the
runner: always exits 0.

Payload shapes handled
  claude / codex : snake_case  hook_event_name, session_id, cwd, prompt | tool_name, tool_input, tool_response
  gemini         : same snake_case keys; events BeforeAgent (prompt) / AfterTool / SessionStart / SessionEnd
  copilot        : camelCase    sessionId, timestamp(ms), cwd, prompt | toolName, toolArgs, toolResult
                   (event name is not in the payload → pass --event userPromptSubmitted etc.)
  anything else  : pass the same snake_case keys yourself, e.g. from a wrapper script
"""
import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from db import add_activity, connect, resolve_username  # noqa: E402

# event name (any runner) → normalized kind
EVENT_MAP = {
    "UserPromptSubmit": "prompt", "userPromptSubmitted": "prompt", "BeforeAgent": "prompt",
    "PostToolUse": "tool", "postToolUse": "tool", "AfterTool": "tool",
    "SessionStart": "start", "sessionStart": "start",
    "Stop": "stop", "SessionEnd": "stop", "sessionEnd": "stop", "AfterAgent": "stop",
}
RUNNER_NAME = {"claude": "claude-code", "codex": "codex", "copilot": "copilot", "gemini": "gemini"}


def detect_runner(payload, event):
    if "sessionId" in payload or "toolName" in payload or (event or "")[:1].islower():
        return "copilot"
    ev = payload.get("hook_event_name", event or "")
    if ev in ("BeforeAgent", "AfterTool", "AfterAgent"):
        return "gemini"
    if "turn_id" in payload or "model" in payload and "transcript_path" in payload:
        return "codex"
    return os.environ.get("WORK_HISTORY_RUNNER_NAME", "claude")


def normalize(payload, runner="auto", event=None):
    """→ dict(kind, summary, session_id, cwd, runner, tool_name, skill_name, status, ts) or None."""
    if runner == "auto":
        runner = detect_runner(payload, event)
    g = lambda *keys: next((payload[k] for k in keys if payload.get(k) is not None), None)
    ev = event or g("hook_event_name", "hookEventName", "event")
    kind = EVENT_MAP.get(ev)
    if not kind:
        return None
    ts = g("timestamp")
    if isinstance(ts, (int, float)):  # copilot: unix ms
        ts = datetime.fromtimestamp(ts / 1000, timezone.utc).isoformat(timespec="seconds")
    out = {"session_id": g("session_id", "sessionId"), "cwd": g("cwd") or os.getcwd(),
           "runner": RUNNER_NAME.get(runner, runner), "ts": ts if isinstance(ts, str) else None,
           "tool_name": None, "skill_name": None, "status": "success"}
    if kind == "prompt":
        out.update(kind="prompt", summary=(g("prompt", "initialPrompt") or "")[:300])
    elif kind == "tool":
        name = g("tool_name", "toolName") or ""
        inp = g("tool_input", "toolArgs") or {}
        if isinstance(inp, str):
            try:
                inp = json.loads(inp)
            except ValueError:
                inp = {"command": inp}
        resp = g("tool_response", "toolResult")
        failed = isinstance(resp, dict) and (resp.get("is_error") or resp.get("interrupted")
                                             or resp.get("resultType") in ("failure", "error"))
        is_skill = name.lower() in ("skill", "activate_skill")
        skill = (inp.get("skill") or inp.get("name")) if is_skill and isinstance(inp, dict) else None
        out.update(kind="skill" if is_skill else "tool", tool_name=name, skill_name=skill,
                   status="failed" if failed else "success", summary=summarize_tool(name, inp, skill))
    elif kind == "start":
        out.update(kind="note", summary=f"세션 시작 ({g('source') or 'startup'})")
    else:
        out.update(kind="note", summary=f"세션 종료 ({g('reason') or 'stop'})" if ev in (
            "SessionEnd", "sessionEnd") else "응답 완료")
    return out


def summarize_tool(name, inp, skill=None):
    inp = inp if isinstance(inp, dict) else {}
    if skill:
        return f"{skill} 스킬 실행"
    if inp.get("command"):
        return f"$ {inp['command']}"[:200]
    path = inp.get("file_path") or inp.get("path") or inp.get("absolute_path")
    if path:
        return f"{name} {path}"
    return f"{name} {json.dumps(inp, ensure_ascii=False)[:150]}"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--runner", default="auto")
    ap.add_argument("--event", default=None)
    a = ap.parse_args()
    try:
        payload = json.load(sys.stdin)
    except Exception:
        return
    rec = normalize(payload, a.runner, a.event)
    if not rec:
        return
    conn = connect()
    add_activity(conn, resolve_username(), rec.pop("kind"), rec.pop("summary"),
                 project=Path(rec.pop("cwd")).name, **rec)


if __name__ == "__main__":
    try:
        main()
    except Exception as e:  # never break the runner
        print(f"[work-history] {e}", file=sys.stderr)
    sys.exit(0)
