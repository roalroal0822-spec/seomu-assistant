"""서식 초안 채우기: 서식 파일의 {{항목}} 자리에 값을 넣어 새 파일로 만든다(원본 서식은 그대로)."""
import io
import re
import zipfile
from datetime import date, timedelta
from xml.sax.saxutils import escape

PH = re.compile(r"\{\{\s*([^{}]+?)\s*\}\}")


def fill_template(data, values):
    """hwpx/docx 서식(zip)의 XML 안 {{항목}}을 바꾼다. 남은 항목 목록도 돌려준다."""
    src = zipfile.ZipFile(io.BytesIO(data))
    out = io.BytesIO()
    left = set()
    with zipfile.ZipFile(out, "w") as dst:
        for info in src.infolist():
            raw = src.read(info.filename)
            if info.filename.endswith(".xml"):
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
    return out.getvalue(), sorted(left)


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
    for k in ("연가", "병가", "공가", "특별휴가"):
        if k in text:
            v["휴가종류"] = k
            break
    if cost:
        for k in ("운임", "일비", "식비", "숙박비", "합계"):
            v[k] = _won(cost.get(k, 0))
    return {k: v.get(k, "") for k in form["fields"]}
