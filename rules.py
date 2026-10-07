"""규정 점검: 업무 시뮬레이터("이렇게 하면 어떻게 되나요?")와 제출 전 사전점검.

규정 문장에서 점검 규칙을 뽑는다. 모델 없이 규칙으로만 판정하고, 모든 판정에 근거 조문을 붙인다.
- 금지 규칙   : "…하여서는 아니 된다", "…할 수 없다" (+ "다만, …" 예외 = 해결 방법)
- 금액 기준   : "추정가격이 200만원을 초과하는 …" (+ 필요한 서류·수량)
- 기한 규칙   : 절차 카드 단계의 기한 (knowledge.parse_deadline)
- 위반 시 조치: "제3조를 위반하여 …" (해당 금지 규칙의 결과로 보여 줌)
판정: 가능(ok) / 주의(warn) / 불가(bad). 근거를 찾지 못하면 판단하지 않는다(gray).
"""
import re
from datetime import date, timedelta

from core import regparse
from core.search import BM25
from core.textutil import strip_josa

from knowledge import PROHIBIT, SYN, deadline_label, parse_docs
from assistant import REGION_METRO, _expand, extract_slots

RX_AMOUNT = re.compile(r"(\d[\d,]*)\s*(천만|만|억)?\s*원\s*(?:을|를|이|가)?\s*(초과|이상|미만|이하)")
RX_VIOLATE = re.compile(r"제(\d+)조(?:의(\d+))?(?:\s*제(\d+)항)?\s*를?\s*위반")
STOP = {"공단", "경우", "다음", "각호", "해당", "규정", "지침", "직원", "사용", "하는", "있는", "이내", "이상", "그밖", "가람구시설관리공단",
        "제1항", "제2항", "따른", "대한", "관한", "통해", "하면", "어떻게", "되나요", "돼요", "했다면", "하면요", "어떻게돼"}
LEVEL = {"ok": 0, "gray": 0, "warn": 1, "bad": 2}
VERDICT = {"ok": "가능", "warn": "주의", "bad": "불가", "gray": "판단 근거 없음"}
ALIAS = {"같이": "함께", "동시에": "함께", "사적": "개인", "개인적": "개인", "본인": "개인", "사서": "구매", "샀": "구매", "사면": "구매",
         "구입": "구매", "결제했": "결제", "긁": "결제", "카드": "법인카드", "회사카드": "법인카드", "공용카드": "법인카드",
         "승인": "결재", "허락": "결재", "출장비": "여비", "정산서": "정산", "술집": "주점", "노래방": "유흥업소"}


def _won(num, unit):
    return int(num.replace(",", "")) * {"만": 10_000, "천만": 10_000_000, "억": 100_000_000}.get(unit or "", 1)


def words(text):
    out = set()
    for w in re.findall(r"[가-힣A-Za-z]{2,}", text):
        w = strip_josa(w)
        for k, v in ALIAS.items():
            if w.startswith(k):
                out.add(v)
        if len(w) >= 2 and w not in STOP:
            out.add(w)
            # '개인물품', '업무용' 같은 붙은 말도 잡도록 앞 두 글자를 함께 넣는다
            if len(w) >= 4:
                out.add(w[:2])
    return out


# ------------------------------------------------------------ 규칙 뽑기

def extract_rules(kb, cards):
    rules = []
    consequences = {}
    for r in kb.regs:
        if r["layer"] not in ("org", "common"):
            continue
        reg = r["reg"]
        for a in reg.articles:
            for p in a.paras:
                sent = (p.text + " " + " ".join(it.text for it in p.items)).strip()
                m = RX_VIOLATE.search(sent)
                if m:
                    consequences.setdefault((r["id"], regparse.akey(int(m.group(1)), int(m.group(2) or 0))), []).append(
                        {"text": sent, "basis": _basis(r, a, p)})
    for r in kb.regs:
        if r["layer"] not in ("org", "common"):
            continue
        reg = r["reg"]
        for a in reg.articles:
            for p in a.paras:
                sent = (p.text + " " + " ".join(it.text for it in p.items)).strip()
                head, _, exc = sent.partition("다만,")
                basis = _basis(r, a, p)
                kw = words(head) | words(a.title) | words(re.sub(r"^\S*공단\s*", "", reg.title))
                if PROHIBIT.search(head):
                    rules.append({"type": "prohibit", "text": head.strip(), "exception": exc.strip(), "kw": kw,
                                  "consequence": consequences.get((r["id"], a.key), []), "basis": basis})
                for m in RX_AMOUNT.finditer(head):
                    cnt = re.search(r"(\d+)\s*인\s*이상", head)
                    rules.append({"type": "amount", "text": head.strip(), "value": _won(m.group(1), m.group(2)), "op": m.group(3),
                                  "docs": parse_docs(head), "count": int(cnt.group(1)) if cnt else 0, "kw": kw, "basis": basis})
    return rules


def _basis(r, a, p):
    return {"reg": r["reg"].title, "reg_id": r["id"], "label": a.label + (f"제{p.no}항" if p.no else ""),
            "title": a.title, "text": a.to_text(), "rel": r["rel"]}


def _other_team(text):
    """'총무팀은 …', '… 총무팀이 계약팀에 의뢰한다'처럼 신청자가 아닌 부서가 처리하는 기준인지."""
    return bool(re.match(r"^\S*팀[은는이]\s", text) or re.search(r"\S+팀이\s", text))


def _hits(op, amount, value):
    return {"초과": amount > value, "이상": amount >= value, "미만": amount < value, "이하": amount <= value}[op]


def _won_fmt(n):
    if n % 10_000 == 0 and n >= 10_000:
        man = n // 10_000
        return f"{man // 10000}억 {man % 10000}만원".replace(" 0만원", "원") if man >= 10000 else f"{man:,}만원"
    return f"{n:,}원"


# ------------------------------------------------------------ 업무 시뮬레이터

RX_DELAY_AFTER = re.compile(r"(\d+)\s*(일|주)\s*(?:뒤|후|만에|지나서|늦게|넘어서|째)")
RX_DELAY_BEFORE = re.compile(r"(\d+)\s*(일|주)\s*전에")
RX_NO_APPROVAL = re.compile(r"(결재|승인|허가|품의)\s*(?:없이|안\s*받고|받기\s*전에|전에|도\s*안)|먼저\s*(?:구매|사|결제|지급|계약)")
RX_PAST = re.compile(r"했다면|했는데|해버렸|했어요|했어|했습니다|했으면")


def _pick_card(cards, text):
    if not cards:
        return None
    idx = BM25(cards, lambda c: " ".join([c["name"], c["regulation"], " ".join(c["keywords"]) * 2,
                                          " ".join(s["title"] + " " + s["action"] for s in c["steps"])]))
    hit = idx.search(_expand(text), k=1)
    return hit[0][0] if hit else None


def _pick_step(card, text):
    idx = BM25(list(range(len(card["steps"]))), lambda i: card["steps"][i]["title"] * 3 + " " + card["steps"][i]["action"])
    h = idx.search(_expand(text), k=1)
    return card["steps"][h[0][0]] if h else None


def simulate(kb, cards, rules, text, today=None):
    today = today or date.today()
    q = words(text)
    slots = extract_slots(text, today)
    m = re.search(r"(\d+)\s*일\s*(?:을|를)?\s*(?:쓰|사용|연속|동안|간\s)", text)
    if m and not slots.get("days"):
        slots["days"] = int(m.group(1))
    findings = []

    # 1) 금지 규칙
    no_appr = bool(RX_NO_APPROVAL.search(text))
    scored = []
    for ru in rules:
        if ru["type"] != "prohibit":
            continue
        s = len(q & ru["kw"])
        if re.search(r"(결재|승인).{0,6}전에", ru["text"]):
            if not no_appr:
                continue  # '결재 전 구매 금지'는 결재 없이 하는 상황에만 적용
            s += 3
        if s >= 3:
            scored.append((s, ru))
    scored.sort(key=lambda x: -x[0])
    for s, ru in scored[:2]:
        if scored and s < scored[0][0] - 1:
            continue
        fix = []
        if ru["exception"]:
            fix.append(("이미 했다면" if RX_PAST.search(text) else "예외") + ": " + ru["exception"])
        for c in ru["consequence"]:
            fix.append(f"위반하면: {c['text']} ({c['basis']['label']})")
        findings.append({"level": "bad", "title": "규정에서 금지하는 행위입니다",
                         "detail": ru["text"], "basis": ru["basis"], "fix": fix or ["이 방법 대신 정해진 절차를 따르세요."]})

    card = _pick_card(cards, text)
    # 2) 기한 규칙
    m_after, m_before = RX_DELAY_AFTER.search(text), RX_DELAY_BEFORE.search(text)
    if card and (m_after or m_before):
        st = _pick_step(card, text)
        dl = st and st.get("deadline")
        cond = ""
        if dl and slots.get("days"):
            # "3일 이상 연속하여 … 5일 전까지" 같은 조건부 기한
            for n_ in st.get("notes", []):
                cm = re.search(r"(\d+)일\s*이상", n_)
                ndl = cm and int(cm.group(1)) <= slots["days"] and __import__("knowledge").parse_deadline(n_)
                if ndl and ndl["kind"] == dl["kind"]:
                    dl, cond = ndl, n_.replace("[예외] ", "")
        if dl and dl.get("days") is not None:
            m = m_after or m_before
            n = int(m.group(1)) * (7 if m.group(2) == "주" else 1)
            basis = dict(st["basis"], label=st["basis"]["label"] + (f"제{st['basis']['para']}항" if st["basis"].get("para") and not cond else ""))
            late = (dl["kind"] == "after" and m_after and n > dl["days"]) or (dl["kind"] == "before" and m_before and n < dl["days"])
            label = deadline_label(dl)
            if late:
                over = n - dl["days"] if dl["kind"] == "after" else dl["days"] - n
                fix = [f"'{label}' 기한 안에 처리하세요."]
                fix += [x.replace("[예외] ", "예외: ") for x in st.get("notes", [])
                        if re.search(r"기한|독촉|사후|긴급", x) and not PROHIBIT.search(x.split("다만")[0])][:2]
                findings.append({"level": "bad", "title": f"{st['title']} 기한을 {over}일 넘깁니다",
                                 "detail": f"말씀하신 시점은 {n}일{' 뒤' if m_after else ' 전'}이고, 규정 기한은 {label}입니다."
                                           + (f" (적용 조항: {cond})" if cond else ""),
                                 "basis": basis, "fix": fix})
            elif (dl["kind"] == "after" and m_after) or (dl["kind"] == "before" and m_before):
                findings.append({"level": "ok", "title": f"{st['title']} 기한 안입니다",
                                 "detail": f"규정 기한은 {label}이고, 말씀하신 시점({n}일{' 뒤' if m_after else ' 전'})은 그 안입니다.",
                                 "basis": basis, "fix": []})

    # 3) 금액 기준
    amount = slots.get("amount")
    if amount:
        for ru in rules:
            if ru["type"] != "amount" or not _hits(ru["op"], amount, ru["value"]):
                continue
            if card and ru["basis"]["reg_id"] != card["reg_id"] and len(q & ru["kw"]) < 2:
                continue
            if _other_team(ru["text"]):
                findings.append({"level": "ok", "title": f"참고: {_won_fmt(ru['value'])} {ru['op']} 기준(담당 부서 처리)",
                                 "detail": ru["text"], "basis": ru["basis"], "fix": []})
                continue
            findings.append({"level": "warn", "title": f"{_won_fmt(ru['value'])} {ru['op']} 기준에 해당합니다",
                             "detail": ru["text"], "basis": ru["basis"],
                             "fix": [f"금액이 {_won_fmt(amount)}이므로 이 조항의 요건을 갖추세요."]})

    # 4) 아무 조항에도 걸리지 않았지만 절차는 찾은 경우
    if card and not any(f["level"] in ("bad", "warn") for f in findings) and not findings:
        first = next((s for s in card["steps"] if s.get("deadline")), card["steps"][0])
        findings.append({"level": "ok", "title": "등록된 규정에서 걸리는 조항을 찾지 못했습니다",
                         "detail": f"'{card['name']}'대로 진행하면 됩니다. 먼저 할 일: {first['title']} ({deadline_label(first.get('deadline')) or '기한 없음'})",
                         "basis": dict(first["basis"]), "fix": []})
    level = max((f["level"] for f in findings), key=lambda x: LEVEL[x], default="gray")
    summary = {"ok": "규정상 문제없이 할 수 있습니다.", "warn": "할 수 있지만 추가로 갖출 요건이 있습니다.",
               "bad": "이대로 하면 규정에 어긋납니다. 아래 해결 방법을 확인하세요.",
               "gray": "등록된 규정에서 판단할 근거를 찾지 못했습니다. 표현을 바꾸거나 관련 규정을 등록해 주세요."}[level]
    return {"verdict": level, "label": VERDICT[level], "summary": summary, "findings": findings,
            "card": {"id": card["id"], "name": card["name"]} if card else None, "slots": slots, "mode": "규칙 기반(규정 대조)"}


# ------------------------------------------------------------ 제출 전 사전점검

def _d(s):
    try:
        return date.fromisoformat(s) if s else None
    except ValueError:
        return None


def _card(cards, category):
    return next((c for c in cards if c.get("category") == category), None)


def _step(card, *names):
    for n in names:
        for s in card["steps"]:
            if s["title"] == n or s["title"].startswith(n):
                return s
    return None


def _req_docs(step):
    """단계에 필요한 서류와 조건: [{'name': '숙박비 영수증', 'cond': '숙박한 경우'}]"""
    out = []
    for d in step.get("docs", []):
        m = re.match(r"(.+?)\s*\(([^)]*경우)\)\s*$", d)
        out.append({"name": (m.group(1) if m else d).strip(), "cond": m.group(2) if m else ""})
    return out


def spec(cards):
    """사전점검 화면에 필요한 문서 종류와 첨부서류 목록."""
    out = {}
    c = _card(cards, "출장")
    st = c and _step(c, "여비 정산")
    if st:
        out["travel"] = {"name": "여비 정산 신청", "card": c["name"], "docs": _req_docs(st)}
    c = _card(cards, "물품")
    st = c and _step(c, "구매 요청")
    if st:
        out["goods"] = {"name": "물품 구매 요청", "card": c["name"], "docs": _req_docs(st) or [{"name": "물품 구매 요청서", "cond": ""}]}
    return out


def _f(level, title, detail, basis=None, fix=None):
    return {"level": level, "title": title, "detail": detail, "basis": basis, "fix": fix or []}


def _sb(step):
    b = step["basis"]
    return dict(b, label=b["label"] + (f"제{b['para']}항" if b.get("para") else ""))


def precheck(kb, cards, rules, kind, f, today=None):
    today = today or date.today()
    out = []
    if kind == "travel":
        out = _check_travel(kb, cards, f, today)
    elif kind == "goods":
        out = _check_goods(cards, rules, f, today)
    else:
        return {"error": "지원하지 않는 문서 종류입니다"}
    level = max((x["level"] for x in out), key=lambda x: LEVEL[x], default="ok")
    counts = {k: sum(1 for x in out if x["level"] == k) for k in ("ok", "warn", "bad")}
    label = {"ok": "정상", "warn": "확인 필요", "bad": "규정 위반 가능성"}[level if level != "gray" else "warn"]
    return {"verdict": level, "label": label, "counts": counts, "findings": out, "mode": "규칙 기반(규정 대조)"}


def _check_travel(kb, cards, f, today):
    out = []
    c = _card(cards, "출장")
    if not c:
        return [_f("warn", "출장 절차를 찾지 못했습니다", "여비규정을 등록하면 점검할 수 있습니다.")]
    start, end = _d(f.get("start")), _d(f.get("end"))
    if not start or not end:
        return [_f("warn", "출장 기간을 입력하세요", "출장 시작일과 종료일이 있어야 기한을 계산합니다.")]
    if end < start:
        return [_f("bad", "출장 기간이 잘못되었습니다", f"종료일({end})이 시작일({start})보다 앞섭니다.")]
    nights = (end - start).days
    period = f"{start.month}월 {start.day}일~{end.month}월 {end.day}일" if start != end else f"{start.month}월 {start.day}일"

    # 출장 신청(사전 승인)
    st = _step(c, "출장 신청")
    if st and st.get("deadline") and st["deadline"]["kind"] == "before":
        due = start - timedelta(days=st["deadline"]["days"])
        ap = _d(f.get("apply_date"))
        exc = [n.replace("[예외] ", "예외: ") for n in st.get("notes", []) if n.startswith("[예외]")]
        if not ap:
            out.append(_f("warn", "출장 신청(사전 승인) 날짜가 비어 있습니다", f"출장 신청은 {deadline_label(st['deadline'])}({due}까지)입니다.", _sb(st), exc))
        elif ap > due:
            out.append(_f("warn", f"출장 신청이 기한보다 {(ap - due).days}일 늦었습니다",
                          f"신청일 {ap} / 기한 {due}({deadline_label(st['deadline'])})", _sb(st), exc or ["사후 승인 사유를 적어 두세요."]))
        else:
            out.append(_f("ok", "출장 신청 기한을 지켰습니다", f"신청일 {ap} (기한 {due})", _sb(st)))

    # 출장 결과 보고
    st = _step(c, "출장 결과 보고")
    rp = _d(f.get("report_date"))
    if st and st.get("deadline") and rp:
        due = end + timedelta(days=st["deadline"]["days"])
        if rp > due:
            out.append(_f("bad", f"출장 결과 보고가 기한을 {(rp - due).days}일 넘겼습니다", f"보고일 {rp} / 기한 {due}", _sb(st)))
        else:
            out.append(_f("ok", "출장 결과 보고 기한을 지켰습니다", f"보고일 {rp} (기한 {due})", _sb(st)))

    # 여비 정산 기한
    st = _step(c, "여비 정산")
    if st and st.get("deadline"):
        due = end + timedelta(days=st["deadline"]["days"])
        sub = _d(f.get("submit_date")) or today
        when = "제출 예정일" if f.get("submit_date") else "오늘"
        if sub > due:
            fix = [n for n in st.get("notes", []) if not n.startswith("[예외]")][:1]
            out.append(_f("bad", f"여비 정산 기한을 {(sub - due).days}일 넘깁니다",
                          f"{when} {sub} / 기한 {due}({deadline_label(st['deadline'])})", _sb(st),
                          ["지연 사유를 적어 바로 제출하세요."] + fix))
        else:
            out.append(_f("ok", "여비 정산 기한 안입니다", f"{when} {sub} (기한 {due}, D-{(due - sub).days})", _sb(st)))

        # 필수 서류
        have = set(f.get("attachments") or [])
        receipts = f.get("receipts") or []
        kinds = {r.get("kind") for r in receipts}
        for d in _req_docs(st):  # 영수증 칸에 넣은 영수증은 첨부한 것으로 본다
            if ("숙박" in d["name"] and "숙박" in kinds) or ("운임" in d["name"] and "운임" in kinds):
                have.add(d["name"])
        for d in _req_docs(st):
            if d["name"] in have:
                continue
            need = True
            if d["cond"]:
                if "숙박" in d["cond"]:
                    need = nights > 0 or "숙박" in kinds
                elif "교통" in d["cond"] or "운임" in d["name"]:
                    need = "운임" in kinds or bool(re.search(r"자가용|렌터카|택시", f.get("transport", "")))
                    if not need and "운임" not in kinds:
                        out.append(_f("warn", f"'{d['name']}' 필요 여부를 확인하세요", f"조건: {d['cond']}", _sb(st)))
                        continue
            if need:
                out.append(_f("bad", f"필수 첨부서류가 빠졌습니다: {d['name']}",
                              f"{d['cond'] + '에는 ' if d['cond'] else ''}{d['name']}을(를) 첨부해야 합니다.".replace("을(를)", "을" if _batchim(d["name"]) else "를"),
                              _sb(st), ["첨부한 뒤 다시 점검하세요."]))

    # 영수증 날짜·금액
    cap, cap_key, src = _lodging_cap(kb, f.get("destination", ""))
    for r in f.get("receipts") or []:
        rd = _d(r.get("date"))
        k = r.get("kind") or "영수증"
        amt = int(str(r.get("amount") or 0).replace(",", "") or 0)
        if rd:
            last = end - timedelta(days=1) if k == "숙박" and nights > 0 else end
            if rd < start or rd > last:
                out.append(_f("warn", f"{k} 영수증 날짜가 출장 기간과 맞지 않습니다",
                              f"출장 기간은 {period}인데 {k} 영수증은 {rd.month}월 {rd.day}일입니다."
                              + (" (숙박은 마지막 날 전날 밤까지)" if k == "숙박" else ""), None,
                              ["날짜가 맞는 영수증으로 바꾸거나, 일정이 바뀌었다면 사유를 적으세요."]))
        if k == "숙박" and cap and amt > cap:
            out.append(_f("warn", f"숙박비가 상한을 {amt - cap:,}원 넘습니다",
                          f"{cap_key} 상한 {cap:,}원, 영수증 {amt:,}원. 초과분은 지급되지 않습니다.",
                          {"reg": "여비 지급 기준", "label": "별표 1", "title": cap_key, "text": f"{cap_key}: 1박 상한 {cap:,}원(실비)", "rel": src}))
    if not any(x["level"] != "ok" for x in out):
        out.append(_f("ok", "규정과 맞지 않는 곳을 찾지 못했습니다", "기한·첨부서류·영수증 날짜·숙박비 상한을 확인했습니다."))
    return out


def _batchim(w):
    c = w.strip()[-1:]
    return bool(c) and "가" <= c <= "힣" and (ord(c) - 0xAC00) % 28 != 0


def _lodging_cap(kb, dest):
    t = kb.table_find(r"여비")
    if not t:
        return None, "", ""
    key = "숙박비(서울특별시)" if "서울" in dest else "숙박비(광역시)" if any(x in dest for x in REGION_METRO) else "숙박비(그 밖의 지역)"
    for r in t["table"]["rows"]:
        if r and r[0] == key and str(r[2]).replace(",", "").isdigit():
            return int(str(r[2]).replace(",", "")), key, t["rel"]
    return None, key, t["rel"]


def _check_goods(cards, rules, f, today):
    out = []
    c = _card(cards, "물품")
    if not c:
        return [_f("warn", "물품 구매 절차를 찾지 못했습니다", "물품관리규정을 등록하면 점검할 수 있습니다.")]
    amount = int(str(f.get("amount") or 0).replace(",", "") or 0)
    st = _step(c, "구매 요청")
    req, wish = _d(f.get("request_date")) or today, _d(f.get("wish_date"))
    if st and st.get("deadline") and st["deadline"]["kind"] == "before":
        if not wish:
            out.append(_f("warn", "구매 희망일을 입력하세요", f"구매 요청은 {deadline_label(st['deadline'])}입니다.", _sb(st)))
        else:
            due = wish - timedelta(days=st["deadline"]["days"])
            if req > due:
                out.append(_f("bad", f"구매 요청이 기한보다 {(req - due).days}일 늦습니다",
                              f"요청일 {req} / 기한 {due}({deadline_label(st['deadline'])})", _sb(st),
                              [f"구매 희망일을 {req + timedelta(days=st['deadline']['days'])} 이후로 조정하거나 총무팀과 협의하세요."]))
            else:
                out.append(_f("ok", "구매 요청 기한 안입니다", f"요청일 {req} (기한 {due})", _sb(st)))
    reg_id = c["reg_id"]
    for ru in rules:
        if ru["basis"]["reg_id"] != reg_id:
            continue
        if ru["type"] == "amount" and amount and _hits(ru["op"], amount, ru["value"]):
            if ru["count"] and any("견적" in d for d in ru["docs"] + [ru["text"]]):
                q = int(f.get("quotes") or 0)
                if q < ru["count"]:
                    out.append(_f("bad", f"구매금액이 {_won_fmt(ru['value'])}을 초과하여 견적서 {ru['count']}부가 필요합니다",
                                  f"구매금액 {amount:,}원, 첨부한 견적서 {q}부", ru["basis"], [f"견적서를 {ru['count'] - q}부 더 받아 첨부하세요."]))
                else:
                    out.append(_f("ok", f"견적서 {q}부를 첨부했습니다", f"{_won_fmt(ru['value'])} {ru['op']} 기준 충족", ru["basis"]))
            elif _other_team(ru["text"]):
                out.append(_f("ok", f"참고: {_won_fmt(ru['value'])} {ru['op']} 기준(담당 부서 처리)", ru["text"], ru["basis"]))
            else:
                out.append(_f("warn", f"{_won_fmt(ru['value'])} {ru['op']} 기준에 해당합니다", ru["text"], ru["basis"]))
        if ru["type"] == "prohibit" and re.search(r"(결재|승인).{0,6}전에", ru["text"]):
            if f.get("purchased") and not f.get("approved"):
                out.append(_f("bad", "결재 전에 구매했습니다", ru["text"], ru["basis"],
                              ["예외: " + ru["exception"]] if ru["exception"] else []))
            elif not f.get("approved"):
                out.append(_f("warn", "아직 결재를 받지 않았습니다", "결재를 받은 뒤에 구매해야 합니다.", ru["basis"]))
    if not any(x["level"] != "ok" for x in out):
        out.append(_f("ok", "규정과 맞지 않는 곳을 찾지 못했습니다", "요청 기한·금액 기준·결재 여부를 확인했습니다."))
    return out
