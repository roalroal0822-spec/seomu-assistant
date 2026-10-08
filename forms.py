"""서식 초안 채우기: 서식 파일에 값을 넣어 새 파일로 만든다(원본 서식은 그대로).

1) {{항목}} 자리표시가 있으면 그 자리에 넣는다(hwpx·docx).
2) 자리표시가 하나도 없는 한글(hwpx) 서식이면 표에서 '성명·소속·직급·출장지·기간' 같은
   항목 이름 칸을 찾아 바로 오른쪽 빈 칸에 넣는다. 찾지 못한 항목은 비워 둔다.
"""
import io
import re
import zipfile
from datetime import date, timedelta
from xml.sax.saxutils import escape, unescape

PH = re.compile(r"\{\{\s*([^{}]+?)\s*\}\}")

# 항목 이름(표의 왼쪽 칸) → 채울 값의 이름
LABELS = {
    "성명": ("성명", "이름", "신청자성명", "출장자성명"), "소속": ("소속", "부서", "소속부서"),
    "직급": ("직급", "직위", "직급직위", "직위직급"), "연락처": ("연락처", "전화번호", "비상연락처", "휴대전화"),
    "출장지": ("출장지", "출장장소", "행선지", "출장지역"), "출장기간": ("출장기간", "출장일시", "기간"),
    "출장목적": ("출장목적", "목적", "용무"), "교통수단": ("교통수단",), "작성일": ("작성일", "작성일자", "신청일", "신청일자"),
    "휴가종류": ("휴가종류",), "휴가기간": ("휴가기간",), "품명": ("품명",), "금액": ("금액", "추정금액", "구매금액"),
    "행사명": ("행사명",), "희망일": ("구매희망일", "희망일"),
}
_LABEL_OF = {v: k for k, vs in LABELS.items() for v in vs}
RX_TR = re.compile(r"<(\w+:)?tr\b[^>]*>.*?</\1tr>", re.S)
RX_TC = re.compile(r"<(\w+:)?tc\b[^>]*>.*?</\1tc>", re.S)
RX_T = re.compile(r"<(?:\w+:)?t\b[^>/]*>([^<]*)</(?:\w+:)?t>")


def _cell_text(tc):
    return unescape("".join(RX_T.findall(tc))).strip()


def _label(text):
    return _LABEL_OF.get(re.sub(r"[\s:：*※()（）·ㆍ\[\]]", "", text))


def _put(tc, value):
    """빈 칸(tc)에 글자를 넣는다. 넣을 자리를 못 찾으면 None."""
    v = escape(str(value))
    for rx, rep in ((r"<(\w+:)?t\s*/>", lambda m: f"<{m.group(1) or ''}t>{v}</{m.group(1) or ''}t>"),
                    (r"<(\w+:)?t\b([^>]*)></\1t>", lambda m: f"<{m.group(1) or ''}t{m.group(2)}>{v}</{m.group(1) or ''}t>"),
                    (r"<(\w+:)?run\b([^>]*)/>", lambda m: f"<{m.group(1) or ''}run{m.group(2)}><{m.group(1) or ''}t>{v}</{m.group(1) or ''}t></{m.group(1) or ''}run>"),
                    (r"</(\w+:)?p>", lambda m: f"<{m.group(1) or ''}run charPrIDRef=\"0\"><{m.group(1) or ''}t>{v}</{m.group(1) or ''}t></{m.group(1) or ''}run></{m.group(1) or ''}p>")):
        new, n = re.subn(rx, rep, tc, count=1)
        if n:
            return new
    return None


def _label_cells(xml):
    """(행 위치, [(칸, 항목 이름, 오른쪽 빈 칸 번호)]) 목록. 표 안의 표가 있는 행은 건드리지 않는다."""
    out = []
    for tr in RX_TR.finditer(xml):
        row = tr.group(0)
        if re.search(r"<(?:\w+:)?tbl\b", row[1:]):
            continue
        cells = [m for m in RX_TC.finditer(row)]
        texts = [_cell_text(c.group(0)) for c in cells]
        hits = []
        for i in range(len(cells) - 1):
            key = _label(texts[i])
            if key and not texts[i + 1] and not _label(texts[i + 1] or "-"):
                hits.append((i, key))
        if hits:
            out.append((tr, cells, hits))
    return out


def label_fields(data):
    """자리표시 없는 hwpx 서식에서 채울 수 있는 항목 이름을 찾는다."""
    try:
        z = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile:
        return []
    fields = []
    for n in z.namelist():
        if re.match(r"Contents/section\d+\.xml$", n):
            for _, _, hits in _label_cells(z.read(n).decode("utf-8", "replace")):
                fields += [k for _, k in hits if k not in fields]
    return fields


def _fill_labels(xml, values, filled, left):
    parts, pos = [], 0
    for tr, cells, hits in _label_cells(xml):
        row = tr.group(0)
        new_row, shift = row, 0
        for i, key in hits:
            v = values.get(key)
            if v in (None, ""):
                left.add(key)
                continue
            c = cells[i + 1]
            put = _put(c.group(0), v)
            if put is None:
                left.add(key)
                continue
            a, b = c.start() + shift, c.end() + shift
            new_row = new_row[:a] + put + new_row[b:]
            shift += len(put) - (c.end() - c.start())
            filled.add(key)
        parts.append(xml[pos:tr.start()] + new_row)
        pos = tr.end()
    parts.append(xml[pos:])
    return "".join(parts)


def fill_template(data, values):
    """hwpx/docx 서식(zip)의 XML 안 {{항목}}을 바꾼다. 남은 항목 목록도 돌려준다."""
    src = zipfile.ZipFile(io.BytesIO(data))
    out = io.BytesIO()
    left, filled = set(), set()
    has_ph = any(PH.search(src.read(n).decode("utf-8", "replace")) for n in src.namelist() if n.endswith(".xml"))
    with zipfile.ZipFile(out, "w") as dst:
        for info in src.infolist():
            raw = src.read(info.filename)
            if not has_ph and re.match(r"Contents/section\d+\.xml$", info.filename):
                # 자리표시가 없는 실제 기관 서식: 항목 이름 오른쪽 빈 칸에 채운다
                raw = _fill_labels(raw.decode("utf-8"), values, filled, left).encode("utf-8")
            elif info.filename.endswith(".xml"):
                text = raw.decode("utf-8")

                def sub(m):
                    k = m.group(1)
                    v = values.get(k)
                    if v in (None, ""):
                        left.add(k)
                        return "          "
                    return escape(str(v))
                text = PH.sub(sub, text)
                raw = text.encode("utf-8")
            # mimetype 은 압축하지 않아야 한글에서 열린다
            ctype = zipfile.ZIP_STORED if info.filename == "mimetype" else zipfile.ZIP_DEFLATED
            dst.writestr(info, raw, compress_type=ctype)
    return out.getvalue(), sorted(left - filled)


def _won(n):
    return f"{int(n):,}" if isinstance(n, (int, float)) else (n or "")


def prefill(form, profile, slots, cost=None, text="", today=None):
    today = today or date.today()
    v = {
        "성명": profile.get("성명", ""), "소속": profile.get("소속", ""), "직급": profile.get("직급", ""),
        "연락처": profile.get("연락처", ""), "작성일": today.strftime("%Y. %m. %d."),
    }
    start = slots.get("date")
    days = slots.get("days")
    end = slots.get("end_date")
    if start and days and not end:
        d0 = date.fromisoformat(start)
        if slots.get("stage") == "after":
            # '어제 1박2일 다녀왔다' → 어제가 마친 날
            d0 = d0 - timedelta(days=days - 1)
            start = d0.isoformat()
        end = (d0 + timedelta(days=days - 1)).isoformat()
    period = f"{start} ~ {end}" if start and end and start != end else (start or "")
    v.update({
        "출장지": slots.get("destination", ""), "출장기간": period, "출장일수": days or "",
        "숙박일수": slots.get("nights", ""), "출장목적": slots.get("purpose", ""), "교통수단": slots.get("transport", ""),
        "금액": _won(slots.get("amount")) if slots.get("amount") else "", "희망일": start or "",
        "휴가기간": period, "휴가일수": days or "", "행사일": start or "", "행사명": slots.get("purpose", ""),
    })
    if slots.get("leave"):
        v["휴가종류"] = slots["leave"]
    else:
        for k, kind in (("연가", "연가"), ("연차", "연가"), ("병가", "병가"), ("공가", "공가"), ("특별휴가", "특별휴가")):
            if k in text:
                v["휴가종류"] = kind
                break
    if cost:
        for k in ("운임", "일비", "식비", "숙박비", "합계"):
            v[k] = _won(cost.get(k, 0))
    return {k: v.get(k, "") for k in form["fields"]}
