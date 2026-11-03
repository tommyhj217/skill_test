#!/usr/bin/env python3
"""Model-first view of the agent skills (used by the `model-agents` entry skill and by agent skills).

  models.py mine                         # models: the ones I ran first (most recent), then the rest
  models.py show MDL-101                 # agents available for the model + whether I/the team used them
  models.py add TV-NEW-01 --name "..." --line TV [--regions "KR, US"] [--due 2026-12-01] [--key MDL-200]
  models.py use MDL-101 model-spec-review [--summary "..."] [--status done|failed]   # record a CLI run
  add --json to mine/show for machine-readable output.

The model can be given by its key (Jira key or local key) or its model code.
The user is WORK_HISTORY_USER → git user.email → OS user (see db.resolve_username).
"""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import db  # noqa: E402
import jira_sync  # noqa: E402
import skills_registry as reg  # noqa: E402


def _skill_info(name, skills):
    sk = skills.get(name)
    return {"label": sk["label"] if sk else name, "icon": sk.get("icon", "") if sk else "",
            "installed": bool(sk)}


def model_detail(conn, ref, username):
    m = db.resolve_model(conn, ref)
    if not m:
        return None
    jira_sync.ensure_agents(conn, m)
    skills = reg.load_skills()
    agents = [{**a, **_skill_info(a["skill_name"], skills)} for a in db.model_agents(conn, m["id"], username)]
    return {"model": m, "agents": agents}


def _short(ts):
    return (ts or "")[:16].replace("T", " ")


def main():
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--user", default=argparse.SUPPRESS)   # accepted before or after the subcommand
    ap = argparse.ArgumentParser(parents=[common])
    sub = ap.add_subparsers(dest="cmd", required=True)
    _add = sub.add_parser
    sub.add_parser = lambda *a, **k: _add(*a, parents=[common], **k)
    m = sub.add_parser("mine"); m.add_argument("--json", action="store_true"); m.add_argument("--limit", type=int, default=30)
    s = sub.add_parser("show"); s.add_argument("model"); s.add_argument("--json", action="store_true")
    a = sub.add_parser("add"); a.add_argument("code"); a.add_argument("--name"); a.add_argument("--line")
    a.add_argument("--regions"); a.add_argument("--due"); a.add_argument("--key"); a.add_argument("--spec-url")
    u = sub.add_parser("use"); u.add_argument("model"); u.add_argument("skill"); u.add_argument("--summary")
    u.add_argument("--status", default="done", choices=["done", "failed"])
    args = ap.parse_args()
    user = getattr(args, "user", None) or db.resolve_username()
    conn = db.connect()

    if args.cmd == "mine":
        rows = db.models_for_user(conn, user)[:args.limit]
        if args.json:
            return print(json.dumps(rows, ensure_ascii=False, indent=2))
        print(f"[{user}] 내가 실행한 모델이 먼저 표시됩니다")
        mine = [r for r in rows if r["my_last"]]
        for title, group in (("■ 최근 실행한 모델", mine), ("□ 다른 모델", [r for r in rows if not r["my_last"]])):
            if group:
                print(title)
            for r in group:
                usage = (f"내 실행 {r['my_runs']}회 · 에이전트 {r['my_agents_used']}/{r['agent_count']} 사용 · "
                         f"최근 {_short(r['my_last'])} {r['my_last_skill']}") if r["my_last"] else \
                        f"에이전트 {r['agent_count']}개 · 팀 실행 {r['team_runs']}회"
                print(f"  {r['jira_key']:10} {r['model_code']:16} {r['name'] or '':22} {usage}")
    elif args.cmd == "show":
        d = model_detail(conn, args.model, user)
        if not d:
            sys.exit(f"모델을 찾을 수 없습니다: {args.model} (models.py add 로 등록)")
        if args.json:
            return print(json.dumps(d, ensure_ascii=False, indent=2))
        mo = d["model"]
        print(f"[현재 사용자: {user}] '사용함/미사용'은 {user} 기준, '팀'은 전체 사용자 기준")
        print(f"{mo['jira_key']} · {mo['model_code']} · {mo['name']} ({mo['product_line'] or '-'}) 마감 {mo['due'] or '-'}")
        for g in d["agents"]:
            used = f"✅ 사용함 {g['my_runs']}회 (최근 {_short(g['my_last'])}, {g['my_last_status']})" if g["my_runs"] \
                else "⚪ 미사용"
            inst = "" if g["installed"] else "  [스킬 미설치]"
            print(f"  {g['icon']} {g['label']:10} /{g['skill_name']:20} {used:40} 팀 {g['team_runs']}회 · "
                  f"담당 {g['assignee'] or '-'} · 상태 {g['status']}{inst}")
    elif args.cmd == "add":
        print(json.dumps(jira_sync.register_model(conn, args.code, args.name, args.line, args.regions, args.due,
                                                  args.key, args.spec_url), ensure_ascii=False))
    elif args.cmd == "use":
        mo = db.resolve_model(conn, args.model)
        if not mo:
            sys.exit(f"모델을 찾을 수 없습니다: {args.model}")
        jira_sync.ensure_agents(conn, mo)
        ag = next((x for x in db.list_agents(conn, model_key=mo["jira_key"]) if x["skill_name"] == args.skill), None)
        if not ag:
            sys.exit(f"{mo['jira_key']}에는 {args.skill} 에이전트가 없습니다")
        rid = db.record_use(conn, user, ag, args.summary, args.status)
        print(f"recorded run #{rid}: {user} · {mo['jira_key']} · {args.skill}")


if __name__ == "__main__":
    main()
