"""서무비서 지식 계층: 규정·지침·서식을 읽어 업무별 절차 카드를 만든다.

층 구조
- common   : 공통 기준(범정부·상위 기준). 조문 단위 참고 자료로 쓴다.
- org      : 기관 규정·지침·서식. 업무별 절차 카드를 만든다. (관리자 등록)
- personal : 개인 보충 메모. 관련 절차에 '개인 팁'으로 붙는다. (사용자 등록)
같은 내용이 겹치면 기관 규정이 공통 기준보다 우선한다.
"""
import hashlib
import re
from pathlib import Path

from core import regparse
from core.loaders import load_folder
from core.textutil import similarity

LAYERS = {"common": "공통 기준", "org": "기관 규정", "personal": "개인 보충"}

SYN = {
    "출장": ["출장", "여비", "정산", "출장비", "교통비", "숙박", "다녀", "KTX", "기차"],
    "휴가": ["휴가", "연가", "병가", "연차", "쉬", "아파", "진단서", "월차", "반차"],
    "초과근무": ["초과근무", "야근", "시간외", "늦게까지", "주말근무"],
    "물품": ["물품", "구매", "사야", "비품", "소모품", "검수", "납품", "구입"],
    "법인카드": ["법인카드", "카드", "결제", "사용 내역"],
    "행사": ["행사", "개최", "이벤트", "설명회", "축제", "워크숍"],
    "회의": ["회의", "회의록"],
    "문서": ["문서", "접수", "등록"],
}

OBLIGATION = re.compile(r"하여야 한다|받아야 한다|알려야 한다|마쳐야|제출한다|등록한다|요청한다|작성하여야|입력하여야|게시하여야|보고하여야|지급하여야")
DOC_WORDS = re.compile(r"([가-힣A-Za-z()\s]{1,20}?(?:신청서|보고서|계획서|요청서|조서|영수증|진단서|사본|확인서|사유서|명세서|안내문|회의록|목록|결과보고서))")
FORM_REF = re.compile(r"별지\s*제?\s*(\d+)\s*호\s*서식(?:의|에 따른)?\s*([가-힣\s]{0,20}?(?:서|조서|대장))?")
ACTOR = re.compile(r"^([가-힣\s]{1,15}?)(?:은|는)\s")

PROHIBIT = re.compile(r"아니\s*된다|할\s*수\s*없다|금지한다|금지된다")
AFTER_ANCHOR = re.compile(r"마친|종료|끝난|복귀|납품된|받은|끝나|완료|근무한")
BEFORE_ANCHOR = re.compile(r"출발|예정일|희망일|개최|시작")


def file_id(layer, rel):
    return layer + "-" + hashlib.md5(rel.encode("utf-8")).hexdigest()[:8]


def parse_deadline(text):
    """조문 문장에서 기한을 읽는다."""
    m = re.search(r"([가-힣\s]{1,20}?)\s*(\d+)일\s*전까지", text)
    if m:
        anchor = _anchor(m.group(1))
        return {"kind": "before", "days": int(m.group(2)), "anchor": anchor, "text": m.group(0).strip(),
                "event": "start"}
    m = re.search(r"([가-힣\s]{2,25}?)(?:날|때)?\s*부터\s*(\d+)일\s*이내", text) or \
        re.search(r"([가-힣\s]{2,25}?)\s*후\s*(\d+)일\s*이내", text)
    if m:
        anchor = _anchor(m.group(1), after=True)
        return {"kind": "after", "days": int(m.group(2)), "anchor": anchor, "text": m.group(0).strip(),
                "event": "end" if AFTER_ANCHOR.search(anchor) or "후" in m.group(0) else "start"}
    m = re.search(r"(\d+)일\s*이내", text)
    if m:
        return {"kind": "after", "days": int(m.group(1)), "anchor": "", "text": m.group(0), "event": "end"}
    m = re.search(r"당일\s*(\d+)시까지", text)
    if m:
        return {"kind": "sameday", "days": 0, "hour": int(m.group(1)), "anchor": "근무 당일", "text": m.group(0), "event": "start"}
    if re.search(r"다음\s*날까지|익일까지", text):
        return {"kind": "after", "days": 1, "anchor": "다음 날", "text": "다음 날까지", "event": "end"}
    if re.search(r"지체\s*없이|즉시", text):
        return {"kind": "now", "days": 0, "anchor": "", "text": "지체 없이", "event": "start"}
    m = re.search(r"매년\s*(\d+)월", text)
    if m:
        return {"kind": "yearly", "month": int(m.group(1)), "days": 0, "anchor": "", "text": m.group(0), "event": ""}
    m = re.search(r"매월\s*(\d+)일까지", text)
    if m:
        return {"kind": "monthly", "day": int(m.group(1)), "days": 0, "anchor": "", "text": m.group(0), "event": ""}
    return None


def _anchor(s, after=False):
    words = s.strip().split()
    if after:
        for i in range(len(words) - 1, -1, -1):
            if AFTER_ANCHOR.search(words[i]):
                prev = words[i - 1] if i > 0 else ""
                if not prev or prev.endswith(("은", "는")) or "경우" in prev:
                    return words[i]
                return f"{prev} {words[i]}"
        return words[-1] if words else ""
    return " ".join(words[-2:]).strip()


def deadline_label(dl):
    if not dl:
        return ""
    k = dl["kind"]
    if k == "before":
        return f"{dl['anchor']} {dl['days']}일 전까지"
    if k == "after":
        a = dl["anchor"]
        if not a:
            return f"{dl['days']}일 이내"
        if a == "다음 날":
            return "다음 날까지"
        if "후" in dl.get("text", "") and "부터" not in dl.get("text", ""):
            return f"{a} 후 {dl['days']}일 이내"
        return f"{a} 날부터 {dl['days']}일 이내"
    if k == "sameday":
        return f"당일 {dl['hour']}시까지"
    if k == "now":
        return "지체 없이"
    if k == "yearly":
        return f"매년 {dl['month']}월"
    if k == "monthly":
        return f"매월 {dl['day']}일까지"
    return dl.get("text", "")


_PARTICLE_END = ("에", "을", "를", "의", "는", "은", "이", "가", "로", "따른", "대한", "관한", "전까지", "이내에", "에게")


def _clean_doc(raw):
    words = raw.strip().split()
    keep = []
    for w in reversed(words):
        if keep and (w.endswith(_PARTICLE_END) or re.search(r"\d", w) or w in ("및", "또는", "각", "호의", "다음")):
            break
        keep.insert(0, w)
        if len(keep) >= 3:
            break
    return " ".join(keep)


def _actor(sentence):
    toks = sentence.split()
    for i, t in enumerate(toks[:8]):
        if t in ("또는", "그리고") or "경우" in t or t.endswith(("려는", "하는", "있는", "없는", "되는", "따르는")):
            continue
        if t.endswith(("은", "는")) and len(t) >= 2:
            phrase = toks[max(0, i - 3): i] + [t[:-1]]
            return " ".join(phrase)
    return ""


def parse_docs(text):
    docs = []
    for m in DOC_WORDS.finditer(text):
        d = _clean_doc(m.group(1))
        if 2 <= len(d) <= 25 and d not in docs and not d.startswith(("따른", "신청서를")):
            docs.append(d)
    return docs


class Knowledge:
    def __init__(self, root):
        self.root = Path(root)  # data/kb
        for l in LAYERS:
            (self.root / l).mkdir(parents=True, exist_ok=True)
        self.docs = {}       # layer -> [Document]
        self.regs = []       # 규정 단위 [{id, layer, rel, title, reg}]
        self.forms = []      # 서식 [{id, layer, rel, name, no, fields}]
        self.tables = []     # 표(별표·위임전결) [{layer, rel, table}]
        self.tips = []       # 개인 보충 [{id, rel, topic, text, loc}]
        self.common_articles = []

    # ------------------------------------------------------------ 읽기
    def load(self):
        self.regs, self.forms, self.tables, self.tips, self.common_articles = [], [], [], [], []
        for layer in LAYERS:
            docs, _ = load_folder(self.root / layer)
            self.docs[layer] = docs
            for d in docs:
                if d.error:
                    continue
                if self._is_form(d):
                    self.forms.append(self._form(layer, d))
                    continue
                if d.tables and d.ext in (".xlsx", ".xlsm", ".csv"):
                    for t in d.tables:
                        self.tables.append({"layer": layer, "rel": d.relpath, "name": t["name"], "table": t,
                                            "title": d.blocks[0].text if d.blocks else d.name})
                    continue
                if layer == "personal":
                    for b in d.blocks:
                        m = re.match(r"^[-•*]\s*([^:：]{1,15})[:：]\s*(.+)$", b.text)
                        if m:
                            self.tips.append({"id": b.id, "rel": d.relpath, "topic": m.group(1).strip(),
                                              "text": m.group(2).strip(), "loc": b.loc})
                        elif b.text.startswith(("-", "•")):
                            self.tips.append({"id": b.id, "rel": d.relpath, "topic": "", "text": b.text.lstrip("-• "), "loc": b.loc})
                    continue
                reg = regparse.parse([(b.loc, b.text) for b in d.blocks], source=d.relpath)
                if not reg.articles:
                    continue
                rid = file_id(layer, d.relpath)
                self.regs.append({"id": rid, "layer": layer, "rel": d.relpath, "title": reg.title or d.name, "reg": reg})
                if layer == "common":
                    for a in reg.articles:
                        self.common_articles.append({"reg_id": rid, "reg_title": reg.title, "rel": d.relpath, "article": a})
        return self

    def _is_form(self, d):
        return bool(re.search(r"서식|별지|양식", d.relpath)) and d.ext in (".hwpx", ".docx")

    def _form(self, layer, d):
        text = d.text
        fields = []
        for f in re.findall(r"\{\{\s*([^{}]+?)\s*\}\}", text):
            if f not in fields:
                fields.append(f)
        mode = "placeholder"
        if not fields and d.ext == ".hwpx":
            # 자리표시가 없으면 표의 항목 이름(성명·소속·출장지 …) 오른쪽 빈 칸을 채운다
            from forms import label_fields
            try:
                fields = label_fields((self.root / layer / d.relpath).read_bytes())
            except OSError:
                fields = []
            mode = "label" if fields else "none"
        m = re.search(r"별지\s*(\d+)", d.name)
        name = re.sub(r"^별지\s*\d+[_\-. ]*", "", Path(d.name).stem)
        return {"id": file_id(layer, d.relpath), "layer": layer, "rel": d.relpath, "name": name,
                "no": int(m.group(1)) if m else None, "fields": fields, "ext": d.ext, "fill_mode": mode}

    # ------------------------------------------------------------ 절차 카드 만들기
    def extract_procedures(self):
        cards = []
        for r in self.regs:
            if r["layer"] != "org":
                continue
            reg = r["reg"]
            groups = {}
            for a in reg.articles:
                groups.setdefault(a.chapter or "", []).append(a)
            chaptered = len([c for c in groups if c]) > 0
            for chapter, arts in groups.items():
                steps = []
                for a in arts:
                    steps.extend(self._steps_from_article(r, a))
                if not steps:
                    continue
                base = re.sub(r"^제\d+장\s*", "", chapter).strip() if chaptered and chapter else ""
                title = re.sub(r"^\S*(?:공단|공공기관)\s*", "", reg.title)
                name = base or re.sub(r"(규정|지침|기준)$", "", title).strip()
                if not name.endswith(("절차", "업무")):
                    name = name + " 절차"
                if base and base not in title:
                    name = f"{name} ({title})"
                refs = [a for a in reg.articles if not any(s["basis"]["key"] == a.key for s in steps)
                        and a.title not in ("목적",)]
                card = {
                    "id": "p-" + hashlib.md5((r["rel"] + chapter).encode("utf-8")).hexdigest()[:8],
                    "name": name, "regulation": reg.title, "reg_id": r["id"], "rel": r["rel"], "layer": "org",
                    "category": self._category(name + " " + reg.title),
                    "keywords": self._keywords(name, reg, steps),
                    "steps": steps,
                    "references": [{"key": a.key, "label": a.label, "title": a.title, "text": a.to_text()} for a in refs][:6],
                    "edited": False,
                }
                cards.append(card)
        return cards

    def _steps_from_article(self, r, a):
        steps = []
        if a.deleted:
            return steps
        main = None
        for p in a.paras:
            sentence = p.text
            items = [it.text for it in p.items]
            full = sentence + " " + " ".join(items)
            if PROHIBIT.search(sentence.split("다만")[0]):
                # 금지 조항은 절차 단계가 아니라 점검 규칙(rules.py)으로 쓴다
                if main is not None:
                    main["notes"].append(sentence)
                continue
            if not OBLIGATION.search(full) and not parse_deadline(full):
                if main is not None and sentence:
                    main["notes"].append(sentence)
                continue
            dl = parse_deadline(sentence)
            forms = [{"no": int(m.group(1)), "name": (m.group(2) or "").strip()} for m in FORM_REF.finditer(sentence)]
            docs = parse_docs(sentence) + [i.strip() for i in items]
            docs = [d for d in docs if not any(f["name"] and (f["name"] in d or d in f["name"]) for f in forms)]
            actor = _actor(sentence)
            approver = ""
            m = re.search(r"([가-힣]{1,10}(?:장|팀))(?:의)?\s*(?:승인|결재)", sentence)
            if m:
                approver = m.group(1)
            is_exception = bool(re.search(r"경우에는|경우\s", sentence)) and main is not None
            step = {
                "title": a.title or a.label,
                "action": sentence,
                "actor": actor,
                "deadline": dl,
                "docs": docs,
                "forms": forms,
                "approver": approver,
                "notes": [],
                "basis": {"reg_id": r["id"], "reg": r["reg"].title, "key": a.key, "label": a.label,
                          "para": p.no, "text": a.to_text(), "rel": r["rel"], "loc": a.loc},
            }
            if is_exception and main is not None and (not actor or actor == main["actor"]):
                main["notes"].append(("[예외] " if "경우" in sentence else "") + sentence)
                continue
            if main is not None:
                if actor and actor != main["actor"] and re.search(r"(팀|부서|과|실)$", actor):
                    step["title"] = f"{a.title} - {actor}"
                elif p.no:
                    step["title"] = f"{a.title} ({regparse.CIRCLED[p.no - 1]})"
            steps.append(step)
            if main is None:
                main = step
        return steps

    def _category(self, text):
        for k in ("법인카드", "출장", "물품", "휴가", "초과근무", "행사", "회의", "문서"):
            if any(s in text for s in SYN[k][:3]):
                return k
        return ""

    def _keywords(self, name, reg, steps):
        kws = []
        for w in re.findall(r"[가-힣]{2,}", name + " " + reg.title + " " + " ".join(s["title"] for s in steps)):
            if w not in kws and w not in ("절차", "공단", "기관", "규정", "지침", "공공기관"):
                kws.append(w)
        for a in reg.articles:
            if a.title == "정의":
                for p in a.paras:
                    for it in p.items:
                        m = re.match(r'"([^"]+)"', it.text)
                        if m and m.group(1) not in kws:
                            kws.append(m.group(1))
        cat = self._category(name + reg.title)
        for s in SYN.get(cat, []):
            if s not in kws:
                kws.append(s)
        return kws

    # ------------------------------------------------------------ 보조
    def form_by_no(self, no):
        for f in self.forms:
            if f["no"] == no and f["layer"] == "org":
                return f
        return None

    def table_find(self, rx):
        for t in self.tables:
            if re.search(rx, t["name"] + " " + t["rel"] + " " + t.get("title", "")):
                return t
        return None

    def tips_for(self, card):
        out = []
        for tip in self.tips:
            topic = tip["topic"]
            hay = card["name"] + " " + " ".join(card["keywords"]) + " " + " ".join(s["title"] for s in card["steps"])
            tw = re.findall(r"[가-힣]{2,}", topic)
            if (tw and tw[0] in hay) or \
               (not topic and any(k in tip["text"] for k in card["keywords"][:6])):
                out.append(tip)
        return out

    def common_overrides(self, card):
        """기관 절차 단계와 비슷한 공통 기준 조문을 찾아 차이를 알려준다."""
        notes = []
        for st in card["steps"]:
            for ca in self.common_articles:
                a = ca["article"]
                if similarity(a.title, st["title"]) >= 0.5 or (a.title and a.title in st["title"]):
                    cdl = parse_deadline(a.plain)
                    odl = st.get("deadline")
                    diff = ""
                    if cdl and odl and cdl.get("days") != odl.get("days"):
                        diff = f"공통 기준 {cdl['days']}일 / 기관 규정 {odl['days']}일 → 기관 규정 우선 적용"
                    if diff or similarity(a.title, st["title"]) >= 0.8:
                        notes.append({"step": st["title"], "common": f"{ca['reg_title']} {a.label}({a.title})",
                                      "text": a.to_text(), "diff": diff})
        return notes
