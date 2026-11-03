#!/usr/bin/env python3
"""Dynamic UI for the model agents — rendered *inside* whatever runner calls the skill.

One view document (JSON), three renderers:
  --format md       markdown with numbered choices      → any chat/CLI runner (Codex, Copilot, Gemini, …)
  --format choices  AskUserQuestion-shaped questions     → hosts with a structured choice tool (Claude Code)
  --format json     the full view document               → your own host / portal renders it as it likes

Views (each item's `action.command` produces the next view):
  view.py home                     models I am working on (ran an agent, or own a role), most recent first
  view.py models                   all models
  view.py model MDL-101            agents available for the model + whether I / the team used them
  view.py agent MDL-101 <skill>    the agent's form (fields from the skill's ui.json, defaults from the model)
                                   + how to invoke the skill on each runner
          [--set FIELD=VALUE …]    apply the user's choices → the invoke lines use them (select values are
                                   validated); with --format choices this returns a confirm question
Common: --user NAME (default: MODEL_AGENTS_USER → git e-mail → OS user), --page N (choices paging)
Schema: ../VIEW_SCHEMA.md
"""
import argparse
import json
import os
import shlex
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import registry as reg  # noqa: E402
import store  # noqa: E402

SCRIPTS = Path(__file__).resolve().parent
ROLE = {"dev": "개발", "qa": "QA", "cert": "인증", "pm": "PM"}
ST = {"todo": "할 일", "in_progress": "진행 중", "done": "완료"}
MAX_OPTS = 4   # AskUserQuestion: 2–4 options per question (it adds its own "Other" for free text)
MAX_QS = 4     # … and at most 4 questions per call
PAGE = MAX_OPTS - 1   # a page shows 3 options + "더 보기" when more remain


def _script(name):
    here = os.path.relpath(SCRIPTS, os.getcwd())
    base = here if not here.startswith("..") else str(SCRIPTS)
    return shlex.quote(f"{base}/{name}")


def _cmd(*args):
    return " ".join(["python3", _script("view.py"), *map(shlex.quote, args)])


def _short(ts):
    return (ts or "")[:16].replace("T", " ")


def _action(view, *args, user=None):
    extra = ["--user", user] if user else []
    return {"view": view, "args": list(args), "command": _cmd(view, *args, *extra)}


# ───────────── view builders ─────────────

def _model_item(m, roles, user):
    badges = []
    if m["my_last"]:
        badges.append({"text": f"최근 실행 {_short(m['my_last'])}", "tone": "info"})
        badges.append({"text": f"에이전트 {m['my_agents_used']}/{m['agent_count']} 사용", "tone": "ok"})
    else:
        badges.append({"text": f"에이전트 {m['agent_count']}개 · 아직 사용 안 함", "tone": "muted"})
    if roles:
        badges.append({"text": "내 담당: " + ", ".join(ROLE.get(r, r) for r in sorted(set(roles))), "tone": "warn"})
    if m.get("due"):
        badges.append({"text": f"마감 {m['due']}", "tone": "muted"})
    return {"id": m["key"], "title": f"{m['model_code']} · {m['name'] or ''}".strip(" ·"),
            "subtitle": " · ".join(x for x in (m["key"] if m["key"] != m["model_code"] else "",
                                               m.get("product_line") or "") if x),
            "badges": badges, "action": _action("model", m["key"], user=user)}


def models_with_agents(conn, user):
    """Attach newly installed agent skills to every model, then return the overview."""
    agents = reg.load_agents()
    for m in conn.execute("SELECT id, product_line, fields FROM models").fetchall():
        store.ensure_agents(conn, {"id": m["id"], "fields": json.loads(m["fields"])},
                            reg.agents_for(m["product_line"], agents))
    return store.models_overview(conn, user)


def view_home(conn, user, everyone=False):
    rows = models_with_agents(conn, user)
    mine = [m for m in rows if m["my_last"] or m["my_roles"]]
    others = [m for m in rows if m not in mine]
    # ran first (most recent), then only-assigned (by due date)
    ran = sorted([m for m in mine if m["my_last"]], key=lambda m: m["my_last"], reverse=True)
    only_assigned = sorted([m for m in mine if not m["my_last"]], key=lambda m: m.get("due") or "9999")
    sections = [{"title": "최근 실행한 모델", "items": [_model_item(m, m["my_roles"], user) for m in ran]},
                {"title": "내 담당 (아직 실행 안 함)", "items": [_model_item(m, m["my_roles"], user) for m in only_assigned]}]
    if everyone:
        sections.append({"title": "다른 모델", "items": [_model_item(m, None, user) for m in others]})
    sections = [s for s in sections if s["items"]]
    actions = []
    if others and not everyone:
        actions.append({"id": "all", "label": f"다른 모델 보기 ({len(others)})", "action": _action("models", user=user)})
    actions.append({"id": "add", "label": "모델 등록",
                    "action": {"view": None, "command": f"python3 {_script('models.py')} add <CODE> --name … --line <제품군>"}})
    return {"view": "models" if everyone else "home", "user": user,
            "title": "전체 모델" if everyone else f"{user} 님이 진행 중인 모델",
            "subtitle": "모델을 고르면 사용할 수 있는 에이전트와 사용 여부가 나옵니다",
            "sections": sections, "actions": actions,
            "prompt": "어떤 모델을 열까요?",
            "empty": "진행 중인 모델이 없습니다. '다른 모델 보기'나 '모델 등록'을 선택하세요." if not sections else None}


def _ctx(model, agent=None):
    ctx = {**model["fields"], "key": model["key"], "model_code": model["model_code"],
           "model_name": model.get("name"), "product_line": model.get("product_line"), "due": model.get("due")}
    if agent:
        ctx.update(role=agent.get("role"), assignee=agent.get("assignee"))
    return ctx


def view_model(conn, ref, user):
    m = store.resolve_model(conn, ref)
    if not m:
        return {"view": "error", "title": f"모델을 찾을 수 없습니다: {ref}", "sections": [],
                "actions": [{"id": "home", "label": "내 모델로", "action": _action("home", user=user)}]}
    skills = reg.load_agents()
    store.ensure_agents(conn, m, reg.agents_for(m.get("product_line"), skills))
    rows = store.model_agents(conn, m["id"], user)
    items, hidden = [], []
    for a in rows:
        sk = skills.get(a["skill_name"])
        if not sk or not reg.applies(sk, m.get("product_line")):
            hidden.append(a["skill_name"])
            continue
        used = a["my_runs"] > 0
        badges = [{"text": f"✅ 사용함 {a['my_runs']}회 · 최근 {_short(a['my_last'])}" if used else "⚪ 미사용",
                   "tone": "ok" if used else "muted"},
                  {"text": f"팀 {a['team_runs']}회 ({a['team_users']}명)", "tone": "muted"},
                  {"text": f"담당 {a['assignee'] or '-'}" + (" (나)" if a["assignee"] == user else ""),
                   "tone": "warn" if a["assignee"] == user else "muted"},
                  {"text": ST.get(a["status"], a["status"]), "tone": "info"}]
        items.append({"id": a["skill_name"], "title": f"{sk.get('icon', '')} {sk['label']}".strip(),
                      "subtitle": f"/{a['skill_name']} — {sk.get('description', '')[:90]}",
                      "used": used, "badges": badges,
                      "action": _action("agent", m["key"], a["skill_name"], user=user)})
    f = m["fields"]
    card = [{"label": "모델", "value": f"{m['model_code']} · {m.get('name') or ''}"},
            {"label": "제품군", "value": m.get("product_line") or "-"},
            {"label": "키", "value": m["key"], "link": m.get("url")},
            {"label": "마감 / 출시", "value": f"{m.get('due') or '-'} / {f.get('launch_date') or '-'}"},
            {"label": "지역", "value": f.get("regions") or "-"}]
    if f.get("spec_url"):
        card.append({"label": "사양서", "value": f["spec_url"], "link": f["spec_url"]})
    order = {i["id"]: (skills[i["id"]]["agent"]["order"], i["title"]) for i in items}
    items.sort(key=lambda i: order[i["id"]])
    used_n = sum(1 for i in items if i["used"])
    return {"view": "model", "user": user, "title": f"{m['model_code']} 에이전트",
            "subtitle": f"사용 가능한 에이전트 {len(items)}개 · 내가 사용 {used_n}개", "card": card,
            "sections": [{"title": "사용 가능한 에이전트", "items": items}],
            "note": f"설치되지 않았거나 이 제품군에 해당하지 않아 숨긴 에이전트: {', '.join(hidden)}" if hidden else None,
            "actions": [{"id": "home", "label": "내 모델 목록", "action": _action("home", user=user)}],
            "prompt": "어떤 에이전트를 실행할까요?"}


class FormError(ValueError):
    pass


def apply_values(fields, chosen):
    """Merge the user's choices into the form; validate names and select options."""
    names = {f["name"]: f for f in fields}
    errors = [f"알 수 없는 입력: {k} (가능: {', '.join(names)})" for k in chosen if k not in names]
    final = {}
    for f in fields:
        v = chosen.get(f["name"], f["default"])
        if f.get("options") and f["name"] in chosen:
            match = next((o for o in f["options"] if o.lower() == str(v).strip().lower()), None)
            if match is None:
                errors.append(f"{f.get('label', f['name'])}: '{v}' 는 선택지에 없습니다 ({', '.join(f['options'])})")
            v = match or f["default"]
        f["value"] = v
        f["changed"] = f["name"] in chosen and v != f["default"]
        final[f["name"]] = v
    return final, errors


def view_agent(conn, ref, skill, user, chosen=None):
    m = store.resolve_model(conn, ref)
    sk = reg.load_agents().get(skill)
    if not m or not sk:
        return {"view": "error", "title": f"모델 또는 스킬을 찾을 수 없습니다: {ref} / {skill}", "sections": [],
                "actions": [{"id": "home", "label": "내 모델로", "action": _action("home", user=user)}]}
    ctx = _ctx(m, store.get_agent(conn, m["id"], skill))
    fields = []
    for i in sk.get("inputs", []):
        fld = {k: i[k] for k in ("name", "label", "type", "options") if k in i}
        fld["default"] = reg.fill(str(i.get("default", "")), ctx)
        fields.append(fld)
    values, errors = apply_values(fields, chosen or {})
    prompt = reg.fill(sk["prompt"], {**ctx, **values})
    invoke = {k: reg.for_runner(k, prompt, skill) for k in reg.SKILL_SYNTAX}
    key = m["key"]
    base = _action("agent", key, skill, user=user)
    return {"view": "agent", "user": user, "title": f"{sk.get('icon', '')} {sk['label']} — {m['model_code']}".strip(),
            "subtitle": sk.get("description", "")[:200],
            "form": {"fields": fields, "values": values, "submitted": chosen is not None, "errors": errors,
                     "prompt_template": sk["prompt"],
                     "context": {k: v for k, v in ctx.items() if isinstance(v, (str, int, float))},
                     # apply choices: <command> --set name=value … (one --set per field, shell-quoted)
                     "submit": {"command": base["command"], "arg": "--set", "format": "{name}={value}"}},
            "invoke": {"skill": skill, "prompt": prompt, "by_runner": invoke},
            "after": {"record_use": f"python3 {_script('models.py')} use {key} {skill} "
                                    f"--summary \"<한 줄 결과>\"" + (f" --user {user}" if user else "")},
            "sections": [],
            "actions": [{"id": "back", "label": f"{m['model_code']} 에이전트 목록", "action": _action("model", key, user=user)},
                        {"id": "home", "label": "내 모델 목록", "action": _action("home", user=user)}],
            "prompt": "이대로 실행할까요? (기본값을 쓰거나 바꿀 값을 알려 주세요)"}


# ───────────── renderers ─────────────

def render_md(v):
    out = [f"## {v['title']}"]
    for e in (v.get("form") or {}).get("errors", []):
        out.append(f"⚠ {e}")
    if v.get("subtitle"):
        out.append(f"_{v['subtitle']}_")
    if v.get("card"):
        out.append("")
        out.append("| | |\n|---|---|")
        out += [f"| {c['label']} | {('[' + c['value'] + '](' + c['link'] + ')') if c.get('link') else c['value']} |"
                for c in v["card"]]
    n = 0
    for sec in v.get("sections", []):
        out.append(f"\n**{sec['title']}**\n")
        if v["view"] == "model":
            out.append("| # | 에이전트 | 내 사용 | 팀 | 담당 | 상태 |\n|---|---|---|---|---|---|")
            for it in sec["items"]:
                n += 1
                b = [x["text"] for x in it["badges"]]
                out.append(f"| {n} | {it['title']} `/{it['id']}` | {b[0]} | {b[1]} | {b[2]} | {b[3]} |")
        else:
            for it in sec["items"]:
                n += 1
                sub = f" ({it['subtitle']})" if it.get("subtitle") else ""
                out.append(f"{n}. **{it['title']}**{sub} — " + " · ".join(x["text"] for x in it["badges"]))
    form = v.get("form")
    if form:
        out.append("\n| 입력 | 값 | 선택지 |\n|---|---|---|")
        for f in form["fields"]:
            val = (f["value"] or "—") + (" ✏️" if f.get("changed") else "")
            out.append(f"| {f.get('label', f['name'])} (`{f['name']}`) | {val} | "
                       f"{', '.join(f.get('options', [])) or '자유 입력'} |")
        out.append("\n**실행 요청** (러너별)")
        out += [f"- {k}: `{p}`" for k, p in v["invoke"]["by_runner"].items()]
        out.append(f"\n실행 후 기록: `{v['after']['record_use']}`")
    if v.get("note"):
        out.append(f"\n> {v['note']}")
    if v.get("empty"):
        out.append(f"\n{v['empty']}")
    acts = [a["label"] for a in v.get("actions", [])]
    if acts:
        out.append("\n" + " · ".join(f"[{'abcdefgh'[i]}] {a}" for i, a in enumerate(acts)))
    if form:
        out.append("\n**이대로 실행할까요?** '실행'이라고 답하거나 바꿀 값을 알려 주세요 (예: `level=full`)"
                   + (" 또는 문자로 이동." if acts else "."))
    elif v.get("prompt"):
        hint = f"번호(1–{n})" if n else ""
        out.append(f"\n**{v['prompt']}** {hint}{' 또는 ' if hint and acts else ''}{'문자' if acts else ''}로 답해 주세요.")
    return "\n".join(out)


def _paged(options, more_label, page_ref):
    """Split options into pages of ≤4: 3 + a 'more' option pointing to the next page (last page may hold 4).
    Returns [page1, page2, …]; page_ref(i) gives what the 'more' option of page i links to."""
    pages, rest, i = [], list(options), 0
    while rest:
        if len(rest) <= MAX_OPTS:
            pages.append(rest)
            break
        i += 1
        pages.append(rest[:PAGE] + [{"label": more_label(len(rest) - PAGE), "description": "다음 선택지",
                                     **page_ref(i)}])
        rest = rest[PAGE:]
    return pages


def _field_questions(f):
    """One question per field; select fields with > 4 options become a chain of follow-up questions."""
    hdr = (f.get("label") or f["name"])[:12]
    q = {"id": f["name"], "field": f["name"], "question": f"{f.get('label', f['name'])}?", "header": hdr,
         "multiSelect": False}
    if f.get("options"):
        opts = sorted(f["options"], key=lambda o: o != f["value"])          # current value first
        opts = [{"label": o[:40], "description": "현재 값" if o == f["value"] else "", "value": o} for o in opts]
        pages = _paged(opts, lambda k: f"다른 값 보기 ({k})", lambda i: {"followup": f"{f['name']}#{i + 1}"})
        if len(pages[0]) < 2:
            pages[0].append({"label": "그대로", "description": "현재 값 유지", "value": f["value"]})
        first = {**q, "options": pages[0]}
        follow = {f"{f['name']}#{i + 1}": {**q, "id": f"{f['name']}#{i + 1}",
                                          "question": f"{f.get('label', f['name'])}? (계속)", "options": p}
                  for i, p in enumerate(pages[1:], start=1)}
        for fq in follow.values():                                          # a follow-up needs ≥ 2 options
            if len(fq["options"]) < 2:
                fq["options"].append({"label": "처음 선택지로", "description": "", "followup": f["name"]})
        return first, follow
    cur = f["value"]
    opts = [{"label": "현재 값 사용" if cur else "비워 두기", "description": cur or "이 값 없이 실행", "value": cur},
            {"label": "직접 입력", "description": "'기타(Other)'에 값을 입력하세요", "value": None}]
    if cur:
        opts.insert(1, {"label": "비워 두기", "description": "이 값 없이 실행", "value": ""})
    return {**q, "options": opts}, {}


def render_choices(v, page=1):
    """AskUserQuestion-shaped output. Every question has 2–4 options; nothing is cut off:
    lists are paged ('더 보기' → next page command), forms are split into rounds of ≤4 questions and
    select fields with > 4 options continue in follow-up questions. Option `value` is what to pass back."""
    form = v.get("form")
    if form and form["submitted"] and not form["errors"]:
        vals = form["values"]
        return {"view": "confirm",
                "context": {"title": v["title"], "values": vals, "changed": [f["name"] for f in form["fields"] if f["changed"]]},
                "questions": [{"id": "confirm", "question": "이 값으로 실행할까요?", "header": "실행 확인", "multiSelect": False,
                               "options": [{"label": "실행", "description": v["invoke"]["prompt"][:120], "run": True},
                                           {"label": "값 다시 고르기", "description": "선택 질문으로 돌아갑니다",
                                            "action": {"command": form["submit"]["command"] + " --format choices"}},
                                           {"label": "취소", "description": "에이전트 목록으로",
                                            "action": v["actions"][0]["action"]}]}],
                "invoke": v["invoke"], "after": v["after"]}
    if form:
        qs, follow = [], {}
        for f in form["fields"]:
            q, fu = _field_questions(f)
            qs.append(q)
            follow.update(fu)
        return {"view": "form", "context": {"title": v["title"], "errors": form["errors"]},
                "rounds": [qs[i:i + MAX_QS] for i in range(0, len(qs), MAX_QS)],
                "followups": follow,
                "submit": form["submit"],
                "how": "Ask each round in order (one tool call per round). If the picked option has `followup`, ask "
                       "followups[that id] next. Answer value = option `value` (null or 'Other' → the typed text). "
                       "Then run submit.command with one `--set name=value` per field and `--format choices`."}
    items = [it for sec in v.get("sections", []) for it in sec["items"]]
    opts = [{"label": it["title"][:40], "description": " · ".join(b["text"] for b in it["badges"])[:120],
             "action": it["action"]} for it in items]
    opts += [{"label": a["label"][:40], "description": "", "action": a["action"]} for a in v.get("actions", [])]
    self_cmd = (v.get("_self_action") or {}).get("command", "")
    pages = _paged(opts, lambda k: f"더 보기 ({k})",
                   lambda i: {"action": {"command": f"{self_cmd} --format choices --page {i + 1}"}})
    cur = pages[min(page, len(pages)) - 1] if pages else []
    while len(cur) < 2:
        cur = cur + [{"label": "닫기", "description": "메뉴를 닫습니다", "action": None}]
    return {"view": v["view"], "page": min(page, len(pages)) if pages else 1, "pages": len(pages),
            "questions": [{"id": v["view"], "question": v.get("prompt") or v["title"], "header": v["view"][:12],
                           "options": cur, "multiSelect": False}],
            "context": {"title": v["title"], "subtitle": v.get("subtitle"), "card": v.get("card"), "empty": v.get("empty")}}


def build(conn, view, args, user, chosen=None):
    if view == "home":
        v = view_home(conn, user)
        v["_self_action"] = _action("home", user=user)
    elif view == "models":
        v = view_home(conn, user, everyone=True)
        v["_self_action"] = _action("models", user=user)
    elif view == "model":
        v = view_model(conn, args[0], user)
        v["_self_action"] = _action("model", args[0], user=user)
    elif view == "agent":
        v = view_agent(conn, args[0], args[1], user, chosen)
    else:
        raise SystemExit(f"unknown view: {view}")
    return v


def parse_sets(pairs):
    out = {}
    for p in pairs or []:
        if "=" not in p:
            raise SystemExit(f"--set expects name=value, got: {p}")
        k, val = p.split("=", 1)
        out[k.strip()] = val.strip()
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("view", choices=["home", "models", "model", "agent"])
    ap.add_argument("args", nargs="*")
    ap.add_argument("--user")
    ap.add_argument("--format", choices=["md", "choices", "json"], default="md")
    ap.add_argument("--page", type=int, default=1)
    ap.add_argument("--set", action="append", metavar="NAME=VALUE", help="agent view: apply a chosen input value")
    a = ap.parse_args()
    need = {"model": 1, "agent": 2}.get(a.view, 0)
    if len(a.args) < need:
        ap.error(f"'{a.view}' needs {need} argument(s)")
    if a.set and a.view != "agent":
        ap.error("--set only applies to the agent view")
    user = a.user or store.resolve_username()
    v = build(store.connect(), a.view, a.args, user, parse_sets(a.set) if a.set else None)
    if a.format == "json":
        v.pop("_self_action", None)
        print(json.dumps(v, ensure_ascii=False, indent=2))
    elif a.format == "choices":
        print(json.dumps(render_choices(v, a.page), ensure_ascii=False, indent=2))
    else:
        print(render_md(v))
    if v.get("form", {}).get("errors"):
        sys.exit(1)            # invalid choices: the output explains them, exit code tells the host


if __name__ == "__main__":
    main()
