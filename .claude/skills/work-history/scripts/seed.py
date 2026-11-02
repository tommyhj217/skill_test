#!/usr/bin/env python3
"""Generate deterministic sample data: 4 users, ~20 sessions, ~200 activities."""
import random
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from db import connect  # noqa: E402

USERS = [
    ("tommyhj", "Tommy", "tommyhj@example.com", "admin"),
    ("alice", "Alice Kim", "alice@example.com", "member"),
    ("bob", "Bob Lee", "bob@example.com", "member"),
    ("jiwoo", "Jiwoo Park", "jiwoo@example.com", "member"),
]
RUNNERS = ["claude-code", "claude-code", "claude-code", "codex", "cursor"]
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
        conn.executescript("DELETE FROM activities; DELETE FROM sessions; DELETE FROM users;"
                           "DELETE FROM sqlite_sequence;")
    base = datetime.now(timezone.utc).replace(second=0, microsecond=0) - timedelta(hours=1)
    n_act = n_sess = 0
    for username, name, email, role in USERS:
        conn.execute("INSERT OR IGNORE INTO users(username, display_name, email, role, created_at)"
                     " VALUES (?,?,?,?,?)", (username, name, email, role, (base - timedelta(days=30)).isoformat()))
        uid = conn.execute("SELECT id FROM users WHERE username=?", (username,)).fetchone()["id"]
        for s in range(rnd.randint(4, 6)):
            start = base - timedelta(days=rnd.randint(0, 13), hours=rnd.randint(0, 9), minutes=rnd.randint(0, 59))
            sid = f"sess_{username}_{s:02d}"
            runner, project = rnd.choice(RUNNERS), rnd.choice(PROJECTS)
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
    return {"users": len(USERS), "sessions": n_sess, "activities": n_act}


if __name__ == "__main__":
    print(seed(reset="--reset" in sys.argv))
