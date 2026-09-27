"""하우징(및 기타 전수검사) 자재 — 아직 STAGE에서 아무 것도 측정 안 한 pending 성적서의
overall_result를 "검토필요"에서 ""(빈 값)로 되돌린다.

배경(2026-09-28): _auto_start_full_inspections()가 예전엔 입고 즉시 overall_result를
"검토필요"로 채워 넣어서, 아직 아무 것도 측정 안 한 신규 건까지 "검토필요" 배지가 붙어
보이는 버그가 있었다(코드는 이미 고쳤음 — 이제부터 새로 등록되는 건은 ""로 시작한다).
이 스크립트는 이미 그 버그 상태로 만들어진 기존 건들을 정리하는 1회성 백필이다.

"아직 아무 것도 측정 안 함" = full_inspections 행이 아예 없거나, 있어도
full_inspection_units에 값이 하나도 없는 경우. 조금이라도 값이 저장된 건은(진행중이든
완료든) 건드리지 않는다 — 그런 건은 _recompute_housing_overall_result()가 다음 저장
시점에 실제 값 기준으로 다시 계산해줄 것이고, 지금 섣불리 건드리면 오히려 실제 진행
상황을 지워버리는 꼴이 된다.

CLAUDE.md 26-2 원칙: db.DB_PATH(절대경로) 사용, 상대경로 'iqc.db' 금지.
"""
import sys, io, sqlite3, json
import database as db

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

conn = sqlite3.connect(db.DB_PATH)
conn.row_factory = sqlite3.Row
cur = conn.cursor()

# 전수검사 설정이 있는 자재(하우징 포함) 목록
material_nos = {r['material_no'] for r in cur.execute(
    "SELECT material_no FROM materials WHERE full_inspect_config IS NOT NULL")}

candidates = cur.execute(
    "SELECT id, material_no, overall_result FROM inspections "
    "WHERE status='pending' AND overall_result='검토필요'").fetchall()

fixed = 0
for r in candidates:
    if r['material_no'] not in material_nos:
        continue  # 전수검사 자재가 아니면(일반 자재의 정상적인 '검토필요') 손대지 않음
    fi = cur.execute("SELECT id FROM full_inspections WHERE inspection_id=?", (r['id'],)).fetchone()
    has_value = False
    if fi:
        units = cur.execute(
            "SELECT values_json FROM full_inspection_units WHERE full_inspection_id=?",
            (fi['id'],)).fetchall()
        for u in units:
            try:
                vals = json.loads(u['values_json'] or '{}')
            except Exception:
                vals = {}
            if any(str(v).strip() for v in vals.values()):
                has_value = True
                break
    if has_value:
        print(f'  id={r["id"]} ({r["material_no"]}) 이미 값이 있어 건드리지 않음')
        continue
    cur.execute("UPDATE inspections SET overall_result='' WHERE id=?", (r['id'],))
    fixed += 1
    print(f'  id={r["id"]} ({r["material_no"]}) 검토필요 -> "" (대기중으로 정정)')

conn.commit()
conn.close()
print(f'\n총 {fixed}건 정정 완료. DONE.')
