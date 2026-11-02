#!/usr/bin/env python3
"""Runner adapters: how to launch / resume / parse each AI CLI.

Adapters are declared in ../runners.json (plus optional ~/.config/work-history/runners.json
or WORK_HISTORY_RUNNERS_FILE for overrides). Every adapter yields *normalized events*:

  {"type": "session", "id": ...}      session/thread id announced by the CLI
  {"type": "text",    "text": ...}    assistant text
  {"type": "tool",    "text": ...}    tool call summary
  {"type": "result",  "text": ..., "is_error": bool, "cost": float|None, "duration_ms": int|None}
  {"type": "log",     "text": ...}    anything unparsed
"""
import json
import os
import re
import shlex
import shutil
from pathlib import Path

SKILL_DIR = Path(__file__).resolve().parent.parent


def _load():
    files = [SKILL_DIR / "runners.json", Path.home() / ".config" / "work-history" / "runners.json"]
    if os.environ.get("WORK_HISTORY_RUNNERS_FILE"):
        files.append(Path(os.environ["WORK_HISTORY_RUNNERS_FILE"]))
    out = {}
    for f in files:
        if f.exists():
            for k, v in json.loads(f.read_text(encoding="utf-8")).items():
                if not k.startswith("_"):
                    out[k] = {**out.get(k, {}), **v}
    return out


ADAPTERS = _load()


def canonical(name):
    """Map a runner name stored in history (e.g. 'claude-code') to an adapter key."""
    if not name:
        return None
    if name in ADAPTERS:
        return name
    for key, a in ADAPTERS.items():
        if name in a.get("aliases", []):
            return key
    return None


def available():
    """[{id,label,installed,can_resume}] for every adapter + the mock runner."""
    rows = [{"id": k, "label": a.get("label", k), "installed": bool(shutil.which(a.get("bin", k))),
             "can_resume": bool(a.get("resume"))} for k, a in ADAPTERS.items()]
    rows.append({"id": "mock", "label": "Mock (데모)", "installed": True, "can_resume": True})
    return rows


def is_installed(key):
    return key == "mock" or (key in ADAPTERS and bool(shutil.which(ADAPTERS[key].get("bin", key))))


def skill_ref(key, skill):
    """How a runner expects a skill to be invoked inside a prompt."""
    syntax = ADAPTERS.get(key, {}).get("skill_syntax", "Use the {skill} skill:")
    return syntax.replace("{skill}", skill)


def adapt_prompt(key, prompt, skill=None):
    """Rewrite a leading '/skill' token (catalog notation) into the runner's own syntax."""
    if not skill or key not in ADAPTERS:  # mock / unknown: keep catalog notation
        return prompt
    return re.sub(rf"^/{re.escape(skill)}\b", lambda m: skill_ref(key, skill), prompt)


def build_cmd(key, prompt, session=None):
    a = ADAPTERS[key]
    tpl = a["resume"] if session and a.get("resume") else a["run"]
    cmd = [part.replace("{prompt}", prompt).replace("{session}", session or "") for part in tpl]
    cmd += shlex.split(os.environ.get(a.get("extra_args_env", ""), "")) if a.get("extra_args_env") else []
    return cmd


def child_env(key):
    strip = set(ADAPTERS.get(key, {}).get("strip_env", []))
    return {k: v for k, v in os.environ.items() if k not in strip}


# ───────────── output parsers ─────────────

def _tool_hint(name, inp):
    inp = inp if isinstance(inp, dict) else {}
    hint = inp.get("command") or inp.get("file_path") or inp.get("path") or inp.get("skill") or ""
    return f"{name} {hint}".strip()[:200]


def parse_claude(ev):
    t = ev.get("type")
    out = []
    if ev.get("session_id"):
        out.append({"type": "session", "id": ev["session_id"]})
    if t == "assistant":
        for b in ev.get("message", {}).get("content", []):
            if b.get("type") == "text" and b.get("text", "").strip():
                out.append({"type": "text", "text": b["text"]})
            elif b.get("type") == "tool_use":
                out.append({"type": "tool", "text": _tool_hint(b.get("name", ""), b.get("input"))})
    elif t == "result":
        out.append({"type": "result", "text": ev.get("result") or "", "is_error": bool(ev.get("is_error")),
                    "cost": ev.get("total_cost_usd"), "duration_ms": ev.get("duration_ms")})
    return out


def parse_codex(ev):
    """codex exec --json: thread.started / item.* / turn.completed / turn.failed / error."""
    t = ev.get("type", "")
    if t == "thread.started":
        return [{"type": "session", "id": ev.get("thread_id")}]
    if t == "item.completed":
        it = ev.get("item", {})
        kind = it.get("type")
        if kind == "agent_message":
            return [{"type": "text", "text": it.get("text", ""), "_final_candidate": True}]
        if kind == "command_execution":
            return [{"type": "tool", "text": f"$ {it.get('command', '')}"[:200]}]
        if kind == "file_change":
            files = ", ".join(c.get("path", "") for c in it.get("changes", []))
            return [{"type": "tool", "text": f"edit {files}"[:200]}]
        if kind in ("mcp_tool_call", "web_search"):
            return [{"type": "tool", "text": f"{kind} {it.get('tool') or it.get('query') or ''}"[:200]}]
        return []
    if t == "turn.completed":
        return [{"type": "result", "text": None, "is_error": False, "usage": ev.get("usage")}]
    if t in ("turn.failed", "error"):
        msg = ev.get("message") or (ev.get("error") or {}).get("message") or json.dumps(ev)[:300]
        return [{"type": "result", "text": msg, "is_error": True}]
    return []


def parse_gemini(ev):
    """gemini -p --output-format stream-json: init / message / tool_use / tool_result / error / result."""
    t = ev.get("type")
    if t == "init":
        return [{"type": "session", "id": ev.get("session_id")}]
    if t == "message" and ev.get("role") == "assistant":
        return [{"type": "text", "text": ev.get("content", ""), "_delta": bool(ev.get("delta"))}]
    if t == "tool_use":
        return [{"type": "tool", "text": _tool_hint(ev.get("tool_name", ""), ev.get("parameters"))}]
    if t == "error":
        return [{"type": "log", "text": ev.get("message", json.dumps(ev))}]
    if t == "result":
        return [{"type": "result", "text": None, "is_error": ev.get("status") == "error",
                 "duration_ms": (ev.get("stats") or {}).get("duration_ms")}]
    return []


PARSERS = {"claude-stream": parse_claude, "codex-jsonl": parse_codex, "gemini-stream": parse_gemini}


class StreamNormalizer:
    """Feeds raw stdout lines, yields normalized events; fills result text from last message."""

    def __init__(self, key):
        self.fmt = ADAPTERS[key].get("format", "text")
        self.parser = PARSERS.get(self.fmt)
        self.texts = []
        self.delta_buf = ""

    def feed(self, line):
        line = line.rstrip("\n")
        if not line.strip():
            return []
        if not self.parser:
            self.texts.append(line)
            return [{"type": "text", "text": line}]
        try:
            ev = json.loads(line)
        except ValueError:
            return [{"type": "log", "text": line}]
        out = []
        for e in self.parser(ev):
            if e.get("_delta"):                       # gemini streams text in chunks
                self.delta_buf += e["text"]
                continue
            if self.delta_buf and e["type"] != "text":
                out.append(self._flush_delta())
            if e["type"] == "text":
                self.texts.append(e["text"])
            if e["type"] == "result" and not e.get("text"):
                e["text"] = self.texts[-1] if self.texts else ""
            out.append({k: v for k, v in e.items() if not k.startswith("_")})
        return out

    def _flush_delta(self):
        t, self.delta_buf = self.delta_buf, ""
        self.texts.append(t)
        return {"type": "text", "text": t}

    def close(self, returncode):
        """Events to emit when the process exits (text runners have no result event)."""
        out = [self._flush_delta()] if self.delta_buf else []
        if not self.parser:
            out.append({"type": "result", "text": "\n".join(self.texts), "is_error": returncode != 0})
        return out
