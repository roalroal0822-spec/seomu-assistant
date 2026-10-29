"""정확도 성적표: 모델·인터넷 없이 같은 결과가 나오는 시험을 돌려 검증결과/에 남긴다.

  python tools/eval.py [--note "무엇을 고친 뒤 쟀는지"]  → 검증결과/scorecard.json, 검증결과/정확도_성적표.md

시험 다섯 가지 (기준일은 2026-10-15로 고정, 함께 넣은 모의 규정으로 시험)
  1. 상황 안내: 개발자가 미리 적은 상황 문장에 대해 알맞은 절차를 고르고 단계별 기한 날짜를 맞게 계산하는지 본다.
     문장은 두 묶음이다. A(30개)는 앱을 고칠 때 써도 되는 묶음, B(30개)는 앱을 고칠 때 보지 않고 한 번만 잰 묶음이다.
     정답 날짜는 모의 규정 조문(출장 신청 3일 전, 결과 보고 5일, 여비 정산 7일 등)으로 손으로 계산해 적었다.
  2. 업무 시뮬레이터 판정: "…하면 어떻게 돼?" 문장 40개(A 20, B 20)에 대해 가능·주의·불가·판단 근거 없음을 맞히는지 본다.
  3. 사전점검: 여비 정산 120건·물품 구매 80건을 무작위로 만들고(시드 고정) 기한 초과·서류 누락·영수증 날짜·숙박비 상한·
     견적서 수·결재 전 구매를 몰래 심어, 앱이 심은 것만 정확히 잡는지(놓침·오탐 모두 실패) 본다.
  4. 모델 감시: 가짜 모델이 지어낸 숫자를 안내 문장에 넣으면 버리는지, 후보 밖 절차·잘못된 날짜를 내면 무시하는지,
     바른 문장은 살리는지 본다.
  5. 개인정보 가림: 개인정보가 든 문장 30개는 가리고, 앱이 실제로 만든 안내·판정 문장은 건드리지 않는지 본다.
문항과 정답은 이 파일에 있고, 틀린 문항은 고치지 않고 성적표에 그대로 적는다.
"""
import json
import os
import random
import re
import sys
import tempfile
import time
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ["APP_DATA_DIR"] = tempfile.mkdtemp(prefix="seomu-eval-")
os.environ["LLM_PROVIDER"] = "none"

import app  # noqa: E402
import assistant  # noqa: E402
import rules as R  # noqa: E402
from core.pii import mask  # noqa: E402

TODAY = date(2026, 10, 15)


def D(s):
    return f"2026-{s}" if len(s) == 5 else s


# ------------------------------------------------------------ 1. 상황 안내
# (상황 문장, 절차 이름에 든 낱말, {단계 이름에 든 낱말: 기한 날짜}) · 회의록은 공통 기준 답(type common)

SITU_A = [
    ("어제 부산에서 1박 2일 출장 다녀왔어요", "출장", {"결과 보고": "10-19", "여비 정산": "10-21"}),
    ("10월 20일부터 22일까지 대전 출장 가요", "출장", {"출장 신청": "10-17"}),
    ("11월 3일에 광주로 당일 출장 갈 예정이에요", "출장", {"출장 신청": "10-31"}),
    ("지난주 금요일에 서울 출장 다녀왔는데 정산 언제까지예요?", "출장", {"결과 보고": "10-14", "여비 정산": "10-16"}),
    ("10월 12일에 출장 끝났어요. 결과 보고서 언제까지?", "출장", {"결과 보고": "10-17"}),
    ("11월 10일에 노트북 받고 싶은데 구매 요청 언제까지 해요?", "물품", {"구매 요청": "10-27"}),
    ("어제 물품이 납품됐어요", "물품", {"검수": "10-17"}),
    ("다음 주 금요일에 연가 쓰려고요", "휴가", {"연가 신청": "10-22"}),
    ("10월 30일에 연차 쓸게요", "휴가", {"연가 신청": "10-29"}),
    ("오늘 야근해야 하는데", "초과근무", {"초과근무 신청": "10-15"}),
    ("11월 20일에 주민 설명회 행사 열려고 해", "행사", {"행사 계획": "10-21", "물품 준비": "10-30", "행사 안내": "11-06"}),
    ("어제 행사 끝났어요", "행사", {"결과 보고": "10-21"}),
    ("법인카드로 10월 13일에 다과 샀어요", "법인카드", {"사용 내역": "10-18"}),
    ("오늘 회의 끝났는데 회의록 언제까지?", "회의록", {"회의록": "10-18"}),
    ("병가 쓰고 오늘 복귀했어요", "휴가", {"병가 (②)": "10-18"}),
    ("12월 1일에 제주 출장 가는데 뭐 해야 해?", "출장", {"출장 신청": "11-28"}),
    ("10월 16일부터 2박 3일로 울산 출장 가요", "출장", {"출장 신청": "10-13"}),
    ("모레 연가 쓰려고 하는데", "휴가", {"연가 신청": "10-16"}),
    ("프린터 토너 11월 5일까지 필요해요", "물품", {"구매 요청": "10-22"}),
    ("10월 25일 체육대회 행사 준비해야 해요", "행사", {"행사 계획": "09-25", "물품 준비": "10-04", "행사 안내": "10-11"}),
    ("법인카드 어제 썼어요", "법인카드", {"사용 내역": "10-19"}),
    ("10월 13일~14일 인천 출장 다녀왔어요", "출장", {"결과 보고": "10-19", "여비 정산": "10-21"}),
    ("내일 연가 쓸 수 있어요?", "휴가", {"연가 신청": "10-15"}),
    ("다음 주 월요일에 세종 출장 갑니다", "출장", {"출장 신청": "10-16"}),
    ("의자 30개 12월 1일에 받고 싶어요", "물품", {"구매 요청": "11-17"}),
    ("11월 7일 행사 끝나면 결과보고 언제까지?", "행사", {"결과 보고": "11-14"}),
    ("어제 회의 마쳤어요 회의록 써야 하나요", "회의록", {"회의록": "10-17"}),
    ("초과근무 실적은 언제까지 입력해?", "초과근무", {}),
    ("물품 납품이 10월 14일에 됐는데 검수 언제까지 해요", "물품", {"검수": "10-17"}),
    ("법인카드 사용 내역서 제출 기한 알려줘", "법인카드", {}),
]

SITU_B = [
    ("그저께 대구 출장에서 돌아왔습니다", "출장", {"결과 보고": "10-18", "여비 정산": "10-20"}),
    ("10월 27일 부산 출장 예정입니다", "출장", {"출장 신청": "10-24"}),
    ("11월 4일부터 6일까지 강릉 출장이 잡혔어", "출장", {"출장 신청": "11-01"}),
    ("출장 갔다 왔는데 영수증 정리 어떻게 해?", "출장", {}),
    ("10월 10일에 출장 마쳤는데 여비 정산 아직 안 했어요", "출장", {"여비 정산": "10-17"}),
    ("모니터 2대 11월 14일까지 필요합니다", "물품", {"구매 요청": "10-31"}),
    ("오늘 물품 납품 받았어요", "물품", {"검수": "10-18"}),
    ("10월 21일 하루 연가 내려고 합니다", "휴가", {"연가 신청": "10-20"}),
    ("다음 주 화요일 반차 쓸게요", "휴가", {"연가 신청": "10-19"}),
    ("오늘 저녁에 초과근무 할 것 같아요", "초과근무", {"초과근무 신청": "10-15"}),
    ("12월 10일 송년 행사 개최 예정", "행사", {"행사 계획": "11-10", "물품 준비": "11-19", "행사 안내": "11-26"}),
    ("10월 11일에 행사를 마쳤습니다", "행사", {"결과 보고": "10-18"}),
    ("법인카드로 그제 회의 다과비 결제했어", "법인카드", {"사용 내역": "10-18"}),
    ("오늘 오전 회의 끝났어요", "회의록", {"회의록": "10-18"}),
    ("병가 3일 쓰고 어제 복귀했어요", "휴가", {"병가 (②)": "10-17"}),
    ("내일 서울로 당일 출장 가요", "출장", {"출장 신청": "10-13"}),
    ("11월 2일부터 3박 4일 제주 출장", "출장", {"출장 신청": "10-30"}),
    ("다음 주 수요일 연가", "휴가", {"연가 신청": "10-20"}),
    ("복사용지 11월 20일까지 구매하고 싶어요", "물품", {"구매 요청": "11-06"}),
    ("11월 15일에 주민 공청회 행사가 있어요", "행사", {"행사 계획": "10-16", "물품 준비": "10-25", "행사 안내": "11-01"}),
    ("법인카드 10월 14일 사용분 내역 언제 내요?", "법인카드", {"사용 내역": "10-19"}),
    ("10월 8일부터 9일까지 광주 출장 다녀왔어요", "출장", {"결과 보고": "10-14", "여비 정산": "10-16"}),
    ("업무용 태블릿 300만원 구매 예정", "물품", {}),
    ("다음 주 목요일에 대전 출장", "출장", {"출장 신청": "10-19"}),
    ("행사 끝나고 결과보고 기한이 어떻게 돼?", "행사", {}),
    ("지난 월요일 회의 회의록 아직 못 썼어요", "회의록", {"회의록": "10-15"}),
    ("야근 신청 언제까지야?", "초과근무", {}),
    ("어제 납품된 책상 검수해야 해요", "물품", {"검수": "10-17"}),
    ("법인카드로 개인 물건 샀어요", "법인카드", {}),
    ("11월 9일 연가 신청하려고", "휴가", {"연가 신청": "11-08"}),
]


def kb():
    return app.kb(), app.STATE["cards"]


def respond(text, llm=None):
    k, cards = kb()
    return assistant.respond(k, cards, text, TODAY, llm)


def check_situation(text, card_kw, dues):
    r = respond(text)
    if card_kw == "회의록":
        if r["type"] != "common":
            return f"공통 기준(회의록) 답이 아님: {r['type']} {r.get('card', {}).get('name', '')}"
        got = r.get("due")
        want = D(dues["회의록"])
        return None if got == want else f"회의록 기한 {got} (정답 {want})"
    if r["type"] != "procedure":
        return f"절차를 찾지 못함({r['type']})"
    if card_kw not in r["card"]["name"]:
        return f"다른 절차: {r['card']['name']}"
    errs = []
    for kw, want in dues.items():
        st = next((s for s in r["steps"] if (s["title"] == kw if "(" in kw else kw in s["title"] and " - " not in s["title"] and "(" not in s["title"])), None)
        if not st:
            errs.append(f"'{kw}' 단계 없음")
        elif st["due"] != D(want):
            errs.append(f"{st['title']} {st['due']} (정답 {D(want)})")
    return "; ".join(errs) or None


def eval_situations():
    out = []
    for name, cases, desc in (("상황 안내 A(개선에 쓴 문장)", SITU_A, "알맞은 절차를 고르고 단계별 기한 날짜를 정답(손 계산)과 똑같이 낸 비율"),
                              ("상황 안내 B(개선에 쓰지 않은 문장)", SITU_B, "A와 다른 표현의 상황 문장. 앱을 고칠 때 보지 않고 한 번만 잼")):
        ok, fails = 0, []
        for text, kw, dues in cases:
            e = check_situation(text, kw, dues)
            if e:
                fails.append(f"'{text}' → {e}")
            else:
                ok += 1
        out.append({"name": name, "passed": ok, "total": len(cases), "failures": fails, "desc": desc})
    return out


# ------------------------------------------------------------ 2. 업무 시뮬레이터 판정

SIM_A = [
    ("출장비 정산을 10일 뒤에 제출하면 어떻게 돼?", {"bad"}),
    ("출장비 정산을 5일 뒤에 제출하면?", {"ok"}),
    ("법인카드로 개인 물품을 같이 결제했다면?", {"bad"}),
    ("법인카드로 주점에서 결제해도 돼?", {"bad"}),
    ("결재 받기 전에 물품 먼저 사도 돼?", {"bad"}),
    ("점심 메뉴 추천해줘", {"gray"}),
    ("300만원 노트북 견적서 1개만 받아도 돼?", {"bad"}),
    ("150만원 물품 견적서 1개만 받아도 돼?", {"ok"}),
    ("출장 이틀 전에 신청해도 돼?", {"warn", "bad"}),
    ("출장 5일 전에 신청하면 돼?", {"ok"}),
    ("연가 당일 아침에 신청해도 돼?", {"warn", "bad"}),
    ("초과근무 17시에 신청해도 돼?", {"warn", "bad"}),
    ("초과근무 15시에 신청하면?", {"ok"}),
    ("행사 안내를 10일 전에 올려도 돼?", {"warn", "bad"}),
    ("행사 계획을 40일 전에 세우면?", {"ok"}),
    ("출장 결과 보고를 7일 뒤에 하면?", {"bad"}),
    ("법인카드 사용 내역서를 3일 뒤에 내면?", {"ok"}),
    ("법인카드 내역서 8일 뒤에 내도 돼?", {"bad"}),
    ("회의록을 5일 뒤에 써도 돼?", {"bad"}),
    ("주차 위반 과태료는 누가 내?", {"gray"}),
]

SIM_B = [
    ("정산 서류를 출장 끝나고 2주 뒤에 내면?", {"bad"}),
    ("출장 마치고 일주일 안에 정산하면 돼?", {"ok"}),
    ("개인 커피를 법인카드로 결제해도 되나요?", {"bad"}),
    ("유흥업소에서 법인카드 써도 돼?", {"bad"}),
    ("급해서 결재 전에 물건을 먼저 샀어", {"bad", "warn"}),
    ("구내식당 메뉴 알려줘", {"gray"}),
    ("500만원짜리 장비 견적서 하나로 가능해?", {"bad"}),
    ("견적서 2개 받아서 250만원 물품 사면?", {"ok"}),
    ("출장 전날 신청해도 돼?", {"warn", "bad"}),
    ("출장 일주일 전에 신청하면?", {"ok"}),
    ("연가를 사흘 전에 신청하면?", {"ok"}),
    ("초과근무를 다음날 신청해도 돼?", {"warn", "bad"}),
    ("행사 안내문을 3주 전에 게시하면?", {"ok"}),
    ("행사 결과보고를 열흘 뒤에 하면?", {"bad"}),
    ("검수를 5일 뒤에 하면?", {"bad"}),
    ("물품 구매 요청을 희망일 7일 전에 하면?", {"warn", "bad"}),
    ("법인카드 사용 내역을 4일 뒤에 제출하면?", {"ok"}),
    ("회의록을 이틀 뒤에 작성해도 돼?", {"ok"}),
    ("출장 결과 보고를 3일 뒤에 하면?", {"ok"}),
    ("사무실 화분 물 주기 당번은?", {"gray"}),
]
VNAME = R.VERDICT


def simulate(text):
    k, cards = kb()
    return R.simulate(k, cards, R.extract_rules(k, cards), text, TODAY)


def eval_simulator():
    out = []
    for name, cases, desc in (("시뮬레이터 판정 A(개선에 쓴 문장)", SIM_A, "가능·주의·불가·판단 근거 없음을 정답과 같게 판정하고, 가능·불가에는 근거 조문을 붙인 비율"),
                              ("시뮬레이터 판정 B(개선에 쓰지 않은 문장)", SIM_B, "A와 다른 표현. 앱을 고칠 때 보지 않고 한 번만 잼")):
        ok, fails = 0, []
        for text, want in cases:
            r = simulate(text)
            based = r["verdict"] == "gray" or any(f.get("basis") for f in r["findings"])
            if r["verdict"] in want and based:
                ok += 1
            else:
                fails.append(f"'{text}' → {VNAME[r['verdict']]}" + ("" if based else "(근거 없음)") + f" (정답 {'/'.join(VNAME[w] for w in sorted(want))})")
        out.append({"name": name, "passed": ok, "total": len(cases), "failures": fails, "desc": desc})
    return out


# ------------------------------------------------------------ 3. 사전점검(무작위, 시드 고정)

CAPS = {"서울": 100000, "부산": 80000, "강릉": 70000}
TAGS = [("출장 신청이 기한보다", "신청 늦음"), ("결과 보고가 기한", "결과 보고 늦음"), ("여비 정산 기한을", "정산 늦음"),
        ("빠졌습니다: 출장 결과 보고서", "결과 보고서 누락"), ("빠졌습니다: 숙박비", "숙박 영수증 누락"), ("빠졌습니다: 운임", "운임 영수증 누락"),
        ("영수증 날짜가", "영수증 날짜"), ("숙박비가 상한", "숙박비 상한"),
        ("구매 요청이 기한보다", "구매 요청 늦음"), ("견적서", "견적서 부족"), ("결재 전에 구매", "결재 전 구매"), ("아직 결재를", "결재 전")]
WATCH = {"신청 늦음", "결과 보고 늦음", "정산 늦음", "결과 보고서 누락", "숙박 영수증 누락", "운임 영수증 누락", "영수증 날짜", "숙박비 상한",
         "구매 요청 늦음", "견적서 부족", "결재 전 구매", "결재 전"}


def tags_of(r):
    got = set()
    for f in r["findings"]:
        if f["level"] == "ok":
            continue
        for key, tag in TAGS:
            if key in f["title"]:
                got.add(tag)
                break
    return got & WATCH


def gen_travel(rnd):
    start = date(2026, 9, 1) + timedelta(days=rnd.randint(0, 40))
    nights = rnd.choice([0, 0, 1, 1, 2, 3])
    end = start + timedelta(days=nights)
    dest = rnd.choice(list(CAPS))
    want = set()
    ap = start - timedelta(days=rnd.randint(0, 6))
    if ap > start - timedelta(days=3):
        want.add("신청 늦음")
    rp = end + timedelta(days=rnd.randint(0, 9))
    if rp > end + timedelta(days=5):
        want.add("결과 보고 늦음")
    sub = end + timedelta(days=rnd.randint(0, 12))
    if sub > end + timedelta(days=7):
        want.add("정산 늦음")
    att = []
    if rnd.random() < 0.8:
        att.append("출장 결과 보고서 사본")
    else:
        want.add("결과 보고서 누락")
    receipts = []
    transport = rnd.choice(["KTX", "KTX", "자가용"])
    if transport == "자가용":
        if rnd.random() < 0.7:
            receipts.append({"kind": "운임", "date": start.isoformat(), "amount": rnd.randint(2, 9) * 10000})
        else:
            want.add("운임 영수증 누락")
    elif rnd.random() < 0.6:
        receipts.append({"kind": "운임", "date": start.isoformat(), "amount": rnd.randint(3, 12) * 10000})
    if nights:
        if rnd.random() < 0.85:
            per = rnd.choice([50000, 65000, 70000, 80000, 90000, 110000, 120000])
            rd = start + timedelta(days=rnd.randint(0, nights - 1))
            if rnd.random() < 0.15:
                rd = end + timedelta(days=rnd.randint(0, 2)) if rnd.random() < 0.5 else start - timedelta(days=rnd.randint(1, 3))
                want.add("영수증 날짜")
            receipts.append({"kind": "숙박", "date": rd.isoformat(), "amount": per * nights})
            if per > CAPS[dest]:
                want.add("숙박비 상한")
        else:
            want.add("숙박 영수증 누락")
    for rc in receipts:
        if rc["kind"] == "운임" and rnd.random() < 0.1:
            rc["date"] = (end + timedelta(days=rnd.randint(1, 3))).isoformat()
            want.add("영수증 날짜")
    f = {"start": start.isoformat(), "end": end.isoformat(), "destination": dest, "apply_date": ap.isoformat(),
         "report_date": rp.isoformat(), "submit_date": sub.isoformat(), "attachments": att, "receipts": receipts, "transport": transport}
    return f, want


def gen_goods(rnd):
    req = date(2026, 10, 1) + timedelta(days=rnd.randint(0, 20))
    wish = req + timedelta(days=rnd.randint(3, 30))
    amount = rnd.choice([300000, 800000, 1500000, 2000000, 2500000, 4000000, 9000000, 25000000])
    quotes = rnd.randint(0, 3)
    approved = rnd.random() < 0.6
    purchased = rnd.random() < 0.3
    want = set()
    if req > wish - timedelta(days=14):
        want.add("구매 요청 늦음")
    if amount > 2000000 and quotes < 2:
        want.add("견적서 부족")
    if purchased and not approved:
        want.add("결재 전 구매")
    elif not approved:
        want.add("결재 전")
    f = {"item": "업무용 물품", "amount": str(amount), "quotes": str(quotes), "request_date": req.isoformat(), "wish_date": wish.isoformat(),
         "approved": approved, "purchased": purchased}
    return f, want


def eval_precheck():
    rnd = random.Random(20261015)
    k, cards = kb()
    rules = R.extract_rules(k, cards)
    out = []
    for kind, n, gen, name, desc in (("travel", 120, gen_travel, "사전점검: 여비 정산", "무작위 신청 120건에 심은 기한 초과·서류 누락·영수증 날짜·숙박비 상한을 빠짐없이, 심지 않은 것은 잡지 않고 찾은 비율"),
                                     ("goods", 80, gen_goods, "사전점검: 물품 구매", "무작위 요청 80건에 심은 요청 기한·견적서 수(200만원 초과 2부)·결재 전 구매를 정확히 찾은 비율")):
        ok, fails = 0, []
        for i in range(n):
            f, want = gen(rnd)
            r = R.precheck(k, cards, rules, kind, json.loads(json.dumps(f)), TODAY)
            got = tags_of(r)
            if got == want:
                ok += 1
            else:
                miss, extra = want - got, got - want
                fails.append(f"#{i + 1} " + (f"놓침 {', '.join(sorted(miss))} " if miss else "") + (f"오탐 {', '.join(sorted(extra))}" if extra else ""))
        out.append({"name": name, "passed": ok, "total": n, "failures": fails, "desc": desc})
    return out


# ------------------------------------------------------------ 4. 모델 감시(가짜 모델)

class FakeLLM:
    enabled = True
    label = "가짜 모델"

    def __init__(self, make):
        self.make = make

    def json(self, system, user):
        ids = re.findall(r"id=(\S+)", user)
        return self.make(ids)


def eval_model_guard():
    rnd = random.Random(7)
    texts = [t for t, kw, _ in SITU_A if kw != "회의록"]
    known_digits = lambda t: set(re.findall(r"\d+", t)) | {"2026", "10", "15"}
    blocked, n, fails = 0, 0, []
    for i in range(100):
        text = texts[i % len(texts)]
        x = rnd.choice([y for y in range(4, 99) if str(y) not in known_digits(text)])
        reply = rnd.choice([f"정산 기한은 {x}일 이내입니다.", f"{x}일 전까지 신청하시면 됩니다.", f"최대 {x}만원까지 지급됩니다.",
                            f"11월 {x % 28 + 1}일까지 처리하세요." if str(x % 28 + 1) not in known_digits(text) else f"{x}일 안에 하세요."])
        r = respond(text, FakeLLM(lambda ids, reply=reply: {"procedure_id": ids[0] if ids else "", "reply": reply}))
        n += 1
        if reply not in r["text"]:
            blocked += 1
        else:
            fails.append(f"'{text}' + 모델 문장 '{reply}' 이 그대로 나감")
    # 후보 밖 절차·형식이 틀린 날짜는 무시: 모델 없이 낸 결과와 절차·단계 날짜가 같아야 한다
    ign, ign_fail = 0, []
    for text in texts:
        base = respond(text)
        r = respond(text, FakeLLM(lambda ids: {"procedure_id": "p-없는절차", "date": "2026/13/45", "end_date": "어제", "reply": ""}))
        same = r["type"] == base["type"] and r.get("card", {}).get("id") == base.get("card", {}).get("id") \
            and [s["due"] for s in r.get("steps", [])] == [s["due"] for s in base.get("steps", [])]
        if same:
            ign += 1
        else:
            ign_fail.append(f"'{text}' → 모델 값 때문에 절차·날짜가 바뀜")
    ign_total = len(texts)
    # 바른 문장(상황에 있는 숫자만 쓰거나 숫자 없음)은 살림
    good, good_fail = 0, []
    for text in texts[:30]:
        nums = re.findall(r"\d+", text)
        reply = f"말씀하신 {nums[0]}일 일정 기준으로 안내드립니다." if nums else "말씀하신 상황에 맞는 절차를 안내드립니다."
        r = respond(text, FakeLLM(lambda ids, reply=reply: {"procedure_id": ids[0] if ids else "", "reply": reply}))
        if reply in r["text"]:
            good += 1
        else:
            good_fail.append(f"'{text}' + '{reply}' 를 잘못 버림")
    return [{"name": "지어낸 숫자 차단", "passed": blocked, "total": n, "failures": fails,
             "desc": "가짜 모델이 안내 문장에 상황·규정에 없는 숫자(기한·금액)를 넣었을 때 그 문장을 버린 횟수"},
            {"name": "후보 밖 절차·틀린 날짜 무시", "passed": ign, "total": ign_total, "failures": ign_fail,
             "desc": "가짜 모델이 없는 절차 id와 형식이 틀린 날짜를 냈을 때 절차·단계 날짜가 모델 없이 낸 결과와 같은 비율"},
            {"name": "바른 문장은 살림", "passed": good, "total": len(texts[:30]), "failures": good_fail,
             "desc": "상황에 있는 숫자만 쓰거나 숫자가 없는 모델 문장을 잘못 버리지 않은 비율"}]


# ------------------------------------------------------------ 5. 개인정보 가림

PII_POS = ["담당 김○○ 010-2345-6789로 연락 바람", "주민번호 850315-2345678 확인", "계좌 110-234-567890 입금", "메일 manager@agency.go.kr",
           "카드 4321-8765-1234-5678 결제", "사무실 02-345-6789", "지역번호 031-456-7890", "외국인등록번호 900101-5123456",
           "여권번호 M12345678 사본", "휴대폰 01098765432", "010.1111.2222 문자", "국민은행 123456-12-345678", "신한 110-12-345678",
           "정산 담당 042-123-4567", "개인 메일 abc.def@example.com 회신", "주민 9001011234567", "농협 301-1234-5678-91",
           "번호 010 3333 4444", "팩스 02-777-8888", "주민등록 760505 1234567", "a_b@test.co.kr로 보내 줘", "070-1234-5678 인터넷전화",
           "카드번호 1111 2222 3333 4444", "우리 1002-345-678901", "051-234-5678 부산", "연락 011-234-5678", "여권 S98765432",
           "메일주소: hong.gildong@agency.kr", "공단 담당 053-230-1234", "계좌 3333-01-1234567"]


def eval_pii():
    ok_pos, pos_fail = 0, []
    for t in PII_POS:
        out, kinds = mask(t)
        if kinds and "@" not in out and not re.search(r"\d{3,}", out.replace(" ", "")):
            ok_pos += 1
        else:
            pos_fail.append(f"'{t}' → '{out}'")
    neg = set()
    for text, _, _ in SITU_A + SITU_B:
        neg.add(text)
        r = respond(text)
        neg.update(x for x in r["text"].split("\n") if x)
        for s in r.get("steps", []):
            neg.update([s["title"], s.get("action", ""), s.get("deadline_label", "") or ""])
    for text, _ in SIM_A + SIM_B:
        neg.add(text)
        for f in simulate(text)["findings"]:
            neg.update([f["title"], f.get("detail", "")] + list(f.get("fix", [])))
    neg.discard("")
    neg = sorted(neg)
    ok_neg, neg_fail = 0, []
    for t in neg:
        _, kinds = mask(t)
        if not kinds:
            ok_neg += 1
        else:
            neg_fail.append(f"잘못 가림({', '.join(kinds)}): {t[:60]}")
    return [{"name": "개인정보 가림", "passed": ok_pos, "total": len(PII_POS), "failures": pos_fail,
             "desc": "주민번호·전화·계좌·카드·이메일·여권이 든 문장 30개를 가린 비율"},
            {"name": "개인정보 오탐 없음", "passed": ok_neg, "total": len(neg), "failures": neg_fail,
             "desc": "앱이 실제로 만든 안내·단계·판정 문장(날짜·금액·조문 번호 포함)을 잘못 가리지 않은 비율"}]


def main():
    import argparse
    ap = argparse.ArgumentParser(description="정확도 성적표 만들기")
    ap.add_argument("--note", default="", help="측정 이력에 남길 한 줄(무엇을 고친 뒤 쟀는지)")
    note = ap.parse_args().note
    t0 = time.time()
    areas = eval_situations() + eval_simulator() + eval_precheck() + eval_model_guard() + eval_pii()
    out = ROOT / "검증결과"
    out.mkdir(exist_ok=True)
    prev = None
    try:
        prev = json.loads((out / "scorecard.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        pass
    summary = ", ".join(f"{a['name']} {a['passed']}/{a['total']}" for a in areas)
    hist = (prev or {}).get("history", [])
    if not hist or hist[-1]["summary"] != summary or note:
        hist.append({"date": date.today().isoformat(), "summary": summary, "note": note})
    sc = {"app": "서무비서", "date": date.today().isoformat(), "command": "python tools/eval.py",
          "author": "개발자 작성(블라인드 아님) · 정답 날짜는 모의 규정 조문으로 손 계산 · 무작위 자료는 시드 고정", "seconds": round(time.time() - t0, 1),
          "areas": areas, "history": hist[-10:]}
    (out / "scorecard.json").write_text(json.dumps(sc, ensure_ascii=False, indent=1), encoding="utf-8")
    md = ["# 정확도 성적표 (서무비서)", "", f"- 측정일: {sc['date']} · 재현: `{sc['command']}` (모델·인터넷 불필요, {sc['seconds']}초, 기준일 {TODAY} 고정)",
          f"- 문항: {sc['author']}", "", "| 시험 | 결과 | 무엇을 쟀나 |", "|---|---|---|"]
    for a in areas:
        pct = f" ({a['passed'] / a['total'] * 100:.1f}%)" if a["total"] else ""
        md.append(f"| {a['name']} | {a['passed']}/{a['total']}{pct} | {a['desc']} |")
    md += ["", "## 틀린 문항 (고치지 않고 공개)", ""]
    fails = [(a["name"], f) for a in areas for f in a["failures"]]
    md += [f"- [{n}] {f}" for n, f in fails] or ["- 없음"]
    md += ["", "## 측정 이력", ""] + [f"- {i}차 {h['date']}" + (f" ({h['note']})" if h.get("note") else "") + f": {h['summary']}" for i, h in enumerate(hist[-10:], 1)]
    (out / "정확도_성적표.md").write_text("\n".join(md) + "\n", encoding="utf-8")
    print("\n".join(md[:8 + len(areas)]))
    print(f"\n틀린 문항 {len(fails)}건 · 결과: {out / '정확도_성적표.md'}")


if __name__ == "__main__":
    main()
