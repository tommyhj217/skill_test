#!/usr/bin/env python3
"""Generate deterministic sample data: 4 users, ~20 sessions, ~200 activities."""
import json
import random
import uuid
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import db  # noqa: E402
from db import connect  # noqa: E402

USERS = [
    ("tommyhj", "Tommy", "tommyhj@example.com", "admin"),
    ("alice", "Alice Kim", "alice@example.com", "member"),
    ("bob", "Bob Lee", "bob@example.com", "member"),
    ("jiwoo", "Jiwoo Park", "jiwoo@example.com", "member"),
]
RUNNERS = ["claude-code", "codex", "copilot", "gemini", "cursor"]  # every user gets each at least once
PROJECTS = ["billing-api", "web-frontend", "data-pipeline", "infra-terraform", "skill_test"]
SKILLS = ["code-review", "pdf", "xlsx", "work-history", "simplify", "security-review", "docx"]
TOOLS = ["Bash", "Edit", "Write", "Read", "Grep", "WebSearch"]
PROMPTS = [
    "로그인 API에 rate limit 추가해줘", "PR #{n} 리뷰해줘", "월간 매출 리포트 엑셀로 만들어줘",
    "테스트 실패 원인 찾아줘", "README 한글로 번역", "terraform plan 결과 요약",
    "결제 모듈 리팩터링", "PDF 계약서에서 금액 추출", "CI 빌드 속도 개선 방법",
    "보안 취약점 점검해줘", "대시보드 차트 색상 정리", "DB 마이그레이션 스크립트 작성",
]
TOOL_SUMMARIES = {
    "Bash": ["npm test 실행", "pytest -q 실행", "git status 확인", "docker build 실행"],
    "Edit": ["src/auth.ts 수정", "api/routes.py 수정", "package.json 버전 변경"],
    "Write": ["새 파일 생성: migrations/0042.sql", "docs/report.md 작성"],
    "Read": ["config.yaml 읽기", "로그 파일 분석"],
    "Grep": ["TODO 검색", "deprecated API 사용처 검색"],
    "WebSearch": ["라이브러리 최신 버전 조회"],
}
TAGS = ["bugfix", "feature", "refactor", "docs", "infra", "urgent", ""]


def seed(db_path=None, reset=False):
    rnd = random.Random(42)
    conn = connect(db_path)
    if reset:
        conn.executescript("DELETE FROM jira_outbox; DELETE FROM agents; DELETE FROM models; DELETE FROM runs;"
                           "DELETE FROM activities; DELETE FROM sessions; DELETE FROM users; DELETE FROM sqlite_sequence;")
    base = datetime.now(timezone.utc).replace(second=0, microsecond=0) - timedelta(hours=1)
    n_act = n_sess = 0
    for username, name, email, role in USERS:
        conn.execute("INSERT OR IGNORE INTO users(username, display_name, email, role, created_at)"
                     " VALUES (?,?,?,?,?)", (username, name, email, role, (base - timedelta(days=30)).isoformat()))
        uid = conn.execute("SELECT id FROM users WHERE username=?", (username,)).fetchone()["id"]
        for s in range(rnd.randint(5, 7)):
            start = base - timedelta(days=rnd.randint(0, 13), hours=rnd.randint(0, 9), minutes=rnd.randint(0, 59))
            runner, project = RUNNERS[s % len(RUNNERS)], rnd.choice(PROJECTS)
            # runners with an adapter get UUID ids like real ones (enables "이어서 하기" in the UI);
            # 'cursor' has no adapter in runners.json → shows that unsupported runners degrade gracefully
            sid = str(uuid.UUID(int=rnd.getrandbits(128), version=4)) if runner != "cursor" \
                else f"sess_{username}_{s:02d}"
            t = start
            rows = []
            for _ in range(rnd.randint(6, 12)):
                t += timedelta(minutes=rnd.randint(1, 15))
                r = rnd.random()
                if r < 0.25:
                    kind, skill, tool = "prompt", None, None
                    summary = rnd.choice(PROMPTS).format(n=rnd.randint(10, 300))
                elif r < 0.45:
                    kind, skill, tool = "skill", rnd.choice(SKILLS), None
                    summary = f"/{skill} 스킬 실행"
                elif r < 0.95:
                    kind, skill, tool = "tool", None, rnd.choice(TOOLS)
                    summary = rnd.choice(TOOL_SUMMARIES[tool])
                else:
                    kind, skill, tool = "note", None, None
                    summary = "메모: 내일 이어서 작업"
                status = rnd.choices(["success", "failed", "in_progress"], [85, 10, 5])[0]
                rows.append((uid, sid, t.isoformat(timespec="seconds"), kind, skill, tool, summary,
                             f"{runner} / {project}", status, rnd.randint(200, 90000),
                             rnd.choice(TAGS) or None))
            conn.execute("INSERT OR REPLACE INTO sessions(id,user_id,runner,project,started_at,ended_at)"
                         " VALUES (?,?,?,?,?,?)", (sid, uid, runner, project,
                                                   start.isoformat(timespec="seconds"), t.isoformat(timespec="seconds")))
            conn.executemany("""INSERT INTO activities(user_id,session_id,ts,kind,skill_name,tool_name,summary,
                                detail,status,duration_ms,tags) VALUES (?,?,?,?,?,?,?,?,?,?,?)""", rows)
            n_sess += 1
            n_act += len(rows)
    conn.commit()
    jira = seed_jira(conn, base)
    return {"users": len(USERS), "sessions": n_sess, "activities": n_act, **jira}


# agent status per (model, skill) in the sample
SAMPLE_STATUS = {
    ("MDL-105", "model-spec-review"): "done", ("MDL-105", "model-test-plan"): "done",
    ("MDL-105", "model-cert-check"): "done", ("MDL-105", "model-release-note"): "in_progress",
    ("MDL-101", "model-spec-review"): "done", ("MDL-101", "model-test-plan"): "in_progress",
    ("MDL-102", "model-cert-check"): "in_progress",
}
# finished sample runs: (model, skill, user, days ago). Anyone may use any agent of a model.
SAMPLE_RUNS = [
    ("MDL-105", "model-spec-review", "alice", 12), ("MDL-105", "model-test-plan", "bob", 10),
    ("MDL-105", "model-cert-check", "jiwoo", 9), ("MDL-105", "model-release-note", "alice", 8),
    ("MDL-101", "model-spec-review", "alice", 3), ("MDL-101", "model-spec-review", "alice", 1),
    ("MDL-101", "model-test-plan", "bob", 2), ("MDL-101", "model-cert-check", "alice", 1),
    ("MDL-103", "model-release-note", "alice", 5), ("MDL-102", "model-cert-check", "jiwoo", 1),
    ("MDL-102", "model-test-plan", "bob", 4),
]


def seed_jira(conn, base):
    """Import the sample Jira models (jira/samples) → models + agents, then add sample usage."""
    import jira_sync
    from runner import _mock_result
    report = jira_sync.ingest(conn, jira_sync.fetch_issues("sample"))
    agents = {(a["jira_key"], a["skill_name"]): a for a in db.list_agents(conn)}
    for k, status in SAMPLE_STATUS.items():
        if k in agents:
            db.set_agent_status(conn, agents[k]["id"], status)
    runs = 0
    for key, skill, user, days in SAMPLE_RUNS:
        ag = agents.get((key, skill))
        if not ag:
            continue
        ctx = {**ag["fields"], "role": ag["role"], "assignee": ag["assignee"]}
        result = _mock_result("", skill, ctx) or "샘플 결과"
        uid = conn.execute("SELECT id FROM users WHERE username=?", (user,)).fetchone()["id"]
        ts = (base - timedelta(days=days, hours=runs)).isoformat(timespec="seconds")
        events = "\n".join(json.dumps(e, ensure_ascii=False) for e in [
            {"type": "meta", "text": "[sample] seeded run"}, {"type": "result", "text": result}]) + "\n"
        aid = db.add_activity(conn, user, "skill", f"[UI·mock] /{skill} {key}", skill_name=skill,
                              ts=ts, runner="web-ui", project=ag["model_code"], tags=key)
        conn.execute("""INSERT INTO runs(user_id, result_activity_id, action_id, skill_name, prompt, mode, status,
                        started_at, ended_at, events, agent_id) VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                     (uid, aid, f"skill:{skill}", skill, f"/{skill} {key} ({ag['model_code']})", "mock", "done",
                      ts, ts, events, ag["id"]))
        runs += 1
    conn.execute("UPDATE agents SET status='in_progress' WHERE status='todo' AND id IN (SELECT agent_id FROM runs)")
    conn.commit()
    return {"models": len(report), "agents": sum(len(r["agents_created"]) for r in report), "sample_runs": runs}


if __name__ == "__main__":
    print(seed(reset="--reset" in sys.argv))
