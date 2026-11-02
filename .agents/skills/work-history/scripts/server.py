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
import runners
import skills_registry as reg
import jira_sync
import models as models_cli
import os

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
            return self._send(200, {"me": me, "users": users, "jira": jira_sync.default_source(),
                                    "runner": self.default_runner(), "runners": runners.available()})
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
                                    "runner": self.default_runner(), "runners": runners.available()})
        if u.path == "/api/models":
            return self._send(200, db.models_for_user(conn, target))
        if len(parts) == 3 and parts[:2] == ["api", "models"]:
            d = models_cli.model_detail(conn, parts[2], target)
            if not d:
                return self._send(404, {"error": "model not found"})
            for a in d["agents"]:
                a["can_run"] = a["installed"] and target == self.current_user
            return self._send(200, {**d, "jira_mode": jira_sync.default_source()})
        if u.path == "/api/agents":
            everyone = g("all") == "1" and self._is_admin(conn)
            return self._send(200, db.list_agents(conn, None if everyone else target, g("status")))
        if u.path == "/api/agents/stats":
            return self._send(200, db.agent_stats(conn, target))
        if len(parts) == 3 and parts[:2] == ["api", "agents"] and parts[2].isdigit():
            ag = db.get_agent(conn, int(parts[2]))
            if not ag:
                return self._send(404, {"error": "not found"})
            action = self._agent_action(ag)
            return self._send(200, {
                "agent": ag, "action": action, "runs": db.agent_runs(conn, ag["id"]),
                "team": [{k: x[k] for k in ("id", "skill_name", "role", "assignee", "status")}
                         for x in db.list_agents(conn, model_key=ag["jira_key"])],
                "comments": [dict(r) for r in conn.execute(
                    "SELECT id, status, created_by, created_at, body FROM jira_outbox WHERE agent_id=? ORDER BY id DESC",
                    (ag["id"],))],
                "can_run": target == self.current_user and action is not None,
                "jira_mode": jira_sync.default_source()})
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

    def _is_admin(self, conn):
        return db.get_or_create_user(conn, self.current_user)["role"] == "admin"

    @staticmethod
    def _ctx(ag):
        return {**ag["fields"], "role": ag["role"], "assignee": ag["assignee"]}

    def _agent_action(self, ag):
        action = reg.agent_action(ag["skill_name"])
        if not action:
            return None
        ctx = self._ctx(ag)
        return {**action, "inputs": [{**i, "default": reg.render_prompt(str(i.get("default", "")), {}, ctx)}
                                     for i in action.get("inputs", [])]}

    def _resolve_agent(self, conn, b):
        """(agent, action, prompt) for a run on one of *my* agents."""
        ag = db.get_agent(conn, int(b["agent_id"]))
        if not ag:
            return None, None, None
        action = reg.agent_action(ag["skill_name"])
        if not action:
            return ag, None, None
        ctx = self._ctx(ag)
        values = {i["name"]: (b.get("values") or {}).get(i["name"], i.get("default", ""))
                  for i in action.get("inputs", [])}
        values = {k: reg.render_prompt(str(v), {}, ctx) for k, v in values.items()}
        return ag, action, reg.render_prompt(action["prompt"], values, ctx)

    def default_runner(self):
        return self.runner_mode or runner.default_runner()

    def _pick_runner(self, action, act, requested):
        """Runner for this action.
        continue → the session's own runner. An explicitly requested runner is used as-is or
        rejected (never silently replaced). Otherwise: the clicked row's runner → default."""
        allowed = action.get("runners")
        ok = lambda k: k and (not allowed or k in allowed or k == "mock") and runners.is_installed(k)
        if action.get("resume"):
            return action["runner"], None
        if requested:
            if not runners.is_installed(requested):
                return None, f"'{requested}' 러너가 설치되어 있지 않습니다"
            if not ok(requested):
                return None, f"이 작업은 {', '.join(allowed)} 러너에서만 실행됩니다"
            return requested, None
        for key in (runners.canonical((act or {}).get("runner")), self.default_runner()):
            if ok(key):
                return key, None
        return None, f"이 작업은 {', '.join(allowed)} 러너에서만 실행됩니다" if allowed else "사용 가능한 러너가 없습니다"

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
        parts = u.path.strip("/").split("/")
        if u.path == "/api/jira/webhook":
            secret = os.environ.get("JIRA_WEBHOOK_SECRET")
            if secret and (parse_qs(u.query).get("secret") or [None])[0] != secret:
                return self._send(403, {"error": "bad secret"})
            return self._send(200, jira_sync.handle_webhook(db.connect(self.db_path), self._body()))
        if u.path == "/api/models":
            b = self._body()
            if not (b.get("model_code") or "").strip():
                return self._send(400, {"error": "model_code required"})
            conn = db.connect(self.db_path)
            return self._send(201, jira_sync.register_model(
                conn, b["model_code"].strip(), b.get("name"), b.get("product_line"), b.get("regions"),
                b.get("due"), b.get("key") or None, b.get("spec_url")))
        if u.path == "/api/jira/sync":
            conn = db.connect(self.db_path)
            if not self._is_admin(conn):
                return self._send(403, {"error": "admin only"})
            try:
                return self._send(200, jira_sync.ingest(conn, jira_sync.fetch_issues()))
            except Exception as e:
                return self._send(502, {"error": f"Jira 동기화 실패: {e}"})
        if len(parts) == 4 and parts[:2] == ["api", "agents"] and parts[2].isdigit() and parts[3] == "comment":
            conn = db.connect(self.db_path)
            ag = db.get_agent(conn, int(parts[2]))
            if not ag:
                return self._send(404, {"error": "not found"})
            b = self._body()
            text = b.get("text")
            if not text and b.get("run_id"):
                run = db.get_run(conn, int(b["run_id"]), self.current_user)  # only my own runs
                if not run or run.get("agent_id") != ag["id"]:
                    return self._send(404, {"error": "run not found"})
                evs = [json.loads(x) for x in (run or {}).get("events", "").splitlines() if x]
                text = next((e["text"] for e in reversed(evs) if e["type"] == "result"), None)
                if text:
                    label = (reg.agent_action(ag["skill_name"]) or {}).get("label", ag["skill_name"])
                    text = f"[{label}] {self.current_user} · work-history run #{b['run_id']}\n\n{text}"
            if not text:
                return self._send(400, {"error": "보낼 내용이 없습니다"})
            return self._send(200, jira_sync.post_comment(conn, ag["jira_key"], text, ag["id"],
                                                          b.get("run_id"), self.current_user))
        if u.path in ("/api/runs", "/api/runs/preview"):
            conn = db.connect(self.db_path)
            b = self._body()
            agent = None
            if b.get("agent_id"):
                agent, action, prompt = self._resolve_agent(conn, b)
                act = None
            else:
                act, action, prompt = self._resolve_action(conn, b)
            if not action:
                return self._send(404, {"error": "unknown activity, agent or action"})
            if not prompt:
                return self._send(400, {"error": "empty prompt"})
            key, err = self._pick_runner(action, act, b.get("runner"))
            skill = action.get("name") if action["id"].startswith("skill:") else None
            if key:
                prompt = runners.adapt_prompt(key, prompt, skill)
            if u.path.endswith("preview"):
                return self._send(200, {"prompt": prompt, "resume": bool(action.get("resume")),
                                        "runner": key, "error": err})
            if not key:
                return self._send(400, {"error": err})
            resume = act["session_id"] if action.get("resume") else None
            rid = runner.start(conn, self.db_path, self.current_user, prompt, action["id"], key,
                               skill_name=skill, source_activity=act, resume_session=resume,
                               workdir=self.workdir, agent=agent, ctx=self._ctx(agent) if agent else None)
            return self._send(201, {"id": rid, "prompt": prompt, "runner": key})
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
        parts = urlparse(self.path).path.strip("/").split("/")
        if len(parts) == 3 and parts[:2] == ["api", "agents"] and parts[2].isdigit():
            conn = db.connect(self.db_path)
            ag = db.get_agent(conn, int(parts[2]))
            if not ag:
                return self._send(404, {"ok": False})
            ok = db.set_agent_status(conn, ag["id"], self._body().get("status"))
            return self._send(200 if ok else 400, {"ok": ok})
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
    p.add_argument("--runner", choices=[*runners.ADAPTERS, "mock"], default=None,
                   help="default runner for UI actions (default: first installed of runners.json, else mock)")
    p.add_argument("--workdir", default=None, help="cwd for claude runs (default: current dir)")
    p.add_argument("--demo", action="store_true",
                   help="load sample history + sample Jira models if the DB is empty, default runner = mock")
    a = p.parse_args()
    if a.demo:
        conn = db.connect(a.db)
        if not conn.execute("SELECT 1 FROM models LIMIT 1").fetchone():
            from seed import seed
            print("demo data:", seed(a.db), flush=True)
        a.runner = a.runner or "mock"
        a.user = a.user or "alice"
    Handler.workdir = a.workdir
    Handler.runner_mode = a.runner
    Handler.current_user = a.user or db.resolve_username()
    Handler.db_path = a.db
    print(f"work-history UI: http://{a.host}:{a.port}  (user={Handler.current_user}, runner={a.runner or runner.default_runner()})", flush=True)
    ThreadingHTTPServer((a.host, a.port), Handler).serve_forever()


if __name__ == "__main__":
    main()
