#!/usr/bin/env python3
"""Model data for the model-agents menu.

  models.py show <KEY|MODEL_CODE> [--json]       model fields (agent skills read this)
  models.py add <MODEL_CODE> --line <제품군> [--name … --key … --due … --regions … --spec-url …]
  models.py use <KEY|MODEL_CODE> <skill> [--summary …] [--status done|failed]   record a run (menu shows ✅)
  models.py seed [--reset]                       load the TEST data in ../samples
  (--user NAME anywhere; default MODEL_AGENTS_USER → git e-mail → OS user)

In-house integration: replace `load_models()` (and the usage import, if any) with your own source —
an issue tracker, a PLM, an internal API … — returning dicts in the shape of samples/models.json.
"""
import argparse
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import compat  # noqa: E402
import registry  # noqa: E402
import store  # noqa: E402

SAMPLES = Path(__file__).resolve().parent.parent / "samples"


def load_models():
    """TEST source. Swap for the in-house one; keep the returned shape:
    [{key, model_code, name, product_line, status, due, url, roles: {role: username}, …extra fields}]"""
    return json.loads((SAMPLES / "models.json").read_text(encoding="utf-8"))["models"]


def import_models(conn, models):
    agents = registry.load_agents()
    report = []
    for m in models:
        mid, new = store.upsert_model(conn, m)
        created = store.ensure_agents(conn, store.resolve_model(conn, m["key"]),
                                      registry.agents_for(m.get("product_line"), agents))
        report.append({"key": m["key"], "new": new, "agents_created": created})
    conn.commit()
    return report


def seed(conn, reset=False):
    if reset:
        conn.executescript("DELETE FROM usage; DELETE FROM agents; DELETE FROM models;")
    report = import_models(conn, load_models())
    now = datetime.now(timezone.utc).replace(microsecond=0)
    n = 0
    for i, u in enumerate(json.loads((SAMPLES / "usage.json").read_text(encoding="utf-8"))["usage"]):
        m = store.resolve_model(conn, u["model"])
        ag = m and store.get_agent(conn, m["id"], u["skill"])
        if ag:
            ts = (now - timedelta(days=u["days_ago"], hours=i)).isoformat()
            store.record_use(conn, ag["id"], u["user"], "sample run", ts=ts)
            n += 1
    return {"models": len(report), "agents": sum(len(r["agents_created"]) for r in report), "usage": n}


def main():
    compat.utf8_stdio()
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--user", default=argparse.SUPPRESS)
    ap = argparse.ArgumentParser(parents=[common])
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("show", parents=[common]); s.add_argument("model"); s.add_argument("--json", action="store_true")
    a = sub.add_parser("add", parents=[common]); a.add_argument("code"); a.add_argument("--line", required=True)
    for opt in ("--name", "--key", "--due", "--regions", "--spec-url", "--status"):
        a.add_argument(opt)
    u = sub.add_parser("use", parents=[common]); u.add_argument("model"); u.add_argument("skill")
    u.add_argument("--summary"); u.add_argument("--status", default="done", choices=["done", "failed"])
    d = sub.add_parser("seed", parents=[common]); d.add_argument("--reset", action="store_true")
    args = ap.parse_args()
    user = getattr(args, "user", None) or store.resolve_username()
    conn = store.connect()

    if args.cmd == "show":
        m = store.resolve_model(conn, args.model)
        if not m:
            sys.exit(f"모델을 찾을 수 없습니다: {args.model}")
        info = {**m.pop("fields"), **{k: v for k, v in m.items() if k not in ("id", "updated_at")}}
        if args.json:
            print(json.dumps(info, ensure_ascii=False, indent=2))
        else:
            for k, v in info.items():
                print(f"{k:13} {v}")
    elif args.cmd == "add":
        m = {"key": args.key or args.code, "model_code": args.code, "name": args.name or args.code,
             "product_line": args.line, "status": args.status, "due": args.due, "regions": args.regions,
             "spec_url": args.spec_url, "roles": {}}
        print(json.dumps(import_models(conn, [m])[0], ensure_ascii=False))
    elif args.cmd == "use":
        m = store.resolve_model(conn, args.model)
        if not m:
            sys.exit(f"모델을 찾을 수 없습니다: {args.model}")
        store.ensure_agents(conn, m, registry.agents_for(m.get("product_line")))
        ag = store.get_agent(conn, m["id"], args.skill)
        if not ag:
            sys.exit(f"{m['key']}에는 {args.skill} 에이전트가 없습니다")
        store.record_use(conn, ag["id"], user, args.summary, args.status)
        print(f"recorded: {user} · {m['key']} · {args.skill}")
    elif args.cmd == "seed":
        print(json.dumps(seed(conn, args.reset), ensure_ascii=False))


if __name__ == "__main__":
    main()
