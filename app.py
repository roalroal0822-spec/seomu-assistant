"""서무비서 웹 서버."""
import base64
import shutil
import tempfile
import time
import uuid
from datetime import date
from pathlib import Path

from core import regparse, webcommon
from core.config import Config
from core.export import export
from core.llm import LLM
from core.loaders import SUPPORTED, load_bytes
from core.server import App, HttpError, file_response
from core.store import Store
from core.textutil import similarity

import assistant
import forms as formlib
from knowledge import LAYERS, Knowledge, deadline_label

VERSION = "1.0.0"
HERE = Path(__file__).resolve().parent
SAMPLE = HERE / "sample_data"

cfg = Config(HERE, {"app_title": "서무비서", "port": 8102})
llm = LLM(cfg["llm"])
store = Store(cfg.data_dir)
KB_ROOT = store.path("kb")
app = App(HERE / "static")
webcommon.register(app, cfg, llm, "서무비서", VERSION)


@app.get("/api/today")
def get_today(req):
    return {"today": today().isoformat()}

STATE = {"kb": None, "cards": []}


def _now():
    return time.strftime("%Y-%m-%d %H:%M:%S")


def log(action, detail=""):
    store.append_log("history.jsonl", {"action": action, "detail": detail})


def today():
    t = cfg.get("today")  # 시연용 기준일 고정(설정 파일)
    return date.fromisoformat(t) if t else date.today()


# ------------------------------------------------------------ 지식 불러오기·절차 갱신

def kb_empty():
    return not any(p.is_file() for p in KB_ROOT.rglob("*")) if KB_ROOT.exists() else True


def load_sample():
    for layer in LAYERS:
        src = SAMPLE / "layers" / layer
        if src.exists():
            shutil.copytree(src, KB_ROOT / layer, dirs_exist_ok=True)
    log("시연용 모의자료 불러오기", str(SAMPLE))


def rebuild(reason=""):
    kb = Knowledge(KB_ROOT).load()
    fresh = kb.extract_procedures()
    saved = store.read("procedures.json", {"cards": []})["cards"]
    by_id = {c["id"]: c for c in saved}
    cards = []
    for c in fresh:
        old = by_id.pop(c["id"], None)
        if old and old.get("edited"):
            # 관리자가 고친 카드는 유지하되, 규정 내용이 바뀌었으면 검토 표시
            if _steps_sig(old.get("source_steps", [])) != _steps_sig(c["steps"]):
                old["needs_review"] = True
                old["source_steps"] = c["steps"]
            cards.append(old)
        else:
            c["source_steps"] = c["steps"]
            cards.append(c)
    for c in by_id.values():  # 직접 만든 카드
        if c.get("manual"):
            cards.append(c)
    store.write("procedures.json", {"cards": cards, "built_at": _now()})
    STATE["kb"], STATE["cards"] = kb, cards
    if reason:
        log("절차 갱신", reason)
    return cards


def _steps_sig(steps):
    return [(s.get("title"), deadline_label(s.get("deadline")), tuple(s.get("docs", [])),
             tuple(f["no"] for f in s.get("forms", []))) for s in steps]


def kb():
    if STATE["kb"] is None:
        if kb_empty():
            load_sample()
        rebuild()
    return STATE["kb"]


def check_role(layer, role):
    if layer in ("common", "org") and role != "admin":
        raise HttpError(403, "공통 기준과 기관 규정은 관리자만 수정할 수 있습니다. 개인 보충은 누구나 등록할 수 있습니다.")


# ------------------------------------------------------------ 규정·서식 관리

@app.get("/api/kb")
def get_kb(req):
    k = kb()
    layers = {}
    for layer, label in LAYERS.items():
        files = []
        for p in sorted((KB_ROOT / layer).rglob("*")):
            if p.is_file():
                rel = p.relative_to(KB_ROOT / layer).as_posix()
                kind = "서식" if any(f["rel"] == rel and f["layer"] == layer for f in k.forms) else \
                       "표" if any(t["rel"] == rel and t["layer"] == layer for t in k.tables) else \
                       "규정" if any(r["rel"] == rel and r["layer"] == layer for r in k.regs) else \
                       "메모" if layer == "personal" else "기타"
                files.append({"rel": rel, "size": p.stat().st_size, "kind": kind,
                              "updated": time.strftime("%Y-%m-%d %H:%M", time.localtime(p.stat().st_mtime))})
        layers[layer] = {"label": label, "files": files}
    return {"layers": layers, "forms": k.forms, "tips": k.tips,
            "regs": [{"id": r["id"], "layer": r["layer"], "rel": r["rel"], "title": r["title"], "articles": len(r["reg"].articles)} for r in k.regs]}


@app.post("/api/kb/sample")
def kb_sample(req):
    load_sample()
    rebuild("모의자료 불러오기")
    return {"ok": True}


@app.post("/api/kb/<layer>/upload")
def kb_upload(req):
    layer = req.params["layer"]
    if layer not in LAYERS:
        raise HttpError(400, "잘못된 층")
    body = req.json
    check_role(layer, body.get("role"))
    sub = body.get("folder", "")
    saved = []
    for f in body.get("files", []):
        name = Path(f["path"].replace("\\", "/")).name
        if Path(name).suffix.lower() not in SUPPORTED:
            continue
        target = KB_ROOT / layer / sub / name
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists():
            _backup(target)
        target.write_bytes(base64.b64decode(f["b64"]))
        saved.append(name)
    rebuild(f"{LAYERS[layer]}에 {len(saved)}개 등록: {', '.join(saved)}")
    return {"saved": saved}


@app.post("/api/kb/<layer>/delete")
def kb_delete(req):
    layer = req.params["layer"]
    body = req.json
    check_role(layer, body.get("role"))
    target = (KB_ROOT / layer / body.get("rel", "")).resolve()
    if (KB_ROOT / layer).resolve() not in target.parents or not target.is_file():
        raise HttpError(404, "파일이 없습니다")
    _backup(target)
    target.unlink()
    rebuild(f"{LAYERS[layer]}에서 삭제: {body.get('rel')}")
    return {"ok": True}


def _backup(path):
    dst = store.path(f"versions/{time.strftime('%Y%m%d-%H%M%S')}/{path.relative_to(KB_ROOT).as_posix()}")
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(path, dst)


@app.get("/api/kb/file")
def kb_file(req):
    k = kb()
    layer, rel = req.query.get("layer"), req.query.get("rel")
    for d in k.docs.get(layer, []):
        if d.relpath == rel:
            return {"rel": rel, "blocks": [{"loc": b.loc, "text": b.text} for b in d.blocks]}
    raise HttpError(404, "파일 없음")


@app.get("/api/kb/form/<fid>")
def form_download(req):
    k = kb()
    for f in k.forms:
        if f["id"] == req.params["fid"]:
            p = KB_ROOT / f["layer"] / f["rel"]
            return file_response(p.read_bytes(), Path(f["rel"]).name)
    raise HttpError(404, "서식 없음")


# ------------------------------------------------------------ 절차 카드

@app.get("/api/procedures")
def procedures(req):
    kb()
    k = STATE["kb"]
    out = []
    for c in STATE["cards"]:
        cc = dict(c)
        cc["tips"] = k.tips_for(c)
        cc["overrides"] = k.common_overrides(c)
        for s in cc["steps"]:
            s["deadline_label"] = deadline_label(s.get("deadline"))
            for f in s.get("forms", []):
                ff = k.form_by_no(f["no"])
                f["exists"] = ff is not None
                f["form_id"] = ff["id"] if ff else None
        out.append(cc)
    return {"cards": out}


@app.post("/api/procedures/save")
def save_card(req):
    kb()
    body = req.json
    if body.get("role") != "admin":
        raise HttpError(403, "절차 카드는 관리자만 고칠 수 있습니다")
    card = body["card"]
    cards = STATE["cards"]
    for i, c in enumerate(cards):
        if c["id"] == card.get("id"):
            card["edited"] = True
            card["needs_review"] = False
            card.setdefault("source_steps", c.get("source_steps", []))
            cards[i] = card
            break
    else:
        card.update(id="m-" + uuid.uuid4().hex[:6], manual=True, edited=True, layer="org",
                    regulation=card.get("regulation") or "직접 등록", source_steps=[])
        card.setdefault("keywords", [])
        card.setdefault("references", [])
        cards.append(card)
    for s in card.get("steps", []):
        s.setdefault("docs", [])
        s.setdefault("forms", [])
        s.setdefault("notes", [])
        if s.get("deadline_text") is not None:
            from knowledge import parse_deadline
            s["deadline"] = parse_deadline(s["deadline_text"]) or ({"kind": "text", "text": s["deadline_text"], "days": 0, "anchor": ""} if s["deadline_text"] else None)
            s.pop("deadline_text")
        s.setdefault("basis", {"reg": card.get("regulation", ""), "label": "직접 입력", "text": "", "key": ""})
    store.write("procedures.json", {"cards": cards, "built_at": _now()})
    log("절차 카드 수정", card["name"])
    return card


@app.post("/api/procedures/delete")
def delete_card(req):
    body = req.json
    if body.get("role") != "admin":
        raise HttpError(403, "관리자만 지울 수 있습니다")
    STATE["cards"] = [c for c in STATE["cards"] if c["id"] != body.get("id")]
    store.write("procedures.json", {"cards": STATE["cards"], "built_at": _now()})
    log("절차 카드 삭제", body.get("id", ""))
    return {"ok": True}


@app.post("/api/procedures/rebuild")
def rebuild_api(req):
    kb()
    return {"cards": len(rebuild("수동 다시 읽기"))}


# ------------------------------------------------------------ 규정 개정 → 절차 갱신

@app.post("/api/revision/preview")
def revision_preview(req):
    k = kb()
    body = req.json
    if body.get("role") != "admin":
        raise HttpError(403, "규정 개정 반영은 관리자만 할 수 있습니다")
    files = body.get("files", [])
    if not files:
        raise HttpError(400, "개정본 파일을 올려 주세요")
    token = uuid.uuid4().hex[:8]
    results = []
    for f in files:
        data = base64.b64decode(f["b64"])
        doc = load_bytes(data, Path(f["path"]).name)
        if doc.error:
            results.append({"file": doc.name, "error": doc.error})
            continue
        new_reg = regparse.parse([(b.loc, b.text) for b in doc.blocks])
        best, score = None, 0
        for r in k.regs:
            if r["layer"] != "org":
                continue
            sc = similarity(r["title"], new_reg.title)
            if sc > score:
                best, score = r, sc
        if not best or score < 0.5:
            results.append({"file": doc.name, "error": "기존 규정 중 같은 규정을 찾지 못했습니다(새 규정이면 규정·서식 관리에서 등록하세요)"})
            continue
        rows = regparse.compare(best["reg"], new_reg)
        # 바뀐 규정으로 절차를 다시 뽑아 단계별 차이 계산
        with tempfile.TemporaryDirectory() as tmp:
            shutil.copytree(KB_ROOT, tmp, dirs_exist_ok=True)
            tgt = Path(tmp) / "org" / best["rel"]
            tgt.unlink()
            tgt = tgt.with_name(Path(best["rel"]).stem + Path(doc.name).suffix)
            tgt.write_bytes(data)
            new_cards = Knowledge(tmp).load().extract_procedures()
        old_cards = [c for c in STATE["cards"] if c.get("reg_id") == best["id"]]
        changes = _proc_changes(old_cards, [c for c in new_cards if c["regulation"] == new_reg.title or c["rel"].startswith(Path(best["rel"]).stem)])
        pend = store.path(f"pending/{token}/{doc.name}")
        pend.parent.mkdir(parents=True, exist_ok=True)
        pend.write_bytes(data)
        results.append({"file": doc.name, "target": best["rel"], "target_title": best["title"], "rows": rows, "changes": changes,
                        "edited_cards": [c["name"] for c in old_cards if c.get("edited")]})
    store.write(f"pending/{token}/meta.json", results)
    return {"token": token, "results": results}


@app.get("/api/revision/sample")
def revision_sample(req):
    p = next((SAMPLE / "revision").glob("*"), None)
    if not p:
        raise HttpError(404, "시연용 개정본이 없습니다")
    return {"path": p.name, "b64": base64.b64encode(p.read_bytes()).decode()}


def _proc_changes(old_cards, new_cards):
    out = []
    old_steps = {(s["basis"].get("key"), s["title"]): s for c in old_cards for s in c.get("source_steps", c["steps"])}
    new_steps = {(s["basis"].get("key"), s["title"]): s for c in new_cards for s in c["steps"]}
    for key, ns in new_steps.items():
        os_ = old_steps.get(key)
        if not os_:
            out.append({"step": ns["title"], "field": "단계", "old": "-", "new": "새 단계"})
            continue
        for field, fn in (("기한", lambda s: deadline_label(s.get("deadline"))), ("필요 서류", lambda s: ", ".join(s.get("docs", []))),
                          ("서식", lambda s: ", ".join(f"별지 {f['no']}호" for f in s.get("forms", [])))):
            a, b = fn(os_), fn(ns)
            if a != b:
                out.append({"step": ns["title"], "field": field, "old": a or "-", "new": b or "-"})
    for key, os_ in old_steps.items():
        if key not in new_steps:
            out.append({"step": os_["title"], "field": "단계", "old": "있음", "new": "삭제됨"})
    return out


@app.post("/api/revision/apply")
def revision_apply(req):
    body = req.json
    if body.get("role") != "admin":
        raise HttpError(403, "관리자만 반영할 수 있습니다")
    token = body.get("token", "")
    meta = store.read(f"pending/{token}/meta.json")
    if not meta:
        raise HttpError(404, "미리보기 결과가 없습니다")
    applied = []
    for r in meta:
        if r.get("error"):
            continue
        old = KB_ROOT / "org" / r["target"]
        if old.exists():
            _backup(old)
            old.unlink()
        new = old.with_name(Path(r["target"]).stem + Path(r["file"]).suffix)
        shutil.copy2(store.path(f"pending/{token}/{r['file']}"), new)
        applied.append(r["target_title"])
    store.remove_tree(f"pending/{token}")
    rebuild(f"규정 개정 반영: {', '.join(applied)}")
    return {"applied": applied}


# ------------------------------------------------------------ 상황 말하기(비서)

@app.post("/api/chat")
def chat(req):
    k = kb()
    text = (req.json.get("text") or "").strip()
    if not text:
        raise HttpError(400, "상황을 입력해 주세요")
    res = assistant.respond(k, STATE["cards"], text, today(), llm)
    res["today"] = today().isoformat()
    res["mode"] = llm.label
    if res["type"] == "procedure":
        case = {"id": time.strftime("%Y%m%d%H%M%S") + uuid.uuid4().hex[:3], "created": _now(), "text": text,
                "card_id": res["card"]["id"], "card_name": res["card"]["name"], "slots": res["slots"],
                "steps": [{"title": s["title"], "due": s["due"], "deadline": s["deadline_label"],
                           "done": s["state"] == "past", "skipped": s["state"] == "past"} for s in res["steps"]],
                "documents": [], "closed": False}
        cases = store.read("cases.json", [])
        cases.insert(0, case)
        store.write("cases.json", cases)
        res["case_id"] = case["id"]
    log("상황 문의", f"{text} → {res.get('card', {}).get('name', res['type'])}")
    return res


@app.get("/api/cases")
def cases(req):
    cs = store.read("cases.json", [])
    t = today()
    for c in cs:
        nxt = next((s for s in c["steps"] if not s["done"]), None)
        c["next"] = nxt
        c["d_day"] = (date.fromisoformat(nxt["due"]) - t).days if nxt and nxt.get("due") else None
    return {"cases": cs, "today": t.isoformat()}


@app.post("/api/cases/<cid>/step")
def case_step(req):
    cs = store.read("cases.json", [])
    body = req.json
    for c in cs:
        if c["id"] == req.params["cid"]:
            c["steps"][int(body["index"])]["done"] = bool(body.get("done", True))
            c["closed"] = all(s["done"] for s in c["steps"])
            store.write("cases.json", cs)
            log("처리 단계 " + ("완료" if body.get("done", True) else "취소"), f"{c['card_name']} - {c['steps'][int(body['index'])]['title']}")
            return c
    raise HttpError(404, "처리 건 없음")


@app.post("/api/cases/<cid>/delete")
def case_delete(req):
    cs = [c for c in store.read("cases.json", []) if c["id"] != req.params["cid"]]
    store.write("cases.json", cs)
    return {"ok": True}


@app.get("/api/cases/export")
def cases_export(req):
    cs = store.read("cases.json", [])
    rows = []
    for c in cs:
        for s in c["steps"]:
            rows.append([c["created"], c["card_name"], c["text"], s["title"], s.get("due") or "", "완료" if s["done"] else "진행 중"])
    data, ctype = export([("title", "서무 처리 이력"), ("table", ["접수", "업무", "상황", "단계", "기한", "상태"], rows)], req.query.get("fmt", "hwpx"))
    return file_response(data, f"서무처리이력.{req.query.get('fmt', 'hwpx')}", ctype)


@app.get("/api/history")
def history(req):
    return {"history": store.read_log("history.jsonl")[::-1][:300]}


# ------------------------------------------------------------ 서식 준비

@app.get("/api/profile")
def get_profile(req):
    return store.read("profile.json", {"성명": "", "소속": "", "직급": "", "연락처": ""})


@app.post("/api/profile")
def set_profile(req):
    p = {k: req.json.get(k, "") for k in ("성명", "소속", "직급", "연락처")}
    store.write("profile.json", p)
    return p


@app.post("/api/forms/prefill")
def form_prefill(req):
    k = kb()
    body = req.json
    form = next((f for f in k.forms if f["id"] == body.get("form_id")), None)
    if not form:
        raise HttpError(404, "서식 파일이 등록되어 있지 않습니다")
    slots = body.get("slots") or {}
    cost = assistant.travel_cost(k, slots) if any(x in form["fields"] for x in ("일비", "합계")) else None
    vals = formlib.prefill(form, store.read("profile.json", {}), slots, cost, body.get("text", ""), today())
    return {"form": form, "values": vals, "cost": cost}


@app.post("/api/forms/fill")
def form_fill(req):
    k = kb()
    body = req.json
    form = next((f for f in k.forms if f["id"] == body.get("form_id")), None)
    if not form:
        raise HttpError(404, "서식 없음")
    data = (KB_ROOT / form["layer"] / form["rel"]).read_bytes()
    filled, left = formlib.fill_template(data, body.get("values", {}))
    name = f"{form['name']}_{time.strftime('%Y%m%d')}{form['ext']}"
    out = store.path(f"outputs/{name}")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(filled)
    if body.get("case_id"):
        cs = store.read("cases.json", [])
        for c in cs:
            if c["id"] == body["case_id"]:
                c["documents"].append({"name": name, "at": _now()})
        store.write("cases.json", cs)
    log("서식 초안 작성", name + (f" (빈 항목: {', '.join(left)})" if left else ""))
    return file_response(filled, name)


@app.post("/api/guide/export")
def guide_export(req):
    """안내 결과를 문서로 저장 (업무 처리 안내서)."""
    r = req.json
    blocks = [("title", f"업무 처리 안내: {r['card']['name']}"), ("p", f"상황: {r.get('question', '')}"),
              ("p", f"근거 규정: {r['card']['regulation']} · 기준일: {r.get('today', '')}"),
              ("table", ["단계", "할 일", "기한", "날짜", "필요 서류·서식", "근거"],
               [[s["title"], s["action"], s.get("deadline_label", ""), s.get("due") or "",
                 ", ".join(s.get("docs", []) + [f"별지 {f['no']}호 {f['name']}" for f in s.get("forms", [])]),
                 f"{s['basis'].get('reg', '')} {s['basis'].get('label', '')}"] for s in r["steps"]], [2, 4, 2, 1, 3, 2])]
    if r.get("approval") and r["approval"].get("line"):
        blocks.append(("p", "결재선: " + " → ".join(r["approval"]["line"])))
    if r.get("cost"):
        c = r["cost"]
        blocks.append(("table", ["항목", "금액(원)"], [[k, f"{c[k]:,}"] for k in ("운임", "일비", "식비", "숙박비", "합계")]))
    if r.get("tips"):
        blocks += [("h2", "개인 보충 메모")] + [("bullet", t["text"]) for t in r["tips"]]
    data, ctype = export(blocks, req.query.get("fmt", "hwpx"))
    return file_response(data, f"업무처리안내_{r['card']['name']}.{req.query.get('fmt', 'hwpx')}", ctype)


def main():
    import argparse
    ap = argparse.ArgumentParser(description="서무비서 실행")
    ap.add_argument("--port", type=int, default=cfg["port"])
    ap.add_argument("--host", default=cfg["host"])
    ap.add_argument("--no-browser", action="store_true")
    a = ap.parse_args()
    kb()
    app.run(a.host, a.port, not a.no_browser, "서무비서")


if __name__ == "__main__":
    main()
