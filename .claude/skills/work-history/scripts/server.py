#!/usr/bin/env python3
"""Local web UI + JSON API for the work-history DB (stdlib only).

  python3 server.py [--port 8765] [--user USERNAME] [--db PATH]

The "connected user" is --user, else WORK_HISTORY_USER, else git user.email, else OS user.
Members only see/modify their own history; admins may view others with ?as=<username>.
"""
import argparse
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import db
import runner
import skills_registry as reg

UI_FILE = Path(__file__).resolve().parent.parent / "ui" / "index.html"


class Handler(BaseHTTPRequestHandler):
    current_user = None
    db_path = None
    workdir = None
    runner_mode = None

    def log_message(self, fmt, *args):  # quieter
        pass

    def _send(self, code, body, ctype="application/json; charset=utf-8"):
        data = body if isinstance(body, bytes) else json.dumps(body, ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _target_user(self, conn, qs):
        """Return the username whose data this request may touch, or None if forbidden."""
        me = db.get_or_create_user(conn, self.current_user)
        target = (qs.get("as") or [self.current_user])[0]
        if target != self.current_user and me["role"] != "admin":
            return None
        return target

    def _body(self):
        n = int(self.headers.get("Content-Length") or 0)
        return json.loads(self.rfile.read(n) or b"{}")

    def do_GET(self):
        u = urlparse(self.path)
        qs = parse_qs(u.query)
        if u.path in ("/", "/index.html"):
            return self._send(200, UI_FILE.read_bytes(), "text/html; charset=utf-8")
        conn = db.connect(self.db_path)
        if u.path == "/api/me":
            me = dict(db.get_or_create_user(conn, self.current_user))
            users = [dict(r) for r in conn.execute(
                "SELECT username, display_name, role FROM users ORDER BY username")] if me["role"] == "admin" else []
            return self._send(200, {"me": me, "users": users,
                                    "runner": self.runner_mode or runner.default_mode()})
        target = self._target_user(conn, qs)
        if target is None:
            return self._send(403, {"error": "forbidden: only admins can view other users"})
        g = lambda k: (qs.get(k) or [None])[0]
        if u.path == "/api/activities":
            return self._send(200, db.list_activities(
                conn, target, int(g("limit") or 100), g("kind"), g("q"), g("status"),
                g("session"), int(g("offset") or 0)))
        if u.path == "/api/stats":
            return self._send(200, db.stats(conn, target))
        if u.path == "/api/sessions":
            return self._send(200, db.list_sessions(conn, target))
        if u.path == "/api/skills":
            return self._send(200, list(reg.load_skills().values()))
        parts = u.path.strip("/").split("/")
        if len(parts) == 3 and parts[:2] == ["api", "activities"] and parts[2].isdigit():
            act = db.get_activity(conn, int(parts[2]), target)
            if not act:
                return self._send(404, {"error": "not found"})
            return self._send(200, {"activity": act, "actions": reg.suggest(act),
                                    "can_run": target == self.current_user,
                                    "runner": self.runner_mode or runner.default_mode()})
        if u.path == "/api/runs":
            return self._send(200, db.list_runs(conn, target))
        if len(parts) == 3 and parts[:2] == ["api", "runs"] and parts[2].isdigit():
            run = db.get_run(conn, int(parts[2]), target)
            if not run:
                return self._send(404, {"error": "not found"})
            since = int(g("since") or 0)
            events = [json.loads(x) for x in run.pop("events").splitlines() if x]
            return self._send(200, {**run, "events": events[since:], "next": len(events)})
        self._send(404, {"error": "not found"})

    def _resolve_action(self, conn, b):
        """Server-side: (activity, action, rendered prompt) for a run request."""
        act = db.get_activity(conn, int(b["activity_id"]), self.current_user) if b.get("activity_id") else None
        if b.get("activity_id") and not act:
            return None, None, None
        actions = reg.suggest(act) if act else reg.suggest({}) + [
            {**sk, "id": f"skill:{sk['name']}"} for sk in reg.load_skills().values()]
        action = next((x for x in actions if x["id"] == b.get("action_id")), None)
        if not action:
            return act, None, None
        values = {i["name"]: (b.get("values") or {}).get(i["name"], i.get("default", ""))
                  for i in action.get("inputs", [])}
        values = {k: reg.render_prompt(str(v), {}, act) for k, v in values.items()}
        return act, action, reg.render_prompt(action["prompt"], values, act)

    def do_POST(self):
        u = urlparse(self.path)
        if u.path in ("/api/runs", "/api/runs/preview"):
            conn = db.connect(self.db_path)
            b = self._body()
            act, action, prompt = self._resolve_action(conn, b)
            if not action:
                return self._send(404, {"error": "unknown activity or action"})
            if not prompt:
                return self._send(400, {"error": "empty prompt"})
            if u.path.endswith("preview"):
                return self._send(200, {"prompt": prompt, "resume": bool(action.get("resume"))})
            resume = act["session_id"] if action.get("resume") else None
            rid = runner.start(conn, self.db_path, self.current_user, prompt, action["id"],
                               skill_name=action.get("name") if action["id"].startswith("skill:") else None,
                               source_activity=act, resume_session=resume, workdir=self.workdir,
                               mode=self.runner_mode)
            return self._send(201, {"id": rid, "prompt": prompt})
        if u.path != "/api/activities":
            return self._send(404, {"error": "not found"})
        b = self._body()
        if not b.get("summary"):
            return self._send(400, {"error": "summary required"})
        conn = db.connect(self.db_path)
        aid = db.add_activity(conn, self.current_user, b.get("kind", "note"), b["summary"],
                              detail=b.get("detail"), tags=b.get("tags"),
                              status=b.get("status", "success"), runner="web-ui")
        self._send(201, {"id": aid})

    def _id_from_path(self, path):
        parts = path.strip("/").split("/")
        if len(parts) == 3 and parts[:2] == ["api", "activities"] and parts[2].isdigit():
            return int(parts[2])
        return None

    def do_PATCH(self):
        aid = self._id_from_path(urlparse(self.path).path)
        if aid is None:
            return self._send(404, {"error": "not found"})
        conn = db.connect(self.db_path)
        ok = db.update_activity(conn, aid, self.current_user, self._body())
        self._send(200 if ok else 404, {"ok": ok})

    def do_DELETE(self):
        parts = urlparse(self.path).path.strip("/").split("/")
        if len(parts) == 3 and parts[:2] == ["api", "runs"] and parts[2].isdigit():
            conn = db.connect(self.db_path)
            if not db.get_run(conn, int(parts[2]), self.current_user):
                return self._send(404, {"ok": False})
            return self._send(200, {"ok": runner.cancel(int(parts[2]))})
        aid = self._id_from_path(urlparse(self.path).path)
        if aid is None:
            return self._send(404, {"error": "not found"})
        conn = db.connect(self.db_path)
        ok = db.delete_activity(conn, aid, self.current_user)
        self._send(200 if ok else 404, {"ok": ok})


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--port", type=int, default=8765)
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--user", default=None)
    p.add_argument("--db", default=None)
    p.add_argument("--runner", choices=["claude", "mock"], default=None,
                   help="claude: run real `claude -p`; mock: simulated output (default: claude if installed)")
    p.add_argument("--workdir", default=None, help="cwd for claude runs (default: current dir)")
    a = p.parse_args()
    Handler.workdir = a.workdir
    Handler.runner_mode = a.runner
    Handler.current_user = a.user or db.resolve_username()
    Handler.db_path = a.db
    print(f"work-history UI: http://{a.host}:{a.port}  (user={Handler.current_user}, runner={a.runner or runner.default_mode()})", flush=True)
    ThreadingHTTPServer((a.host, a.port), Handler).serve_forever()


if __name__ == "__main__":
    main()
