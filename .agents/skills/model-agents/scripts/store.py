#!/usr/bin/env python3
"""Storage for the model-agents menu (SQLite, Python stdlib only).

Tables
  models  one row per product model (key, code, name, product line, due, extra fields as JSON)
  agents  agent skills attached to a model (+ owner role / person / status)
  usage   one row each time a user ran an agent on a model  → "✅ 사용함 / ⚪ 미사용"

DB path: MODEL_AGENTS_DB, default <this skill>/data/model_agents.db
User:    MODEL_AGENTS_USER → git user.email (local part) → OS user
"""
import getpass
import json
import os
import sqlite3
import subprocess
from datetime import datetime, timezone
from pathlib import Path

SKILL_DIR = Path(__file__).resolve().parent.parent

SCHEMA = """
CREATE TABLE IF NOT EXISTS models (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    key          TEXT NOT NULL UNIQUE,          -- e.g. MDL-101 (issue key) or the model code
    model_code   TEXT NOT NULL,
    name         TEXT,
    product_line TEXT,
    status       TEXT,
    due          TEXT,
    url          TEXT,
    fields       TEXT NOT NULL DEFAULT '{}',    -- extra fields (regions, spec_url, roles, …) as JSON
    updated_at   TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS agents (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    model_id   INTEGER NOT NULL REFERENCES models(id) ON DELETE CASCADE,
    skill_name TEXT NOT NULL,
    role       TEXT,
    assignee   TEXT,
    status     TEXT NOT NULL DEFAULT 'todo',    -- todo | in_progress | done
    UNIQUE(model_id, skill_name)
);
CREATE TABLE IF NOT EXISTS usage (
    id       INTEGER PRIMARY KEY AUTOINCREMENT,
    agent_id INTEGER NOT NULL REFERENCES agents(id) ON DELETE CASCADE,
    username TEXT NOT NULL,
    status   TEXT NOT NULL DEFAULT 'done',      -- done | failed
    summary  TEXT,
    ts       TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_usage_user ON usage(username, ts DESC);
"""


def now_iso():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def connect(db_path=None):
    path = Path(db_path or os.environ.get("MODEL_AGENTS_DB") or SKILL_DIR / "data" / "model_agents.db")
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.executescript(SCHEMA)
    return conn


def resolve_username():
    if os.environ.get("MODEL_AGENTS_USER"):
        return os.environ["MODEL_AGENTS_USER"]
    try:
        email = subprocess.run(["git", "config", "user.email"], capture_output=True, text=True,
                               encoding="utf-8", errors="replace", timeout=3).stdout.strip()
        if email:
            return email.split("@")[0]
    except Exception:
        pass
    return getpass.getuser()


def _model(row):
    if not row:
        return None
    d = dict(row)
    d["fields"] = json.loads(d.get("fields") or "{}")
    return d


# ───────────── models & agents ─────────────

def upsert_model(conn, m):
    """m: {key, model_code, name, product_line, status, due, url, **extra}. Returns (id, created)."""
    base = ("key", "model_code", "name", "product_line", "status", "due", "url")
    extra = {k: v for k, v in m.items() if k not in base}
    vals = (m["model_code"], m.get("name"), m.get("product_line"), m.get("status"), m.get("due"), m.get("url"),
            json.dumps(extra, ensure_ascii=False), now_iso())
    row = conn.execute("SELECT id FROM models WHERE key=?", (m["key"],)).fetchone()
    if row:
        conn.execute("""UPDATE models SET model_code=?, name=?, product_line=?, status=?, due=?, url=?, fields=?,
                        updated_at=? WHERE id=?""", (*vals, row["id"]))
        return row["id"], False
    cur = conn.execute("""INSERT INTO models(model_code, name, product_line, status, due, url, fields, updated_at, key)
                          VALUES (?,?,?,?,?,?,?,?,?)""", (*vals, m["key"]))
    return cur.lastrowid, True


def resolve_model(conn, ref):
    """By key or model code (case-insensitive)."""
    return _model(conn.execute("SELECT * FROM models WHERE key=? OR UPPER(model_code)=UPPER(?)",
                               (ref, ref)).fetchone())


def ensure_agents(conn, model, agent_skills):
    """Attach every declared agent skill that applies to the model; owner comes from fields.roles[role].
    agent_skills: [(skill_name, role)]. Idempotent; always commits."""
    roles = model["fields"].get("roles") or {}
    created = []
    for skill, role in agent_skills:
        cur = conn.execute("INSERT OR IGNORE INTO agents(model_id, skill_name, role, assignee) VALUES (?,?,?,?)",
                           (model["id"], skill, role, roles.get(role)))
        if cur.rowcount:
            created.append(skill)
    conn.commit()
    return created


def get_agent(conn, model_id, skill):
    r = conn.execute("SELECT * FROM agents WHERE model_id=? AND skill_name=?", (model_id, skill)).fetchone()
    return dict(r) if r else None


def model_agents(conn, model_id, user):
    """Agents of a model with my usage (count / last / last status) and the team's."""
    return [dict(r) for r in conn.execute("""
        SELECT g.id, g.skill_name, g.role, g.assignee, g.status,
               COUNT(CASE WHEN u.username = :me THEN 1 END)            AS my_runs,
               MAX(CASE WHEN u.username = :me THEN u.ts END)           AS my_last,
               COUNT(u.id)                                             AS team_runs,
               COUNT(DISTINCT u.username)                              AS team_users
        FROM agents g LEFT JOIN usage u ON u.agent_id = g.id
        WHERE g.model_id = :mid GROUP BY g.id ORDER BY g.id""", {"me": user, "mid": model_id})]


def models_overview(conn, user):
    """Every model with: my usage, agent count, and the roles I own on it."""
    rows = conn.execute("""
        SELECT m.*,
               COUNT(DISTINCT g.id)                                              AS agent_count,
               COUNT(DISTINCT CASE WHEN u.username = :me THEN u.id END)          AS my_runs,
               COUNT(DISTINCT CASE WHEN u.username = :me THEN g.id END)          AS my_agents_used,
               MAX(CASE WHEN u.username = :me THEN u.ts END)                     AS my_last,
               GROUP_CONCAT(DISTINCT CASE WHEN g.assignee = :me THEN g.role END) AS my_roles
        FROM models m LEFT JOIN agents g ON g.model_id = m.id LEFT JOIN usage u ON u.agent_id = g.id
        GROUP BY m.id""", {"me": user})
    out = []
    for r in rows:
        d = _model(r)
        d["my_roles"] = sorted(set((d["my_roles"] or "").split(","))) if d["my_roles"] else []
        out.append(d)
    return out


def record_use(conn, agent_id, user, summary=None, status="done", ts=None):
    conn.execute("INSERT INTO usage(agent_id, username, status, summary, ts) VALUES (?,?,?,?,?)",
                 (agent_id, user, status, summary, ts or now_iso()))
    conn.execute("UPDATE agents SET status='in_progress' WHERE id=? AND status='todo'", (agent_id,))
    conn.commit()
