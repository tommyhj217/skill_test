#!/usr/bin/env python3
"""Jira "model" issues → models + agents (skills) per assignee.

  jira_sync.py pull [--source sample|jira] [--jql "..."]   # import models, generate agents
  jira_sync.py show MDL-101 [--json]                         # model info (used by agent skills)
  jira_sync.py agents [--user alice]                         # list generated agents
  jira_sync.py comment MDL-101 "text"                        # post a comment (sample: stored only)

Source: `sample` reads jira/samples/jira_search_response.json (no network). `jira` calls the REST API:
  JIRA_BASE_URL=https://your.atlassian.net  + JIRA_EMAIL + JIRA_API_TOKEN   (Cloud, basic auth)
  JIRA_BASE_URL=https://jira.company.com    + JIRA_PAT  + JIRA_DEPLOYMENT=server (Server/DC, bearer)
Default source: `jira` when JIRA_BASE_URL is set, else `sample`.
Field ids / roles: jira/jira_mapping.json · which agents per model: jira/agent_templates.json
"""
import argparse
import base64
import json
import os
import sys
import urllib.parse
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import db  # noqa: E402

JIRA_DIR = Path(__file__).resolve().parent.parent / "jira"
SAMPLE = JIRA_DIR / "samples" / "jira_search_response.json"


def _load(name):
    return json.loads((JIRA_DIR / name).read_text(encoding="utf-8"))


def mapping():
    return _load("jira_mapping.json")


def templates():
    return _load("agent_templates.json")


def default_source():
    return "jira" if os.environ.get("JIRA_BASE_URL") else "sample"


# ───────────── normalization ─────────────

def _user(v, users_map):
    """Jira user object → local username (email local part > accountId map > name)."""
    if not isinstance(v, dict):
        return None
    if v.get("emailAddress"):
        return v["emailAddress"].split("@")[0]
    if v.get("accountId") in users_map:
        return users_map[v["accountId"]]
    return v.get("name") or v.get("accountId")


def _value(v):
    if isinstance(v, dict):
        return v.get("value") or v.get("name") or v.get("displayName")
    if isinstance(v, list):
        return [_value(x) for x in v]
    return v


def normalize_issue(issue, m=None):
    m = m or mapping()
    f = issue.get("fields", {})
    users_map = {k: v for k, v in m.get("users", {}).items() if not k.startswith("_")}
    base = os.environ.get("JIRA_BASE_URL") or issue.get("self", "").split("/rest/")[0]
    norm = {
        "jira_key": issue["key"],
        "model_name": f.get("summary"),
        "status": _value(f.get("status")),
        "due": f.get("duedate"),
        "url": f"{base}/browse/{issue['key']}" if base else None,
        "created": f.get("created"),
    }
    for name, fid in m["fields"].items():
        norm[name] = _value(f.get(fid))
    if isinstance(norm.get("regions"), list):
        norm["regions"] = ", ".join(x for x in norm["regions"] if x)
    norm["roles"] = {role: _user(f.get(fid), users_map) for role, fid in m["roles"].items()}
    names = {}
    for role, fid in m["roles"].items():
        u = f.get(fid)
        if isinstance(u, dict) and norm["roles"][role]:
            names[norm["roles"][role]] = (u.get("displayName"), u.get("emailAddress"))
    norm["_people"] = names
    return norm


def upsert_model(conn, norm):
    row = conn.execute("SELECT id FROM models WHERE jira_key=?", (norm["jira_key"],)).fetchone()
    fields = {k: v for k, v in norm.items() if not k.startswith("_")}
    vals = (norm.get("model_code"), norm.get("model_name"), norm.get("product_line"), norm.get("status"),
            norm.get("due"), norm.get("url"), json.dumps(fields, ensure_ascii=False), db.now_iso())
    if row:
        conn.execute("""UPDATE models SET model_code=?, name=?, product_line=?, status=?, due=?, url=?,
                        fields=?, synced_at=? WHERE id=?""", (*vals, row["id"]))
        return row["id"], False
    cur = conn.execute("""INSERT INTO models(model_code, name, product_line, status, due, url, fields,
                          synced_at, jira_key) VALUES (?,?,?,?,?,?,?,?,?)""", (*vals, norm["jira_key"]))
    return cur.lastrowid, True


def agents_for(norm, tpl=None):
    tpl = tpl or templates()
    return tpl.get("by_product_line", {}).get(norm.get("product_line")) or tpl["default"]


def generate_agents(conn, model_id, norm):
    """Create the model's agents (idempotent); keep assignees in sync with Jira."""
    for person, (display, email) in norm.get("_people", {}).items():
        db.get_or_create_user(conn, person, display, email)
    created = []
    for spec in agents_for(norm):
        assignee = norm["roles"].get(spec["role"])
        cur = conn.execute(
            """INSERT OR IGNORE INTO agents(model_id, skill_name, role, assignee, status, created_at, updated_at)
               VALUES (?,?,?,?, 'todo', ?, ?)""",
            (model_id, spec["skill"], spec["role"], assignee, db.now_iso(), db.now_iso()))
        if cur.rowcount:
            created.append(spec["skill"])
        else:
            conn.execute("UPDATE agents SET assignee=? WHERE model_id=? AND skill_name=? AND assignee IS NOT ?",
                         (assignee, model_id, spec["skill"], assignee))
    return created


def ingest(conn, issues):
    report = []
    for issue in issues:
        norm = normalize_issue(issue)
        model_id, new = upsert_model(conn, norm)
        created = generate_agents(conn, model_id, norm)
        report.append({"jira_key": norm["jira_key"], "new_model": new, "agents_created": created})
    conn.commit()
    return report


# ───────────── Jira REST ─────────────

def _headers():
    h = {"Accept": "application/json", "Content-Type": "application/json"}
    if os.environ.get("JIRA_PAT"):
        h["Authorization"] = f"Bearer {os.environ['JIRA_PAT']}"
    elif os.environ.get("JIRA_EMAIL") and os.environ.get("JIRA_API_TOKEN"):
        tok = base64.b64encode(f"{os.environ['JIRA_EMAIL']}:{os.environ['JIRA_API_TOKEN']}".encode()).decode()
        h["Authorization"] = f"Basic {tok}"
    return h


def _req(method, path, params=None, body=None):
    url = os.environ["JIRA_BASE_URL"].rstrip("/") + path
    if params:
        url += "?" + urllib.parse.urlencode(params)
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method, headers=_headers())
    with urllib.request.urlopen(req, timeout=30) as r:
        txt = r.read().decode()
        return json.loads(txt) if txt else {}


def _server():
    return os.environ.get("JIRA_DEPLOYMENT", "cloud").lower() in ("server", "dc", "datacenter")


def fetch_issues(source=None, jql=None):
    source = source or default_source()
    if source == "sample":
        return json.loads(SAMPLE.read_text(encoding="utf-8"))["issues"]
    m = mapping()
    jql = jql or m["jql"]
    field_ids = ["summary", "status", "duedate", "created", "updated", "issuetype",
                 *m["fields"].values(), *m["roles"].values()]
    issues = []
    if _server():                                   # Server / Data Center: /rest/api/2/search
        start = 0
        while True:
            r = _req("GET", "/rest/api/2/search", {"jql": jql, "fields": ",".join(field_ids),
                                                   "startAt": start, "maxResults": 100})
            issues += r.get("issues", [])
            start += len(r.get("issues", []))
            if not r.get("issues") or start >= r.get("total", 0):
                return issues
    token = None                                    # Cloud: enhanced search with nextPageToken
    while True:
        params = {"jql": jql, "fields": ",".join(field_ids), "maxResults": 100}
        if token:
            params["nextPageToken"] = token
        r = _req("GET", "/rest/api/3/search/jql", params)
        issues += r.get("issues", [])
        token = r.get("nextPageToken")
        if r.get("isLast", True) or not token:
            return issues


def post_comment(conn, jira_key, text, agent_id=None, run_id=None, user=None, source=None):
    source = source or default_source()
    status, resp = "sample", "sample 모드: Jira로 전송하지 않고 로컬에만 저장했습니다"
    if source == "jira":
        try:
            body = {"body": text} if _server() else {"body": {"type": "doc", "version": 1, "content": [
                {"type": "paragraph", "content": [{"type": "text", "text": line or " "}]}
                for line in text.splitlines()]}}
            r = _req("POST", f"/rest/api/{2 if _server() else 3}/issue/{jira_key}/comment", body=body)
            status, resp = "sent", json.dumps({"id": r.get("id"), "self": r.get("self")})
        except Exception as e:
            status, resp = "failed", str(e)
    cur = conn.execute("""INSERT INTO jira_outbox(jira_key, agent_id, run_id, body, status, response, created_by,
                          created_at) VALUES (?,?,?,?,?,?,?,?)""",
                       (jira_key, agent_id, run_id, text, status, resp, user, db.now_iso()))
    conn.commit()
    return {"id": cur.lastrowid, "status": status, "response": resp}


def handle_webhook(conn, payload):
    """Jira webhook (jira:issue_created / jira:issue_updated) → ingest when it is a model issue."""
    issue = payload.get("issue") or {}
    itype = ((issue.get("fields") or {}).get("issuetype") or {}).get("name")
    if not issue.get("key") or itype != mapping().get("issue_type", "Model"):
        return {"ignored": True, "reason": f"not a model issue ({itype})"}
    return {"event": payload.get("webhookEvent"), **ingest(conn, [issue])[0]}


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("pull"); p.add_argument("--source", choices=["sample", "jira"]); p.add_argument("--jql")
    s = sub.add_parser("show"); s.add_argument("key"); s.add_argument("--json", action="store_true")
    g = sub.add_parser("agents"); g.add_argument("--user")
    c = sub.add_parser("comment"); c.add_argument("key"); c.add_argument("text"); c.add_argument("--source")
    a = ap.parse_args()
    conn = db.connect()
    if a.cmd == "pull":
        print(json.dumps(ingest(conn, fetch_issues(a.source, a.jql)), ensure_ascii=False, indent=2))
    elif a.cmd == "show":
        r = conn.execute("SELECT fields FROM models WHERE jira_key=?", (a.key,)).fetchone()
        if not r:
            sys.exit(f"{a.key}: not synced yet (run: jira_sync.py pull)")
        f = json.loads(r["fields"])
        if a.json:
            print(json.dumps(f, ensure_ascii=False, indent=2))
        else:
            for k, v in f.items():
                print(f"{k:13} {v}")
    elif a.cmd == "agents":
        for r in db.list_agents(conn, a.user):
            print(f"#{r['id']:<3} {r['jira_key']:8} {r['model_code']:15} {r['skill_name']:20} "
                  f"{r['role']:5} {r['assignee'] or '-':8} {r['status']}")
    elif a.cmd == "comment":
        print(json.dumps(post_comment(conn, a.key, a.text, source=a.source), ensure_ascii=False))


if __name__ == "__main__":
    main()
