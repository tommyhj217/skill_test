#!/usr/bin/env python3
"""model-agents: agent discovery, dynamic views (md / choices / json), usage, install. Stdlib only.

  python3 tests/test_model_agents.py -v
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
TMP = Path(tempfile.mkdtemp(prefix="model-agents-"))
EXTRA = TMP / "team-skills"                     # a team's own skill folder outside the repo
os.environ.update(MODEL_AGENTS_DB=str(TMP / "t.db"), MODEL_AGENTS_SKILL_ROOTS=str(EXTRA))
sys.path.insert(0, str(SCRIPTS))

import models  # noqa: E402
import registry  # noqa: E402
import store  # noqa: E402


def sh(cmd):
    out = subprocess.run(cmd, shell=True, capture_output=True, text=True, env=os.environ)
    assert out.returncode == 0, out.stderr
    return out.stdout


def view(*args, user="alice", fmt="json"):
    out = sh(" ".join([sys.executable, str(SCRIPTS / "view.py"), *args, "--user", user, "--format", fmt]))
    return out if fmt == "md" else json.loads(out)


def follow(action, fmt="json"):
    return json.loads(sh(action["command"] + f" --format {fmt}"))


class Discovery(unittest.TestCase):
    def test_declared_agents_only(self):
        ag = registry.load_agents()
        self.assertEqual(sorted(ag), ["model-cert-check", "model-release-note", "model-spec-review",
                                      "model-test-plan", "team-bom-check"])     # menu itself is not an agent

    def test_frontmatter_only_skill(self):
        bom = registry.load_agents()["team-bom-check"]
        self.assertEqual((bom["label"], bom["icon"], bom["agent"]["role"], bom["agent"]["order"]),
                         ("BOM 점검", "🧾", "dev", 50))
        self.assertEqual(bom["prompt"], "/team-bom-check {key} ({model_code}) {args}")

    def test_product_lines_and_order(self):
        self.assertEqual([s for s, _ in registry.agents_for("TV")],
                         ["model-spec-review", "model-test-plan", "model-cert-check", "model-release-note", "team-bom-check"])
        self.assertEqual([s for s, _ in registry.agents_for("액세서리")], ["model-spec-review", "model-release-note"])
        self.assertEqual(len(registry.agents_for("오디오")), 4)

    def test_runner_syntax(self):
        p = "/model-test-plan MDL-101"
        self.assertEqual(registry.for_runner("codex", p, "model-test-plan"), "$model-test-plan MDL-101")
        self.assertEqual(registry.for_runner("claude", p, "model-test-plan"), p)


class Views(unittest.TestCase):
    def setUp(self):
        models.seed(store.connect(), reset=True)

    def test_home_mine_first(self):
        v = view("home")
        self.assertEqual(v["sections"][0]["title"], "최근 실행한 모델")
        self.assertEqual([i["id"] for i in v["sections"][0]["items"]], ["MDL-101", "MDL-103", "MDL-105"])
        self.assertEqual([i["id"] for i in v["sections"][1]["items"]], ["MDL-102", "MDL-104"])   # owned, by due

    def test_home_only_my_models(self):
        v = view("home", user="bob")                                        # bob has no role on MDL-104
        self.assertNotIn("MDL-104", [i["id"] for s in v["sections"] for i in s["items"]])
        allv = follow(next(a for a in v["actions"] if a["id"] == "all")["action"])
        self.assertIn("MDL-104", [i["id"] for s in allv["sections"] for i in s["items"]])

    def test_model_agents_usage(self):
        items = view("model", "MDL-101")["sections"][0]["items"]
        self.assertEqual({i["id"]: i["used"] for i in items},
                         {"model-spec-review": True, "model-test-plan": False, "model-cert-check": True,
                          "model-release-note": False, "team-bom-check": False})
        self.assertEqual(len(view("model", "acc-remote-v2")["sections"][0]["items"]), 2)   # by code, any case

    def test_agent_form(self):
        v = view("agent", "MDL-101", "model-test-plan")
        f = {x["name"]: x for x in v["form"]["fields"]}
        self.assertEqual(f["regions"]["default"], "KR, US, EU")
        self.assertTrue(v["invoke"]["by_runner"]["codex"].startswith("$model-test-plan MDL-101 (TV-OLED65-X1)"))

    def test_full_loop(self):
        """home → model → agent → record use → ✅, only by running the commands the views hand out."""
        mi = next(i for s in view("home", user="jiwoo")["sections"] for i in s["items"] if i["id"] == "MDL-103")
        rn = next(i for i in follow(mi["action"])["sections"][0]["items"] if i["id"] == "model-release-note")
        self.assertFalse(rn["used"])
        av = follow(rn["action"])
        sh(av["after"]["record_use"].replace("<한 줄 결과>", "초안 작성"))
        mv = follow(av["actions"][0]["action"])
        self.assertTrue(next(i for i in mv["sections"][0]["items"] if i["id"] == "model-release-note")["used"])
        self.assertEqual(view("home", user="jiwoo")["sections"][0]["items"][0]["id"], "MDL-103")

    # ── choices (AskUserQuestion-shaped): paging, follow-ups, rounds, applying values ──

    def pages_of(self, *args, user="alice"):
        first = view(*args, user=user, fmt="choices")
        pages = [first]
        for p in range(2, first["pages"] + 1):
            pages.append(view(*args, "--page", str(p), user=user, fmt="choices"))
        return pages

    def test_list_paging_keeps_every_item_and_action(self):
        for args, user in ((("home",), "alice"), (("home",), "bob"), (("model", "MDL-101"), "alice")):
            full = view(*args, user=user)
            expected = [i["title"][:40] for s in full["sections"] for i in s["items"]] + \
                       [a["label"][:40] for a in full["actions"]]
            seen = []
            for pg in self.pages_of(*args, user=user):
                opts = pg["questions"][0]["options"]
                self.assertTrue(2 <= len(opts) <= 4, (args, len(opts)))
                seen += [o["label"] for o in opts if not o["label"].startswith("더 보기")]
            self.assertEqual(seen, expected, args)
        more = view("model", "MDL-101", fmt="choices")["questions"][0]["options"][-1]
        self.assertIn("--page 2", more["action"]["command"])             # the more-option opens the next page

    def chain(self, form, field):
        """Walk a field's question + follow-ups like a host would; return all selectable values."""
        q = next(q for r in form["rounds"] for q in r if q["field"] == field)
        values = []
        while q:
            self.assertTrue(2 <= len(q["options"]) <= 4)
            values += [o["value"] for o in q["options"] if "value" in o]
            nxt = next((o["followup"] for o in q["options"] if o.get("followup", "").startswith(field + "#")), None)
            q = form["followups"].get(nxt) if nxt else None
        return values

    def test_select_over_4_options_uses_followup(self):
        form = view("agent", "MDL-101", "model-spec-review", fmt="choices")     # focus: 5 options
        first = form["rounds"][0][0]
        self.assertEqual([o["label"] for o in first["options"]][-1], "다른 값 보기 (2)")
        self.assertEqual(first["options"][0]["value"], "전체")                  # current value first
        self.assertEqual(sorted(self.chain(form, "focus")), sorted(["전체", "전원", "디스플레이", "통신", "기구"]))

    def test_big_form_rounds_and_followups(self):
        d = EXTRA / "big-form-agent"
        d.mkdir(parents=True, exist_ok=True)
        (d / "SKILL.md").write_text("---\nname: big-form-agent\ndescription: test\n---\n", encoding="utf-8")
        regions = ["KR", "US", "EU", "JP", "CN", "IN", "BR", "AU", "CA"]
        (d / "ui.json").write_text(json.dumps({
            "label": "큰 폼", "agent": {"role": "qa", "product_lines": ["*"]},
            "inputs": [{"name": "region", "label": "지역", "type": "select", "options": regions, "default": "EU"}] +
                      [{"name": f"f{i}", "label": f"필드{i}", "type": "text", "default": f"d{i}"} for i in range(1, 6)],
            "prompt": "/big-form-agent {key} {region} {f1} {f5}"}, ensure_ascii=False), encoding="utf-8")
        try:
            form = view("agent", "MDL-101", "big-form-agent", fmt="choices")
            self.assertEqual([len(r) for r in form["rounds"]], [4, 2])             # 6 fields → 2 rounds
            vals = self.chain(form, "region")
            self.assertEqual(sorted(vals), sorted(regions))                        # 9 values, none lost
            self.assertEqual(len(vals), 9)
            self.assertEqual(vals[0], "EU")
            conf = view("agent", "MDL-101", "big-form-agent", "--set", "region=br", "--set", "f5=마지막",
                        fmt="choices")
            self.assertEqual(conf["invoke"]["prompt"], "/big-form-agent MDL-101 BR d1 마지막")
        finally:
            for f in d.iterdir():
                f.unlink()
            d.rmdir()

    def test_set_applies_to_invoke(self):
        conf = view("agent", "MDL-101", "model-spec-review", "--set", "'focus=기구'", "--set", "'notes=EU 대기전력'",
                    fmt="choices")
        self.assertEqual(conf["view"], "confirm")
        self.assertEqual(conf["context"]["values"]["focus"], "기구")
        self.assertEqual(sorted(conf["context"]["changed"]), ["focus", "notes"])
        for runner, prefix in (("claude", "/model-spec-review"), ("codex", "$model-spec-review")):
            line = conf["invoke"]["by_runner"][runner]
            self.assertTrue(line.startswith(prefix) and "범위: 기구" in line and "EU 대기전력" in line, line)
        self.assertEqual([o["label"] for o in conf["questions"][0]["options"]], ["실행", "값 다시 고르기", "취소"])
        md = view("agent", "MDL-101", "model-spec-review", "--set", "focus=통신", fmt="md")
        self.assertIn("| 검토 범위 (`focus`) | 통신 ✏️", md)
        self.assertIn("범위: 통신", md)

    def test_set_invalid_values(self):
        cmd = f"{sys.executable} {SCRIPTS / 'view.py'} agent MDL-101 model-spec-review --user alice"
        bad = subprocess.run(cmd + " --set focus=배터리 --set nope=1", shell=True, capture_output=True, text=True,
                             env=os.environ)
        self.assertEqual(bad.returncode, 1)
        self.assertIn("'배터리' 는 선택지에 없습니다", bad.stdout)
        self.assertIn("알 수 없는 입력: nope", bad.stdout)
        ok = view("agent", "MDL-101", "model-test-plan", "--set", "level=FULL", fmt="json")   # case-insensitive
        self.assertEqual(ok["form"]["values"]["level"], "full")

    def test_markdown(self):
        md = view("model", "MDL-101", fmt="md")
        for s in ("| 5 | 🧾 BOM 점검 `/team-bom-check`", "✅ 사용함", "⚪ 미사용", "번호(1–5)"):
            self.assertIn(s, md)

    def test_add_model_and_drop_in_skill(self):
        sh(f"{sys.executable} {SCRIPTS / 'models.py'} add TV-NEW-01 --name '새 TV' --line TV")
        self.assertEqual(len(view("model", "TV-NEW-01")["sections"][0]["items"]), 5)
        d = EXTRA / "hw-thermal-check"
        d.mkdir(parents=True, exist_ok=True)
        (d / "SKILL.md").write_text(textwrap.dedent("""\
            ---
            name: hw-thermal-check
            description: Thermal review for a model.
            metadata:
              model-agent: "true"
              agent-role: dev
              agent-product-lines: "*"
              agent-label: 발열 검토
              agent-icon: 🌡
            ---
            """), encoding="utf-8")
        try:
            items = {i["id"]: i for i in view("model", "MDL-104")["sections"][0]["items"]}
            self.assertEqual(items["hw-thermal-check"]["title"], "🌡 발열 검토")
            self.assertEqual(view("agent", "MDL-104", "hw-thermal-check")["invoke"]["by_runner"]["claude"],
                             "/hw-thermal-check MDL-104 (ACC-REMOTE-V2)")
        finally:
            (d / "SKILL.md").unlink()
        self.assertNotIn("hw-thermal-check", [i["id"] for i in view("model", "MDL-104")["sections"][0]["items"]])

    def test_show_json(self):
        info = json.loads(sh(f"{sys.executable} {SCRIPTS / 'models.py'} show MDL-102 --json"))
        self.assertEqual((info["model_code"], info["roles"]["qa"]), ("MON-27Q-PRO", "bob"))


class Install(unittest.TestCase):
    def test_install_project(self):
        proj = TMP / "proj"
        cmd = f"{sys.executable} {SCRIPTS / 'install.py'} --project {proj} --runners claude,codex"
        sh(cmd)
        sh(cmd)                                                               # idempotent
        names = sorted(p.name for p in (proj / ".agents" / "skills").iterdir())
        self.assertEqual(names, ["model-agents", "model-cert-check", "model-release-note", "model-spec-review",
                                 "model-test-plan", "team-bom-check"])
        self.assertTrue((proj / ".claude" / "skills" / "model-agents" / "scripts" / "view.py").exists())
        self.assertFalse((proj / ".agents" / "skills" / "model-agents" / "tests").exists())
        allow = json.loads((proj / ".claude" / "settings.json").read_text())["permissions"]["allow"]
        self.assertEqual(len(allow), 4)
        self.assertFalse((proj / ".gemini").exists())


if __name__ == "__main__":
    unittest.main()
