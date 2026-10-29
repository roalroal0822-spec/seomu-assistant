"use strict";
const role = () => $("#role").value;
let LAST = null;

// ------------------------------------------------------------ 상황 말하기
function basisChip(b) {
  return el("span", { class: "chip", title: b.text, onclick: () => modal(`${b.reg} ${b.label}`, el("pre", { class: "doc" }, b.text || "(조문 내용 없음)"),
    el("p", { class: "small muted" }, `파일: ${b.rel || ""} ${b.loc ? "· " + b.loc : ""}`)) }, `${shortName(b.reg)} ${b.label}`);
}

function dTag(s) {
  if (!s.due) return el("span", { class: "tag gray" }, s.due_base || "날짜 미정");
  const d = s.d_day;
  const cls = d < 0 ? "bad" : d <= 3 ? "warn" : "info";
  return el("span", { class: "tag " + cls }, `${s.due} (${d < 0 ? "D+" + (-d) : "D-" + d})`);
}

function maskNote(out, r) {
  if (r.masked && r.masked.length) out.append(el("p", { class: "small muted" }, `※ 적은 내용의 개인정보(${r.masked.join(", ")})를 ○○○로 가린 뒤 처리했습니다.`));
}

async function ask(text) {
  text = (text || $("#say").value).trim();
  if (!text) return;
  $("#say").value = text;
  const out = $("#answer");
  out.innerHTML = "";
  out.append(el("div", { class: "panel" }, el("span", { class: "spinner" }), " 규정에서 절차를 찾는 중"));
  const r = await api("POST", "/api/chat", { text });
  r.question = text;
  LAST = r;
  out.innerHTML = "";
  maskNote(out, r);
  if (r.type === "none") {
    const d = r.delegation;
    out.append(el("div", { class: "panel" }, el("p", {}, r.text),
      d ? el("table", { class: "t" }, el("tr", {}, d.header.map(h => el("th", {}, h))), d.rows.map(row => el("tr", {}, row.map(c => el("td", {}, c ?? ""))))) : null,
      d ? el("p", { class: "small muted" }, `근거: ${d.source}`) : null));
    return;
  }
  if (r.type === "common") {
    out.append(el("div", { class: "panel" }, el("p", {}, r.text),
      r.deadline ? el("p", {}, el("b", {}, "기한: "), r.deadline, r.due ? ` → ${r.due}` : "") : null,
      el("pre", { class: "doc" }, r.article.text), el("p", { class: "small muted" }, `근거: ${r.article.reg} (${r.article.rel})`)));
    return;
  }
  const head = el("div", { class: "panel" },
    el("div", { class: "row" }, el("h2", { style: { flex: 1, margin: 0 } }, r.card.name),
      el("span", { class: "small muted" }, `기준일 ${r.today} · ${r.mode}`)),
    el("p", { style: { whiteSpace: "pre-wrap", fontSize: "15px" } }, r.text),
    Object.keys(r.slots).length ? el("div", { class: "small muted" }, "알아들은 정보: ",
      Object.entries(r.slots).map(([k, v]) => el("span", { class: "chip" }, `${{ date: "날짜", end_date: "마친 날", days: "일수", nights: "박", destination: "장소", purpose: "목적", amount: "금액", transport: "교통", fare: "운임", stage: "단계", leave: "휴가 종류" }[k] || k}: ${v === "after" ? "사후" : v === "before" ? "사전" : v}`))) : null,
    r.alternatives.length ? el("div", { class: "small muted", style: { marginTop: "6px" } }, "다른 후보: ", r.alternatives.map(a => el("span", { class: "chip" }, a.name))) : null,
    el("div", { class: "row", style: { marginTop: "8px" } }, el("span", { class: "small muted" }, "안내서 저장:"), exportButtons("/api/guide/export", () => LAST)));
  out.append(head);

  const tl = el("ol", { class: "timeline" });
  for (const s of r.steps) {
    tl.append(el("li", { class: (s.focus ? "focus " : "") + s.state },
      el("div", { class: "row" }, el("b", {}, s.title), s.focus ? el("span", { class: "tag info" }, "지금 할 일") : null,
        s.state === "past" ? el("span", { class: "tag gray" }, "지난 단계") : null, s.state === "overdue" ? el("span", { class: "tag bad" }, "기한 지남") : null,
        s.state === "na" ? el("span", { class: "tag gray", title: s.condition || "" }, "해당 없음") : null,
        el("span", { style: { flex: 1 } }), el("span", { class: "small" }, s.deadline_label || "기한 없음"), dTag(s)),
      el("div", { class: "small", style: { margin: "3px 0" } }, s.action),
      s.condition ? el("div", { class: "small muted" }, "적용 조건: " + s.condition) : null,
      s.docs.length ? el("div", { class: "small" }, el("b", {}, "필요 서류: "), s.docs.join(", ")) : null,
      s.forms.length ? el("div", { class: "small row" }, el("b", {}, "서식: "), s.forms.map(f => f.exists
        ? el("span", {}, el("button", { class: "small primary", onclick: () => fillForm(f.form_id, r) }, `별지 ${f.no}호 ${f.name} 초안 채우기`),
            el("a", { href: `/api/kb/form/${f.form_id}`, class: "small", style: { marginLeft: "4px" } }, "빈 서식"))
        : el("span", { class: "tag warn", title: "규정에는 있지만 서식 파일이 등록되지 않았습니다" }, `별지 ${f.no}호 ${f.name} (서식 파일 없음)`))) : null,
      s.notes.length ? el("div", { class: "small muted" }, s.notes.map(n => el("div", {}, "· " + n))) : null,
      el("div", { class: "small" }, "근거: ", basisChip(s.basis))));
  }
  out.append(el("div", { class: "panel" }, el("h2", {}, "처리 순서와 기한"), tl,
    el("p", { class: "small muted" }, "날짜는 규정의 'O일 이내/전까지'를 달력일 기준으로 계산했습니다. 공휴일 여부는 확인하세요.")));

  const side = el("div", { class: "grid2" });
  if (r.approval) {
    const a = r.approval;
    side.append(el("div", { class: "panel" }, el("h2", {}, "결재선"),
      a.line ? el("div", { style: { fontSize: "16px" } }, a.line.join("  →  ")) : el("p", { class: "muted" }, a.note),
      a.line && a.note ? el("p", { class: "small muted" }, a.note) : null,
      a.basis ? el("p", { class: "small muted" }, `위임전결 기준: ${a.basis} (${a.source})`) : null,
      el("details", {}, el("summary", { class: "small" }, "전체 기준 보기"), el("table", { class: "t" }, el("tr", {}, ["세부", "기안", "검토", "전결"].map(h => el("th", {}, h))),
        a.candidates.map(c => el("tr", {}, el("td", {}, c.detail), el("td", {}, c.draft), el("td", {}, c.review), el("td", {}, c.final)))))));
  }
  if (r.cost) {
    const c = r.cost;
    side.append(el("div", { class: "panel" }, el("h2", {}, "여비 예상 금액"),
      el("table", { class: "t" }, ["운임", "일비", "식비", "숙박비", "합계"].map(k => el("tr", {}, el("td", {}, k), el("td", { style: { textAlign: "right" } }, (c[k] || 0).toLocaleString() + "원")))),
      el("p", { class: "small muted" }, `${c.days}일 ${c.nights}박 · ${c.숙박기준} · ${c.note} · 기준: ${c.source}`)));
  }
  if (r.tips.length) side.append(el("div", { class: "panel" }, el("h2", {}, "개인 보충 메모"), el("ul", {}, r.tips.map(t => el("li", {}, t.text, el("span", { class: "small muted" }, ` (${t.rel})`))))));
  if (r.overrides.length) side.append(el("div", { class: "panel" }, el("h2", {}, "공통 기준과 비교"),
    r.overrides.map(o => el("div", { class: "small" }, el("b", {}, o.step + ": "), o.diff || "공통 기준과 같음", el("div", { class: "muted" }, o.common)))));
  if (r.references.length) side.append(el("div", { class: "panel" }, el("h2", {}, "함께 볼 조문"),
    r.references.map(a => el("details", {}, el("summary", { class: "small" }, `${a.label}(${a.title})`), el("pre", { class: "doc small" }, a.text)))));
  out.append(side);
}

async function fillForm(formId, r) {
  const p = await api("POST", "/api/forms/prefill", { form_id: formId, slots: r.slots, text: r.question });
  const inputs = {};
  const body = el("div", {},
    el("p", { class: "small muted" }, "상황·내 정보·여비 기준으로 미리 채웠습니다. 고친 뒤 내려받으세요. 원본 서식은 바뀌지 않습니다."),
    p.form.fill_mode === "label" ? el("p", { class: "small muted" }, "이 서식에는 {{항목}} 자리표시가 없어, 표에서 항목 이름 오른쪽 빈 칸을 찾아 채웁니다. 내려받은 뒤 위치를 확인하세요.") : null,
    !p.form.fields.length ? el("p", { class: "small", style: { color: "var(--bad, #c0392b)" } }, "이 서식에서 채울 자리({{항목}} 자리표시나 '성명·소속' 같은 항목 이름 옆 빈 칸)를 찾지 못했습니다. 내려받으면 원본 사본이 나옵니다.") : null,
    el("div", { class: "kv", style: { gridTemplateColumns: "120px 1fr" } }, p.form.fields.map(f => [el("div", {}, f), inputs[f] = el("input", { class: "wide", value: p.values[f] ?? "" })])));
  modal(`${p.form.name} 초안`, body, el("div", { class: "row", style: { marginTop: "12px" } },
    el("button", { class: "primary", onclick: () => {
      const values = {}; for (const [k, i] of Object.entries(inputs)) values[k] = i.value;
      download("/api/forms/fill", { form_id: formId, values, case_id: r.case_id });
    } }, "한글 파일로 내려받기")));
}

// ------------------------------------------------------------ 다음 할 일
async function renderCases() {
  const sec = $("#tab-cases");
  const { cases, today } = await api("GET", "/api/cases");
  sec.innerHTML = "";
  const open = cases.filter(c => !c.closed).sort((a, b) => (a.d_day ?? 999) - (b.d_day ?? 999));
  sec.append(el("div", { class: "panel" }, el("div", { class: "row" }, el("h2", { style: { flex: 1, margin: 0 } }, `다음 할 일 (진행 중 ${open.length}건 · 기준일 ${today})`),
    el("span", { class: "small muted" }, "처리 이력 저장:"), el("span", { class: "row" },
      ...["hwpx", "docx", "md"].map(f => el("a", { class: "btn small", href: `/api/cases/export?fmt=${f}` }, f.toUpperCase())))),
    open.length ? el("table", { class: "t", style: { marginTop: "8px" } }, el("tr", {}, ["업무", "상황", "다음 단계", "기한", ""].map(h => el("th", {}, h))),
      open.map(c => el("tr", {}, el("td", {}, c.card_name), el("td", { class: "small" }, c.text),
        el("td", {}, c.next ? c.next.title : "-"), el("td", {}, c.next ? dTag({ due: c.next.due, d_day: c.d_day, due_base: c.next.deadline }) : ""),
        el("td", {}, c.next ? el("button", { class: "small", onclick: async () => { await api("POST", `/api/cases/${c.id}/step`, { index: c.steps.indexOf(c.steps.find(s => !s.done)), done: true }); renderCases(); } }, "완료") : null)))) :
      el("div", { class: "empty" }, "진행 중인 일이 없습니다. '상황 말하기'에서 안내를 받으면 여기에 쌓입니다.")));
  const hist = el("div", { class: "panel" }, el("h2", {}, `처리 이력 (${cases.length}건)`));
  for (const c of cases) {
    hist.append(el("details", {}, el("summary", {}, el("span", { class: "tag " + (c.closed ? "ok" : "warn") }, c.closed ? "완료" : "진행 중"), ` ${c.created} · ${c.card_name} · ${c.text}`),
      el("table", { class: "t" }, c.steps.map((s, i) => el("tr", {},
        el("td", {}, el("input", { type: "checkbox", checked: s.done, onchange: async e => { await api("POST", `/api/cases/${c.id}/step`, { index: i, done: e.target.checked }); renderCases(); } })),
        el("td", {}, s.title), el("td", { class: "small" }, s.deadline), el("td", {}, s.due || "")))),
      c.documents.length ? el("div", { class: "small" }, "작성한 서식: " + c.documents.map(d => d.name).join(", ")) : null,
      el("button", { class: "small danger", onclick: async () => { await api("POST", `/api/cases/${c.id}/delete`); renderCases(); } }, "이력 삭제")));
  }
  sec.append(hist);
}

// ------------------------------------------------------------ 절차 카드
async function renderCards() {
  const sec = $("#tab-cards");
  const { cards } = await api("GET", "/api/procedures");
  sec.innerHTML = "";
  sec.append(el("div", { class: "panel" }, el("div", { class: "row" }, el("h2", { style: { flex: 1, margin: 0 } }, `업무별 절차 (${cards.length})`),
    el("button", { onclick: async () => { await api("POST", "/api/procedures/rebuild"); toast("규정을 다시 읽었습니다"); renderCards(); } }, "규정 다시 읽기"),
    el("button", { onclick: () => editCard({ name: "", regulation: "", keywords: [], steps: [] }) }, "+ 절차 직접 등록")),
    el("p", { class: "small muted" }, "기관 규정에서 자동으로 정리한 절차입니다. 관리자는 순서·기한·서류를 고칠 수 있고, 고친 카드는 규정이 다시 바뀌면 '검토 필요'로 표시됩니다.")));
  for (const c of cards) {
    sec.append(el("div", { class: "panel" },
      el("div", { class: "row" }, el("h2", { style: { flex: 1, margin: 0 } }, c.name),
        c.needs_review ? el("span", { class: "tag bad" }, "규정 변경 · 검토 필요") : null,
        c.edited ? el("span", { class: "tag info" }, "관리자 수정") : el("span", { class: "tag gray" }, "자동 정리"),
        el("button", { class: "small", onclick: () => editCard(c) }, "수정"),
        el("button", { class: "small danger", onclick: async () => { if (confirm("이 절차 카드를 지울까요?")) { await api("POST", "/api/procedures/delete", { id: c.id, role: role() }); renderCards(); } } }, "삭제")),
      el("p", { class: "small muted" }, `근거: ${c.regulation} · 찾는 말: ${c.keywords.slice(0, 10).join(", ")}`),
      el("table", { class: "t" }, el("tr", {}, ["순서", "단계", "할 일", "기한", "서류·서식", "근거"].map(h => el("th", {}, h))),
        c.steps.map((s, i) => el("tr", {}, el("td", {}, i + 1), el("td", {}, s.title), el("td", { class: "small" }, s.action, s.notes && s.notes.length ? el("div", { class: "muted" }, s.notes.join(" / ")) : null),
          el("td", { class: "small" }, s.deadline_label || "-"),
          el("td", { class: "small" }, (s.docs || []).join(", "), (s.forms || []).map(f => el("div", {}, el("span", { class: "tag " + (f.exists ? "ok" : "warn") }, `별지 ${f.no}호 ${f.name}${f.exists ? "" : " (파일 없음)"}`)))),
          el("td", {}, s.basis && s.basis.label ? basisChip(s.basis) : "-")))),
      c.tips.length ? el("div", { class: "small", style: { marginTop: "6px" } }, el("b", {}, "개인 보충: "), c.tips.map(t => t.text).join(" / ")) : null,
      c.overrides.filter(o => o.diff).length ? el("div", { class: "small" }, el("b", {}, "공통 기준 비교: "), c.overrides.filter(o => o.diff).map(o => `${o.step} - ${o.diff}`).join(" / ")) : null));
  }
}

function editCard(c) {
  if (role() !== "admin") return toast("관리자로 바꾼 뒤 수정할 수 있습니다(오른쪽 위 사용자 구분)", true);
  const name = el("input", { class: "wide", value: c.name });
  const reg = el("input", { class: "wide", value: c.regulation || "" });
  const kws = el("input", { class: "wide", value: (c.keywords || []).join(", ") });
  const rows = el("tbody");
  const addRow = s => {
    s = s || { title: "", action: "", docs: [], forms: [] };
    const tr = el("tr", {},
      el("td", {}, el("input", { value: s.title, "data-k": "title" })),
      el("td", {}, el("input", { value: s.action, "data-k": "action", style: { width: "260px" } })),
      el("td", {}, el("input", { value: s.deadline_label || "", "data-k": "deadline_text", placeholder: "예: 출장을 마친 날부터 7일 이내" })),
      el("td", {}, el("input", { value: (s.docs || []).join(", "), "data-k": "docs" })),
      el("td", {}, el("input", { value: (s.forms || []).map(f => f.no).join(", "), "data-k": "forms", style: { width: "60px" } })),
      el("td", {}, el("button", { class: "small danger", onclick: () => tr.remove() }, "X")));
    tr._orig = s;
    rows.append(tr);
  };
  (c.steps || []).forEach(addRow);
  modal(c.id ? "절차 카드 수정" : "절차 직접 등록",
    el("label", { class: "f" }, "업무 이름"), name, el("label", { class: "f" }, "근거 규정"), reg,
    el("label", { class: "f" }, "찾는 말(쉼표로 구분)"), kws,
    el("table", { class: "t", style: { marginTop: "10px" } }, el("thead", {}, el("tr", {}, ["단계", "할 일", "기한", "필요 서류", "별지 번호", ""].map(h => el("th", {}, h)))), rows),
    el("div", { class: "row", style: { marginTop: "8px" } }, el("button", { onclick: () => addRow() }, "+ 단계 추가"),
      el("button", { class: "primary", onclick: async () => {
        const steps = [...rows.children].map(tr => {
          const g = k => tr.querySelector(`[data-k=${k}]`).value.trim();
          const o = tr._orig || {};
          return { ...o, title: g("title"), action: g("action"), deadline_text: g("deadline_text"),
            docs: g("docs") ? g("docs").split(/\s*,\s*/) : [], forms: g("forms") ? g("forms").split(/\s*,\s*/).map(n => ({ no: Number(n), name: (o.forms || []).find(f => f.no == n)?.name || "" })) : [] };
        });
        await api("POST", "/api/procedures/save", { role: role(), card: { ...c, name: name.value, regulation: reg.value, keywords: kws.value.split(/\s*,\s*/).filter(Boolean), steps } });
        $$(".modal-bg").forEach(m => m.remove()); toast("저장했습니다"); renderCards();
      } }, "저장")));
}

// ------------------------------------------------------------ 규정·서식 관리
async function renderKB() {
  const sec = $("#tab-kb");
  const kb = await api("GET", "/api/kb");
  sec.innerHTML = "";
  sec.append(el("div", { class: "panel" }, el("div", { class: "row" }, el("h2", { style: { flex: 1, margin: 0 } }, "규정·서식 관리"),
    el("button", { onclick: async () => { if (confirm("시연용 모의 규정·서식을 다시 불러올까요? (같은 이름 파일은 덮어씀)")) { await api("POST", "/api/kb/sample"); renderKB(); } } }, "시연용 모의자료 다시 불러오기")),
    el("p", { class: "small muted" }, "공통 기준 → 기관 규정 → 개인 보충 순으로 쌓입니다. 같은 내용이면 기관 규정이 공통 기준보다 우선합니다. 공통·기관은 관리자, 개인 보충은 사용자 누구나 등록합니다. 파일을 등록·삭제하면 절차가 자동으로 다시 정리됩니다.")));
  const cols = el("div", { class: "grid3" });
  for (const [layer, info] of Object.entries(kb.layers)) {
    const fileIn = el("input", { type: "file", multiple: true });
    const folder = el("select", {}, ...(layer === "org" ? ["규정", "서식"] : [""]).map(f => el("option", { value: f }, f || "(기본)")));
    cols.append(el("div", { class: "panel layer-col" }, el("h3", {}, info.label, el("span", { class: "small muted" }, layer === "personal" ? " · 사용자" : " · 관리자")),
      el("table", { class: "t" }, info.files.map(f => el("tr", {},
        el("td", {}, el("a", { href: "#", onclick: async e => { e.preventDefault(); const r = await api("GET", `/api/kb/file?layer=${layer}&rel=${encodeURIComponent(f.rel)}`); modal(f.rel, el("pre", { class: "doc" }, r.blocks.map(b => `[${b.loc}] ${b.text}`).join("\n"))); } }, f.rel)),
        el("td", {}, el("span", { class: "tag gray" }, f.kind)),
        el("td", {}, el("button", { class: "small danger", onclick: async () => { if (confirm(`${f.rel} 을(를) 지울까요? (이전 판은 보관됩니다)`)) { await api("POST", `/api/kb/${layer}/delete`, { rel: f.rel, role: role() }); renderKB(); } } }, "삭제"))))),
      el("div", { class: "row", style: { marginTop: "8px" } }, layer === "org" ? folder : null, fileIn,
        el("button", { class: "small primary", onclick: async () => {
          if (!fileIn.files.length) return toast("파일을 선택하세요", true);
          await api("POST", `/api/kb/${layer}/upload`, { role: role(), folder: folder.value, files: await readFiles(fileIn.files) });
          toast("등록하고 절차를 다시 정리했습니다"); renderKB();
        } }, "등록"))));
  }
  sec.append(cols);
  sec.append(el("div", { class: "panel" }, el("h2", {}, `등록된 서식 (${kb.forms.length})`),
    el("p", { class: "small muted" }, "서식 파일 안의 {{항목}} 자리표시를 채우고, 자리표시가 없는 한글(hwpx) 서식은 표에서 '성명·소속·직급·출장지·기간' 같은 항목 이름 오른쪽 빈 칸을 찾아 채웁니다(찾지 못한 항목은 비워 둡니다). 파일 이름을 '별지3_여비정산신청서.hwpx'처럼 번호로 시작하면 규정의 별지 번호와 연결됩니다."),
    el("table", { class: "t" }, el("tr", {}, ["별지", "서식", "채우는 항목", ""].map(h => el("th", {}, h))),
      kb.forms.map(f => el("tr", {}, el("td", {}, f.no ? `제${f.no}호` : "-"), el("td", {}, f.name), el("td", { class: "small" }, f.fields.join(", ")),
        el("td", {}, el("a", { href: `/api/kb/form/${f.id}` }, "내려받기")))))));
}

// ------------------------------------------------------------ 개정 반영
async function revPreview(files) {
  const r = await api("POST", "/api/revision/preview", { role: role(), files });
  const out = $("#rev-out");
  out.innerHTML = "";
  for (const x of r.results) {
    if (x.error) { out.append(el("div", { class: "panel" }, el("b", {}, x.file), ": ", x.error)); continue; }
    out.append(el("div", { class: "panel" }, el("h2", {}, `${x.target_title} 개정`),
      el("h3", {}, "바뀌는 절차"),
      x.changes.length ? el("table", { class: "t" }, el("tr", {}, ["단계", "항목", "현재", "개정 후"].map(h => el("th", {}, h))),
        x.changes.map(c => el("tr", {}, el("td", {}, c.step), el("td", {}, c.field), el("td", {}, c.old), el("td", {}, el("b", {}, c.new))))) : el("p", { class: "muted" }, "절차(기한·서류·서식) 변화는 없습니다."),
      x.edited_cards.length ? el("p", { class: "tag warn" }, `관리자가 고친 카드(${x.edited_cards.join(", ")})는 그대로 두고 '검토 필요'로 표시합니다`) : null,
      el("h3", {}, "신구조문대비"),
      el("table", { class: "t" }, el("tr", {}, el("th", {}, "현행"), el("th", {}, "개정안")),
        x.rows.map(row => el("tr", {}, el("td", { html: rich(row.old) }), el("td", { html: rich(row.new) }))))));
  }
  if (r.results.some(x => !x.error)) out.append(el("div", { class: "panel" }, el("button", { class: "primary", onclick: async () => {
    const a = await api("POST", "/api/revision/apply", { role: role(), token: r.token });
    toast(`반영 완료: ${a.applied.join(", ")}`); out.innerHTML = ""; out.append(el("div", { class: "panel" }, "반영했습니다. '업무별 절차'에서 바뀐 기한을 확인하세요."));
  } }, "이대로 반영")));
}

// ------------------------------------------------------------ 기타
async function renderProfile() {
  const p = await api("GET", "/api/profile");
  const f = $("#profile-form");
  f.innerHTML = "";
  for (const k of ["성명", "소속", "직급", "연락처"]) f.append(el("label", { class: "f" }, k), el("input", { class: "wide", "data-k": k, value: p[k] || "" }));
}
async function renderLog() {
  const { history } = await api("GET", "/api/history");
  const sec = $("#tab-log");
  sec.innerHTML = "";
  sec.append(el("div", { class: "panel" }, el("h2", {}, "작업 기록"), el("table", { class: "t" }, history.map(h => el("tr", {}, el("td", { class: "small" }, h.at), el("td", {}, h.action), el("td", { class: "small" }, h.detail))))));
}

// ------------------------------------------------------------ 판정 결과 (시뮬레이터·사전점검 공통)
const LV = { ok: "가능", warn: "주의", bad: "불가", gray: "판단 근거 없음(확인 필요)" };
function verdictView(r, title, question) {
  const box = el("div", { class: "panel" });
  box.append(el("div", { class: "verdict " + r.verdict },
    el("div", { class: "big" }, r.label),
    el("div", { class: "desc" }, r.summary || (r.counts ? `정상 ${r.counts.ok} · 확인 필요 ${r.counts.warn} · 규정 위반 가능성 ${r.counts.bad}` : ""))));
  const list = el("div", {});
  for (const f of r.findings) {
    list.append(el("div", { class: "finding" }, el("i", { class: "dot " + f.level }),
      el("div", {}, el("div", { class: "ttl" }, f.title),
        f.detail ? el("div", { class: "muted", style: { marginTop: "2px" } }, f.detail) : null,
        f.fix && f.fix.length ? el("div", { class: "fix" }, el("b", {}, "해결 방법 "), f.fix.map(x => el("div", {}, x))) : null,
        f.basis ? el("div", { style: { marginTop: "6px" } }, basisChip(f.basis)) : null)));
  }
  box.append(list);
  box.append(el("div", { class: "row", style: { marginTop: "16px", justifyContent: "space-between" } },
    el("span", { class: "small muted" }, `점검 방식: ${r.mode} · 기준일 ${r.today || ""}`),
    el("span", { class: "row" }, el("span", { class: "small muted" }, "결과 저장"),
      exportButtons("/api/check/export", () => ({ ...r, title, question })))));
  return box;
}

async function simulate(text) {
  text = (text || $("#sim-text").value).trim();
  if (!text) return;
  $("#sim-text").value = text;
  const out = $("#sim-out");
  out.innerHTML = "";
  out.append(el("div", { class: "panel" }, el("span", { class: "spinner" }), " 규정과 대조하는 중"));
  const r = await api("POST", "/api/simulate", { text });
  out.innerHTML = "";
  maskNote(out, r);
  out.append(verdictView(r, "업무 시뮬레이션 결과", text));
}

// ------------------------------------------------------------ 사전점검
let PRE = { kind: "travel", spec: null };
function iso(d) { return d.toISOString().slice(0, 10); }
function addDays(s, n) { const d = new Date(s + "T00:00:00"); d.setDate(d.getDate() + n); return iso(d); }

async function renderPre() {
  if (!PRE.spec) PRE.spec = await api("GET", "/api/precheck/spec");
  const kinds = PRE.spec.kinds;
  const k = $("#pre-kinds"); k.innerHTML = "";
  for (const [id, v] of Object.entries(kinds)) {
    k.append(el("label", {}, el("input", { type: "radio", name: "pre-kind", value: id, checked: PRE.kind === id,
      onchange: () => { PRE.kind = id; renderPreForm(); } }), v.name));
  }
  renderPreForm();
}

function field(label, input) { return el("div", {}, el("label", { class: "f" }, label), input); }

function renderPreForm() {
  const f = $("#pre-form"); f.innerHTML = ""; $("#pre-out").innerHTML = "";
  const spec = PRE.spec.kinds[PRE.kind];
  if (!spec) { f.append(el("div", { class: "empty" }, "점검할 절차가 규정에 없습니다.")); return; }
  const inp = (id, type = "text", ph = "") => el("input", { id, type, placeholder: ph, class: "wide" });
  if (PRE.kind === "travel") {
    f.append(el("div", { class: "grid3" },
      field("출장 시작일", inp("p-start", "date")), field("출장 종료일", inp("p-end", "date")), field("출장지", inp("p-dest", "text", "예: 부산")),
      field("출장 신청(승인)일", inp("p-apply", "date")), field("출장 결과 보고일", inp("p-report", "date")), field("정산 제출(예정)일", inp("p-submit", "date"))));
    f.append(el("label", { class: "f" }, "영수증"), el("div", { id: "p-receipts" }),
      el("button", { class: "small", onclick: () => addReceipt() }, "+ 영수증 추가"));
    f.append(el("label", { class: "f" }, "첨부서류 (" + spec.card + ")"),
      el("div", { class: "row" }, spec.docs.map(d => el("label", { class: "small", style: { marginRight: "14px" } },
        el("input", { type: "checkbox", class: "p-doc", value: d.name }), " " + d.name + (d.cond ? ` (${d.cond})` : "")))));
  } else {
    f.append(el("div", { class: "grid3" },
      field("품명", inp("p-item", "text", "예: 업무용 노트북")), field("구매금액(원)", inp("p-amount", "number")), field("견적서 수", inp("p-quotes", "number")),
      field("구매 요청일", inp("p-req", "date")), field("구매 희망일", inp("p-wish", "date")), el("div", {})));
    f.append(el("div", { class: "row", style: { marginTop: "12px" } },
      el("label", {}, el("input", { type: "checkbox", id: "p-approved" }), " 구매 요청 결재를 받았습니다"),
      el("label", {}, el("input", { type: "checkbox", id: "p-purchased" }), " 이미 구매(결제)했습니다")));
  }
  f.append(el("div", { class: "row", style: { marginTop: "20px" } },
    el("button", { class: "primary", onclick: runPre }, "AI 사전점검"),
    el("button", { onclick: () => fillPreExample() }, "예시 채우기 (일부러 틀린 곳 포함)")));
  if (PRE.kind === "travel") addReceipt();
}

function addReceipt(v = {}) {
  const row = el("div", { class: "row p-rc", style: { marginBottom: "6px" } },
    el("select", {}, ["숙박", "운임", "식비", "기타"].map(x => el("option", { value: x, selected: v.kind === x }, x))),
    el("input", { type: "date", value: v.date || "" }),
    el("input", { type: "number", placeholder: "금액(원)", value: v.amount || "" }),
    el("button", { class: "small danger", onclick: () => row.remove() }, "삭제"));
  $("#p-receipts").append(row);
}

function fillPreExample() {
  const t = PRE.spec.today;
  if (PRE.kind === "travel") {
    const s = addDays(t, -12), e = addDays(t, -10);
    $("#p-start").value = s; $("#p-end").value = e; $("#p-dest").value = "부산";
    $("#p-apply").value = addDays(s, -1); $("#p-report").value = addDays(e, 3); $("#p-submit").value = t;
    $("#p-receipts").innerHTML = "";
    addReceipt({ kind: "숙박", date: e, amount: 190000 });  // 2박 합계(1박 95,000원 → 광역시 상한 80,000원 초과)
    addReceipt({ kind: "운임", date: s, amount: 59800 });
    $$(".p-doc").forEach(c => c.checked = false);
  } else {
    $("#p-item").value = "업무용 노트북 2대"; $("#p-amount").value = 2500000; $("#p-quotes").value = 1;
    $("#p-req").value = t; $("#p-wish").value = addDays(t, 8);
    $("#p-approved").checked = false; $("#p-purchased").checked = true;
  }
  toast("예시를 채웠습니다. AI 사전점검을 눌러 보세요.");
}

async function runPre() {
  const v = id => ($("#" + id) || {}).value || "";
  let fields;
  if (PRE.kind === "travel") {
    fields = { start: v("p-start"), end: v("p-end"), destination: v("p-dest"), apply_date: v("p-apply"), report_date: v("p-report"),
      submit_date: v("p-submit"), attachments: $$(".p-doc").filter(c => c.checked).map(c => c.value),
      receipts: $$(".p-rc").map(r => { const [k, d, a] = r.querySelectorAll("select, input"); return { kind: k.value, date: d.value, amount: a.value }; }).filter(x => x.date || x.amount) };
  } else {
    fields = { item: v("p-item"), amount: v("p-amount"), quotes: v("p-quotes"), request_date: v("p-req"), wish_date: v("p-wish"),
      approved: $("#p-approved").checked, purchased: $("#p-purchased").checked };
  }
  const r = await api("POST", "/api/precheck", { kind: PRE.kind, fields });
  r.today = PRE.spec.today;
  const out = $("#pre-out"); out.innerHTML = "";
  maskNote(out, r);
  out.append(verdictView(r, "제출 전 사전점검 결과 (" + PRE.spec.kinds[PRE.kind].name + ")", ""));
  out.scrollIntoView({ behavior: "smooth", block: "start" });
}

document.addEventListener("DOMContentLoaded", async () => {
  try { const r = localStorage.getItem("seomu:role"); if (r) $("#role").value = r; } catch (e) {}
  $("#role").onchange = () => { try { localStorage.setItem("seomu:role", role()); } catch (e) {} };
  $("#send").onclick = () => ask();
  $("#say").addEventListener("keydown", e => { if (e.key === "Enter") ask(); });
  ["출장 다녀와서 정산해야 해요. 어제 부산에서 1박 2일로 다녀왔어요. KTX 운임 119,600원", "다음 주 금요일에 연가 쓰려고요",
   "노트북 300만원짜리 사려고 하는데 뭐부터 해요?", "11월 20일에 주민 설명회 행사 열려고 해", "오늘 야근해야 하는데", "회의록 언제까지 써야 해?"]
    .forEach(x => $("#examples").append(el("span", { class: "chip", onclick: () => ask(x) }, x)));
  $("#profile-save").onclick = async () => {
    const body = {}; $$("#profile-form input").forEach(i => body[i.dataset.k] = i.value);
    await api("POST", "/api/profile", body); toast("저장했습니다");
  };
  $("#rev-preview").onclick = async () => { const f = $("#rev-file").files; if (!f.length) return toast("개정본 파일을 선택하세요", true); revPreview(await readFiles(f)); };
  $("#rev-sample").onclick = async () => { const s = await api("GET", "/api/revision/sample"); revPreview([s]); };
  $("#sim-run").onclick = () => simulate();
  $("#sim-text").addEventListener("keydown", e => { if (e.key === "Enter") simulate(); });
  ["출장비 정산을 10일 뒤에 제출하면 어떻게 돼?", "법인카드로 개인 물품을 같이 결제했다면?", "결재 없이 먼저 물품을 구매하면?",
   "노트북 300만원짜리 사도 돼?", "연가를 3일 쓰려는데 2일 전에 신청하면?", "출장비 정산을 5일 뒤에 내면?"]
    .forEach(x => $("#sim-examples").append(el("span", { class: "chip", onclick: () => simulate(x) }, x)));
  setupTabs(id => ({ pre: renderPre, cases: renderCases, cards: renderCards, kb: renderKB, profile: renderProfile, log: renderLog, about: () => renderAbout() }[id] || (() => {}))());
  const t = await api("GET", "/api/today");
  $("#today-note").textContent = `기준일: ${t.today} · 등록된 규정에서만 답하고, 모든 안내에 근거 조문을 붙입니다.`;
});
