# -*- coding: utf-8 -*-
"""
빈 성적서 양식(자재별로 항목/규격만 채워진 파일)을 여러 개 한 번에 업로드해서
규격표(specs 테이블)에 자동 등록하는 파서.

셀 위치를 고정으로 믿지 않고, 라벨 텍스트를 스캔해서 위치를 찾는다:
  - 자재번호/품명: 앞부분 몇 행을 스캔해서 "자재번호"/"품명" 텍스트가 있는 셀을 찾고
    같은 셀의 콜론 뒤 또는 바로 오른쪽 셀 값을 사용
  - 항목표: "검사 항목"과 "AQL"이 같이 있는 헤더 행을 찾고, 그 헤더 텍스트로
    번호/검사항목/AQL/검사방법 열 위치를 판단 (셀 위치가 파일마다 달라도 대응)
  - 한 파일 안에 시트가 여러 개면(자재마다 시트가 다름) 시트마다 각각 처리
"""
import re
import openpyxl

# "108.5 ± 0.8" 같은 대칭 공차
_RE_PLUS_MINUS = re.compile(r"([\d.]+)\s*±\s*([\d.]+)")
# "63 - 0.2" 같은 한쪽(하한 방향) 공차 — 전체 문자열이 이 패턴 하나여야 함
_RE_ONE_SIDED = re.compile(r"^\s*([\d.]+)\s*-\s*([\d.]+)\s*$")
# "(17.1~20.9)" 같은 괄호 안 명시적 범위 — 있으면 이걸 최우선으로 사용
_RE_RANGE = re.compile(r"\(?\s*([\d.]+)\s*~\s*([\d.]+)\s*\)?")
# "72 (-0.05~-0.1)" 같은 경우 — 앞의 숫자(72)가 기준값이고 괄호 안은 그 기준에서 뺄/더할
# 공차 오프셋(둘 다 부호가 명시돼 있어야만 이 패턴으로 인식 — 위 절대범위와 구분됨)
_RE_OFFSET_RANGE = re.compile(r"^\s*(-?[\d.]+)\s*\(\s*([+-][\d.]+)\s*~\s*([+-][\d.]+)\s*\)\s*$")
# "5㎛ 이상" / "10 이상" 같은 최소값만 있는 단측 표기 — 상한 없음
_RE_AT_LEAST = re.compile(r"([\d.]+)\s*(?:[^\d\s]*)\s*이상")
# "10 이하" 같은 최대값만 있는 단측 표기 — 하한 없음
_RE_AT_MOST = re.compile(r"([\d.]+)\s*(?:[^\d\s]*)\s*이하")
# "4T" 같은 두께(Thickness) 단독 표기 — 공차 정보가 없어 자동으로 하한/상한을 못 채움
# (인식은 하되 "두께 표기로 인식됨" 안내를 위해 별도로 구분)
_RE_THICKNESS_ONLY = re.compile(r"^\s*([\d.]+)\s*T\s*$")

MAX_LABEL_SEARCH_ROW = 12     # 자재번호/품명 라벨을 찾을 때 앞에서 몇 행까지 볼지
MAX_HEADER_SEARCH_ROW = 15    # 항목표 헤더 행을 찾을 때 앞에서 몇 행까지 볼지
MAX_DATA_ROWS = 40            # 항목표를 최대 몇 행까지 읽을지 (무한루프 방지)


def _clean(v):
    if v is None:
        return ""
    return v.replace("\n", "").strip() if isinstance(v, str) else str(v).strip()


def _extract_after_colon(text):
    """'자재번호：602106P103' -> '602106P103'. 전각/반각 콜론 둘 다 지원."""
    text = _clean(text)
    for sep in ("：", ":"):
        if sep in text:
            return text.split(sep, 1)[1].strip()
    return text


def _find_label_value(ws, label_keywords, max_row=MAX_LABEL_SEARCH_ROW, max_col=20):
    """
    앞부분 셀들을 스캔해서 label_keywords(예: ["자재번호"]) 중 하나라도 포함된 셀을 찾고,
    그 셀 텍스트에 콜론 뒤 값이 있으면 그 값을, 없으면 같은 행 바로 오른쪽 셀 값을 반환.
    못 찾으면 "".
    """
    for r in range(1, max_row + 1):
        for c in range(1, max_col + 1):
            v = ws.cell(row=r, column=c).value
            if not v or not isinstance(v, str):
                continue
            text = _clean(v)
            if any(kw in text for kw in label_keywords):
                after = _extract_after_colon(text)
                if after and after != text:
                    return after
                # 콜론이 없는 라벨이면 오른쪽 셀 값을 시도
                right = ws.cell(row=r, column=c + 1).value
                if right:
                    return _clean(right)
    return ""


def _find_header_row(ws, max_row=MAX_HEADER_SEARCH_ROW, max_col=20):
    """
    "검사 항목"(또는 "항목")과 "AQL"이 같은 행에 있는 헤더 행을 찾는다.
    반환: (header_row_idx 또는 None, {col_idx: 정제된 헤더텍스트})
    """
    for r in range(1, max_row + 1):
        row_texts = {}
        for c in range(1, max_col + 1):
            v = ws.cell(row=r, column=c).value
            if v and isinstance(v, str):
                row_texts[c] = _clean(v)
        texts = row_texts.values()
        has_item_header = any(("검사" in t and "항목" in t) or t == "항목" for t in texts)
        has_aql_header = any("AQL" in t.upper() for t in texts)
        if has_item_header and has_aql_header:
            return r, row_texts
    return None, {}


def _map_columns(header_texts):
    """헤더 텍스트 딕셔너리에서 번호/검사항목/AQL/검사방법 열 인덱스를 찾는다."""
    cols = {"no": None, "spec": None, "aql": None, "method": None}
    for c, t in header_texts.items():
        if t == "번호" and cols["no"] is None:
            cols["no"] = c
        elif ("검사" in t and "항목" in t) or t == "항목":
            cols["spec"] = c
        elif "AQL" in t.upper():
            cols["aql"] = c
        elif "검사" in t and "방법" in t:
            cols["method"] = c
    return cols


def _parse_tolerance(spec_display, is_visual):
    """
    규격 표기 텍스트에서 하한/상한을 뽑아낸다.
    반환: (lower, upper, needs_review: bool, review_note: str 또는 None)
    """
    if is_visual:
        return None, None, False, None

    text = spec_display or ""

    m = _RE_OFFSET_RANGE.match(text)
    if m:
        nominal, off1, off2 = float(m.group(1)), float(m.group(2)), float(m.group(3))
        lo, hi = nominal + min(off1, off2), nominal + max(off1, off2)
        return lo, hi, False, None

    m = _RE_RANGE.search(text)
    if m:
        lo, hi = float(m.group(1)), float(m.group(2))
        return min(lo, hi), max(lo, hi), False, None

    m = _RE_PLUS_MINUS.search(text)
    if m:
        nominal, tol = float(m.group(1)), float(m.group(2))
        return nominal - tol, nominal + tol, False, None

    m = _RE_ONE_SIDED.match(text)
    if m:
        nominal, tol = float(m.group(1)), float(m.group(2))
        return nominal - tol, nominal, False, None

    m = _RE_AT_LEAST.search(text)
    if m:
        # "5㎛ 이상" — 최소값만 있고 상한은 없음(무제한). 자동 인식은 하되 하한만 채움
        return float(m.group(1)), None, False, None

    m = _RE_AT_MOST.search(text)
    if m:
        # "10 이하" — 최대값만 있고 하한은 없음(무제한)
        return None, float(m.group(1)), False, None

    m = _RE_THICKNESS_ONLY.match(text)
    if m:
        # "4T" — 두께 표기로는 인식했지만 공차 정보가 없어서 자동으로 범위를 못 채움 →
        # 확인 필요 목록에 "두께 표기"라고 구체적으로 안내
        return None, None, True, "두께(T) 표기로 인식됐지만 허용 오차가 적혀있지 않아 하한/상한을 직접 입력해야 해"

    return None, None, True, None


def _parse_sheet(ws, source_name):
    """
    시트 하나를 파싱한다.
    반환: (material_no, material_name, items, item_warnings, fail_reason)
    - 성공: fail_reason은 None, material_no/items가 채워짐 (item_warnings는 항목별 확인요청, 있을 수도 없을 수도)
    - 실패: fail_reason에 사람이 읽을 실패 사유 문자열, material_no는 ""일 수도 값이 있을 수도(자재번호는 찾았지만 항목표를 못 읽은 경우 등)
    """
    sheet_label = f"{source_name} / 시트 '{ws.title}'"

    material_no = _find_label_value(ws, ["자재번호", "자재 번호"])
    material_name = _find_label_value(ws, ["품명", "품명 및 규격"])

    if not material_no:
        return "", material_name, [], [], f"[{sheet_label}] 자재번호를 찾을 수 있는 셀이 없어."

    header_row, header_texts = _find_header_row(ws)
    if header_row is None:
        return material_no, material_name, [], [], \
            f"[{sheet_label} / {material_no}] '검사 항목'/'AQL' 헤더 행을 못 찾았어 — 셀 구조를 확인해줘."

    cols = _map_columns(header_texts)
    if cols["spec"] is None or cols["aql"] is None:
        return material_no, material_name, [], [], \
            f"[{sheet_label} / {material_no}] 헤더는 찾았지만 '검사항목' 또는 'AQL' 열 위치를 특정 못 했어."

    items = []
    item_warnings = []
    order = 1
    empty_streak = 0
    for r in range(header_row + 1, header_row + 1 + MAX_DATA_ROWS):
        spec_val = ws.cell(row=r, column=cols["spec"]).value
        spec_display = _clean(spec_val)
        if not spec_display:
            empty_streak += 1
            if empty_streak >= 2:   # 빈 행이 연속 2개면 표가 끝난 것으로 판단
                break
            continue
        empty_streak = 0

        item_name = _clean(ws.cell(row=r, column=cols["no"]).value) if cols["no"] else str(order)
        aql = ws.cell(row=r, column=cols["aql"]).value
        inspect_method = _clean(ws.cell(row=r, column=cols["method"]).value) if cols["method"] else ""

        is_visual = ("외관" in spec_display) or (inspect_method == "육안")
        judge_type = "visual" if is_visual else "numeric"

        lower, upper, needs_review, review_note = _parse_tolerance(spec_display, is_visual)
        if needs_review:
            reason = review_note or f"규격 표기 '{spec_display}'에서 하한/상한을 자동으로 못 읽었어"
            item_warnings.append(
                f"[{sheet_label} / {material_no} / 항목 {item_name}] "
                f"{reason} — 등록은 됐지만 규격 상세 화면에서 직접 확인·수정해줘."
            )

        items.append({
            "item_name": item_name,
            "spec_display": spec_display,
            "judge_type": judge_type,
            "lower_limit": lower,
            "upper_limit": upper,
            "inspect_method": inspect_method,
            "aql": aql,
            "item_order": order,
        })
        order += 1

    if not items:
        return material_no, material_name, [], [], \
            f"[{sheet_label} / {material_no}] 헤더는 찾았는데 항목 데이터가 하나도 없어."

    return material_no, material_name, items, item_warnings, None


def parse_spec_file(file_stream_or_path, source_name=""):
    """
    xlsx 파일 하나(시트 여러 개 가능)를 읽어서 시트별 결과 리스트를 반환.
    반환: [(material_no, material_name, items, item_warnings, fail_reason), ...] — 시트 개수만큼.
    fail_reason이 None이면 성공(등록 대상), 아니면 실패(등록 대상 아님).
    """
    wb = openpyxl.load_workbook(file_stream_or_path, data_only=True)
    results = []
    for ws in wb.worksheets:
        results.append(_parse_sheet(ws, source_name))
    return results
