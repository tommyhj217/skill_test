#!/usr/bin/env python3
"""End-to-end tests for multi-runner support (stdlib unittest, no API calls).

Fake `codex`, `copilot`, `gemini` CLIs in tests/fakebin emit the output formats documented by
each vendor; `claude` is hidden from PATH so nothing real is called.

  python3 tests/test_multi_runner.py -v
"""
import json
import os
import socket
import subprocess
import sys
import tempfile
import time
import unittest
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
SCRIPTS = HERE.parent / "scripts"
sys.path.insert(0, str(SCRIPTS))

TMP = tempfile.mkdtemp(prefix="wh-test-")
os.environ["WORK_HISTORY_DB"] = str(Path(TMP) / "t.db")
os.environ["WORK_HISTORY_USER"] = "tester"
# only fake CLIs + basic system dirs on PATH (no real claude)
os.environ["PATH"] = os.pathsep.join([str(HERE / "fakebin"), os.path.dirname(sys.executable), "/usr/bin", "/bin"])

import db  # noqa: E402
import hook_logger  # noqa: E402
import runners  # noqa: E402


def free_port():
    s = socket.socket(); s.bind(("127.0.0.1", 0)); p = s.getsockname()[1]; s.close(); return p


class HookNormalization(unittest.TestCase):
    """Payload samples follow each runner's hook documentation."""

    def test_claude_prompt(self):
        r = hook_logger.normalize({"hook_event_name": "UserPromptSubmit", "session_id": "abc",
                                   "cwd": "/repo", "prompt": "hi"}, "claude")
        self.assertEqual((r["kind"], r["runner"], r["session_id"], r["summary"]), ("prompt", "claude-code", "abc", "hi"))

    def test_claude_skill_tool(self):
        r = hook_logger.normalize({"hook_event_name": "PostToolUse", "session_id": "abc", "cwd": "/r",
                                   "tool_name": "Skill", "tool_input": {"skill": "pdf"}, "tool_response": {}}, "claude")
        self.assertEqual((r["kind"], r["skill_name"]), ("skill", "pdf"))

    def test_codex_post_tool_auto_detect(self):
        p = {"session_id": "thr_456", "transcript_path": "/repo/.codex/rollout.jsonl", "cwd": "/repo",
             "hook_event_name": "PostToolUse", "model": "m", "turn_id": "turn_9", "tool_name": "Bash",
             "tool_use_id": "call_42", "tool_input": {"command": "npm test"}, "tool_response": "Tests failed: 2"}
        r = hook_logger.normalize(p, "auto")
        self.assertEqual((r["runner"], r["kind"], r["summary"]), ("codex", "tool", "$ npm test"))

    def test_copilot_camelcase(self):
        p = {"sessionId": "s-1", "timestamp": 1762041600000, "cwd": "/repo", "toolName": "bash",
             "toolArgs": json.dumps({"command": "ls"}), "toolResult": {"resultType": "success", "textResultForLlm": "x"}}
        r = hook_logger.normalize(p, "copilot", "postToolUse")
        self.assertEqual((r["runner"], r["kind"], r["summary"], r["session_id"]), ("copilot", "tool", "$ ls", "s-1"))
        self.assertTrue(r["ts"].startswith("2025-11-02"))
        r = hook_logger.normalize({"sessionId": "s-1", "timestamp": 1, "cwd": "/r", "prompt": "fix"}, "auto",
                                  "userPromptSubmitted")
        self.assertEqual((r["runner"], r["kind"]), ("copilot", "prompt"))

    def test_gemini_events(self):
        r = hook_logger.normalize({"hook_event_name": "BeforeAgent", "session_id": "g1", "cwd": "/r",
                                   "prompt": "explain"}, "auto")
        self.assertEqual((r["runner"], r["kind"]), ("gemini", "prompt"))
        r = hook_logger.normalize({"hook_event_name": "AfterTool", "session_id": "g1", "cwd": "/r",
                                   "tool_name": "read_file", "tool_input": {"absolute_path": "/r/a.py"}}, "gemini")
        self.assertEqual(r["summary"], "read_file /r/a.py")

    def test_unknown_event_ignored(self):
        self.assertIsNone(hook_logger.normalize({"hook_event_name": "Whatever"}, "claude"))

    def test_hook_script_never_fails(self):
        p = subprocess.run([sys.executable, str(SCRIPTS / "hook_logger.py")], input="not json",
                           text=True, capture_output=True)
        self.assertEqual(p.returncode, 0)


class Adapters(unittest.TestCase):
    def test_skill_syntax(self):
        self.assertEqual(runners.adapt_prompt("claude", "/pdf 요약", "pdf"), "/pdf 요약")
        self.assertEqual(runners.adapt_prompt("codex", "/pdf 요약", "pdf"), "$pdf 요약")
        self.assertEqual(runners.adapt_prompt("copilot", "/pdf 요약", "pdf"), "Use the pdf skill: 요약")

    def test_resume_commands(self):
        self.assertEqual(runners.build_cmd("codex", "go", "T1")[-3:], ["resume", "T1", "go"])
        self.assertIn("--resume", runners.build_cmd("gemini", "go", "G1"))
        self.assertNotIn("--resume", runners.build_cmd("claude", "go"))

    def test_alias(self):
        self.assertEqual(runners.canonical("claude-code"), "claude")
        self.assertIsNone(runners.canonical("cursor"))


class ServerE2E(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.port = free_port()
        cls.base = f"http://127.0.0.1:{cls.port}"
        cls.proc = subprocess.Popen([sys.executable, str(SCRIPTS / "server.py"), "--port", str(cls.port),
                                     "--workdir", TMP], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        conn = db.connect()
        cls.acts = {}
        for r, sid in [("codex", "0199a213-81c0-7800-8aa1-bbab2a035a53"), ("copilot", "cp-123"),
                       ("gemini", "gm-456"), ("cursor", "cu-789"), ("claude-code", "cl-000")]:
            cls.acts[r] = db.add_activity(conn, "tester", "tool", f"{r} 작업", tool_name="Edit",
                                          session_id=sid, runner=r, project="demo")
        for _ in range(50):
            try:
                urllib.request.urlopen(cls.base + "/api/me"); break
            except OSError:
                time.sleep(0.1)

    @classmethod
    def tearDownClass(cls):
        cls.proc.terminate()

    def call(self, path, body=None, method=None):
        req = urllib.request.Request(self.base + path, method=method or ("POST" if body is not None else "GET"),
                                     data=json.dumps(body).encode() if body is not None else None)
        try:
            with urllib.request.urlopen(req) as r:
                return r.status, json.loads(r.read())
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read())

    def wait_run(self, rid):
        for _ in range(100):
            _, r = self.call(f"/api/runs/{rid}")
            if r["status"] != "running":
                return r
            time.sleep(0.1)
        self.fail("run did not finish")

    def test_runner_inventory(self):
        _, me = self.call("/api/me")
        inst = {r["id"]: r["installed"] for r in me["runners"]}
        self.assertEqual(inst, {"claude": False, "codex": True, "copilot": True, "gemini": True, "mock": True})
        self.assertEqual(me["runner"], "codex")  # first installed adapter

    def test_continue_each_runner(self):
        expect = {"codex": "[codex] resumed done: 계속", "gemini": "[gemini] done: 계속",
                  "copilot": "resumed session cp-123"}
        sessions = {"codex": "0199a213-81c0-7800-8aa1-bbab2a035a53", "gemini": "gm-456", "copilot": "cp-123"}
        for r, needle in expect.items():
            with self.subTest(runner=r):
                _, d = self.call(f"/api/activities/{self.acts[r]}")
                cont = d["actions"][0]
                self.assertEqual((cont["id"], cont["runner"]), ("continue", r))
                code, run = self.call("/api/runs", {"activity_id": self.acts[r], "action_id": "continue",
                                                    "values": {"args": "계속"}})
                self.assertEqual((code, run["runner"]), (201, r))
                res = self.wait_run(run["id"])
                self.assertEqual(res["status"], "done", res["events"])
                result = [e for e in res["events"] if e["type"] == "result"][-1]["text"]
                self.assertIn(needle, result)
                if r != "copilot":  # text runner: no session id in output, keeps the resumed one
                    self.assertEqual(res["session_id"], sessions[r])
                # the result row is linked to the session → can be continued again
                _, d2 = self.call(f"/api/activities/{res['result_activity_id']}")
                self.assertEqual(d2["actions"][0]["id"], "continue")

    def test_codex_tool_events_parsed(self):
        _, run = self.call("/api/runs", {"activity_id": self.acts["codex"], "action_id": "free", "runner": "codex"})
        res = self.wait_run(run["id"])
        tools = [e["text"] for e in res["events"] if e["type"] == "tool"]
        self.assertEqual(tools, ["$ bash -lc ls", "edit src/app.py"])

    def test_no_resume_for_unknown_runner(self):
        _, d = self.call(f"/api/activities/{self.acts['cursor']}")
        self.assertNotIn("continue", [a["id"] for a in d["actions"]])
        _, pv = self.call("/api/runs/preview", {"activity_id": self.acts["cursor"], "action_id": "free"})
        self.assertEqual(pv["runner"], "codex")  # falls back to default runner

    def test_restricted_skill_rejects_other_runner(self):
        code, r = self.call("/api/runs", {"activity_id": self.acts["codex"], "action_id": "skill:code-review",
                                          "runner": "codex"})
        self.assertEqual(code, 400)
        self.assertIn("claude", r["error"])

    def test_uninstalled_runner(self):
        code, r = self.call("/api/runs", {"activity_id": self.acts["codex"], "action_id": "free", "runner": "claude"})
        self.assertEqual(code, 400)
        self.assertIn("설치", r["error"])

    def test_skill_prompt_translated_per_runner(self):
        for r, prefix in [("codex", "$work-history"), ("gemini", "Use the work-history skill:")]:
            _, pv = self.call("/api/runs/preview", {"activity_id": self.acts[r], "action_id": "skill:work-history"})
            self.assertTrue(pv["prompt"].startswith(prefix), pv)


if __name__ == "__main__":
    unittest.main()
