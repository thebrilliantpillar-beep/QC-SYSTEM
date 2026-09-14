# 성적서 항목 판정 수동 오버라이드 기능 — 구현 스펙 / 실행계획

> 이 문서는 `writing-plans` 스킬 형식을 따르되, 이 프로젝트에는 자동화 테스트 스위트(pytest 등)가 없고 CLAUDE.md 11절/19절이 명시한 대로 "실제 화면·xlsx/PDF를 직접 열어서 확인"하는 수동 검증이 표준이므로, 각 태스크의 "테스트" 단계는 `pytest` 대신 **Flask 개발서버 + sqlite3 CLI + 실제 iqc.db(개발용 사본)를 이용한 수동/스크립트 검증**으로 대체했다. 코드는 이 문서를 작성한 planner가 아니라 **developer 에이전트가 작성**해야 한다(planner는 Edit/Write 도구가 없음).

**Goal:** 검사자/관리자가 이미 측정 완료된 성적서의 개별 항목 판정(합격/불합격)을 자동판정 결과와 무관하게 사후에 수동으로 뒤집을 수 있게 하고, 이 수동판정이 부적합 통보서(NCR) 자동생성·승인 전 검토 화면·성적서 상세화면에 일관되게 반영되게 한다.

**Architecture:** 원본 자동판정(`inspection_items.result`, `judge_numeric`/`judge_visual` 결과)은 절대 건드리지 않는다. 대신 새 테이블 `inspection_item_overrides`에 "현재 유효한 오버라이드"만 저장하고(과거 이력은 `activity_log`가 담당 — `ncr_waived` 토글과 동일한 기존 관례), `app.py`의 신규 헬퍼 `effective_result(it)` 하나로 "실제로 적용되는 판정"을 계산해서 NCR 생성·승인 전 화면·상세화면 배지에서만 이 값을 쓴다. **공식 출력물(xlsx/PDF, 커스텀 성적서)은 의도적으로 건드리지 않는다** — 승인 시점에 굳힌 `content_hash`/`pdf_hash` 위변조 검증 체계와 충돌을 피하기 위한 설계 결정.

**Tech Stack:** Flask, SQLite(`database.py`의 `get_conn()` 패턴), Jinja2 템플릿, 기존 `record_change()` 감사로그.

**Spec:** 이 문서 자체가 스펙 겸 계획이다. 배경 사고 사례: 성적서 #774(자재 605007P000) C항목이 AQL 허용범위(Ac1) 안이라 시스템이 자동으로 "합격" 처리했는데, 사후에 NCR을 보내야 하는 상황이 실제로 발생함.

## Global Constraints

- `inspection_items.result`(자동판정 원본)은 **절대 UPDATE하지 않는다**.
- 오버라이드는 **공식 출력 문서(xlsx/PDF/커스텀 성적서)의 내용을 바꾸지 않는다**.
- 오버라이드 대상 값은 `"합격"` 또는 `"불합격"` 두 가지만 허용한다.
- 오버라이드는 원본 자동판정이 `"합격"` 또는 `"불합격"`으로 이미 확정된 항목에만 허용한다.
- 사유(`reason`) 입력은 필수다.
- `status == 'superseded'` 성적서에는 오버라이드를 허용하지 않는다.
- 모든 오버라이드 설정/취소는 `record_change()`로 활동로그에 남긴다.
- 코드 수정 완료 후 quality-watcher 검증 필수(CLAUDE.md 11절).

---

## 실제 코드 확인 결과 (요청서의 추정을 코드로 재검증)

| 요청서의 추정 | 실제 확인 결과 |
|---|---|
| "app.py의 `ncr_form` 라우트" | **실제 함수명은 `ncr_new`**, 라우트 `/ncr/new/<int:inspection_id>` (`@perm_required("ncr", "approve")`). `app.py:5858` |
| "`defect_items` 필터가 `ncr_form` 근처" | `app.py:5929-5930`. 같은 함수 안에 동일 조건의 게이트가 하나 더 있다 — `app.py:5872-5876`의 `has_fail` 변수. **두 곳 다 고쳐야 함.** |
| (요청서에 없던 발견) | `inspection_detail.html`도 `has_fail_items`(`app.py:3108`)가 False면 NCR 작성 섹션 링크 자체를 안 보여준다. 여기도 고쳐야 함. |
| (요청서에 없던 발견) | `approve_view`(`app.py:4075`)에도 `problem_items`/`pending_items`/`pass_items`(`app.py:4142-4145`) 분류가 있다. 오버라이드는 여기도 반영돼야 승인자가 알 수 있다. |
| (요청서에 없던 발견) | `_custom_items_from_inspection`(`app.py:4914-4925`)과 `_build_result`(`app.py:3429-3457`, 정식 xlsx/PDF 생성)는 원본 결과를 그대로 쓴다. **이 두 곳은 의도적으로 그대로 둔다.** |

---

## 왜 승인된 성적서에도 오버라이드를 허용하면서 공식 출력물은 그대로 두는가

배경 사고 사례가 "승인 이후에 NCR을 보내야겠다고 판단"하는 상황을 명시한다. 승인된 성적서 내용을 바꾸면:

- `inspections.content_hash`(승인 시점 판정 내용 스냅샷)와 어긋나서 `verify_inspection_integrity()`가 "변조 의심"으로 오탐한다.
- CLAUDE.md 8-2-10절 규칙(승인 회수 시에만 해시를 지운다)과 맞물려, 이미 발행된 문서를 재승인 절차 없이 뒤집는 결과가 된다.

**결론**: 오버라이드는 `inspection_items.result` 원본도 `content_hash`/`pdf_hash`가 보호하는 공식 문서 내용도 건드리지 않는 별도의 주석(annotation) 레이어로만 존재한다. NCR 자동생성·승인 전 검토 화면·성적서 상세 웹페이지 표시, 딱 3곳에만 영향을 준다.

---

## 스키마 확인 (재사용/참고할 기존 코드)

- `database.py:206-243` — `inspections`/`inspection_items` 테이블 정의, `PRAGMA table_info` 기반 멱등 마이그레이션 패턴(`database.py:245-286`).
- `database.py:386-396` — 최근 추가 테이블(`change_points`)이 `init_db()` 안에 직접 `CREATE TABLE IF NOT EXISTS`로 들어간 것 — 신규 기능 테이블은 `init_db()` 안에 바로 추가하는 관례.
- `database.py:1430-1482` — `create_inspection()`: `part_material_no`는 항상 resolve해서 저장. 옛 행은 NULL일 수 있어 `get_inspection()`의 specs 조인(`database.py:1571-1574`)이 `COALESCE`로 방어. 오버라이드 조인도 동일 패턴 필요.
- `database.py:1527-1550` — `update_inspection_items()`: 매번 전량 삭제 후 재삽입. 오버라이드를 `inspection_items.id`가 아니라 `(inspection_id, part_material_no, item_name)`으로 키 잡아야 하는 이유.
- `app.py:3131-3157`(`inspection_ncr_waive`) — "토글 컬럼 + `record_change()`" 패턴의 선례. 오버라이드 라우트가 그대로 따를 구조.

---

## Task 1: DB 스키마 — `inspection_item_overrides` 테이블 + set/clear 함수

**Files:** `database.py:254` 부근에 테이블 생성, `rename_inspection_item()` 다음에 함수 추가.

```python
    cur.execute("""
        CREATE TABLE IF NOT EXISTS inspection_item_overrides (
            id                 INTEGER PRIMARY KEY AUTOINCREMENT,
            inspection_id      INTEGER NOT NULL,
            part_material_no   TEXT NOT NULL,
            item_name          TEXT NOT NULL,
            override_result    TEXT NOT NULL,
            reason             TEXT NOT NULL,
            created_by_user_id INTEGER,
            created_by_name    TEXT,
            created_at         TEXT DEFAULT (datetime('now', 'localtime')),
            FOREIGN KEY (inspection_id) REFERENCES inspections(id),
            UNIQUE(inspection_id, part_material_no, item_name)
        )
    """)
```

```python
def set_item_override(inspection_id, part_material_no, item_name, override_result, reason,
                       user_id, user_name):
    """검사 항목의 자동판정 결과를 사람이 사후에 합격/불합격으로 덮어쓴다.
    inspection_items.result 원본은 건드리지 않는다."""
    conn = get_conn()
    existing = conn.execute(
        "SELECT id FROM inspection_item_overrides WHERE inspection_id=? AND part_material_no=? AND item_name=?",
        (inspection_id, part_material_no, item_name)).fetchone()
    if existing:
        conn.execute("""
            UPDATE inspection_item_overrides
               SET override_result=?, reason=?, created_by_user_id=?, created_by_name=?,
                   created_at=datetime('now','localtime')
             WHERE id=?
        """, (override_result, reason, user_id, user_name, existing["id"]))
    else:
        conn.execute("""
            INSERT INTO inspection_item_overrides
                (inspection_id, part_material_no, item_name, override_result, reason,
                 created_by_user_id, created_by_name)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (inspection_id, part_material_no, item_name, override_result, reason, user_id, user_name))
    conn.commit()
    conn.close()


def clear_item_override(inspection_id, part_material_no, item_name):
    """오버라이드를 지워서 자동판정 결과로 되돌린다."""
    conn = get_conn()
    conn.execute(
        "DELETE FROM inspection_item_overrides WHERE inspection_id=? AND part_material_no=? AND item_name=?",
        (inspection_id, part_material_no, item_name))
    conn.commit()
    conn.close()
```

검증: `sqlite3 iqc.db "SELECT sql FROM sqlite_master WHERE name='inspection_item_overrides';"` + set/update/clear 스크립트 검증(가짜 inspection_id=999999로 실데이터와 안 섞이게).

---

## Task 2: `get_inspection()`에 오버라이드 조인 추가

**Files:** `database.py:1554-1579`

기존 쿼리의 SELECT/JOIN에 아래 추가:

```sql
               io.override_result AS override_result,
               io.reason          AS override_reason,
               io.created_by_name AS override_by,
               io.created_at      AS override_at
        ...
        LEFT JOIN inspection_item_overrides io
               ON io.inspection_id = ii.inspection_id
              AND io.part_material_no = COALESCE(ii.part_material_no,
                                            (SELECT material_no FROM inspections WHERE id = ?))
              AND io.item_name = ii.item_name
```

파라미터 튜플이 `?` 3개로 늘어남에 주의.

---

## Task 3: `app.py` — `effective_result()` 헬퍼 + Jinja 필터 등록

**Files:** `app.py:3005`(`judge_visual` 함수 끝) 다음.

```python
def effective_result(it):
    """항목의 '실제로 적용되는' 판정 — 수동 오버라이드가 있으면 그 값,
    없으면 자동판정 결과 그대로. inspection_items.result 원본은 안 바꾸고,
    이 함수를 거치는 곳(NCR 자동생성, 승인 전 검토 화면, 상세화면 배지)에서만
    반영된다 — 공식 xlsx/PDF 출력물은 이 함수를 쓰지 않는다."""
    override = it["override_result"] if "override_result" in it.keys() else None
    return override or (it["result"] or "")


app.jinja_env.filters['effective_result'] = effective_result
```

---

## Task 4: 읽기 경로 3곳을 `effective_result()`로 교체

- `inspection_detail`(`app.py:3108-3109`)의 `has_fail_items`
- `approve_view`(`app.py:4142-4145`)의 `problem_items`/`pass_items` (NOT_READY_RESULTS 분류는 원본 그대로 유지)
- `ncr_new`의 `has_fail`(`app.py:5872-5876`)과 `defect_items`(`app.py:5929-5930`)

모두 `it["result"]` → `effective_result(it)` 로 교체. `all_items`는 dict 병합 결과라 `override_result` 컬럼이 이미 포함돼 있어 추가 전달 불필요.

---

## Task 5: 오버라이드 설정/취소 라우트

**Files:** `app.py` — `inspection_ncr_waive`(`app.py:3131-3157`) 다음에 추가.

권한(추천): `@perm_required("ncr", "approve")` — `ncr_new`와 동일 권한 재사용.

```python
@app.route("/inspection/<int:inspection_id>/item-override", methods=["POST"])
@perm_required("ncr", "approve")
def inspection_item_override(inspection_id):
    """검사 항목 판정을 자동판정과 무관하게 수동으로 합격/불합격 전환(또는 취소)."""
    header, items = db.get_inspection(inspection_id)
    if header is None:
        flash("존재하지 않는 성적서야.")
        return redirect(url_for("home"))
    if header["status"] == "superseded":
        flash("대체된 성적서는 판정을 변경할 수 없어.")
        return redirect(url_for("inspection_detail", inspection_id=inspection_id))

    item_name = request.form.get("item_name", "")
    part_material_no = request.form.get("part_material_no") or header["material_no"]
    target = next((it for it in items
                   if it["item_name"] == item_name
                   and (it["part_material_no"] or header["material_no"]) == part_material_no), None)
    if target is None:
        flash("항목을 찾을 수 없어.")
        return redirect(url_for("inspection_detail", inspection_id=inspection_id))

    action = request.form.get("action", "set")

    if action == "revert":
        was_override = target["override_result"]
        db.clear_item_override(inspection_id, part_material_no, item_name)
        flash("수동판정이 취소됐어. 자동판정 결과로 돌아가.")
        record_change("항목 판정 수동변경 취소", "inspection", inspection_id,
                      f"{item_name}: 수동 {was_override} → 자동판정({target['result']})으로 복귀")
        return redirect(url_for("inspection_detail", inspection_id=inspection_id))

    if target["result"] not in ("합격", "불합격"):
        flash("측정이 끝나지 않았거나 규격이 없는 항목은 판정을 수동으로 바꿀 수 없어.")
        return redirect(url_for("inspection_detail", inspection_id=inspection_id))

    new_result = request.form.get("override_result", "").strip()
    reason = request.form.get("reason", "").strip()
    if new_result not in ("합격", "불합격"):
        flash("합격 또는 불합격 중에서 골라줘.")
        return redirect(url_for("inspection_detail", inspection_id=inspection_id))
    if not reason:
        flash("판정을 수동으로 바꾸는 사유를 입력해줘.")
        return redirect(url_for("inspection_detail", inspection_id=inspection_id))
    if new_result == target["result"] and not target["override_result"]:
        flash("자동판정과 같은 값이야 — 바꿀 필요 없어.")
        return redirect(url_for("inspection_detail", inspection_id=inspection_id))

    user_name = g.user["display_name"] or g.user["username"]
    db.set_item_override(inspection_id, part_material_no, item_name, new_result, reason,
                          g.user["id"], user_name)
    flash(f"'{item_name}' 항목 판정을 '{new_result}'(으)로 수동 변경했어.")
    record_change("항목 판정 수동변경", "inspection", inspection_id,
                  f"{item_name}: 자동판정 {target['result']} → 수동 {new_result} (사유: {reason})")
    return redirect(url_for("inspection_detail", inspection_id=inspection_id))
```

---

## Task 6: `edit_inspection_submit` — 측정값이 바뀌면 해당 항목 오버라이드 자동 해제

**왜 필요한가**: 오버라이드는 `(inspection_id, part_material_no, item_name)` 키로 저장돼 `update_inspection_items()`의 전량 재삽입에도 안 지워진다. 이건 대체로 바람직하지만, **측정값 자체가 바뀌어 자동판정 결과가 달라졌는데 옛 오버라이드가 새 데이터에 그대로 적용되는 건 위험**하다.

**Files:** `app.py:3263-3328`(`edit_inspection_submit`)

1. `header, _ = db.get_inspection(inspection_id)` → `header, old_items = db.get_inspection(inspection_id)`
2. `for spec in specs_with_sample:` 루프 종료 직후 삽입:

```python
    old_by_key = {(it["part_material_no"], it["item_name"]): it for it in old_items}
    cleared_overrides = []
    for new_it in items_with_results:
        key = (new_it["part_material_no"], new_it["item_name"])
        old_it = old_by_key.get(key)
        if (old_it is not None and old_it["override_result"]
                and old_it["result"] != new_it["result"]):
            db.clear_item_override(inspection_id, new_it["part_material_no"], new_it["item_name"])
            cleared_overrides.append(new_it["item_name"])
    if cleared_overrides:
        flash("측정값이 바뀌어서 다음 항목의 수동판정이 초기화됐어(자동판정으로 복귀): "
              + ", ".join(cleared_overrides))
```

3. `record_change` 로그에 `cleared_overrides` 반영.

이 태스크는 `_get_specs_for_material`이 실제 규격에 의존해서 순수 스크립트로 완결 검증하기 어렵다 — **실제 개발 DB의 자재로 브라우저에서 직접 재현해서 확인할 것.**

---

## Task 7: `inspection_detail.html` — 오버라이드 UI

**Files:** `templates/inspection_detail.html:238` 부근(판정 배지 셀)

1. 판정 배지를 `it['result']` 대신 `it | effective_result` 기준으로 표시. 오버라이드가 걸려있으면 원래 자동판정 값도 작게 같이 표시(계측기 유효기간 셀의 조건부 추가줄 패턴 참고).
2. 오버라이드 설정/취소 폼 추가:
   - 노출 조건: `('ncr' in user_perms or 'approve' in user_perms) and header['status'] != 'superseded' and it['result'] in ('합격', '불합격')`
   - 없는 상태: "판정 수동변경" 버튼 → 합격/불합격 선택 + 사유(필수) 입력 폼 → `POST /inspection/{{ header['id'] }}/item-override`
   - 있는 상태: 사유·변경자(`it['override_by']`)·시각(`it['override_at']`, `|datetime_korean`) 표시 + "되돌리기" 버튼
   - 참고 패턴: 같은 화면의 NCR 생략 토글(`templates/inspection_detail.html:389-447`)
   - 사유 textarea는 `required` 필수

---

## Task 8: `approve_form.html` — 승인 전 검토 화면에도 오버라이드 반영 표시

**Files:** `templates/approve_form.html:360, 424`

`it['result']` 출력을 `it | effective_result`로 교체. 오버라이드된 항목은 "(수동)" 표시 추가. **설정 UI는 넣지 않는다** — 설정/취소는 `inspection_detail.html` 한 곳에서만(8-1절 원칙).

---

## Task 9: README.txt 갱신 + 전체 end-to-end 검증

1. README.txt에 비개발자 관점 안내 추가.
2. 전체 흐름 수동 검증(CLAUDE.md 10절):
   - 시각검사(O/X) 항목, AQL Ac1 이상 허용 자재로 성적서 생성 → 6개 중 1개 X → 자동판정 "합격" 확인
   - 상세화면에서 해당 항목 "불합격" 수동 오버라이드(사유 입력)
   - `/ncr/new/<id>` 진입 시 그 항목이 불합격 목록에 자동으로 잡히는지 확인
   - 승인 → 출력 → 생성된 xlsx/PDF에서 해당 항목이 여전히 "합격"으로 인쇄돼 있는지(공식 문서는 안 바뀌어야 함) 확인
   - `/inspection/<id>/verify` 결과가 정상(변조 오탐 없음)인지 확인
   - "되돌리기" 클릭 시 배지가 "합격"으로 복귀하는지 확인
   - `/logs`에 "항목 판정 수동변경"/"취소" 로그가 남는지 확인
3. quality-watcher 검증.

---

## 사용자에게 물어봐야 할 것 (확정 필요)

1. **권한**: `ncr`/`approve` 권한 재사용 vs 새 권한(예: `result_override`) 신설.
2. **승인된(approved) 성적서에도 오버라이드 허용**에 동의하는지 — 대신 공식 출력물(xlsx/PDF)은 그대로 유지(재출력해도 안 바뀜)하는 타협안. 재출력 시 PDF에도 반영을 원하면 승인회수→오버라이드→재승인을 강제하는 훨씬 복잡한 흐름이 필요.
3. **오버라이드가 성적서 전체 판정(overall_result)/승인 게이트에 영향을 주는지**: 이 계획은 항목 오버라이드가 전체 판정에 전혀 영향 없음으로 설계(개별 항목 표시 + NCR 생성용으로만). "항목 하나라도 수동 불합격 처리하면 성적서 전체도 불합격/검토필요로 바뀌어야 한다"를 원하면 `_final_decision_block_reason`·`quality_report` 집계까지 재설계 필요(범위 커짐).
4. **사유 필수 여부**: 설정 시 필수(취소/되돌리기는 사유 불필요)로 설계함 — 이견 없는지.
5. (3번과 연결) 승인자가 "항목 하나는 수동 불합격 표시돼 있는데 합격 승인 버튼을 누를 수 있는" 상태가 그대로 남는데, 이게 실제 운용에서 괜찮은지.

---

## 2026-09-14 갱신 — 사용자 결정 반영 (전체 판정 연동 설계 확정)

사용자가 확정한 4가지: ① 승인된 성적서도 오버라이드 허용(출력물은 그대로) ② **오버라이드가 성적서 전체 판정(overall_result)에도 연동돼야 함** ③ 권한은 `ncr`/`approve` 재사용 ④ 사유(reason) 필수.

②는 ①과 정면 충돌하는 지점이었다(승인 시점 해시가 `overall_result`를 포함해서 굳히는데, 승인 이후 오버라이드로 그 값이 바뀌면 무결성 검증이 "변조 의심"으로 오탐한다). 아래 설계로 해소했다.

### 핵심 해법

**`compute_content_hash()`가 `overall_result`를 "저장된 컬럼 값"이 아니라 "원본 항목 result로 그 자리에서 다시 계산한 값"으로 바꾼다.** 그러면:
- `inspections.overall_result` 컬럼 자체는 오버라이드에 따라 승인 이후에도 자유롭게 갱신 가능(표시/게이트용 필드가 됨)
- 해시는 "측정 당시 원본 항목 결과"만 보므로 오버라이드와 무관하게 고정 — 위변조 오탐 원천 차단
- 기존에 만들어진 모든 승인건은 `overall_result`가 지금까지 한 번도 사후 수정된 적이 없어서(코드 확인됨: `overall_result`를 쓰는 경로는 `create_inspection`/`update_inspection_items`/신규 오버라이드 재계산 셋뿐이고 항상 같은 공식을 씀) **이 공식 변경 전후로 기존 해시값이 완전히 동일** — 소급 파손 없음. 단, 배포 전 실제 승인건 하나로 `stored == recomputed` 재검증 필수(아래 Task 4 Step 5).

`status`/`approval_type`/서명/`pdf_hash`는 오버라이드가 **절대 자동으로 안 건드린다** — 8-2-8/8-2-10절 "최종결정은 서명+사유 필수, 회수는 별도 액션" 원칙 유지. `approve_revoke`도 재사용하지 않는다(완전히 독립된 액션). 실제 승인 결정 자체를 바꾸려면 여전히 사람이 "승인회수 → 재결정"을 별도로 밟아야 한다.

**quality_report/대시보드 집계(`_lot_state`, `status`+`approval_type` 기반)는 이번엔 손 안 댄다** — `overall_result`를 안 보므로 오버라이드해도 불량률 집계는 그대로. "화면상 검토필요인데 대시보드엔 여전히 합격으로 잡힘"이라는 간극이 남는데, 아래 열린 질문 참고.

### Global Constraints — 추가

```
- 항목 오버라이드 설정/취소는 성적서 상태(pending/approved/rejected)와 무관하게 항상
  inspections.overall_result를 effective_result 기준으로 재계산해서 갱신한다. 단
  status/approval_type/서명/content_hash/pdf_hash는 자동으로 안 건드린다 — 최종 결정
  변경은 여전히 승인회수 후 재결정이라는 별도의 사람 행위가 필요하다(8-2-8/8-2-10절).
- compute_content_hash()는 overall_result를 저장된 컬럼이 아니라 원본 항목 result로
  그 자리에서 재계산한 값을 쓴다 — 오버라이드로 컬럼이 사후에 바뀌어도 승인 시점 해시가
  절대 어긋나지 않게 하기 위함.
- 승인된(approved) 성적서에 활성 오버라이드가 있으면 상세화면에 경고 배너를 띄운다.
```

### Task 1 — 추가: `update_inspection_overall_result(inspection_id, overall_result)` 함수

`database.py`, `rename_inspection_item()` 다음(신규 오버라이드 함수들 옆)에 추가. status/approval_type/서명/해시는 안 건드리고 `overall_result` 컬럼만 UPDATE.

### Task 3 — 추가: `_recompute_overall_result(inspection_id)` 헬퍼

`app.py`, `effective_result()`/필터 등록 다음에 추가. `db.get_inspection()`으로 항목을 읽어 `all(effective_result(it) == "합격" for it in items)`로 "합격"/"검토필요" 계산 후, 바뀌었을 때만 `db.update_inspection_overall_result()` 호출. 반환값은 새 overall_result 문자열.

### Task 4 — 확장: `compute_content_hash()`도 같이 수정

기존 Task 4(읽기 경로 3곳 `effective_result()` 교체)에 더해:
- `inspection_detail()`에 `has_active_override = any(it["override_result"] for it in items)` 계산 추가, 템플릿에 전달.
- `compute_content_hash()`(`app.py:3501-3529`)의 payload 조립에서 `"overall_result": header["overall_result"]` 줄을, 원본 항목 result로 그 자리에서 재계산한 `raw_overall_result`(= `"합격" if all(it["result"]=="합격") else "검토필요"`)로 교체.
- **배포 전 필수 검증**: 개발 DB에서 `content_hash`가 있는 실제 승인건 하나를 골라 `stored == a.compute_content_hash(id)`를 재확인 — 실패하면 이 변경을 배포하면 안 됨(기존 승인건이 전부 "변조됨"으로 오탐하게 됨).

### Task 5 — 확장: 오버라이드 라우트에 재계산 호출 추가

`inspection_item_override()`의 `set`/`revert` 두 분기 모두, `db.set_item_override(...)`/`db.clear_item_override(...)` 호출 직후 `new_overall = _recompute_overall_result(inspection_id)`를 호출하고 `record_change()` 로그 문구에 `f" / 성적서 전체 판정: {new_overall}"`을 덧붙인다.

### Task 6 — 확장: 측정값 수정 시 살아남은 오버라이드도 재반영

기존 Task 6(판정 바뀐 항목의 오버라이드 해제)만으로는, **바뀌지 않은 항목의 살아있는 오버라이드**가 `edit_inspection_submit`의 자체 `overall_result` 재계산(원본 기준)에 반영이 안 돼서 오버라이드 이전 값으로 되돌아가는 회귀가 생긴다. `db.update_inspection_items(...)` 호출 직후 `_recompute_overall_result(inspection_id)`를 추가 호출해서 해결.

### Task 7 — 확장: 승인건 경고 배너

`inspection_detail.html` 상단(overall_result 배지 근처)에 `has_active_override and header['status'] == 'approved'`일 때만 보이는 배너 추가. 문구 예시(확정 필요, 아래 열린 질문 참고):
> "⚠ 항목 판정이 수동변경돼 승인 당시 판정(결과)과 달라졌어. 최종 승인 상태(합격승인/특채/불합격확정)는 그대로 유지되고 있어 — 이것 자체를 다시 결정하려면 승인회수 후 다시 진행해줘."

### 사용자에게 물어봐야 할 것 — 남은 것 2개

1. **quality_report/대시보드 집계까지 이번에 연동할지**: 이번 설계는 표시용 `overall_result`만 연동하고 불량률 집계(`status`+`approval_type` 기반)는 손 안 댄다(최종결정은 사람이 승인회수→재결정해야 바뀐다는 원칙 유지 때문). 이 간극을 이번에 메울지, 다음 기회로 미룰지.
2. **경고 배너 문구 확정**: 위 예시 문구 그대로 쓸지, 다른 표현을 원하는지.

---

## 2026-09-14 갱신2 — 대시보드 집계(`quality_report`) 연동

### Global Constraints — 추가

```
- `_lot_state()`는 오버라이드 반영 여부에 따라 두 가지 모드로 동작한다: `overall_result`
  인자를 넘기면(집계·통계용 호출) 오버라이드가 반영된 로트 상태를, 안 넘기면(기본값 None,
  공식 출력물용 호출) 예전 그대로 status+approval_type만 본 로트 상태를 돌려준다.
  공식 출력물(커스텀 자유양식 성적서 등)을 만드는 호출부는 절대로 이 인자를 넘기면 안 된다.
- 재분류는 "합격 승인(approval_type='normal')인데 오버라이드로 overall_result가
  '합격'이 아니게 된 경우 → 불합격으로 재분류" 이 한 방향만 존재한다. 특채/불합격확정
  로트는 오버라이드가 나중에 뭐가 되든 재분류하지 않는다(아래 표 참고).
```

### 재분류 규칙 표 (확정)

| 원래 상태 | 오버라이드 있음? | 재분류 후 `_lot_state()` | 근거 |
|---|---|---|---|
| `pending`/`rejected` | 무관 | `미결` (그대로) | 최종 결정 전이므로 집계에서 계속 제외 |
| `approved`+`normal` | 없음, 또는 effective overall_result 여전히 '합격' | `합격` (그대로) | 재분류 조건 미충족 |
| `approved`+`normal` | **있고 overall_result가 '검토필요'** | **`불합격`로 재분류** | 배경 사고 사례(#774) 간극을 메움 |
| `approved`+`special`(특채) | 무관 | `특채` (그대로) | 특채는 게이트상 overall_result가 애초에 항상 '합격'이 아닌 상태로만 존재 |
| `approved`+`failed`(불합격확정) | 무관 | `불합격` (그대로) | 이미 서명받은 최종 결정을 오버라이드로 되돌리지 않음 |
| `superseded` | 무관 | `대체됨` (그대로) | 집계에서 원래도 통째로 제외 |

**분모(판정확정=합격+특채+불합격) 불변**: 재분류가 일어나는 유일한 케이스(합격승인→불합격)는 원래도 확정수량에 포함돼 있던 로트가 버킷만 이동하는 것 — 분모는 그대로, 분자(불합격수량)만 늘어난다. `finish()`(`database.py:3639-3651`)는 안 건드려도 PPM·규격이탈률까지 자동으로 따라간다.

### Task 10: `_lot_state()`에 `overall_result` 파라미터 추가 + 호출부 3곳 반영

**Files:**
- `database.py:3359-3374`(`_lot_state`) — 시그니처에 `overall_result=None` 추가. `approved`+`normal`인데 `overall_result is not None and overall_result != "합격"`이면 `"불합격"` 반환, 그 외 로직은 동일.
- `database.py:3548`, `3623`(`quality_report` 내부 두 호출부) — `_lot_state(r["status"], r["approval_type"], r["overall_result"])`로 세 번째 인자 추가.
- `app.py:4284`(`_collect_approval_history`) — 마찬가지로 세 번째 인자 추가(안 하면 대시보드와 승인이력 화면의 로트상태 버킷이 서로 어긋나는 새 불일치가 생김).
- **건드리지 않음**: `app.py:4898`(`_custom_fields_from_header`, 커스텀 성적서 "종합판정" 필드) — 공식 출력물이라 승인 당시 라벨 유지. 개발자는 diff 리뷰 시 이 줄이 실수로 안 바뀌었는지 반드시 확인할 것.

검증 스크립트로 `_lot_state()` 6개 케이스(합격유지/불합격재분류/특채유지/불합격확정유지/미결/인자생략 하위호환)와 실제 `quality_report()` 호출까지 확인.

### 성능

문제없음, 조치 불필요 — 오버라이드 테이블을 새로 조인하지 않는다(`inspections.overall_result`는 이미 `SELECT *`로 가져오던 컬럼). 소규모 내부 시스템이라 함수 인자 하나 추가 수준의 오버헤드는 무시 가능.

### 참고사항(조치 불필요)

업체 월간 품질 성적표(8-2-7절)는 생성 시점에 `quality_report()` 결과를 JSON 스냅샷으로 고정 저장한다 — 이미 생성된(draft/approved/sent) 성적표는 이번 재분류 로직 변경과 무관하게 옛 스냅샷 그대로 남는다. 이후 그 기간을 다시 생성해야 재분류가 반영된다(기존 설계상 당연한 동작, 별도 조치 불필요).

---

## 사용자 확정 사항 요약 (최종, 열린 질문 없음)

1. 승인된 성적서도 오버라이드 허용, 공식 출력물(xlsx/PDF/커스텀양식)은 절대 안 바뀜.
2. 오버라이드는 `inspections.overall_result`(표시용) + 대시보드 집계(`quality_report`/`_lot_state`)에 반영. `status`/`approval_type`/서명/해시는 오버라이드가 자동으로 안 건드림 — 최종결정 자체를 바꾸려면 여전히 승인회수→재결정 필요.
3. 권한: `ncr` 또는 `approve` 재사용.
4. 사유(reason) 오버라이드 설정 시 필수, 취소는 불필요.
5. 승인건에 활성 오버라이드 있으면 경고 배너(문구 확정: "⚠ 항목 판정이 수동변경돼 승인 당시 판정(결과)과 달라졌어. 최종 승인 상태(합격승인/특채/불합격확정)는 그대로 유지되고 있어 — 이것 자체를 다시 결정하려면 승인회수 후 다시 진행해줘.")

**Execution Handoff**: Task 1~10 순서대로 developer 에이전트가 구현. 각 태스크 커밋 후 최종적으로 quality-watcher 검증 + Task 9의 end-to-end 시나리오를 실제로 재현해서 확인.

---

## 파일 경로 요약

- `database.py` — 신규 테이블 + `set_item_override`/`clear_item_override` + `get_inspection()` 조인 수정
- `app.py` — `effective_result()` 헬퍼, 신규 라우트 `inspection_item_override`, 기존 함수 4곳(`inspection_detail`, `approve_view`, `ncr_new`, `edit_inspection_submit`) 수정
- `templates/inspection_detail.html` — 오버라이드 UI(신규)
- `templates/approve_form.html` — 오버라이드 반영 표시(읽기 전용)
- `README.txt` — 사용자 안내 갱신
