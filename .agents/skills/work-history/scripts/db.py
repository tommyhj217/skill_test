#!/usr/bin/env python3
"""Work-history DB layer + CLI (Python stdlib only, SQLite).

Usage:
  db.py init                       # create schema
  db.py seed [--reset]             # load sample data
  db.py whoami                     # show resolved current user
  db.py log --kind prompt --summary "..." [--skill X] [--tool Y] [--status success] [--project P]
  db.py list [--user U] [--limit N] [--kind K] [--q TEXT]
  db.py stats [--user U]
"""
import argparse
import getpass
import json
import os
import sqlite3
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

SKILL_DIR = Path(__file__).resolve().parent.parent
DEFAULT_DB = Path(os.environ.get("WORK_HISTORY_DB", SKILL_DIR / "data" / "work_history.db"))

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    username     TEXT NOT NULL UNIQUE,
    display_name TEXT,
    email        TEXT,
    role         TEXT NOT NULL DEFAULT 'member',  -- member | admin
    created_at   TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS sessions (
    id          TEXT PRIMARY KEY,              -- runner session id
    user_id     INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    runner      TEXT NOT NULL,                 -- claude-code | codex | cursor | ...
    project     TEXT,
    started_at  TEXT NOT NULL,
    ended_at    TEXT
);
CREATE TABLE IF NOT EXISTS activities (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id     INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    session_id  TEXT REFERENCES sessions(id) ON DELETE SET NULL,
    ts          TEXT NOT NULL,
    kind        TEXT NOT NULL,                 -- prompt | tool | skill | note
    skill_name  TEXT,
    tool_name   TEXT,
    summary     TEXT NOT NULL,
    detail      TEXT,
    status      TEXT NOT NULL DEFAULT 'success', -- success | failed | in_progress
    duration_ms INTEGER,
    tags        TEXT,                          -- comma separated
    pinned      INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS runs (                -- actions launched from the UI
    id                 INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id            INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    source_activity_id INTEGER,                   -- the history row the user clicked
    result_activity_id INTEGER,                   -- the history row this run created
    action_id          TEXT,                      -- continue | free | skill:<name>
    skill_name         TEXT,
    prompt             TEXT NOT NULL,
    mode               TEXT NOT NULL,             -- claude | mock
    status             TEXT NOT NULL,             -- running | done | failed | cancelled
    session_id         TEXT,
    started_at         TEXT NOT NULL,
    ended_at           TEXT,
    events             TEXT NOT NULL DEFAULT ''   -- JSON lines
);
CREATE INDEX IF NOT EXISTS idx_act_user_ts ON activities(user_id, ts DESC);
CREATE INDEX IF NOT EXISTS idx_act_session ON activities(session_id);
"""


def now_iso():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def connect(db_path=None):
    path = Path(db_path or DEFAULT_DB)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.executescript(SCHEMA)
    return conn


def resolve_username():
    """Who is the 'connected' user? env > git email > OS user."""
    if os.environ.get("WORK_HISTORY_USER"):
        return os.environ["WORK_HISTORY_USER"]
    try:
        email = subprocess.run(["git", "config", "user.email"], capture_output=True,
                               text=True, timeout=3).stdout.strip()
        if email:
            return email.split("@")[0]
    except Exception:
        pass
    return getpass.getuser()


def get_or_create_user(conn, username, display_name=None, email=None, role="member"):
    row = conn.execute("SELECT * FROM users WHERE username=?", (username,)).fetchone()
    if row:
        return row
    conn.execute(
        "INSERT INTO users(username, display_name, email, role, created_at) VALUES (?,?,?,?,?)",
        (username, display_name or username, email, role, now_iso()))
    conn.commit()
    return conn.execute("SELECT * FROM users WHERE username=?", (username,)).fetchone()


def ensure_session(conn, session_id, user_id, runner="claude-code", project=None):
    if not session_id:
        return None
    conn.execute(
        "INSERT OR IGNORE INTO sessions(id, user_id, runner, project, started_at) VALUES (?,?,?,?,?)",
        (session_id, user_id, runner, project, now_iso()))
    return session_id


def add_activity(conn, username, kind, summary, **kw):
    user = get_or_create_user(conn, username)
    sid = ensure_session(conn, kw.get("session_id"), user["id"],
                         kw.get("runner", "claude-code"), kw.get("project"))
    cur = conn.execute(
        """INSERT INTO activities(user_id, session_id, ts, kind, skill_name, tool_name, summary,
                                  detail, status, duration_ms, tags)
           VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
        (user["id"], sid, kw.get("ts") or now_iso(), kind, kw.get("skill_name"),
         kw.get("tool_name"), summary[:500], kw.get("detail"), kw.get("status", "success"),
         kw.get("duration_ms"), kw.get("tags")))
    conn.commit()
    return cur.lastrowid


def list_activities(conn, username, limit=100, kind=None, q=None, status=None,
                    session_id=None, offset=0):
    sql = """SELECT a.*, s.runner, s.project FROM activities a
             JOIN users u ON u.id = a.user_id
             LEFT JOIN sessions s ON s.id = a.session_id
             WHERE u.username = ?"""
    args = [username]
    if kind:
        sql += " AND a.kind = ?"; args.append(kind)
    if status:
        sql += " AND a.status = ?"; args.append(status)
    if session_id:
        sql += " AND a.session_id = ?"; args.append(session_id)
    if q:
        sql += " AND (a.summary LIKE ? OR a.detail LIKE ? OR a.tags LIKE ? OR a.skill_name LIKE ?)"
        args += [f"%{q}%"] * 4
    sql += " ORDER BY a.pinned DESC, a.ts DESC LIMIT ? OFFSET ?"
    args += [limit, offset]
    return [dict(r) for r in conn.execute(sql, args)]


def stats(conn, username):
    uid_row = conn.execute("SELECT id FROM users WHERE username=?", (username,)).fetchone()
    if not uid_row:
        return {"total": 0, "by_kind": {}, "by_status": {}, "top_skills": [], "sessions": 0, "daily": []}
    uid = uid_row["id"]
    one = lambda sql: conn.execute(sql, (uid,)).fetchall()
    return {
        "total": one("SELECT COUNT(*) c FROM activities WHERE user_id=?")[0]["c"],
        "sessions": one("SELECT COUNT(*) c FROM sessions WHERE user_id=?")[0]["c"],
        "by_kind": {r["kind"]: r["c"] for r in one(
            "SELECT kind, COUNT(*) c FROM activities WHERE user_id=? GROUP BY kind")},
        "by_status": {r["status"]: r["c"] for r in one(
            "SELECT status, COUNT(*) c FROM activities WHERE user_id=? GROUP BY status")},
        "top_skills": [dict(r) for r in one(
            """SELECT skill_name, COUNT(*) c FROM activities WHERE user_id=? AND skill_name IS NOT NULL
               GROUP BY skill_name ORDER BY c DESC LIMIT 5""")],
        "daily": [dict(r) for r in one(
            """SELECT substr(ts,1,10) day, COUNT(*) c FROM activities WHERE user_id=?
               GROUP BY day ORDER BY day DESC LIMIT 14""")],
    }


def list_sessions(conn, username):
    return [dict(r) for r in conn.execute(
        """SELECT s.*, COUNT(a.id) activity_count FROM sessions s
           JOIN users u ON u.id = s.user_id
           LEFT JOIN activities a ON a.session_id = s.id
           WHERE u.username = ? GROUP BY s.id ORDER BY s.started_at DESC""", (username,))]


def get_activity(conn, activity_id, username):
    r = conn.execute(
        """SELECT a.*, s.runner, s.project FROM activities a JOIN users u ON u.id=a.user_id
           LEFT JOIN sessions s ON s.id=a.session_id WHERE a.id=? AND u.username=?""",
        (activity_id, username)).fetchone()
    return dict(r) if r else None


def list_runs(conn, username, limit=50):
    return [dict(r) for r in conn.execute(
        """SELECT r.id, r.source_activity_id, r.action_id, r.skill_name, r.prompt, r.mode, r.status,
                  r.session_id, r.started_at, r.ended_at
           FROM runs r JOIN users u ON u.id=r.user_id WHERE u.username=?
           ORDER BY r.id DESC LIMIT ?""", (username, limit))]


def get_run(conn, run_id, username):
    r = conn.execute("""SELECT r.* FROM runs r JOIN users u ON u.id=r.user_id
                        WHERE r.id=? AND u.username=?""", (run_id, username)).fetchone()
    return dict(r) if r else None


def owned(conn, activity_id, username):
    return conn.execute(
        """SELECT a.id FROM activities a JOIN users u ON u.id=a.user_id
           WHERE a.id=? AND u.username=?""", (activity_id, username)).fetchone() is not None


def update_activity(conn, activity_id, username, fields):
    allowed = {"summary", "detail", "status", "tags", "pinned"}
    fields = {k: v for k, v in fields.items() if k in allowed}
    if not fields or not owned(conn, activity_id, username):
        return False
    sets = ", ".join(f"{k}=?" for k in fields)
    conn.execute(f"UPDATE activities SET {sets} WHERE id=?", [*fields.values(), activity_id])
    conn.commit()
    return True


def delete_activity(conn, activity_id, username):
    if not owned(conn, activity_id, username):
        return False
    conn.execute("DELETE FROM activities WHERE id=?", (activity_id,))
    conn.commit()
    return True


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--db", default=None)
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("init")
    s = sub.add_parser("seed"); s.add_argument("--reset", action="store_true")
    sub.add_parser("whoami")
    lg = sub.add_parser("log")
    lg.add_argument("--kind", default="note"); lg.add_argument("--summary", required=True)
    lg.add_argument("--detail"); lg.add_argument("--skill"); lg.add_argument("--tool")
    lg.add_argument("--status", default="success"); lg.add_argument("--project")
    lg.add_argument("--session"); lg.add_argument("--tags"); lg.add_argument("--user")
    ls = sub.add_parser("list")
    ls.add_argument("--user"); ls.add_argument("--limit", type=int, default=20)
    ls.add_argument("--kind"); ls.add_argument("--q")
    st = sub.add_parser("stats"); st.add_argument("--user")
    a = p.parse_args()

    if a.cmd == "seed":
        from seed import seed
        print(json.dumps(seed(a.db, reset=a.reset), ensure_ascii=False))
        return
    conn = connect(a.db)
    if a.cmd == "init":
        print(f"initialized {a.db or DEFAULT_DB}")
    elif a.cmd == "whoami":
        print(resolve_username())
    elif a.cmd == "log":
        aid = add_activity(conn, a.user or resolve_username(), a.kind, a.summary, detail=a.detail,
                           skill_name=a.skill, tool_name=a.tool, status=a.status,
                           project=a.project, session_id=a.session, tags=a.tags)
        print(f"logged activity #{aid}")
    elif a.cmd == "list":
        for r in list_activities(conn, a.user or resolve_username(), a.limit, a.kind, a.q):
            print(f"#{r['id']:<4} {r['ts']}  {r['kind']:<6} {r['status']:<11} "
                  f"{(r['skill_name'] or r['tool_name'] or '-'):<16} {r['summary']}")
    elif a.cmd == "stats":
        print(json.dumps(stats(conn, a.user or resolve_username()), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    sys.path.insert(0, str(Path(__file__).parent))
    main()
