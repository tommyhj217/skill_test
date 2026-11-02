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

UI_FILE = Path(__file__).resolve().parent.parent / "ui" / "index.html"


class Handler(BaseHTTPRequestHandler):
    current_user = None
    db_path = None

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
            return self._send(200, {"me": me, "users": users})
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
        self._send(404, {"error": "not found"})

    def do_POST(self):
        u = urlparse(self.path)
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
    a = p.parse_args()
    Handler.current_user = a.user or db.resolve_username()
    Handler.db_path = a.db
    print(f"work-history UI: http://{a.host}:{a.port}  (user={Handler.current_user})", flush=True)
    ThreadingHTTPServer((a.host, a.port), Handler).serve_forever()


if __name__ == "__main__":
    main()
