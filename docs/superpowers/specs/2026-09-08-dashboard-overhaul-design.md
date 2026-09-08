# 품질현황 대시보드 전면 개편 — 설계 스펙

작성일: 2026-09-08  
승인: 사용자 확인 완료 (섹션 1~4)

---

## 0. 개요

기존 품질현황 대시보드(`/dashboard`)를 전면 재설계한다. 핵심 변경 방향:

- **불량(불합격)** 과 **규격이탈(특채)** 을 독립 관리 트랙으로 분리
- Chart.js 기반 인터랙티브 차트 3패널 도입 (지표 토글, 애니메이션)
- 수기 NCR · 사후 불량을 품질 집계에 반영
- PC-first 레이아웃 (KPI 카드 가로, 패널 좌우 분할)
- 기간단위 선택 시 날짜 자동입력 (JS)

---

## 1. 데이터 레이어

### 1-1. DB 변경 — `ncr` 테이블 컬럼 2개 추가

```sql
ALTER TABLE ncr ADD COLUMN occurrence_type TEXT DEFAULT '입고검사';
-- '입고검사' | '사후'
-- 기존 레코드(NULL)는 '입고검사'로 간주

ALTER TABLE ncr ADD COLUMN defect_type TEXT;
-- '치수불량' | '외관불량' | '기능불량' | '재질불량' | '수량불량' | '기타'
```

마이그레이션: `database.py`의 기존 컬럼 추가 패턴(`PRAGMA table_info` + `ALTER TABLE`) 사용, 멱등 보장.

### 1-2. `quality_report()` 확장 — NCR 레이어 추가

현재 `quality_report()`는 `inspections` 테이블만 집계. NCR 데이터를 추가 레이어로 병합:

**NCR 집계 추가 항목:**
- `ncr_건수`: 기간 내 발행된 NCR 총 건수 (issued_date 기준)
- `사후불량_건수`: `occurrence_type='사후'` NCR 건수
- `불량유형별`: `{defect_type: 건수}` 딕셔너리 — 도넛 차트용
- `ncr_업체별`: 업체별 NCR 건수 — 불량 업체 순위용

**`by_period` 각 구간에 추가:**
```python
{
  ...기존 필드...,
  "ncr_건수": int,
  "사후불량_건수": int,
}
```

**`by_supplier` 각 업체에 추가:**
```python
{
  ...기존 필드...,
  "ncr_건수": int,
  "사후불량_건수": int,
}
```

**`by_material` 각 자재에 추가 (규격이탈 분석용):**
```python
{
  ...기존 필드...,
  "특채건수": int,   # inspection.approval_type='special' 건수
}
```

**최상위 집계에 추가:**
```python
{
  ...기존 키...,
  "ncr_건수": int,
  "사후불량_건수": int,
  "불량유형별": {"치수불량": 3, "외관불량": 1, ...},
}
```

### 1-3. `/dashboard/chart-data` 엔드포인트 신설

```
GET /dashboard/chart-data?{현재 필터 파라미터와 동일}
권한: defect_history 또는 inspect_history
```

반환 JSON 구조:
```json
{
  "periods": [
    {
      "label": "9월",        // 사람이 읽기 좋은 라벨
      "key": "2026-09",      // 원본 키
      "수량": 1200,
      "불합격수량": 24,
      "특채수량": 8,
      "합격수량": 1168,
      "불량률": 2.0,
      "규격이탈률": 2.667,
      "PPM": 20000,
      "검사표본수": 350,
      "ncr_건수": 5,
      "사후불량_건수": 2
    }
  ],
  "defect_type_dist": [
    {"label": "치수불량", "value": 12},
    {"label": "외관불량", "value": 5}
  ],
  "supplier_ncr_rank": [
    {"name": "ACE", "ncr_건수": 7, "불합격수량": 150},
    ...
  ],
  "material_deviation_rank": [
    {"material_no": "600001P001", "material_name": "...", "특채건수": 4},
    ...
  ],
  "supplier_deviation_rank": [
    {"name": "ACE", "특채건수": 6}
  ]
}
```

---

## 2. 백엔드 & NCR 폼 변경

### 2-1. `/ncr/new` 폼 — 필드 2개 추가

**발생 시점 (라디오):**
```html
<label><input type="radio" name="occurrence_type" value="입고검사" checked> 입고검사 중</label>
<label><input type="radio" name="occurrence_type" value="사후"> 사후 불량</label>
```

**불량 유형 (드롭다운):**
```html
<select name="defect_type">
  <option value="">-- 선택 --</option>
  <option>치수불량</option>
  <option>외관불량</option>
  <option>기능불량</option>
  <option>재질불량</option>
  <option>수량불량</option>
  <option>기타</option>
</select>
```

`ncr_new_manual()` POST 핸들러: `occurrence_type`, `defect_type` 값 읽어 `create_ncr()` 전달.

### 2-2. `/ncr/new/<inspection_id>` 폼 — 불량 유형만 추가

성적서 연결형 NCR은 발생 시점이 항상 `'입고검사'`이므로 라디오 없이 불량 유형 드롭다운만 추가. `occurrence_type='입고검사'` 고정으로 저장.

### 2-3. `_resolve_period()` — 변경 없음

서버 로직은 이미 정확함. 클라이언트 JS만 추가 (섹션 4 참조).

### 2-4. `_dashboard_params()` — 변경 없음

기존 파라미터 구조 유지. `/dashboard/chart-data`도 동일 함수 재사용.

---

## 3. 차트 시스템 — Chart.js 3패널

Chart.js CDN: `https://cdnjs.cloudflare.com/ajax/libs/Chart.js/4.4.1/chart.umd.min.js`

### 3-1. 패널 A — 기간별 추이

**Chart.js Mixed 차트** (막대 + 선 혼합):

| 지표 | Y축 | 타입 | 기본 켜짐 | 색상 |
|---|---|---|---|---|
| 입고 수량 | 좌(수량) | 막대 | ✅ | 파랑 `#bfdbfe` |
| 불합격 수량 | 좌(수량) | 스택 막대 | ✅ | 빨강 `#fca5a5` |
| 특채 수량 | 좌(수량) | 스택 막대 | ✅ | 주황 `#fed7aa` |
| 합격 수량 | 좌(수량) | 스택 막대 | ❌ | 초록 `#bbf7d0` |
| 불량률 % | 우(%) | 선 | ✅ | 빨강 `#dc2626` |
| 규격이탈률 % | 우(%) | 선(점선) | ✅ | 주황 `#f59e0b` |
| PPM | 우(PPM) | 선 | ❌ | 보라 `#a78bfa` |
| 검사 표본수 | 좌(수량) | 막대 | ❌ | 회색 `#e5e7eb` |

X축 라벨 변환 규칙:
```
"2026-09"   → "9월"
"2026-W36"  → "36주차"
"2026-09-08"→ "9/8"
"2026-Q3"   → "3분기"
"2026-H2"   → "하반기"
"2026"      → "2026년"
```

토글 버튼: 패널 상단 뱃지형. 선택 상태 `localStorage['dashboard_panel_a_hidden']`에 JSON 저장.

### 3-2. 패널 B — 불량 분석 (가로 절반)

3개 서브 차트:
1. **업체별 불량 순위** — 가로 막대, 불합격수량 + NCR건수 듀얼 막대
2. **불량 유형 분포** — 도넛 차트, `defect_type` 기준
3. **NCR 건수 추이** — 입고검사 vs 사후 스택 막대 (기간별)

### 3-3. 패널 C — 규격이탈 분석 (가로 절반)

3개 서브 차트:
1. **이탈 자재 순위** — 특채건수 기준 Top 10, 가로 막대
2. **이탈 업체 순위** — 특채건수 기준 Top 10, 가로 막대
3. **규격이탈 추이** — 특채 수량 월별 선 차트 (패널 A와 다른 독립 뷰)

패널 B/C는 `/dashboard/chart-data` JSON을 페이지 로드 시 한 번 fetch, 이후 토글은 클라이언트에서만 처리.

---

## 4. UI / PC-first 레이아웃

### 4-1. 전체 구조

```
┌─────────────────────────────────────────────────────────────────┐
│  필터 바 (가로 한 줄)                                              │
│  [기간단위▼] [시작일] [종료일] [업체칩] [자재] [조회] [초기화]       │
├───────┬───────┬───────┬──────────┬──────────────────────────────┤
│ 로트수 │입고수량│ 불량률 │ 규격이탈률 │ NCR건수(입고/사후)             │
├─────────────────────────────────────────────────────────────────┤
│  패널 A — 기간별 추이 (전체 너비)                                   │
│  [토글뱃지 ...] Chart.js Mixed                                    │
├──────────────────────────────┬──────────────────────────────────┤
│  패널 B — 불량 분석           │  패널 C — 규격이탈 분석             │
│  업체순위 | 유형도넛 | NCR추이  │  이탈자재 | 이탈업체 | 이탈추이      │
├─────────────────────────────────────────────────────────────────┤
│  [탭: 업체별 | 자재별 | 성적서 목록]                                │
└─────────────────────────────────────────────────────────────────┘
```

패널 B/C 분할: `display:grid; grid-template-columns: 1fr 1fr; gap:16px`. 태블릿(≤900px)에서는 1열로 전환.

### 4-2. 기간단위 → 날짜 자동입력 JS

```javascript
const PERIOD_RANGES = {
  daily:     () => [today, today],
  weekly:    () => [mondayOfThisWeek, today],
  monthly:   () => [firstOfThisMonth, today],
  quarterly: () => [firstOfThisQuarter, today],
  half:      () => [firstOfThisHalf, today],
  yearly:    () => [jan1, today],
};
periodSelect.addEventListener('change', e => {
  const [s, t] = PERIOD_RANGES[e.target.value]();
  startInput.value = fmt(s);
  endInput.value = fmt(t);
  // 수동 수정 여부 플래그 리셋
  startInput.dataset.manual = '';
  endInput.dataset.manual = '';
});
startInput.addEventListener('input', () => { startInput.dataset.manual = '1'; });
endInput.addEventListener('input', () => { endInput.dataset.manual = '1'; });
```

### 4-3. 애니메이션 (Designer 에이전트 전담)

- **KPI 카드 숫자 카운트업**: 페이지 로드 시 0→실제값 1초 애니메이션
- **Chart.js 진입 애니메이션**: 막대 아래→위, 선 왼→오른쪽 (`animation.duration: 800`)
- **지표 토글**: `chart.update('active')` — Chart.js 내장 페이드
- **패널 로딩**: 스켈레톤 shimmer → 데이터 로드 완료 시 페이드인
- **KPI 카드 호버**: 살짝 위로 올라오는 transform

### 4-4. X축 날짜 라벨

하단 항목명(X축)에 날짜가 보여야 한다는 요구사항 반영. 구간 키를 사람이 읽기 좋게 변환하는 `formatPeriodLabel(key, periodType)` JS 함수를 별도로 작성, 패널 A의 Chart.js `labels` 배열에 적용.

---

## 5. 작업 범위 요약

| 파일 | 변경 내용 |
|---|---|
| `database.py` | `ncr` 테이블 컬럼 2개 추가, `quality_report()` NCR 레이어 병합 |
| `app.py` | `/dashboard/chart-data` 엔드포인트 신설, `ncr_new_manual()` / `ncr_new()` 핸들러 필드 추가 |
| `templates/dashboard.html` | 전면 재작성 — PC-first 레이아웃, Chart.js 3패널, KPI 카드, 토글 뱃지, 기간 자동입력 JS |
| `templates/ncr_new_manual.html` | `occurrence_type` 라디오, `defect_type` 드롭다운 추가 |
| `templates/ncr_form.html` | `defect_type` 드롭다운 추가 |

---

## 6. 제약 사항

- Chart.js: `https://cdnjs.cloudflare.com/ajax/libs/Chart.js/4.4.1/chart.umd.min.js` (CSP 허용)
- 기존 `quality_report()` 반환 구조 키 변경 금지 — 엑셀 내보내기·JSON 내보내기가 이 구조에 의존
- `inspections` 테이블 구조 변경 없음 — 특채는 `approval_type='special'` 기존 필드 그대로 사용
- 색상: 규격이탈(특채)에 빨간색 사용 금지 — 이 시스템에서 빨강은 "불량/오류" 전용 (CLAUDE.md 12절)
