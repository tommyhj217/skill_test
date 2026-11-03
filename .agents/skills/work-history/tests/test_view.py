#!/usr/bin/env python3
"""Dynamic view (view.py) + agent-skill discovery. Runs the real CLI like a runner would.

  python3 tests/test_view.py -v
"""
import json
import os
import subprocess
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
SCRIPTS = HERE.parent / "scripts"
TMP = tempfile.mkdtemp(prefix="wh-view-")
EXTRA_SKILLS = Path(TMP) / "my-skills"          # a team's own skill folder, outside the repo
os.environ.update(WORK_HISTORY_DB=str(Path(TMP) / "v.db"), WORK_HISTORY_SKILL_ROOTS=str(EXTRA_SKILLS))
sys.path.insert(0, str(SCRIPTS))

import db  # noqa: E402
import skills_registry as reg  # noqa: E402
from seed import seed  # noqa: E402


def view(*args, user="alice", fmt="json"):
    out = subprocess.run([sys.executable, str(SCRIPTS / "view.py"), *args, "--user", user, "--format", fmt],
                         capture_output=True, text=True, env=os.environ)
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout) if fmt != "md" else out.stdout


def run_cmd(cmd, fmt="json"):
    out = subprocess.run(cmd + f" --format {fmt}", shell=True, capture_output=True, text=True, env=os.environ)
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout)


class Discovery(unittest.TestCase):
    def test_declared_agents(self):
        ag = reg.agent_skills()
        for name in ("model-spec-review", "model-test-plan", "model-cert-check", "model-release-note", "team-bom-check"):
            self.assertIn(name, ag)
        self.assertNotIn("work-history", ag)
        self.assertNotIn("model-agents", ag)                     # the menu itself is not an agent

    def test_frontmatter_only_skill(self):
        bom = reg.agent_skills()["team-bom-check"]
        self.assertEqual((bom["label"], bom["icon"], bom["agent"]["role"], bom["agent"]["order"]),
                         ("BOM 점검", "🧾", "dev", 50))
        self.assertTrue(reg.agent_applies(bom, "TV"))
        self.assertFalse(reg.agent_applies(bom, "오디오"))

    def test_product_line_rules(self):
        ag = reg.agent_skills()
        acc = sorted(n for n, s in ag.items() if reg.agent_applies(s, "액세서리"))
        self.assertEqual(acc, ["model-release-note", "model-spec-review"])


class Views(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        seed(reset=True)

    def test_home_mine_first(self):
        v = view("home")
        titles = [s["title"] for s in v["sections"]]
        self.assertEqual(titles[0], "최근 실행한 모델")
        ran = [i["id"] for i in v["sections"][0]["items"]]
        self.assertEqual(ran, ["MDL-101", "MDL-103", "MDL-105"])          # most recent first (seed)
        self.assertIn("내 담당 (아직 실행 안 함)", titles)

    def test_home_only_my_models(self):
        v = view("home", user="bob")                                       # bob owns nothing on MDL-104
        ids = [i["id"] for s in v["sections"] for i in s["items"]]
        self.assertNotIn("MDL-104", ids)
        self.assertTrue(any(a["id"] == "all" for a in v["actions"]))
        allv = run_cmd(next(a for a in v["actions"] if a["id"] == "all")["action"]["command"])
        self.assertIn("MDL-104", [i["id"] for s in allv["sections"] for i in s["items"]])

    def test_model_agents_and_usage(self):
        v = view("model", "MDL-101")
        items = v["sections"][0]["items"]
        self.assertEqual([i["id"] for i in items], ["model-spec-review", "model-test-plan", "model-cert-check",
                                                    "model-release-note", "team-bom-check"])     # declared order
        used = {i["id"]: i["used"] for i in items}
        self.assertEqual(used, {"model-spec-review": True, "model-test-plan": False, "model-cert-check": True,
                                "model-release-note": False, "team-bom-check": False})
        acc = view("model", "ACC-REMOTE-V2")                               # by model code; accessory rule
        self.assertEqual(len(acc["sections"][0]["items"]), 2)

    def test_agent_form_and_invoke(self):
        v = view("agent", "MDL-101", "model-test-plan")
        f = {x["name"]: x for x in v["form"]["fields"]}
        self.assertEqual(f["regions"]["default"], "KR, US, EU")            # filled from the model
        self.assertEqual(f["level"]["options"], ["smoke", "regression", "full"])
        by = v["invoke"]["by_runner"]
        self.assertTrue(by["claude"].startswith("/model-test-plan MDL-101"))
        self.assertTrue(by["codex"].startswith("$model-test-plan MDL-101"))

    def test_full_loop_by_following_commands(self):
        """home → model → agent → record use → model shows ✅ — only by running the commands in the views."""
        home = view("home", user="jiwoo")
        model_item = next(i for s in home["sections"] for i in s["items"] if i["id"] == "MDL-103")
        mv = run_cmd(model_item["action"]["command"])
        bom = [i for i in mv["sections"][0]["items"] if i["id"] == "model-release-note"][0]
        self.assertFalse(bom["used"])
        av = run_cmd(bom["action"]["command"])
        rec = av["after"]["record_use"].replace("<한 줄 결과>", "릴리즈 노트 초안")
        self.assertEqual(subprocess.run(rec, shell=True, env=os.environ, capture_output=True).returncode, 0)
        mv2 = run_cmd(av["actions"][0]["action"]["command"])
        self.assertTrue([i for i in mv2["sections"][0]["items"] if i["id"] == "model-release-note"][0]["used"])
        home2 = view("home", user="jiwoo")
        self.assertEqual(home2["sections"][0]["items"][0]["id"], "MDL-103")   # now most recent

    def test_choices_paging(self):
        c = view("model", "MDL-101", fmt="choices")
        opts = c["questions"][0]["options"]
        self.assertLessEqual(len(opts), 4)
        self.assertTrue(opts[-1]["label"].startswith("더 보기"))
        nxt = subprocess.run(opts[-1]["action"]["command"], shell=True, capture_output=True, text=True, env=os.environ)
        page2 = json.loads(nxt.stdout)["questions"][0]["options"]
        self.assertIn("🧾 BOM 점검", [o["label"] for o in page2])
        self.assertTrue(all(2 <= len(q["options"]) <= 4 for q in view("agent", "MDL-101", "model-test-plan",
                                                                       fmt="choices")["questions"]))

    def test_markdown(self):
        md = view("model", "MDL-101", fmt="md")
        self.assertIn("| 5 | 🧾 BOM 점검 `/team-bom-check`", md)
        self.assertIn("✅ 사용함", md)
        self.assertIn("⚪ 미사용", md)

    def test_drop_in_skill_from_another_folder(self):
        """A team's own skill in any skills folder joins the menu with no code/config change."""
        d = EXTRA_SKILLS / "hw-thermal-check"
        d.mkdir(parents=True, exist_ok=True)
        (d / "SKILL.md").write_text(textwrap.dedent("""\
            ---
            name: hw-thermal-check
            description: Thermal simulation review for a model.
            metadata:
              model-agent: "true"
              agent-role: dev
              agent-product-lines: "*"
              agent-label: 발열 검토
              agent-icon: 🌡
            ---
            # Thermal check
            """), encoding="utf-8")
        try:
            v = view("model", "MDL-104")
            items = {i["id"]: i for i in v["sections"][0]["items"]}
            self.assertIn("hw-thermal-check", items)
            self.assertEqual(items["hw-thermal-check"]["title"], "🌡 발열 검토")
            av = view("agent", "MDL-104", "hw-thermal-check")
            self.assertEqual(av["invoke"]["by_runner"]["claude"], "/hw-thermal-check MDL-104 (ACC-REMOTE-V2)")
        finally:
            (d / "SKILL.md").unlink()
        v = view("model", "MDL-104")                                        # removed → hidden again
        self.assertNotIn("hw-thermal-check", [i["id"] for i in v["sections"][0]["items"]])


if __name__ == "__main__":
    unittest.main()
