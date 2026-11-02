#!/usr/bin/env python3
"""Background execution of skill actions.

Runner modes:
  claude : `claude -p <prompt> --output-format stream-json --verbose [--resume <sid>]`
           extra flags from WORK_HISTORY_CLAUDE_ARGS (e.g. "--permission-mode acceptEdits")
  mock   : simulated streaming output (no API calls) – for demos / tests
Each run is stored in the `runs` table; events are appended as JSON lines so the UI can poll.
"""
import json
import os
import shlex
import shutil
import subprocess
import threading
import time
from datetime import datetime, timezone

import db

_procs = {}
_lock = threading.Lock()


def default_mode():
    return os.environ.get("WORK_HISTORY_RUNNER") or ("claude" if shutil.which("claude") else "mock")


def _now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _append(run_id, db_path, **event):
    conn = db.connect(db_path)
    conn.execute("UPDATE runs SET events = events || ? WHERE id=?",
                 (json.dumps(event, ensure_ascii=False) + "\n", run_id))
    conn.commit()
    conn.close()


def _finish(run_id, db_path, status, session_id=None):
    conn = db.connect(db_path)
    conn.execute("UPDATE runs SET status=?, ended_at=?, session_id=COALESCE(?, session_id) WHERE id=?",
                 (status, _now(), session_id, run_id))
    conn.execute("UPDATE activities SET status=? WHERE id=(SELECT result_activity_id FROM runs WHERE id=?)",
                 ("success" if status == "done" else "failed", run_id))
    if session_id and not session_id.startswith("mock-"):
        # link the result row to the real Claude session so it can be resumed from the UI
        row = conn.execute("SELECT user_id, result_activity_id FROM runs WHERE id=?", (run_id,)).fetchone()
        project = conn.execute("SELECT s.project FROM activities a LEFT JOIN sessions s ON s.id=a.session_id "
                               "WHERE a.id=(SELECT source_activity_id FROM runs WHERE id=?)", (run_id,)).fetchone()
        db.ensure_session(conn, session_id, row["user_id"], "claude-code", project["project"] if project else None)
        conn.execute("UPDATE activities SET session_id=? WHERE id=?", (session_id, row["result_activity_id"]))
    conn.commit()
    conn.close()


def start(conn, db_path, username, prompt, action_id, skill_name=None, source_activity=None,
          resume_session=None, workdir=None, mode=None):
    mode = mode or default_mode()
    user = db.get_or_create_user(conn, username)
    src_id = source_activity["id"] if source_activity else None
    project = (source_activity or {}).get("project")
    act_id = db.add_activity(conn, username, "skill" if skill_name else "prompt",
                             f"[UI] {prompt}"[:300], skill_name=skill_name, status="in_progress",
                             detail=f"from activity #{src_id}" if src_id else None,
                             session_id=resume_session, project=project, runner="web-ui")
    cur = conn.execute(
        """INSERT INTO runs(user_id, source_activity_id, result_activity_id, action_id, skill_name,
                            prompt, mode, status, session_id, started_at, events)
           VALUES (?,?,?,?,?,?,?,?,?,?, '')""",
        (user["id"], src_id, act_id, action_id, skill_name, prompt, mode, "running",
         resume_session, _now()))
    conn.commit()
    run_id = cur.lastrowid
    target = _run_claude if mode == "claude" else _run_mock
    threading.Thread(target=target, args=(run_id, db_path, prompt, resume_session, workdir),
                     daemon=True).start()
    return run_id


def cancel(run_id):
    with _lock:
        p = _procs.get(run_id)
    if p and p.poll() is None:
        p.terminate()
        return True
    return False


def _run_claude(run_id, db_path, prompt, resume, workdir):
    cmd = ["claude", "-p", prompt, "--output-format", "stream-json", "--verbose"]
    if resume:
        cmd += ["--resume", resume]
    cmd += shlex.split(os.environ.get("WORK_HISTORY_CLAUDE_ARGS", ""))
    _append(run_id, db_path, type="meta", text="$ " + " ".join(shlex.quote(c) for c in cmd[:3]) +
            (f" --resume {resume}" if resume else ""))
    session_id, status = None, "failed"
    try:
        # don't let a parent Claude Code session leak its identity into the child run
        env = {k: v for k, v in os.environ.items()
               if k not in ("CLAUDE_CODE_SESSION_ID", "CLAUDECODE", "CLAUDE_CODE_ENTRYPOINT")}
        p = subprocess.Popen(cmd, cwd=workdir or os.getcwd(), env=env, stdout=subprocess.PIPE,
                             stderr=subprocess.PIPE, text=True, bufsize=1)
        with _lock:
            _procs[run_id] = p
        for line in p.stdout:
            try:
                ev = json.loads(line)
            except ValueError:
                _append(run_id, db_path, type="log", text=line.rstrip())
                continue
            session_id = ev.get("session_id") or session_id
            t = ev.get("type")
            if t == "assistant":
                for block in ev.get("message", {}).get("content", []):
                    if block.get("type") == "text" and block["text"].strip():
                        _append(run_id, db_path, type="text", text=block["text"])
                    elif block.get("type") == "tool_use":
                        inp = block.get("input", {})
                        hint = inp.get("command") or inp.get("file_path") or inp.get("skill") or ""
                        _append(run_id, db_path, type="tool", text=f"{block['name']} {hint}"[:200])
            elif t == "result":
                status = "failed" if ev.get("is_error") else "done"
                _append(run_id, db_path, type="result", text=ev.get("result") or "",
                        cost=ev.get("total_cost_usd"), duration_ms=ev.get("duration_ms"))
        p.wait()
        err = p.stderr.read().strip()
        if p.returncode not in (0, None) and status != "done":
            if p.returncode < 0:
                status = "cancelled"
            _append(run_id, db_path, type="error", text=err[-2000:] or f"exit {p.returncode}")
    except Exception as e:
        _append(run_id, db_path, type="error", text=str(e))
    finally:
        with _lock:
            _procs.pop(run_id, None)
        _finish(run_id, db_path, status, session_id)


def _run_mock(run_id, db_path, prompt, resume, workdir):
    steps = [
        ("meta", f"[mock runner] prompt: {prompt}" + (f" (resume {resume})" if resume else "")),
        ("tool", "Read README.md"),
        ("tool", "Bash git log --oneline -5"),
        ("text", "요청한 작업을 확인했습니다. 관련 파일을 살펴보는 중입니다…"),
        ("tool", "Grep TODO"),
    ]
    for t, text in steps:
        time.sleep(0.4)
        _append(run_id, db_path, type=t, text=text)
    result = (f"## 결과 요약\n\n**요청**: {prompt}\n\n"
              "| 항목 | 상태 | 비고 |\n|---|---|---|\n"
              "| 파일 분석 | 완료 | 3개 파일 |\n| 이슈 | 2건 | 1건 수정 권장 |\n| 테스트 | 통과 | 12/12 |\n\n"
              "- 다음 단계: 수정 사항을 커밋하고 PR 생성\n- `mock` 모드 출력입니다 (실제 실행은 runner=claude)")
    _append(run_id, db_path, type="result", text=result, cost=0, duration_ms=2000)
    _finish(run_id, db_path, "done", resume or f"mock-{run_id}")
