"""하우징 3종 자재 — specs.stage_group(1차/2차/3차) 채우기 (하우징 성적서 2단계).

PROGRESS.md 2026-09-25 설계: "item_order 기준 위치로 판단"이라고 적혀 있었지만
실제 0)단계 조회 결과(2026-09-26) item_order는 [3차 3개 → 2차 7개 → 1차 4개 →
X-선검사 → 외관검사] 순서라 "맨 앞 4개=1차/맨 뒤 3개=3차"라는 위치 가정과 정반대였다.
그래서 위치가 아니라 item_name(기호) 자체로 분류한다 — 3개 자재 전부 A~N(+V,R) 기호
구조가 동일하다는 걸 0)단계 조회로 확인했다.

- 1차 치수검사(AQL 샘플): I, J, K, L (외경 치수 4종, 전부 numeric)
- 2차 기능검사(전수): D(CT극성) E(CT저항값) F(고저항 CT SIDE) R(고저항 ROD SIDE)
  G(주회로저항) H(동작검사) V(VD쇼트) N(외관검사)
- 3차 절연내력시험(전수): A(충격파내전압) B(내전압검사) C(PD충격)

M(X-선 검사: 균열·기포 등 이상 없음)은 PROGRESS.md 설계 문서의 2차 항목 나열
(외관·CT극성·CT저항·VD쇼트·고저항·동작·주회로저항)에 원래 없던 항목이라 애매해서
처음엔 2차로 임시분류했었다. 2026-09-26 사용자 확인: "일단 더미 데이터로 만들고
필요할 때 활성화 하자" — X-선 검사 장비/공정이 아직 준비 안 됐다는 뜻으로 해석,
stage_group을 NULL로 되돌려서 비활성화한다. app.py의 _housing_columns_by_stage()가
stage_group이 1/2/3이 아니면 그 항목을 조용히 건너뛰도록 이미 짜여 있어서(반드시
그 원칙을 유지할 것 — 새 "활성/비활성" 플래그를 따로 만들지 마라, 8-1절), M은
입력화면·성적서 어디에도 안 나타난다. 나중에 활성화하려면 이 스크립트의
STAGE_MAP에 'M': 2(또는 맞는 차수)를 다시 추가하고 재실행하면 끝 — 코드 변경 불필요.
"""
import sys, io, sqlite3

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

HOUSING = ['602013P015', '604013P000', '608013P011']

STAGE_MAP = {
    'I': 1, 'J': 1, 'K': 1, 'L': 1,
    'D': 2, 'E': 2, 'F': 2, 'R': 2, 'G': 2, 'H': 2, 'V': 2, 'N': 2,
    'A': 3, 'B': 3, 'C': 3,
}

# M(X-선 검사)은 2026-09-26 사용자 지시로 당장은 비활성(더미) — stage_group을
# 명시적으로 NULL 처리한다(STAGE_MAP에서 그냥 빼기만 하면 기존 값이 안 지워짐).
DISABLE_ITEMS = {'M'}

conn = sqlite3.connect('iqc.db')
conn.row_factory = sqlite3.Row
cur = conn.cursor()

existing_cols = [row[1] for row in cur.execute("PRAGMA table_info(specs)").fetchall()]
if "stage_group" not in existing_cols:
    cur.execute("ALTER TABLE specs ADD COLUMN stage_group INTEGER DEFAULT NULL")
    print("specs.stage_group 컬럼 추가함")

for mn in HOUSING:
    print(f'\n=== {mn} ===')
    rows = cur.execute("SELECT id, item_name, item_order, stage_group FROM specs "
                        "WHERE material_no=? ORDER BY item_order", (mn,)).fetchall()
    for r in rows:
        if r['item_name'] in DISABLE_ITEMS:
            if r['stage_group'] is None:
                print(f'  {r["item_name"]}: 이미 비활성(stage_group=NULL) — 스킵')
                continue
            cur.execute("UPDATE specs SET stage_group=NULL WHERE id=?", (r['id'],))
            print(f'  {r["item_name"]}: stage_group -> NULL (비활성화)')
            continue
        stage = STAGE_MAP.get(r['item_name'])
        if stage is None:
            print(f'  ! 매핑 없음: item_name={r["item_name"]!r} (item_order={r["item_order"]}) — 건드리지 않음')
            continue
        if r['stage_group'] == stage:
            print(f'  {r["item_name"]}: 이미 stage_group={stage} — 스킵')
            continue
        cur.execute("UPDATE specs SET stage_group=? WHERE id=?", (stage, r['id']))
        print(f'  {r["item_name"]}: stage_group -> {stage}')

conn.commit()

print('\n\n=== 최종 상태 ===')
for mn in HOUSING:
    print(f'\n[{mn}]')
    for r in conn.execute('SELECT item_order, item_name, judge_type, stage_group, spec_display '
                           'FROM specs WHERE material_no=? ORDER BY item_order', (mn,)):
        print(f'  {r["item_order"]:2} {r["item_name"]:3} stage={r["stage_group"]} '
              f'{r["judge_type"]:14} {r["spec_display"]!r}')

conn.close()
print('\nDONE.')
