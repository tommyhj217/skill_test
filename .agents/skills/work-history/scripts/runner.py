#!/usr/bin/env python3
"""Background execution of UI actions on any AI runner (see runners.py / runners.json).

Each run is stored in the `runs` table; normalized events are appended as JSON lines so
the UI can poll them. The `mock` runner simulates output without calling any CLI.
"""
import json
import os
import subprocess
import threading
import time
from pathlib import Path
from datetime import datetime, timezone

import db
import runners

_procs = {}
_lock = threading.Lock()


def default_runner():
    env = os.environ.get("WORK_HISTORY_RUNNER")
    if env:
        return env
    for r in runners.available():
        if r["installed"] and r["id"] != "mock":
            return r["id"]
    return "mock"


def _now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _append(run_id, db_path, **event):
    conn = db.connect(db_path)
    conn.execute("UPDATE runs SET events = events || ? WHERE id=?",
                 (json.dumps(event, ensure_ascii=False) + "\n", run_id))
    conn.commit()
    conn.close()


def _finish(run_id, db_path, status, session_id=None, runner_key=None):
    conn = db.connect(db_path)
    conn.execute("UPDATE runs SET status=?, ended_at=?, session_id=COALESCE(?, session_id) WHERE id=?",
                 (status, _now(), session_id, run_id))
    row = conn.execute("SELECT user_id, result_activity_id, source_activity_id FROM runs WHERE id=?",
                       (run_id,)).fetchone()
    conn.execute("UPDATE activities SET status=? WHERE id=?",
                 ("success" if status == "done" else "failed", row["result_activity_id"]))
    if session_id and runner_key:
        # link the result row to the runner session so it can be continued from the UI
        src = conn.execute("SELECT s.project FROM activities a LEFT JOIN sessions s ON s.id=a.session_id "
                           "WHERE a.id=?", (row["source_activity_id"],)).fetchone()
        stored = (runners.ADAPTERS.get(runner_key, {}).get("aliases") or [runner_key])[0]
        db.ensure_session(conn, session_id, row["user_id"], stored, src["project"] if src else None)
        conn.execute("UPDATE activities SET session_id=? WHERE id=?", (session_id, row["result_activity_id"]))
    conn.commit()
    conn.close()


def start(conn, db_path, username, prompt, action_id, runner_key, skill_name=None,
          source_activity=None, resume_session=None, workdir=None, agent=None, ctx=None):
    user = db.get_or_create_user(conn, username)
    src_id = source_activity["id"] if source_activity else None
    project = (source_activity or {}).get("project") or (agent or {}).get("model_code")
    act_id = db.add_activity(conn, username, "skill" if skill_name else "prompt",
                             f"[UI·{runner_key}] {prompt}"[:300], skill_name=skill_name,
                             status="in_progress", detail=f"from activity #{src_id}" if src_id else None,
                             project=project, runner="web-ui",
                             tags=(agent or {}).get("jira_key"))
    cur = conn.execute(
        """INSERT INTO runs(user_id, source_activity_id, result_activity_id, action_id, skill_name,
                            prompt, mode, status, session_id, started_at, events, agent_id)
           VALUES (?,?,?,?,?,?,?,?,?,?, '', ?)""",
        (user["id"], src_id, act_id, action_id, skill_name, prompt, runner_key, "running",
         resume_session, _now(), (agent or {}).get("id")))
    if agent and agent.get("status") == "todo":
        db.set_agent_status(conn, agent["id"], "in_progress")
    conn.commit()
    run_id = cur.lastrowid
    if runner_key == "mock":
        args = (run_id, db_path, runner_key, prompt, resume_session, workdir, skill_name, ctx)
        target = _run_mock
    else:
        args = (run_id, db_path, runner_key, prompt, resume_session, workdir)
        target = _run_cli
    threading.Thread(target=target, args=args, daemon=True).start()
    return run_id


def cancel(run_id):
    with _lock:
        p = _procs.get(run_id)
    if p and p.poll() is None:
        p.terminate()
        return True
    return False


def _run_cli(run_id, db_path, key, prompt, resume, workdir):
    cmd = runners.build_cmd(key, prompt, resume)
    shown = " ".join(c if c != prompt else "<prompt>" for c in cmd)
    _append(run_id, db_path, type="meta", text=f"$ {shown}")
    session_id, status, norm = None, "failed", runners.StreamNormalizer(key)
    try:
        p = subprocess.Popen(cmd, cwd=workdir or os.getcwd(), env=runners.child_env(key),
                             stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                             text=True, bufsize=1)
        with _lock:
            _procs[run_id] = p
        events = []
        for line in p.stdout:
            events = norm.feed(line)
            for e in events:
                if e["type"] == "session":
                    session_id = e["id"] or session_id
                    continue
                if e["type"] == "result":
                    status = "failed" if e.pop("is_error", False) else "done"
                _append(run_id, db_path, **e)
        p.wait()
        for e in norm.close(p.returncode):
            if e["type"] == "result":
                status = "failed" if e.pop("is_error", False) else "done"
            _append(run_id, db_path, **e)
        err = p.stderr.read().strip()
        if p.returncode != 0:
            status = "cancelled" if p.returncode < 0 else "failed"
            _append(run_id, db_path, type="error", text=err[-2000:] or f"exit {p.returncode}")
    except FileNotFoundError:
        _append(run_id, db_path, type="error", text=f"'{cmd[0]}' 실행 파일을 찾을 수 없습니다 (설치/PATH 확인)")
    except Exception as e:
        _append(run_id, db_path, type="error", text=str(e))
    finally:
        with _lock:
            _procs.pop(run_id, None)
        _finish(run_id, db_path, status, session_id or resume, key)


def _mock_result(prompt, skill, ctx):
    """Use the skill's examples/sample_output.md (filled with model fields) when it has one."""
    if skill:
        import skills_registry as reg
        sk = reg.load_skills().get(skill)
        src = sk and sk.get("source")
        sample = Path(src) / "examples" / "sample_output.md" if src and src != "catalog" else None
        if sample and sample.exists():
            return reg.render_prompt(sample.read_text(encoding="utf-8"), {}, ctx or {}, keep_newlines=True)
    return None


def _run_mock(run_id, db_path, key, prompt, resume, workdir, skill=None, ctx=None):
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
    result = _mock_result(prompt, skill, ctx) or (f"## 결과 요약\n\n**요청**: {prompt}\n\n"
              "| 항목 | 상태 | 비고 |\n|---|---|---|\n"
              "| 파일 분석 | 완료 | 3개 파일 |\n| 이슈 | 2건 | 1건 수정 권장 |\n| 테스트 | 통과 | 12/12 |\n\n"
              "- 다음 단계: 수정 사항을 커밋하고 PR 생성\n- `mock` 모드 출력입니다")
    _append(run_id, db_path, type="result", text=result, cost=0, duration_ms=2000)
    _finish(run_id, db_path, "done", resume or f"mock-{run_id}", None)
