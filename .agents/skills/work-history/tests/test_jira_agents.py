#!/usr/bin/env python3
"""Jira model → agent skills → per-assignee UI API. No network: sample data + a fake Jira REST server.

  python3 tests/test_jira_agents.py -v
"""
import base64
import copy
import json
import os
import socket
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

HERE = Path(__file__).resolve().parent
SCRIPTS = HERE.parent / "scripts"
SAMPLES = HERE.parent / "jira" / "samples"
sys.path.insert(0, str(SCRIPTS))

TMP = tempfile.mkdtemp(prefix="wh-jira-")
DB = str(Path(TMP) / "t.db")
os.environ["WORK_HISTORY_DB"] = DB
for k in ("JIRA_BASE_URL", "JIRA_EMAIL", "JIRA_API_TOKEN", "JIRA_PAT", "JIRA_DEPLOYMENT", "JIRA_WEBHOOK_SECRET"):
    os.environ.pop(k, None)

import db  # noqa: E402
import jira_sync  # noqa: E402
from seed import seed  # noqa: E402


def free_port():
    s = socket.socket(); s.bind(("127.0.0.1", 0)); p = s.getsockname()[1]; s.close(); return p


def sample_issues():
    return json.loads((SAMPLES / "jira_search_response.json").read_text(encoding="utf-8"))["issues"]


class Ingest(unittest.TestCase):
    def setUp(self):
        self.conn = db.connect(str(Path(TMP) / f"{self._testMethodName}.db"))

    def test_sample_creates_models_and_agents(self):
        rep = jira_sync.ingest(self.conn, sample_issues())
        self.assertEqual(len(rep), 5)
        agents = db.list_agents(self.conn)
        # every installed agent skill that applies to the product line: TV/모니터 also get the team BOM skill
        self.assertEqual(len(agents), 21)                       # 3 × 5 (TV, 모니터) + 오디오 × 4 + 액세서리 × 2
        acc = [a["skill_name"] for a in agents if a["jira_key"] == "MDL-104"]
        self.assertEqual(sorted(acc), ["model-release-note", "model-spec-review"])
        mdl101 = {a["skill_name"]: a["assignee"] for a in agents if a["jira_key"] == "MDL-101"}
        self.assertEqual(mdl101, {"model-spec-review": "alice", "model-test-plan": "bob",
                                  "model-cert-check": "jiwoo", "model-release-note": "tommyhj",
                                  "team-bom-check": "alice"})  # pm via accountId map

    def test_idempotent_and_assignee_follows_jira(self):
        issues = sample_issues()
        jira_sync.ingest(self.conn, issues)
        again = jira_sync.ingest(self.conn, issues)
        self.assertTrue(all(not r["new_model"] and not r["agents_created"] for r in again))
        changed = copy.deepcopy(issues[0])
        changed["fields"]["customfield_10061"] = {"accountId": "x", "emailAddress": "jiwoo@example.com"}
        jira_sync.ingest(self.conn, [changed])
        qa = [a for a in db.list_agents(self.conn, model_key="MDL-101") if a["role"] == "qa"][0]
        self.assertEqual(qa["assignee"], "jiwoo")

    def test_normalize_fields(self):
        n = jira_sync.normalize_issue(sample_issues()[0])
        self.assertEqual((n["model_code"], n["product_line"], n["regions"]), ("TV-OLED65-X1", "TV", "KR, US, EU"))
        self.assertEqual(n["url"], "https://example.atlassian.net/browse/MDL-101")

    def test_webhook(self):
        payload = json.loads((SAMPLES / "webhook_issue_created.json").read_text(encoding="utf-8"))
        r = jira_sync.handle_webhook(self.conn, payload)
        self.assertEqual((r["jira_key"], r["new_model"], len(r["agents_created"])), ("MDL-106", True, 5))
        bug = {"webhookEvent": "jira:issue_created", "issue": {"key": "BUG-1", "fields": {"issuetype": {"name": "Bug"}}}}
        self.assertTrue(jira_sync.handle_webhook(self.conn, bug)["ignored"])


class FakeJira(BaseHTTPRequestHandler):
    """Minimal Jira Cloud: /rest/api/3/search/jql (2 pages) + /issue/{key}/comment."""
    seen = []

    def log_message(self, *a):
        pass

    def _json(self, code, body):
        data = json.dumps(body).encode()
        self.send_response(code); self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data))); self.end_headers(); self.wfile.write(data)

    def do_GET(self):
        FakeJira.seen.append(("GET", self.path, self.headers.get("Authorization")))
        u = urlparse(self.path); qs = parse_qs(u.query)
        if u.path != "/rest/api/3/search/jql":
            return self._json(404, {})
        issues = sample_issues()
        if "nextPageToken" in qs:
            return self._json(200, {"issues": issues[3:], "isLast": True})
        return self._json(200, {"issues": issues[:3], "nextPageToken": "p2", "isLast": False})

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        FakeJira.seen.append(("POST", self.path, body))
        self._json(201, {"id": "10001", "self": "x"})


class JiraRest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.srv = ThreadingHTTPServer(("127.0.0.1", free_port()), FakeJira)
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()
        os.environ.update(JIRA_BASE_URL=f"http://127.0.0.1:{cls.srv.server_port}",
                          JIRA_EMAIL="bot@example.com", JIRA_API_TOKEN="tok")

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()
        for k in ("JIRA_BASE_URL", "JIRA_EMAIL", "JIRA_API_TOKEN"):
            os.environ.pop(k)

    def test_pull_paginates_with_basic_auth(self):
        FakeJira.seen.clear()
        issues = jira_sync.fetch_issues()          # JIRA_BASE_URL set → source=jira
        self.assertEqual(len(issues), 5)
        gets = [s for s in FakeJira.seen if s[0] == "GET"]
        self.assertEqual(len(gets), 2)
        self.assertEqual(gets[0][2], "Basic " + base64.b64encode(b"bot@example.com:tok").decode())
        self.assertIn("customfield_10050", gets[0][1])

    def test_comment_uses_adf(self):
        conn = db.connect(str(Path(TMP) / "rest.db"))
        r = jira_sync.post_comment(conn, "MDL-101", "line1\nline2")
        self.assertEqual(r["status"], "sent")
        path, body = [s for s in FakeJira.seen if s[0] == "POST"][-1][1:]
        self.assertEqual(path, "/rest/api/3/issue/MDL-101/comment")
        self.assertEqual(body["body"]["type"], "doc")
        self.assertEqual(len(body["body"]["content"]), 2)


class ServerAPI(unittest.TestCase):
    """Three users on one shared DB: alice (dev), bob (qa), tommyhj (admin/pm)."""

    @classmethod
    def setUpClass(cls):
        seed(DB, reset=True)
        cls.procs, cls.base = [], {}
        env = {**os.environ, "JIRA_WEBHOOK_SECRET": "s3cret"}
        for user in ("alice", "bob", "jiwoo", "tommyhj"):
            port = free_port()
            cls.procs.append(subprocess.Popen(
                [sys.executable, str(SCRIPTS / "server.py"), "--port", str(port), "--user", user,
                 "--runner", "mock", "--db", DB], env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL))
            cls.base[user] = f"http://127.0.0.1:{port}"
        for b in cls.base.values():
            for _ in range(50):
                try:
                    urllib.request.urlopen(b + "/api/me"); break
                except OSError:
                    time.sleep(0.1)

    @classmethod
    def tearDownClass(cls):
        for p in cls.procs:
            p.terminate()

    def call(self, user, path, body=None, method=None):
        req = urllib.request.Request(self.base[user] + path, method=method or ("POST" if body is not None else "GET"),
                                     data=json.dumps(body).encode() if body is not None else None)
        try:
            with urllib.request.urlopen(req) as r:
                return r.status, json.loads(r.read())
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read())

    def first_todo(self, user):
        _, rows = self.call(user, "/api/agents?status=todo")
        return rows[0]

    def test_each_user_sees_only_own_agents(self):
        for user in ("alice", "bob"):
            _, rows = self.call(user, "/api/agents")
            self.assertTrue(rows)
            self.assertEqual({r["assignee"] for r in rows}, {user})
        _, everyone = self.call("bob", "/api/agents?all=1")       # non-admin: ignored
        self.assertEqual({r["assignee"] for r in everyone}, {"bob"})
        _, everyone = self.call("tommyhj", "/api/agents?all=1")   # admin: whole team
        self.assertGreaterEqual(len({r["assignee"] for r in everyone}), 4)

    def test_agent_detail_prefills_model_fields(self):
        ag = self.first_todo("alice")
        _, d = self.call("alice", f"/api/agents/{ag['id']}")
        self.assertTrue(d["can_run"])
        defaults = {i["name"]: i["default"] for i in d["action"]["inputs"]}
        if "spec" in defaults:
            self.assertEqual(defaults["spec"], d["agent"]["fields"]["spec_url"])
        self.assertTrue(any(t["assignee"] != "alice" for t in d["team"]))

    def test_run_agent_then_comment_to_jira(self):
        ag = self.first_todo("alice")
        code, run = self.call("alice", "/api/runs", {"agent_id": ag["id"], "values": {}})
        self.assertEqual(code, 201)
        self.assertIn(ag["jira_key"], run["prompt"])
        for _ in range(60):
            _, r = self.call("alice", f"/api/runs/{run['id']}")
            if r["status"] != "running":
                break
            time.sleep(0.1)
        self.assertEqual(r["status"], "done")
        result = [e for e in r["events"] if e["type"] == "result"][-1]["text"]
        self.assertIn(ag["model_code"], result)                  # sample_output.md filled with model fields
        _, d = self.call("alice", f"/api/agents/{ag['id']}")
        self.assertEqual(d["agent"]["status"], "in_progress")    # todo → in_progress on first run
        self.assertEqual(d["runs"][0]["id"], run["id"])
        code, c = self.call("alice", f"/api/agents/{ag['id']}/comment", {"run_id": run["id"]})
        self.assertEqual((code, c["status"]), (200, "sample"))
        _, d = self.call("alice", f"/api/agents/{ag['id']}")
        self.assertIn(ag["model_code"], d["comments"][0]["body"])

    def wait(self, user, rid):
        for _ in range(60):
            _, r = self.call(user, f"/api/runs/{rid}")
            if r["status"] != "running":
                return r
            time.sleep(0.1)
        self.fail("run did not finish")

    def test_models_i_ran_come_first(self):
        _, rows = self.call("alice", "/api/models")
        ran = [r["jira_key"] for r in rows if r["my_last"]]
        self.assertEqual(ran, [r["jira_key"] for r in rows][:len(ran)])        # mine first
        self.assertEqual(ran[0], "MDL-101")                                     # most recent first (seed)
        lasts = [r["my_last"] for r in rows if r["my_last"]]
        self.assertEqual(lasts, sorted(lasts, reverse=True))
        _, bob = self.call("bob", "/api/models")
        self.assertNotEqual([r["jira_key"] for r in bob if r["my_last"]], ran)  # per user

    def test_model_detail_usage(self):
        _, d = self.call("alice", "/api/models/MDL-101")
        u = {a["skill_name"]: a for a in d["agents"]}
        self.assertEqual(u["model-spec-review"]["my_runs"], 2)
        self.assertEqual(u["model-test-plan"]["my_runs"], 0)                   # bob ran it, not alice
        self.assertEqual(u["model-test-plan"]["team_runs"], 1)
        self.assertTrue(all(a["installed"] and a["can_run"] for a in d["agents"]))
        _, d2 = self.call("alice", "/api/models/tv-oled65-x1")                 # by model code
        self.assertEqual(d2["model"]["jira_key"], "MDL-101")

    def test_anyone_can_use_any_agent_usage_is_personal(self):
        _, d = self.call("bob", "/api/models/MDL-104")                         # bob never ran MDL-104
        ag = d["agents"][0]
        self.assertEqual(ag["my_runs"], 0)
        code, run = self.call("bob", "/api/runs", {"agent_id": ag["id"]})
        self.assertEqual(code, 201)
        self.wait("bob", run["id"])
        _, rows = self.call("bob", "/api/models")
        self.assertEqual(rows[0]["jira_key"], "MDL-104")                       # now bob's most recent model
        _, mine = self.call("bob", "/api/models/MDL-104")
        self.assertEqual(mine["agents"][0]["my_runs"], 1)
        _, alice = self.call("alice", "/api/models/MDL-104")
        self.assertEqual((alice["agents"][0]["my_runs"], alice["agents"][0]["team_runs"]), (0, 1))
        # bob's result cannot be posted by alice
        self.assertEqual(self.call("alice", f"/api/agents/{ag['id']}/comment", {"run_id": run["id"]})[0], 404)
        # admin looking at alice's screen: view only
        _, adm = self.call("tommyhj", "/api/models/MDL-104?as=alice")
        self.assertFalse(any(a["can_run"] for a in adm["agents"]))

    def test_register_local_model(self):
        code, r = self.call("jiwoo", "/api/models", {"model_code": "ACC-CABLE-01", "name": "케이블", "product_line": "액세서리"})
        self.assertEqual((code, r["jira_key"], len(r["agents_created"])), (201, "ACC-CABLE-01", 2))
        self.assertEqual(self.call("jiwoo", "/api/models", {"name": "no code"})[0], 400)

    def test_cli_use_is_recorded(self):
        env = {**os.environ, "WORK_HISTORY_DB": DB, "WORK_HISTORY_USER": "jiwoo"}
        out = subprocess.run([sys.executable, str(SCRIPTS / "models.py"), "use", "MDL-103", "model-cert-check",
                              "--summary", "CLI 인증 점검"], env=env, capture_output=True, text=True)
        self.assertIn("recorded run", out.stdout, out.stderr)
        _, rows = self.call("tommyhj", "/api/models?as=jiwoo")
        self.assertEqual(rows[0]["jira_key"], "MDL-103")
        self.assertEqual(rows[0]["my_last_skill"], "model-cert-check")
        out = subprocess.run([sys.executable, str(SCRIPTS / "models.py"), "show", "MDL-103"], env=env,
                             capture_output=True, text=True)
        self.assertIn("✅ 사용함", out.stdout)

    def test_status_change(self):
        ag = self.first_todo("bob")
        self.assertEqual(self.call("bob", f"/api/agents/{ag['id']}", {"status": "done"}, "PATCH")[0], 200)
        self.assertEqual(self.call("bob", f"/api/agents/{ag['id']}", {"status": "nope"}, "PATCH")[0], 400)

    def test_webhook_endpoint(self):
        payload = json.loads((SAMPLES / "webhook_issue_created.json").read_text(encoding="utf-8"))
        self.assertEqual(self.call("alice", "/api/jira/webhook?secret=wrong", payload)[0], 403)
        code, r = self.call("alice", "/api/jira/webhook?secret=s3cret", payload)
        self.assertEqual(code, 200)
        _, rows = self.call("alice", "/api/agents")
        self.assertIn("MDL-106", {x["jira_key"] for x in rows})

    def test_sync_admin_only(self):
        self.assertEqual(self.call("alice", "/api/jira/sync", {})[0], 403)
        code, rep = self.call("tommyhj", "/api/jira/sync", {})
        self.assertEqual((code, len(rep)), (200, 5))


if __name__ == "__main__":
    unittest.main()
