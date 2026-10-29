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

from knowledge import OBLIGATION, PROHIBIT, SYN, deadline_label, parse_deadline, parse_docs
from assistant import REGION_METRO, _expand, extract_slots, team_of

RX_AMOUNT = re.compile(r"(\d[\d,]*)\s*(천만|만|억)?\s*원\s*(?:을|를|이|가)?\s*(초과|이상|미만|이하)")
RX_VIOLATE = re.compile(r"제(\d+)조(?:의(\d+))?(?:\s*제(\d+)항)?\s*를?\s*위반")
STOP = {"공단", "기관", "경우", "다음", "각호", "해당", "규정", "지침", "직원", "사용", "하는", "있는", "이내", "이상", "그밖", "공공기관",
        "제1항", "제2항", "따른", "대한", "관한", "통해", "하면", "어떻게", "되나요", "돼요", "했다면", "하면요", "어떻게돼"}
LEVEL = {"ok": 0, "gray": 0, "warn": 1, "bad": 2}
VERDICT = {"ok": "가능", "warn": "주의", "bad": "불가", "gray": "판단 근거 없음(확인 필요)"}
ALIAS = {"같이": "함께", "동시에": "함께", "사적": "개인", "개인적": "개인", "본인": "개인", "사서": "구매", "샀": "구매", "사면": "구매",
         "구입": "구매", "결제했": "결제", "긁": "결제", "카드": "법인카드", "회사카드": "법인카드", "공용카드": "법인카드",
         "승인": "결재", "허락": "결재", "출장비": "여비", "정산서": "정산", "연차": "연가", "술집": "주점", "노래방": "유흥업소",
         "주점": "주점", "호프": "주점", "포차": "주점", "이자카야": "주점", "유흥": "유흥업소", "룸살롱": "유흥업소",
         "나이트": "유흥업소", "클럽": "유흥업소", "가라오케": "유흥업소"}


def _won(num, unit):
    return int(num.replace(",", "")) * {"만": 10_000, "천만": 10_000_000, "억": 100_000_000}.get(unit or "", 1)


def words(text):
    out = set()
    for w in re.findall(r"[가-힣A-Za-z]{2,}", text):
        w = strip_josa(w)
        for k, v in ALIAS.items():
            # 두 글자 이상 별칭은 낱말 안 어디에 있어도 잡는다('단란주점' → 주점)
            if w.startswith(k) or (len(k) >= 2 and k in w):
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
                kw = words(head) | words(a.title) | words(re.sub(r"^\S*(?:공단|공공기관)\s*", "", reg.title))
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

def _pick_card(cards, text):
    """질문과 가장 가까운 절차 카드와 점수. 카드 핵심어가 질문에 하나도 없으면 관련 없는 것으로 본다."""
    if not cards:
        return None, 0
    qx = _expand(text)
    idx = BM25(cards, lambda c: " ".join([c["name"], c["regulation"], " ".join(c["keywords"]) * 2,
                                          " ".join(s["title"] + " " + s["action"] for s in c["steps"])]))
    hit = idx.search(qx, k=1)
    if not hit:
        return None, 0
    c, sc = hit[0]
    if not any(len(k) >= 2 and k in qx for k in c["keywords"]):
        return None, 0
    return c, sc


def _pick_common(kb, text):
    """공통 기준 조문 중 질문과 가장 가까운 것을 절차 단계 모양으로 돌려준다."""
    if not kb.common_articles:
        return None, 0
    idx = BM25(kb.common_articles, lambda a: a["article"].title + " " + a["article"].plain)
    hit = idx.search(_expand(text), k=1)
    if not hit:
        return None, 0
    ca, sc = hit[0]
    a = ca["article"]
    step = {"title": a.title or a.label, "action": a.plain, "deadline": parse_deadline(a.plain), "notes": [], "docs": [],
            "basis": {"reg": ca["reg_title"], "reg_id": ca["reg_id"], "label": a.label, "title": a.title,
                      "text": a.to_text(), "rel": ca["rel"]}}
    return step, sc


def _pick_step(card, text):
    idx = BM25(list(range(len(card["steps"]))), lambda i: (card["steps"][i]["title"] + " ") * 3 + card["steps"][i]["action"]
               + " " + " ".join(card["steps"][i].get("docs", [])))
    # 질문 낱말 그대로 먼저 찾는다(동의어를 넓히면 '출장' 질문이 모두 '정산' 단계로 끌려간다)
    t = text
    for rx in (RX_DELAY_AFTER, RX_DELAY_BEFORE, RX_NEXT_DAY, RX_SAMEDAY):
        t = rx.sub(" ", t)  # '다음날'이 '다음 각 호'에 걸리지 않도록 시점 표현은 뺀다
    qq = t + " " + " ".join(words(t))
    h = idx.search(qq, k=1) or idx.search(_expand(text), k=1)
    return card["steps"][h[0][0]] if h else None


RX_DELAY_AFTER = re.compile(r"(\d+)\s*(일|주)\s*(?:뒤|후|만에|지나서|늦게|넘어서|째)")
RX_DELAY_BEFORE = re.compile(r"(\d+)\s*(일|주)\s*전에")
RX_NEXT_DAY = re.compile(r"다음\s*날|익일|이튿날")
RX_SAMEDAY = re.compile(r"당일")
RX_HOUR = re.compile(r"(오전|오후|저녁|밤)?\s*(\d{1,2})\s*시")
RX_NO_APPROVAL = re.compile(r"(결재|승인|허가|품의)\s*(?:없이|안\s*받고|받기\s*전에|전에|도\s*안)|먼저\s*(?:구매|사|결제|지급|계약)")
RX_PAST = re.compile(r"했다면|했는데|해버렸|했어요|했어|했습니다|했으면")
# "정산 안 하면", "진단서 안 내도", "기한 안 지켜도", "보고하지 않으면"
RX_NOT_DO = re.compile(r"안\s*(?:하면|해도|하고|했|내면|내도|내고|냈|지켜|지키면|올리면|올려도|써도|쓰면)|"
                       r"(?:하|지키|내|제출하|정산하|신청하|보고하|작성하|입력하)지\s*(?:않|말|못)|미제출|미신청|어기|어겨|빼먹|놓치|건너뛰")
RX_QUOTES = re.compile(r"견적(?:서)?\s*(?:를|을|은|는)?\s*(\d+|하나|한|두|둘|세|셋)\s*(?:개|부|장|곳|군데|건|만)?|"
                       r"(\d+|한|두|세)\s*(?:개|부|장|곳|군데|건)\s*(?:의\s*)?(?:업체\s*)?(?:에서\s*)?견적")
KOR_NUM = {"한": 1, "하나": 1, "두": 2, "둘": 2, "세": 3, "셋": 3}


def _num(s):
    return int(s) if s.isdigit() else KOR_NUM.get(s)


def _threshold(sentence):
    """'6일 이상 병가를 사용한 직원은 …'처럼 기간 조건이 붙은 조항이면 그 일수."""
    m = re.search(r"(\d+)\s*일\s*이상", sentence.split("다만")[0])
    return int(m.group(1)) if m else None


DAY_WORDS = [("일주일", "7일"), ("하루", "1일"), ("이틀", "2일"), ("사흘", "3일"), ("나흘", "4일"), ("닷새", "5일"),
             ("엿새", "6일"), ("이레", "7일"), ("열흘", "10일"), ("보름", "15일")]


def _timing(text, slots, today):
    """질문 속 시점: (방향, 일수, 설명). 방향 before=기준일 전, after=기준일 뒤, same=당일."""
    for w, n in DAY_WORDS:  # '이틀 전에', '열흘 뒤' 같은 우리말 날수
        text = text.replace(w, n)
    m = RX_DELAY_AFTER.search(text)
    if m:
        n = int(m.group(1)) * (7 if m.group(2) == "주" else 1)
        return "after", n, f"{n}일 뒤"
    m = RX_DELAY_BEFORE.search(text)
    if m:
        n = int(m.group(1)) * (7 if m.group(2) == "주" else 1)
        return "before", n, f"{n}일 전"
    if RX_NEXT_DAY.search(text):
        return "after", 1, "다음 날"
    if RX_SAMEDAY.search(text):
        return "same", 0, "당일"
    if slots.get("date") and not RX_PAST.search(text):
        d = date.fromisoformat(slots["date"])
        if d >= today:
            n = (d - today).days
            return "date", n, f"{d.month}월 {d.day}일 사용·시작이라 오늘 신청해도 {n}일 전" if n else "오늘 바로"
    return None, None, ""


def _fix_notes(st):
    out = []
    for x in st.get("notes", []):
        if PROHIBIT.search(x.split("다만")[0]):
            continue
        if re.search(r"아니한\s*경우|않은\s*경우|독촉|위반", x):
            out.append("지키지 않으면: " + x.replace("[예외] ", ""))
        elif re.search(r"기한|사후|긴급", x):
            out.append("예외: " + x.replace("[예외] ", ""))
    return out[:2]


def _sub(word, josa):
    """받침에 맞는 조사: _sub('정산', '은는') → '정산은'"""
    return word + (josa[0] if _batchim(word) else josa[1])


def _kw_hits(q, kw):
    return len(q & kw)


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
        s = _kw_hits(q, ru["kw"])
        if re.search(r"(결재|승인).{0,6}전에", ru["text"]):
            if not no_appr:
                continue  # '결재 전 구매 금지'는 결재 없이 하는 상황에만 적용
            s += 3
        if re.search(r"함께|동시에|같이", ru["text"]) and "함께" not in q:
            continue  # '함께 결제 금지'는 함께(같이) 한 상황에만 적용
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

    # 질문에 맞는 절차: 기관 절차 카드가 약하고 공통 기준 조문이 뚜렷하면 공통 기준으로 판단한다
    card, cscore = _pick_card(cards, text)
    common, mscore = _pick_common(kb, text)
    st = None
    if common and mscore > 0 and (not card or cscore < mscore * 0.6) and common.get("deadline"):
        st, card = common, None
    elif card:
        st = _pick_step(card, text)

    # 2) 기한·의무 규칙
    if st:
        dl = st.get("deadline")
        th = _threshold(st["action"])
        days = slots.get("days")
        applies = not (th and days is not None and days < th)
        cond = ""
        if dl and days:
            # "3일 이상 연속하여 … 5일 전까지" 같은 조건부 기한
            for n_ in st.get("notes", []):
                cm = re.search(r"(\d+)일\s*이상", n_)
                ndl = cm and int(cm.group(1)) <= days and parse_deadline(n_)
                if ndl and ndl["kind"] == dl["kind"]:
                    dl, cond = ndl, n_.replace("[예외] ", "")
        b = st["basis"]
        basis = dict(b, label=b["label"] + (f"제{b['para']}항" if b.get("para") and not cond else ""))
        label = deadline_label(dl)
        direction, n, when = _timing(text, slots, today)
        if direction == "date" and not (dl and dl["kind"] == "before"):
            direction = None  # 날짜만 말한 경우는 '…일 전까지' 기한에만 쓴다
        late, ontime = None, False
        if dl and applies and direction:
            k, need = dl["kind"], dl.get("days") or 0
            if k == "before":
                if direction == "after":
                    late = f"{st['title']}은(는) 사전에 해야 합니다"
                elif n < need:
                    late = f"{st['title']} 기한보다 {need - n}일 늦습니다"
                else:
                    ontime = True
            elif k == "after":
                if direction == "after" and n > need:
                    late = f"{st['title']} 기한을 {n - need}일 넘깁니다"
                elif direction in ("after", "same"):
                    ontime = True
            elif k == "sameday":
                if direction == "after" and n >= 1:
                    late = f"{st['title']} 기한(당일 {dl['hour']}시)을 넘깁니다"
                elif direction in ("before", "same", "date"):
                    ontime = True
            elif k == "now" and direction == "after" and n >= 1:
                findings.append({"level": "warn", "title": f"{st['title']}은(는) 지체 없이 해야 합니다",
                                 "detail": f"말씀하신 시점은 {when}입니다.", "basis": basis, "fix": _fix_notes(st)})
        if dl and applies and dl["kind"] == "sameday" and not late:
            hm = RX_HOUR.search(text)
            if hm:
                h = int(hm.group(2)) + (12 if hm.group(1) in ("오후", "저녁", "밤") and int(hm.group(2)) < 12 else 0)
                if h > dl["hour"] or (h == dl["hour"] and re.search(r"\d+\s*시\s*\d+\s*분|시\s*반", text)):
                    late, when = f"{st['title']} 기한(당일 {dl['hour']}시)을 넘깁니다", f"{h}시"
                else:
                    ontime, when = True, f"{h}시"
        if late:
            fix = [f"'{label}' 기한 안에 처리하세요."] + _fix_notes(st)
            findings.append({"level": "bad", "title": late.replace("은(는)", "은" if _batchim(st["title"]) else "는"),
                             "detail": f"말씀하신 시점은 {when}이고, 규정 기한은 {label}입니다." + (f" (적용 조항: {cond})" if cond else ""),
                             "basis": basis, "fix": fix})
        elif ontime and not RX_NOT_DO.search(text):
            findings.append({"level": "ok", "title": f"{st['title']} 기한 안입니다",
                             "detail": f"규정 기한은 {label}이고, 말씀하신 시점({when})은 그 안입니다." + (f" (적용 조항: {cond})" if cond else ""),
                             "basis": basis, "fix": []})

        # 해야 할 일을 하지 않는 경우: "정산 안 하면?", "진단서 안 내도 돼?"
        if not late and RX_NOT_DO.search(text) and (dl or OBLIGATION.search(st["action"])):
            if th and days is not None and days < th:
                findings.append({"level": "ok", "title": f"{th}일 미만이라 이 조항의 대상이 아닙니다",
                                 "detail": f"이 조항은 {th}일 이상인 경우에 적용됩니다(말씀하신 기간 {days}일). 근거: {st['action']}",
                                 "basis": basis, "fix": []})
            elif th and days is None:
                findings.append({"level": "warn", "title": f"{th}일 이상이면 반드시 해야 합니다",
                                 "detail": st["action"], "basis": basis, "fix": [f"기간이 {th}일 이상이면 '{label or '정해진 기한'}' 안에 하세요."]})
            else:
                name = re.sub(r"\s*\([①-⑳]\)$", "", st["title"])
                findings.append({"level": "bad", "title": f"{name} 의무를 지키지 않게 됩니다",
                                 "detail": (f"{th}일 이상에 해당합니다(말씀하신 기간 {days}일). " if th else "")
                                           + st["action"] + (f" (기한: {label})" if label else ""), "basis": basis,
                                 "fix": ([f"'{label}' 기한 안에 하세요."] if label else []) + _fix_notes(st)})

    # 3) 금액 기준
    amount = slots.get("amount")
    if amount:
        qm = RX_QUOTES.search(text)
        quotes = _num(qm.group(1) or qm.group(2)) if qm else None
        for ru in rules:
            if ru["type"] != "amount":
                continue
            if card and ru["basis"]["reg_id"] != card["reg_id"] and len(q & ru["kw"]) < 2:
                continue
            if not _hits(ru["op"], amount, ru["value"]):
                if ru["count"] and "견적" in ru["text"] and quotes is not None:  # 기준 금액 이하면 견적 요건 대상이 아님
                    findings.append({"level": "ok", "title": f"{_won_fmt(amount)}은 {_won_fmt(ru['value'])} {ru['op']} 기준에 해당하지 않습니다",
                                     "detail": f"{ru['text']} (말씀하신 금액 {_won_fmt(amount)}, 견적 {quotes}개)", "basis": ru["basis"], "fix": []})
                continue
            if _other_team(ru["text"]):
                findings.append({"level": "ok", "ref": True, "title": f"참고: {_won_fmt(ru['value'])} {ru['op']} 기준(담당 부서 처리)",
                                 "detail": ru["text"], "basis": ru["basis"], "fix": []})
                continue
            if ru["count"] and "견적" in ru["text"] and quotes is not None:
                if quotes < ru["count"]:
                    findings.append({"level": "bad", "title": f"견적서가 부족합니다: {ru['count']}인 이상 견적이 필요합니다",
                                     "detail": f"{ru['text']} (말씀하신 견적 {quotes}개, 금액 {_won_fmt(amount)})", "basis": ru["basis"],
                                     "fix": [f"견적서를 {ru['count'] - quotes}개 더 받아 구매 요청서에 첨부하세요."]})
                else:
                    findings.append({"level": "ok", "title": f"견적서 {quotes}개로 {ru['count']}인 이상 견적 요건을 갖춥니다",
                                     "detail": ru["text"], "basis": ru["basis"], "fix": []})
                continue
            findings.append({"level": "warn", "title": f"{_won_fmt(ru['value'])} {ru['op']} 기준에 해당합니다",
                             "detail": ru["text"], "basis": ru["basis"],
                             "fix": [f"금액이 {_won_fmt(amount)}이므로 이 조항의 요건을 갖추세요."]})

    # 4) 판단할 조항을 찾지 못한 경우: '가능'이라고 하지 않는다
    team = team_of(kb, card["reg_id"]) if card else (team_of(kb, st["basis"]["reg_id"]) if st else "담당 부서")
    if not any(not f.get("ref") for f in findings):
        detail = "등록된 규정에 이 상황을 직접 다루는 조항이 없어 가능 여부를 판단하지 않습니다."
        basis = None
        if card:
            first = next((s for s in card["steps"] if s.get("deadline")), card["steps"][0])
            detail += f" 관련 절차는 '{card['name']}'이며, 먼저 할 일은 {first['title']}({deadline_label(first.get('deadline')) or '기한 없음'})입니다."
            basis = dict(first["basis"])
        findings.insert(0, {"level": "gray", "title": "등록된 규정에서 판단할 조항을 찾지 못했습니다", "detail": detail, "basis": basis,
                            "fix": [f"진행하기 전에 {team}에 확인하세요.", "관련 규정·지침이 따로 있다면 규정·서식 관리에 등록하면 다시 판정할 수 있습니다."]})
    level = max((f["level"] for f in findings if not f.get("ref")), key=lambda x: LEVEL[x], default="gray")
    if level == "ok" and not any(f["level"] == "ok" and not f.get("ref") for f in findings):
        level = "gray"
    summary = {"ok": "규정상 문제없이 할 수 있습니다.", "warn": "할 수 있지만 추가로 갖출 요건이 있습니다.",
               "bad": "이대로 하면 규정에 어긋납니다. 아래 해결 방법을 확인하세요.",
               "gray": f"등록된 규정에서 판단할 근거를 찾지 못했습니다. '가능'으로 단정하지 않으니 {team}에 확인하세요."}[level]
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


class InputError(ValueError):
    """사용자가 고칠 수 있는 입력 오류(화면에 그대로 보여 준다)."""


RX_MONEY = re.compile(r"^(-?)\s*(\d[\d,]*)\s*(천만|만|억)?\s*원?$")


def _money(v, label):
    """'1,500,000', '150만원' 같은 금액을 정수로. 글자·음수는 알기 쉬운 오류로 돌려준다."""
    if v in (None, ""):
        return 0
    if isinstance(v, bool):
        raise InputError(f"{label}에는 숫자를 넣어 주세요.")
    if isinstance(v, (int, float)):
        n = int(v)
    else:
        m = RX_MONEY.match(str(v).strip())
        if not m:
            raise InputError(f"{label}에는 숫자만 넣어 주세요(예: 1500000 또는 150만원). 입력한 값: {str(v)[:20]}")
        n = _won(m.group(2), m.group(3)) * (-1 if m.group(1) else 1)
    if n < 0:
        raise InputError(f"{label}에는 0원 이상을 넣어 주세요(음수는 안 됩니다). 입력한 값: {v}")
    return n


def _count(v, label):
    if v in (None, ""):
        return 0
    s = str(v).strip()
    if isinstance(v, bool) or not re.fullmatch(r"\d+\s*(?:개|부|장)?", s):
        raise InputError(f"{label}에는 0 이상의 숫자를 넣어 주세요(예: 2). 입력한 값: {s[:20]}")
    return int(re.match(r"\d+", s).group(0))


def _date_in(v, label):
    """비어 있으면 None, 날짜가 틀리면 알기 쉬운 오류."""
    if v in (None, ""):
        return None
    try:
        return date.fromisoformat(str(v).strip())
    except ValueError:
        raise InputError(f"{label} 날짜가 올바르지 않습니다(예: 2026-10-08). 입력한 값: {str(v)[:20]}") from None


TRAVEL_DATES = {"start": "출장 시작일", "end": "출장 종료일", "apply_date": "출장 신청(승인)일",
                "report_date": "출장 결과 보고일", "submit_date": "정산 제출(예정)일"}
GOODS_DATES = {"request_date": "구매 요청일", "wish_date": "구매 희망일"}


def _validate(kind, f):
    """입력값을 먼저 검사해 숫자·날짜 오류를 한 번에 알려 준다."""
    if not isinstance(f, dict):
        raise InputError("점검할 내용이 비어 있습니다.")
    for k, label in (TRAVEL_DATES if kind == "travel" else GOODS_DATES).items():
        _date_in(f.get(k), label)
    if kind == "travel":
        rs = f.get("receipts") or []
        if not isinstance(rs, list):
            raise InputError("영수증 목록 형식이 올바르지 않습니다.")
        for i, r in enumerate(rs, 1):
            if not isinstance(r, dict):
                raise InputError(f"{i}번째 영수증 형식이 올바르지 않습니다.")
            _money(r.get("amount"), f"{i}번째 영수증({r.get('kind') or '영수증'}) 금액")
            _date_in(r.get("date"), f"{i}번째 영수증({r.get('kind') or '영수증'})")
    else:
        _money(f.get("amount"), "구매금액")
        _count(f.get("quotes"), "견적서 수")


def precheck(kb, cards, rules, kind, f, today=None):
    today = today or date.today()
    out = []
    if kind not in ("travel", "goods"):
        return {"error": "지원하지 않는 문서 종류입니다"}
    try:
        _validate(kind, f)
    except InputError as e:
        return {"error": str(e)}
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
    lodging = 0
    for r in f.get("receipts") or []:
        rd = _d(r.get("date"))
        k = r.get("kind") or "영수증"
        amt = _money(r.get("amount"), "영수증 금액")
        if k == "숙박":
            lodging += amt
        if rd:
            last = end - timedelta(days=1) if k == "숙박" and nights > 0 else end
            if rd < start or rd > last:
                out.append(_f("warn", f"{k} 영수증 날짜가 출장 기간과 맞지 않습니다",
                              f"출장 기간은 {period}인데 {k} 영수증은 {rd.month}월 {rd.day}일입니다."
                              + (" (숙박은 마지막 날 전날 밤까지)" if k == "숙박" else ""), None,
                              ["날짜가 맞는 영수증으로 바꾸거나, 일정이 바뀌었다면 사유를 적으세요."]))
    # 숙박비 상한은 1박 기준: 영수증 합계를 숙박일수만큼의 상한과 비교한다(영수증 1장에 여러 박이 들어 있을 수 있음)
    if cap and lodging:
        n = max(nights, 1)
        limit = cap * n
        if lodging > limit:
            out.append(_f("warn", f"숙박비가 상한을 {lodging - limit:,}원 넘습니다",
                          f"{cap_key} 1박 상한 {cap:,}원 × {n}박 = {limit:,}원, 숙박 영수증 합계 {lodging:,}원"
                          f"(1박 평균 {lodging // n:,}원). 초과분은 지급되지 않습니다.",
                          {"reg": "여비 지급 기준", "label": "별표 1", "title": cap_key, "text": f"{cap_key}: 1박 상한 {cap:,}원(실비)", "rel": src}))
        else:
            out.append(_f("ok", "숙박비가 상한 안입니다", f"{cap_key} 1박 상한 {cap:,}원 × {n}박 = {limit:,}원, 숙박 영수증 합계 {lodging:,}원"))
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
    amount = _money(f.get("amount"), "구매금액")
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
                q = _count(f.get("quotes"), "견적서 수")
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
