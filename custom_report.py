# -*- coding: utf-8 -*-
"""
커스텀(자유양식) 성적서 PDF 생성 엔진.

기존 표준 성적서는 xlsx 템플릿 + LibreOffice로 만들지만, 커스텀 성적서는
드래그앤드롭 디자이너로 요소를 '아무 위치에나' 놓기 때문에 엑셀 셀 격자와 안 맞는다.
그래서 좌표 기반으로 PDF에 직접 그리는 reportlab을 쓴다.

좌표계 변환
  - 캔버스(웹 디자이너): 원점 좌상단, x→오른쪽, y→아래. 크기 canvas_w × canvas_h(px).
  - PDF(reportlab): 원점 좌하단, y→위. 그래서 y축을 뒤집는다.
  - A4 세로 = 595.28 × 841.89 pt. 캔버스가 A4 비율(1.414)이라 스케일은 x·y 거의 같다.
  - 폰트 크기도 스케일을 곱해 WYSIWYG(화면에서 본 크기 = 출력 크기)를 맞춘다.

검사항목표는 항목 수만큼 세로로 자동 확장되고, 표 아래에 놓인 요소들은 그만큼 밀려 내려간다.
"""

import os
import json

from reportlab.pdfgen import canvas as pdfcanvas
from reportlab.lib.pagesizes import A4, landscape
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.lib.utils import ImageReader

BASE_DIR = os.path.dirname(__file__)
LOGO_PATH = os.path.join(BASE_DIR, "logo.png")

# ── 한글 폰트 등록 ──────────────────────────────────────────────
# reportlab 기본 폰트(Helvetica)는 한글을 못 찍는다. 시스템에서 한글 TTF를 찾아 등록한다.
_FONT = "KFont"
_FONT_BOLD = "KFont-Bold"
_font_ready = False

_FONT_CANDIDATES = [
    # (일반, 볼드) — 있는 첫 조합을 쓴다
    (r"C:\Windows\Fonts\malgun.ttf",   r"C:\Windows\Fonts\malgunbd.ttf"),
    (r"C:\Windows\Fonts\NanumGothic.ttf", r"C:\Windows\Fonts\NanumGothicBold.ttf"),
    (r"C:\Windows\Fonts\gulim.ttc",    None),
    (r"C:\Windows\Fonts\batang.ttc",   None),
    ("/usr/share/fonts/truetype/nanum/NanumGothic.ttf",
     "/usr/share/fonts/truetype/nanum/NanumGothicBold.ttf"),
    ("/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
     "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc"),
]


def _ensure_font():
    """한글 폰트를 1회 등록. 성공하면 (일반폰트명, 볼드폰트명), 실패하면 (Helvetica, Helvetica-Bold)."""
    global _font_ready
    if _font_ready:
        return (_FONT, _FONT_BOLD)
    for regular, bold in _FONT_CANDIDATES:
        if not os.path.exists(regular):
            continue
        try:
            # .ttc(컬렉션)는 subfontIndex 필요
            kw = {"subfontIndex": 0} if regular.lower().endswith(".ttc") else {}
            pdfmetrics.registerFont(TTFont(_FONT, regular, **kw))
            if bold and os.path.exists(bold):
                kwb = {"subfontIndex": 0} if bold.lower().endswith(".ttc") else {}
                pdfmetrics.registerFont(TTFont(_FONT_BOLD, bold, **kwb))
            else:
                # 볼드 파일이 없으면 일반 폰트를 볼드 이름으로도 등록(굵기만 시각적으로 덜함)
                pdfmetrics.registerFont(TTFont(_FONT_BOLD, regular, **kw))
            _font_ready = True
            return (_FONT, _FONT_BOLD)
        except Exception:
            continue
    # 한글 폰트를 못 찾음 — 최소한 크래시는 막는다(한글은 깨질 수 있음)
    return ("Helvetica", "Helvetica-Bold")


# ── 표 렌더링 상수(캔버스 px 기준) ──
TABLE_HEADER_H = 22
TABLE_ROW_H = 20

# 열을 따로 지정하지 않은(옛) 표의 기본 구성 — 예전 5열 그대로.
DEFAULT_TABLE_COLS = [
    {"header": "항목",   "bind": "항목",   "width": 13, "align": "center"},
    {"header": "규격",   "bind": "규격",   "width": 34, "align": "left"},
    {"header": "검사방법", "bind": "검사방법", "width": 16, "align": "center"},
    {"header": "측정값",  "bind": "측정값",  "width": 22, "align": "center"},
    {"header": "판정",   "bind": "판정",   "width": 15, "align": "center"},
]

# bind 값 → 검사 항목 행(dict)의 어느 값인지
_BIND_TO_KEY = {"항목": "label", "규격": "spec", "검사방법": "method",
                "측정값": "value", "판정": "verdict"}


def _num(v, default):
    try:
        return float(v)
    except (TypeError, ValueError):
        return float(default)


def _table_config(e):
    """표 요소에서 (columns, groups, summaries)를 정규화해 돌려준다.
    columns가 없으면 옛 기본 5열로 폴백한다(기존 양식 호환)."""
    raw_cols = e.get("columns")
    if not raw_cols:
        raw_cols = DEFAULT_TABLE_COLS
    cols = []
    for c0 in raw_cols:
        if not isinstance(c0, dict):
            continue
        cols.append({
            "header": str(c0.get("header", "")),
            "bind":   c0.get("bind") or "blank",
            "width":  max(1.0, _num(c0.get("width"), 10)),
            "align":  c0.get("align") if c0.get("align") in ("left", "center", "right") else "center",
        })
    if not cols:
        cols = [dict(c) for c in DEFAULT_TABLE_COLS]

    # 묶음 머리행: start..start+span-1 이 연속·비중복이어야 함
    groups = []
    used = set()
    raw_groups = sorted(
        [g for g in (e.get("groups") or []) if isinstance(g, dict)],
        key=lambda g: _num(g.get("start"), 0))
    for g in raw_groups:
        try:
            st = int(g.get("start")); sp = int(g.get("span"))
        except (TypeError, ValueError):
            continue
        if sp < 2 or st < 0 or st + sp > len(cols):
            continue
        rng = set(range(st, st + sp))
        if rng & used:
            continue
        used |= rng
        groups.append({"label": str(g.get("label", "")), "start": st, "span": sp})

    summaries = []
    for s in (e.get("summaries") or []):
        if not isinstance(s, dict):
            continue
        summaries.append({
            "label":  str(s.get("label", "")),
            "mode":   "auto" if s.get("mode") == "auto" else "text",
            "value":  str(s.get("value", "")),
            "source": str(s.get("source", "")),
        })
    return cols, groups, summaries


def _table_height_px(e, n_items):
    """표의 실제 높이(캔버스 px). 머리행(묶음 있으면 2줄)+본문+요약행."""
    cols, groups, summaries = _table_config(e)
    head_rows = 2 if groups else 1
    body_rows = max(1, n_items)
    return (TABLE_HEADER_H * head_rows
            + body_rows * TABLE_ROW_H
            + len(summaries) * TABLE_ROW_H)


def _cell_value(bind, row):
    """열 bind에 맞는 검사값을 돌려준다. '측정값N'은 콤마구분 N번째 샘플."""
    if not bind or bind == "blank":
        return ""
    key = _BIND_TO_KEY.get(bind)
    if key:
        return str(row.get(key, "") or "")
    if bind.startswith("측정값"):
        rest = bind[len("측정값"):].strip()
        if rest.isdigit():
            parts = [p.strip() for p in str(row.get("value", "") or "").split(",")]
            idx = int(rest) - 1
            return parts[idx] if 0 <= idx < len(parts) else ""
    return ""


def _field_value(field, fields):
    return str(fields.get(field, "")) if fields else ""


def build_custom_report(template, data, out_path):
    """
    template: db.get_custom_template() 결과 dict (canvas_w, canvas_h, layout_json, orientation, page_size)
    data: {
        "fields":  {"자재번호":..., "제품명":..., ...},
        "items":   [{"label","spec","method","value","verdict"}, ...],
        "signature_path": PNG 경로 또는 None,
        "logo_path": 로고 PNG 경로(없으면 기본 logo.png),
    }
    out_path: 저장할 .pdf 경로
    반환: (pdf_path 또는 None, error 또는 None)
    """
    try:
        return _build(template, data, out_path)
    except Exception:
        import traceback
        return None, f"커스텀 성적서 PDF 생성 오류:\n{traceback.format_exc(limit=4)}"


def _build(template, data, out_path):
    reg_font, bold_font = _ensure_font()

    cw = template.get("canvas_w") or 495
    ch = template.get("canvas_h") or 700
    orientation = template.get("orientation") or "portrait"
    page = A4 if orientation != "landscape" else landscape(A4)
    page_w, page_h = page

    sx = page_w / cw
    sy = page_h / ch

    try:
        layout = json.loads(template.get("layout_json") or "[]")
    except (ValueError, TypeError):
        layout = []

    fields = data.get("fields") or {}
    items = data.get("items") or []
    logo_path = data.get("logo_path") or LOGO_PATH
    signature_path = data.get("signature_path")

    # ── 표 자동 확장: 표의 실제 높이를 계산하고, 표 아래 요소를 밀어 내린다 ──
    #    (여러 표가 있으면 위에서 아래로 누적 적용)
    elems = [dict(e) for e in layout if isinstance(e, dict)]
    tables = sorted([e for e in elems if e.get("kind") == "table"],
                    key=lambda e: e.get("y", 0))
    for t in tables:
        actual_h = _table_height_px(t, len(items))
        declared_h = t.get("h", actual_h)
        delta = actual_h - declared_h
        t["h"] = actual_h
        if delta > 0:
            ty = t.get("y", 0)
            for e in elems:
                if e is t:
                    continue
                if e.get("y", 0) > ty:
                    e["y"] = e.get("y", 0) + delta

    c = pdfcanvas.Canvas(out_path, pagesize=page)

    def to_pdf_top(y):
        """캔버스 y(위에서부터) → PDF y(요소 상단의 좌하단원점 좌표)."""
        return page_h - y * sy

    for e in elems:
        kind = e.get("kind")
        x = e.get("x", 0) * sx
        w = e.get("w", 100) * sx
        h = e.get("h", 20) * sy
        y_top = to_pdf_top(e.get("y", 0))       # 요소 상단
        y_bot = y_top - h                        # 요소 하단
        size = (e.get("size") or 12) * sx
        bold = bool(e.get("bold"))
        align = e.get("align") or "left"
        font = bold_font if bold else reg_font

        if kind == "text":
            _draw_text(c, e.get("text") or "", x, w, y_top, h, size, font, align)
        elif kind == "field":
            val = _field_value(e.get("field"), fields)
            _draw_text(c, val, x, w, y_top, h, size, font, align)
        elif kind == "divider":
            c.setStrokeColorRGB(0.2, 0.27, 0.35)
            c.setLineWidth(1)
            midy = y_top - h / 2
            c.line(x, midy, x + w, midy)
        elif kind == "logo":
            _draw_image_fit(c, logo_path, x, y_bot, w, h)
        elif kind == "sign":
            if signature_path and os.path.exists(signature_path):
                _draw_image_fit(c, signature_path, x, y_bot, w, h)
            else:
                c.setStrokeColorRGB(0.5, 0.55, 0.62)
                c.setLineWidth(0.8)
                c.rect(x, y_bot, w, h)
                c.setFont(reg_font, min(9 * sx, h * 0.5))
                c.setFillColorRGB(0.5, 0.55, 0.62)
                c.drawCentredString(x + w / 2, y_bot + h / 2 - 3, "서명")
                c.setFillColorRGB(0, 0, 0)
        elif kind == "table":
            _draw_table(c, e, items, x, w, y_top, sx, reg_font, bold_font, fields)

    c.showPage()
    c.save()
    return out_path, None


def _draw_text(c, text, x, w, y_top, h, size, font, align):
    """여러 줄(\n) 지원. 박스 안에서 세로 중앙 정렬."""
    if text is None:
        text = ""
    lines = str(text).split("\n")
    line_h = size * 1.3
    total_h = line_h * len(lines)
    # 박스 세로 중앙: 첫 줄 baseline
    start_baseline = y_top - (h - total_h) / 2 - size
    c.setFont(font, size)
    c.setFillColorRGB(0.07, 0.19, 0.31)
    for i, line in enumerate(lines):
        by = start_baseline - i * line_h
        if align == "center":
            c.drawCentredString(x + w / 2, by, line)
        elif align == "right":
            c.drawRightString(x + w, by, line)
        else:
            c.drawString(x, by, line)
    c.setFillColorRGB(0, 0, 0)


def _draw_image_fit(c, path, x, y, w, h):
    """이미지를 박스(x,y,w,h) 안에 비율 유지하며 좌상단 기준으로 맞춰 그린다."""
    if not path or not os.path.exists(path):
        return
    try:
        img = ImageReader(path)
        iw, ih = img.getSize()
        if iw <= 0 or ih <= 0:
            return
        ratio = min(w / iw, h / ih)
        dw, dh = iw * ratio, ih * ratio
        # 박스 상단 정렬(좌측 기준)
        dx = x
        dy = y + h - dh
        c.drawImage(img, dx, dy, width=dw, height=dh, mask="auto")
    except Exception:
        pass


def _cell_text(c, val, x0, x1, by, align):
    """셀 안에 정렬해서 글자 하나 찍기(좌/중/우). 좌우엔 3pt 여백."""
    val = str(val or "")
    if align == "left":
        c.drawString(x0 + 3, by, val)
    elif align == "right":
        c.drawRightString(x1 - 3, by, val)
    else:
        c.drawCentredString((x0 + x1) / 2, by, val)


def _summary_value(s, fields):
    if s.get("mode") == "auto":
        return str((fields or {}).get(s.get("source", ""), "") or "")
    return s.get("value", "")


def _draw_table(c, e, items, x, w, y_top, sx, reg_font, bold_font, fields=None):
    """설정(columns/groups/summaries)대로 표를 그린다. 병합은 셀별 테두리로 처리한다.
    캔버스가 A4 비율이라 sx≈sy이므로 세로도 sx로 변환한다."""
    cols, groups, summaries = _table_config(e)
    header_h = TABLE_HEADER_H * sx
    row_h = TABLE_ROW_H * sx
    has_groups = bool(groups)
    head_total = header_h * (2 if has_groups else 1)
    rows = items if items else [{"label": "", "spec": "", "method": "",
                                 "value": "", "verdict": ""}]

    # 열 x 경계(너비 비율 정규화)
    tot = sum(cc["width"] for cc in cols) or 1.0
    xs = [x]
    for cc in cols:
        xs.append(xs[-1] + w * (cc["width"] / tot))
    xs[-1] = x + w

    c.setLineWidth(0.7)
    c.setStrokeColorRGB(0.55, 0.6, 0.67)
    hdr_sz = min(TABLE_ROW_H * sx * 0.42, header_h * 0.5)
    body_sz = min(TABLE_ROW_H * sx * 0.42, row_h * 0.5)

    grouped_cols = set()
    for g in groups:
        grouped_cols |= set(range(g["start"], g["start"] + g["span"]))

    top = y_top
    # 헤더 배경(전체 밴드)
    c.setFillColorRGB(0.93, 0.95, 0.97)
    c.rect(x, top - head_total, w, head_total, fill=1, stroke=0)
    c.setFillColorRGB(0.13, 0.19, 0.25)

    if has_groups:
        # 위 밴드: 묶음 머리글(병합 셀)
        c.setFont(bold_font, hdr_sz)
        for g in groups:
            gx0 = xs[g["start"]]
            gx1 = xs[g["start"] + g["span"]]
            c.rect(gx0, top - header_h, gx1 - gx0, header_h, stroke=1, fill=0)
            c.drawCentredString((gx0 + gx1) / 2, top - header_h + header_h * 0.32, g["label"])
        # 묶이지 않은 열: 머리글이 두 밴드를 세로로 덮음(병합)
        for i, cc in enumerate(cols):
            if i in grouped_cols:
                continue
            c.rect(xs[i], top - head_total, xs[i + 1] - xs[i], head_total, stroke=1, fill=0)
            c.drawCentredString((xs[i] + xs[i + 1]) / 2,
                                top - head_total + head_total * 0.5 - hdr_sz * 0.35, cc["header"])
        # 아래 밴드: 묶인 열들의 개별 머리글
        for i in sorted(grouped_cols):
            c.rect(xs[i], top - head_total, xs[i + 1] - xs[i], header_h, stroke=1, fill=0)
            c.drawCentredString((xs[i] + xs[i + 1]) / 2,
                                top - head_total + header_h * 0.32, cols[i]["header"])
    else:
        c.setFont(bold_font, hdr_sz)
        for i, cc in enumerate(cols):
            c.rect(xs[i], top - header_h, xs[i + 1] - xs[i], header_h, stroke=1, fill=0)
            c.drawCentredString((xs[i] + xs[i + 1]) / 2,
                                top - header_h + header_h * 0.32, cc["header"])

    # 본문(항목 수만큼 자동)
    c.setFont(reg_font, body_sz)
    c.setFillColorRGB(0.1, 0.13, 0.17)
    cur = top - head_total
    for r in rows:
        nxt = cur - row_h
        for i, cc in enumerate(cols):
            c.rect(xs[i], nxt, xs[i + 1] - xs[i], row_h, stroke=1, fill=0)
            _cell_text(c, _cell_value(cc["bind"], r), xs[i], xs[i + 1],
                       nxt + row_h * 0.32, cc["align"])
        cur = nxt

    # 요약행(고정): 첫 열=제목, 나머지=값(병합)
    lx1 = xs[1] if len(xs) > 1 else x + w * 0.2
    for s in summaries:
        nxt = cur - row_h
        c.setFillColorRGB(0.96, 0.97, 0.98)
        c.rect(x, nxt, w, row_h, fill=1, stroke=0)
        c.setFillColorRGB(0.1, 0.13, 0.17)
        c.setFont(bold_font, body_sz)
        c.rect(x, nxt, lx1 - x, row_h, stroke=1, fill=0)
        c.drawCentredString((x + lx1) / 2, nxt + row_h * 0.32, s.get("label", ""))
        c.setFont(reg_font, body_sz)
        c.rect(lx1, nxt, (x + w) - lx1, row_h, stroke=1, fill=0)
        _cell_text(c, _summary_value(s, fields), lx1, x + w, nxt + row_h * 0.32, "center")
        cur = nxt

    # 바깥 테두리
    c.rect(x, cur, w, top - cur, stroke=1, fill=0)
    c.setFillColorRGB(0, 0, 0)


# ═══════════════════════════════════════════════════════════════
#  전수검사 기록지 PDF 생성
# ═══════════════════════════════════════════════════════════════

_FI_ROWS_PER_PAGE = 30   # 페이지당 유닛 행 수


def _fi_col_widths(columns, page_w, margin):
    """열 width% → pt 절대값. 총 합이 page_w - 2*margin에 맞게 정규화."""
    total_w = page_w - 2 * margin
    total_pct = sum(c.get("width", 10) for c in columns)
    if total_pct <= 0:
        total_pct = len(columns) * 10
    return [total_w * c.get("width", 10) / total_pct for c in columns]


def _fi_cell(c, text, x, y, w, h, font, sz, align="center", fill_rgb=None, bold=False):
    """셀 하나: 배경(옵션) → 테두리 → 텍스트."""
    if fill_rgb:
        c.setFillColorRGB(*fill_rgb)
        c.rect(x, y, w, h, fill=1, stroke=0)
        c.setFillColorRGB(0, 0, 0)
    c.rect(x, y, w, h, stroke=1, fill=0)
    if bold and "-Bold" not in font:
        bfont = font.replace("KFont", "KFont-Bold").replace("Helvetica", "Helvetica-Bold")
    else:
        bfont = font
    c.setFont(bfont, sz)
    text = str(text) if text is not None else ""
    if not text:
        return
    pad = 2
    if align == "center":
        c.drawCentredString(x + w / 2, y + h * 0.28, text)
    elif align == "right":
        c.drawRightString(x + w - pad, y + h * 0.28, text)
    else:
        # 긴 텍스트는 잘라서 표시
        c.drawString(x + pad, y + h * 0.28, text[:20])


def _fi_result_symbol(val):
    """입력값(OK/NG/○/△/×/숫자)을 표시용 문자열로 정규화."""
    if val is None:
        return ""
    s = str(val).strip()
    if s.upper() in ("OK", "O", "○", "합격", "pass"):
        return "○"
    if s.upper() in ("NG", "X", "×", "△", "불합격", "fail"):
        return "△"
    return s


def build_full_inspection_sheet(fi_header, units, columns, config, inspection_header, out_path):
    """전수검사 기록지 PDF를 out_path에 저장.

    fi_header         : dict  (inspect_date, complete_date, status)
    units             : list  [{"unit_no","serial_no","values":{},"result","remark"}]
    columns           : list  [{key,header,type,lo,hi}] — specs 테이블에서 파생
    config            : dict  {"enabled":True,"note":"..."} — 메모 등 부가 정보만
    inspection_header : dict  (성적서 헤더 — 담당·입고날짜·자재명 등)
    """
    reg_font, bold_font = _ensure_font()
    note = (config or {}).get("note", "")

    # 항상 번호 + 제품번호를 앞에 고정 열로 추가(columns에 없으면)
    col_keys = [c["key"] for c in columns]
    fixed_cols = []
    if "serial" not in col_keys:
        fixed_cols = [{"key": "serial", "header": "제품번호", "type": "text", "width": 14, "group": None}]
    # 시험결과 열은 values_json의 result 필드로 자동
    result_col = {"key": "_result", "header": "시험결과", "type": "_result", "width": 7, "group": None}
    all_cols = fixed_cols + columns + [result_col]

    # 그룹 처리: 같은 group 이름을 가진 연속 열을 묶는다
    def _groups(cols):
        groups = []
        i = 0
        while i < len(cols):
            g = cols[i].get("group")
            if g:
                j = i + 1
                while j < len(cols) and cols[j].get("group") == g:
                    j += 1
                groups.append({"label": g, "start": i, "span": j - i})
                i = j
            else:
                groups.append({"label": None, "start": i, "span": 1})
                i += 1
        return groups

    col_groups = _groups(all_cols)
    has_groups = any(g["label"] for g in col_groups)
    head_rows = 2 if has_groups else 1

    # PDF 설정
    PAGE_W, PAGE_H = A4   # 595 × 841 pt
    MARGIN = 20
    CONTENT_W = PAGE_W - 2 * MARGIN
    HEADER_BLOCK_H = 60 if note else 46   # 상단 헤더 영역
    INFO_ROW_H = 12   # 날짜·담당 등 정보 행 높이
    COL_H = 11        # 열 머리행 높이 (1행)
    ROW_H = 10        # 데이터 행 높이
    FONT_TITLE = 11
    FONT_HEAD = 7
    FONT_BODY = 7

    col_ws = _fi_col_widths(all_cols, PAGE_W, MARGIN)
    # 번호 열 고정 너비(좁게)
    no_w = 14

    c = pdfcanvas.Canvas(out_path, pagesize=A4)

    def draw_page_header(page_no, total_pages):
        """상단 헤더(회사명·제목·날짜 정보·시험조건·열 머리행) 그리기."""
        y = PAGE_H - MARGIN

        # 회사명 + 제목
        c.setFont(bold_font, FONT_TITLE)
        c.drawCentredString(PAGE_W / 2, y - 10, "샤든코리아 수입검사")
        mat_name = inspection_header.get("material_name", "")
        qty = inspection_header.get("quantity", "")
        subtitle = f"{mat_name}({qty} EA) 기본검사" if qty else mat_name
        c.setFont(reg_font, FONT_HEAD + 1)
        c.drawCentredString(PAGE_W / 2, y - 20, subtitle)
        c.line(MARGIN, y - 24, PAGE_W - MARGIN, y - 24)

        # 날짜·담당·집계 정보 행
        y2 = y - 24 - INFO_ROW_H
        info_items = [
            ("입고날짜", inspection_header.get("intake_date", "")),
            ("검사날짜", fi_header.get("inspect_date", "")),
            ("완료날짜", fi_header.get("complete_date", "") if fi_header.get("status") == "complete" else ""),
            ("담당", inspection_header.get("inspector", "")),
            ("팀장", ""),   # 서명은 별도 처리
        ]
        # 오른쪽에 합계
        ok_cnt = sum(1 for u in units if u.get("result", "").upper() in ("OK", "○", "합격"))
        ng_cnt = len(units) - ok_cnt
        c.setFont(reg_font, FONT_HEAD)
        col_w_info = CONTENT_W / len(info_items)
        for i2, (lbl, val) in enumerate(info_items):
            ix = MARGIN + i2 * col_w_info
            c.rect(ix, y2, col_w_info, INFO_ROW_H, stroke=1, fill=0)
            c.setFont(bold_font, FONT_HEAD - 1)
            c.drawString(ix + 2, y2 + INFO_ROW_H * 0.3, lbl)
            c.setFont(reg_font, FONT_HEAD)
            c.drawString(ix + 30, y2 + INFO_ROW_H * 0.3, str(val))
        # 집계 행
        y3 = y2 - INFO_ROW_H
        c.rect(MARGIN, y3, CONTENT_W / 2, INFO_ROW_H, stroke=1, fill=0)
        c.setFont(reg_font, FONT_HEAD)
        c.drawString(MARGIN + 3, y3 + INFO_ROW_H * 0.3,
                     f"합계 :  합격 {ok_cnt}개  /  불합격 {ng_cnt}개  (총 {len(units)}개)")
        # 페이지 번호
        c.setFont(reg_font, FONT_HEAD - 1)
        c.drawRightString(PAGE_W - MARGIN, y3 + INFO_ROW_H * 0.3, f"({page_no}/{total_pages})")

        # 시험 조건 텍스트
        y4 = y3
        if note:
            y4 -= INFO_ROW_H
            c.setFont(reg_font, FONT_HEAD - 1)
            c.drawString(MARGIN, y4 + INFO_ROW_H * 0.3, note[:200])

        # 열 머리행
        y5 = y4 - head_rows * COL_H
        hdr_fill = (0.88, 0.91, 0.96)

        # 번호 열 (항상 제일 왼쪽)
        col_x = MARGIN
        if has_groups:
            _fi_cell(c, "번호", col_x, y5, no_w, COL_H * 2, bold_font, FONT_HEAD,
                     fill_rgb=hdr_fill, bold=True)
        else:
            _fi_cell(c, "번호", col_x, y5, no_w, COL_H, bold_font, FONT_HEAD,
                     fill_rgb=hdr_fill, bold=True)
        col_x += no_w

        if has_groups:
            # 그룹 머리행 (위쪽 행)
            gx = col_x
            for g in col_groups:
                gw = sum(col_ws[g["start"]:g["start"] + g["span"]])
                if g["label"]:
                    _fi_cell(c, g["label"], gx, y5 + COL_H, gw, COL_H,
                             bold_font, FONT_HEAD, fill_rgb=hdr_fill, bold=True)
                else:
                    # 그룹 없는 열은 두 행을 병합(단일 셀처럼)
                    _fi_cell(c, all_cols[g["start"]]["header"], gx, y5, gw, COL_H * 2,
                             bold_font, FONT_HEAD, fill_rgb=hdr_fill, bold=True)
                gx += gw
            # 하위 열 머리행 (아래쪽 행)
            cx2 = col_x
            for g in col_groups:
                if g["label"]:  # 그룹 있는 것만 하위 행
                    for ci in range(g["start"], g["start"] + g["span"]):
                        cw2 = col_ws[ci]
                        _fi_cell(c, all_cols[ci]["header"], cx2, y5, cw2, COL_H,
                                 reg_font, FONT_HEAD - 1, fill_rgb=hdr_fill)
                        cx2 += cw2
                else:
                    cx2 += col_ws[g["start"]]
        else:
            cx2 = col_x
            for ci, col in enumerate(all_cols):
                _fi_cell(c, col["header"], cx2, y5, col_ws[ci], COL_H,
                         bold_font, FONT_HEAD, fill_rgb=hdr_fill, bold=True)
                cx2 += col_ws[ci]

        return y5  # 데이터 시작 y

    total_pages = max(1, -(-len(units) // _FI_ROWS_PER_PAGE))  # ceiling div
    if not units:
        total_pages = 1

    for page_no in range(1, total_pages + 1):
        page_units = units[(page_no - 1) * _FI_ROWS_PER_PAGE: page_no * _FI_ROWS_PER_PAGE]
        data_y = draw_page_header(page_no, total_pages)

        # 데이터 행
        cy = data_y
        for u in page_units:
            cy -= ROW_H
            vals = u.get("values", {})
            row_result = u.get("result", "")
            # 행 배경 (불합격이면 연한 분홍)
            if row_result.upper() in ("NG", "×", "△", "불합격"):
                c.setFillColorRGB(1, 0.94, 0.94)
                c.rect(MARGIN, cy, CONTENT_W, ROW_H, fill=1, stroke=0)
                c.setFillColorRGB(0, 0, 0)

            # 번호 셀
            _fi_cell(c, str(u.get("unit_no", "")), MARGIN, cy, no_w, ROW_H, reg_font, FONT_BODY)
            cx = MARGIN + no_w

            for ci, col in enumerate(all_cols):
                cw = col_ws[ci]
                key = col["key"]
                ctype = col.get("type", "text")
                if key == "_result":
                    raw = row_result
                elif key == "serial":
                    raw = u.get("serial_no", "")
                else:
                    raw = vals.get(key, "")

                if ctype == "pf":
                    display = _fi_result_symbol(raw)
                else:
                    display = str(raw) if raw else ""

                _fi_cell(c, display, cx, cy, cw, ROW_H, reg_font, FONT_BODY,
                         align="center" if ctype in ("pf", "_result") else "left")
                cx += cw

        # 빈 행으로 채우기 (마지막 페이지)
        remaining = _FI_ROWS_PER_PAGE - len(page_units)
        for _ in range(remaining):
            cy -= ROW_H
            _fi_cell(c, "", MARGIN, cy, no_w, ROW_H, reg_font, FONT_BODY)
            cx = MARGIN + no_w
            for ci in range(len(all_cols)):
                _fi_cell(c, "", cx, cy, col_ws[ci], ROW_H, reg_font, FONT_BODY)
                cx += col_ws[ci]

        if page_no < total_pages:
            c.showPage()

    c.save()
    return out_path
