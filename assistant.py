"""상황 말하기 → 절차 찾기 → 할 일·기한 안내 → 서식·결재선·여비 준비."""
import re
from datetime import date, timedelta

from core.search import BM25
from core.textutil import parse_relative_date, RE_FULL_DATE, RE_MD

from knowledge import SYN, deadline_label

AFTER_CUES = re.compile(r"다녀왔|다녀와서|갔다\s*왔|끝났|끝나고|마쳤|마치고|복귀|납품\s*(됐|되었|받)|받았|했어요|했는데|후에|지났")
BEFORE_CUES = re.compile(r"갈\s|가려|갈\s*예정|예정|하려고|하려는|할\s*거|계획|신청하려|다음\s*주|내일|모레|쓰려|사려|열려고|개최")

REGION_METRO = ("부산", "대구", "인천", "광주", "대전", "울산", "세종")


# ------------------------------------------------------------ 상황에서 정보 뽑기

def extract_slots(text, today):
    s = {}
    dates = []
    for m in RE_FULL_DATE.finditer(text):
        try:
            dates.append(date(int(m.group(1)), int(m.group(2)), int(m.group(3))))
        except ValueError:
            pass
    if not dates:
        for m in RE_MD.finditer(text):
            try:
                d = date(today.year, int(m.group(1)), int(m.group(2)))
                if d > today + timedelta(days=200):
                    d = date(today.year - 1, d.month, d.day)
                dates.append(d)
            except ValueError:
                pass
    if not dates:
        d = parse_relative_date(text, today)
        if d:
            dates.append(d)
    m = re.search(r"(\d+)\s*박\s*(\d+)\s*일", text)
    if m:
        s["nights"], s["days"] = int(m.group(1)), int(m.group(2))
    else:
        m = re.search(r"(\d+)\s*일\s*(?:동안|간|짜리)", text) or re.search(r"(?:연가|휴가|병가)\s*(\d+)\s*일", text) \
            or re.search(r"(\d+)\s*일\s*(?:연가|휴가|병가|출장)", text)
        if m:
            s["days"] = int(m.group(1))
    m = re.search(r"당일", text)
    if m and "days" not in s:
        s["days"], s["nights"] = 1, 0
    if dates:
        s["date"] = dates[0].isoformat()
        if len(dates) > 1:
            s["end_date"] = dates[-1].isoformat()
            s.setdefault("days", (dates[-1] - dates[0]).days + 1)
    m = re.search(r"([가-힣]{2,10}?)(?:으로|로|에)\s*(?:출장|다녀|갔|가)", text)
    if not m:
        m = re.search(r"([가-힣]{2,6}?)(?:에서|으로|로)\s", text)
    if m and m.group(1) not in ("출장", "어제", "오늘", "내일", "그제", "지난주", "다음주", "회사", "사무실", "근무지"):
        s["destination"] = m.group(1)
    m = re.search(r"([가-힣\s]{2,20}?)\s*(회의|교육|점검|행사|설명회|워크숍|협의)(?:\s*때문|\s*참석|\s*하러|\s*으로|\s*로|\s*가|\s*에)", text)
    if m:
        s["purpose"] = (m.group(1).strip().split()[-1] + " " + m.group(2)).strip()
    m = re.search(r"(\d[\d,]*)\s*(만|천만|억)?\s*원", text)
    if m:
        n = int(m.group(1).replace(",", ""))
        n *= {"만": 10_000, "천만": 10_000_000, "억": 100_000_000}.get(m.group(2) or "", 1)
        s["amount"] = n
    m = re.search(r"(KTX|기차|버스|자가용|비행기|택시|SRT)", text, re.I)
    if m:
        s["transport"] = m.group(1)
    m = re.search(r"(?:운임|교통비|차비|기차표|KTX)\D{0,6}(\d[\d,]*)\s*원", text)
    if m:
        s["fare"] = int(m.group(1).replace(",", ""))
    if AFTER_CUES.search(text):
        s["stage"] = "after"
    elif BEFORE_CUES.search(text):
        s["stage"] = "before"
    return s


def _expand(q):
    extra = []
    for k, words in SYN.items():
        if any(w in q for w in words):
            extra += [k] * 2 + words[:3]
    return q + " " + " ".join(extra)


# ------------------------------------------------------------ 기한 계산

def compute_due(dl, slots, today):
    """기한(날짜)과 설명을 돌려준다. 기준일이 없으면 오늘을 기준으로 계산하고 그 사실을 알린다."""
    if not dl:
        return None, ""
    k = dl["kind"]
    base_txt = ""
    if k == "before":
        if slots.get("stage") == "after":
            return None, "이미 지난 단계(사전 절차)"
        if slots.get("date"):
            base = date.fromisoformat(slots["date"])
        else:
            return None, "시작일을 알려주면 기한을 계산합니다"
        return base - timedelta(days=dl["days"]), f"{base.isoformat()} 기준"
    if k == "after":
        if slots.get("end_date"):
            base = date.fromisoformat(slots["end_date"])
        elif slots.get("date") and (slots.get("stage") == "after" or dl.get("event") == "end"):
            base = date.fromisoformat(slots["date"])
            if slots.get("days") and slots.get("stage") != "after":
                base = base + timedelta(days=slots["days"] - 1)
        elif slots.get("stage") == "after":
            base = today
            base_txt = "오늘 기준(마친 날을 알려주면 다시 계산)"
        else:
            return None, "해당 시점부터 계산"
        return base + timedelta(days=dl["days"]), base_txt or f"{base.isoformat()} 기준"
    if k == "sameday":
        return (date.fromisoformat(slots["date"]) if slots.get("date") else today), f"{dl['hour']}시까지"
    if k == "now":
        if slots.get("stage") == "before":
            return None, "해당 시 지체 없이"
        return today, "지체 없이"
    if k == "yearly":
        y = today.year if today.month <= dl["month"] else today.year + 1
        return date(y, dl["month"], 1), f"매년 {dl['month']}월"
    if k == "monthly":
        d = date(today.year, today.month, min(dl["day"], 28))
        if d < today:
            d = date(today.year + (today.month == 12), (today.month % 12) + 1, min(dl["day"], 28))
        return d, f"매월 {dl['day']}일"
    return None, ""


# ------------------------------------------------------------ 결재선

def approval_line(kb, card, slots):
    t = kb.table_find(r"위임전결|전결")
    if not t or not card.get("category"):
        return None
    header = t["table"]["header"]
    idx = {h: i for i, h in enumerate(header)}
    rows = [r for r in t["table"]["rows"] if r and card["category"] in (r[idx.get("업무", 0)] or "")]
    if card["category"] == "출장" and not rows:
        rows = [r for r in t["table"]["rows"] if r and "출장" in r[0]]
    if not rows:
        return None
    chosen, why = None, ""
    days, amount = slots.get("days"), slots.get("amount")
    assumed = ""
    if days is None and card["category"] == "휴가" and slots.get("date"):
        days, assumed = 1, "기간을 말하지 않아 1일로 가정"
    dest = slots.get("destination", "")
    for r in rows:
        detail = r[idx.get("세부 내용", 1)] or ""
        ok = True
        m = re.search(r"(\d+)일\s*(이내|초과)", detail)
        if m:
            if days is None:
                ok = False
            else:
                ok = days <= int(m.group(1)) if m.group(2) == "이내" else days > int(m.group(1))
        m = re.findall(r"(\d+)(천만|만|억)?원\s*(이하|초과)", detail)
        for num, unit, cmp in m:
            v = int(num) * {"만": 10_000, "천만": 10_000_000, "억": 100_000_000}.get(unit, 1)
            if amount is None:
                ok = False
            else:
                ok = ok and (amount <= v if cmp == "이하" else amount > v)
        if "관내" in detail and dest and dest not in ("관내", "구청", "가람"):
            ok = False
        if "관외" in detail and not dest:
            ok = ok and days is not None
        if ok:
            chosen, why = r, detail
            break
    cand = [{"detail": r[1], "draft": r[2], "review": r[3], "final": r[4]} for r in rows]
    if not chosen:
        return {"line": None, "candidates": cand, "note": "기간·금액을 알려주면 결재선을 정할 수 있습니다", "source": t["rel"]}
    line = [chosen[idx.get("기안", 2)]] + [x.strip() for x in (chosen[idx.get("검토", 3)] or "").split(",") if x.strip()] + [chosen[idx.get("전결권자", 4)]]
    return {"line": [x for x in line if x], "basis": why, "candidates": cand, "source": t["rel"], "note": assumed}


# ------------------------------------------------------------ 여비 계산

def travel_cost(kb, slots):
    t = kb.table_find(r"여비")
    if not t:
        return None
    rates = {}
    for r in t["table"]["rows"]:
        if len(r) >= 3 and str(r[2]).replace(",", "").isdigit():
            rates[r[0]] = int(str(r[2]).replace(",", ""))
    days = slots.get("days") or 1
    nights = slots.get("nights", max(0, days - 1))
    dest = slots.get("destination", "")
    if "서울" in dest:
        lodge_key = "숙박비(서울특별시)"
    elif any(x in dest for x in REGION_METRO):
        lodge_key = "숙박비(광역시)"
    else:
        lodge_key = "숙박비(그 밖의 지역)"
    daily = rates.get("일비", 0) * days
    meal = rates.get("식비", 0) * days
    lodge = rates.get(lodge_key, 0) * nights
    fare = slots.get("fare", 0)
    return {"days": days, "nights": nights, "일비": daily, "식비": meal, "숙박비": lodge, "숙박기준": lodge_key,
            "운임": fare, "합계": daily + meal + lodge + fare, "source": t["rel"],
            "note": "숙박비는 상한액 기준(실비 정산), 운임은 영수증 금액"}


# ------------------------------------------------------------ 응답

LLM_SYSTEM = """당신은 공공기관 서무 담당자를 돕는 비서입니다. 사용자의 상황 문장을 읽고 후보 절차 중 하나를 고르고 필요한 정보를 뽑습니다.
후보에 없는 절차를 만들지 않습니다. 날짜는 YYYY-MM-DD 로 씁니다. 모르면 비워 둡니다.
출력 JSON: {"procedure_id":"후보 id","stage":"before 또는 after","date":"","end_date":"","days":0,"nights":0,
"destination":"","purpose":"","amount":0,"reply":"사용자에게 줄 한두 문장 안내"}"""


def respond(kb, cards, text, today=None, llm=None):
    today = today or date.today()
    slots = extract_slots(text, today)
    q = _expand(text)
    idx = BM25(cards, lambda c: " ".join([c["name"], c["regulation"], " ".join(c["keywords"]) * 2,
                                          " ".join(s["title"] + " " + s["action"] for s in c["steps"])]))
    ranked = idx.search(q, k=3)
    llm_note, llm_reply = "", ""
    if llm is not None and llm.enabled and ranked:
        cands = "\n".join(f"- id={c['id']} 이름={c['name']} 단계={', '.join(s['title'] for s in c['steps'])}" for c, _ in ranked)
        try:
            out = llm.json(LLM_SYSTEM, f"오늘: {today.isoformat()}\n후보 절차:\n{cands}\n\n상황: {text}")
            ids = {c["id"]: c for c, _ in ranked}
            if out.get("procedure_id") in ids:
                ranked = [(ids[out["procedure_id"]], 99)] + [x for x in ranked if x[0]["id"] != out["procedure_id"]]
            for k in ("stage", "date", "end_date", "destination", "purpose"):
                v = out.get(k)
                if v and (k not in ("date", "end_date") or re.match(r"\d{4}-\d{2}-\d{2}$", str(v))):
                    slots.setdefault(k, v)
            for k in ("days", "nights", "amount"):
                if isinstance(out.get(k), int) and out[k] > 0:
                    slots.setdefault(k, out[k])
            llm_reply = (out.get("reply") or "").strip()
        except Exception as e:
            llm_note = f"모델 응답 실패로 규칙 기반 결과를 보여 드립니다 ({e})"

    # 공통 기준 조문으로만 답할 수 있는 질문
    common_hit = None
    if kb.common_articles:
        cidx = BM25(kb.common_articles, lambda a: a["article"].title + " " + a["article"].plain)
        ch = cidx.search(q, k=1)
        if ch:
            common_hit = ch[0]
    if not ranked or (common_hit and ranked[0][1] < common_hit[1] * 0.6):
        if common_hit:
            a = common_hit[0]["article"]
            dl = None
            from knowledge import parse_deadline
            dl = parse_deadline(a.plain)
            due, base = compute_due(dl, slots, today)
            return {"type": "common", "text": f"기관 절차 카드에는 없고, 공통 기준에 관련 조문이 있습니다: {a.label}({a.title})",
                    "article": {"label": a.label, "title": a.title, "text": a.to_text(), "reg": common_hit[0]["reg_title"],
                                "rel": common_hit[0]["rel"]},
                    "deadline": deadline_label(dl), "due": due.isoformat() if due else None, "slots": slots}
        return {"type": "none", "text": "등록된 규정에서 해당 업무를 찾지 못했습니다. 규정·서식 관리에서 관련 규정을 등록하거나 표현을 바꿔 다시 말해 주세요.",
                "slots": slots}

    card = ranked[0][0]
    # 현재 단계: 질문과 가장 가까운 단계, 사후 상황이면 사후 단계부터
    sidx = BM25(list(range(len(card["steps"]))), lambda i: card["steps"][i]["title"] * 3 + " " + card["steps"][i]["action"])
    sh = sidx.search(q, k=1)
    focus = sh[0][0] if sh else 0
    if slots.get("stage") == "after":
        after_steps = [i for i, s in enumerate(card["steps"]) if s.get("deadline") and s["deadline"]["kind"] in ("after", "now")]
        if after_steps and focus not in after_steps:
            focus = after_steps[0]
    elif re.search(r"뭐부터|처음|어떻게\s*해|절차|순서", text) or slots.get("stage") == "before":
        for i, s in enumerate(card["steps"]):
            due, _ = compute_due(s.get("deadline"), slots, today)
            if due is None or due >= today:
                focus = i
                break
    steps = []
    for i, s in enumerate(card["steps"]):
        due, base = compute_due(s.get("deadline"), slots, today)
        state = "todo"
        d_day = (due - today).days if due else None
        if slots.get("stage") == "after" and s.get("deadline") and s["deadline"]["kind"] == "before":
            state = "past"
        elif d_day is not None and d_day < 0:
            state = "overdue"
        forms = []
        for f in s["forms"]:
            ff = kb.form_by_no(f["no"])
            forms.append({"no": f["no"], "name": f["name"] or (ff["name"] if ff else ""), "form_id": ff["id"] if ff else None,
                          "exists": ff is not None})
        steps.append({**s, "deadline_label": deadline_label(s.get("deadline")), "due": due.isoformat() if due else None,
                      "due_base": base, "d_day": d_day, "state": state, "forms": forms, "focus": i == focus})
    todo = [s for s in steps if s["state"] == "todo"]
    nxt = next((s for s in todo if s["focus"]), todo[0] if todo else None)
    approval = approval_line(kb, card, slots)
    cost = travel_cost(kb, slots) if card.get("category") == "출장" else None
    tips = kb.tips_for(card)
    overrides = kb.common_overrides(card)
    lines = []
    if llm_reply:
        lines.append(llm_reply)
    lines.append(f"'{card['name']}'에 해당합니다 (근거: {card['regulation']}).")
    if nxt:
        lines.append(f"지금 할 일: {nxt['title']} — {nxt['deadline_label'] or '기한 없음'}"
                     + (f" → {nxt['due']}까지 (D{'-' if nxt['d_day'] >= 0 else '+'}{abs(nxt['d_day'])})" if nxt["due"] and nxt["d_day"] is not None else ""))
    past_exc = [n for s in steps if s["state"] == "past" for n in s.get("notes", []) if n.startswith("[예외]")]
    if past_exc:
        lines.append("사전 단계를 하지 않았다면 예외 규정을 확인하세요: " + past_exc[0].replace("[예외] ", ""))
    over = [s for s in steps if s["state"] == "overdue"]
    if over:
        lines.append("기한이 지난 단계: " + ", ".join(s["title"] for s in over))
    if llm_note:
        lines.append(llm_note)
    return {"type": "procedure", "text": "\n".join(lines), "card": {k: card[k] for k in ("id", "name", "regulation", "rel", "category")},
            "steps": steps, "next": nxt["title"] if nxt else None, "approval": approval, "cost": cost, "tips": tips,
            "overrides": overrides, "slots": slots, "alternatives": [{"id": c["id"], "name": c["name"]} for c, _ in ranked[1:]],
            "references": card.get("references", [])}
