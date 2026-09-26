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

**2026-09-26 정정**: 로컬 개발DB에는 M(X-선검사, 별도항목)과 N(외관검사)이 둘 다
존재해서 "M=X-선검사, 아직 미사용이니 더미처리"로 처음 판단했었다. 그런데 실제
프로덕션 DB를 조회해보니 로컬과 데이터가 달랐다 — 602013P015(15KV)는 A~L+N(외관검사)
14개 항목이고 M 자체가 없고, 604013P000/608013P011(27KV/38KV)은 A~L+M(외관검사)
14개 항목이고 N이 없다. 즉 프로덕션엔 X-선검사라는 별도 항목이 애초에 존재하지
않고, "M"은 27KV/38KV 두 자재에서 그냥 "외관검사"를 가리키는 문자일 뿐이었다
(로컬DB의 M=X-선검사는 실사용 데이터가 아닌 로컬 전용 테스트 데이터였던 것으로
추정 — 프로덕션과 로컬이 왜 갈렸는지 정확한 경위는 추적 안 함). 그래서 M을
DISABLE_ITEMS로 비활성화했던 최초 실행이 27KV/38KV 두 자재의 진짜 외관검사
항목을 2차 화면에서 사라지게 만드는 실제 사고였다 — 이 스크립트 재실행으로
바로잡는다: M도 N과 동일하게 2차(외관검사)로 분류한다.
"""
import sys, io, sqlite3
import database as db

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

HOUSING = ['602013P015', '604013P000', '608013P011']

STAGE_MAP = {
    'I': 1, 'J': 1, 'K': 1, 'L': 1,
    'D': 2, 'E': 2, 'F': 2, 'R': 2, 'G': 2, 'H': 2, 'V': 2, 'N': 2, 'M': 2,
    'A': 3, 'B': 3, 'C': 3,
}

# 프로덕션 실측 결과 X-선검사라는 별도 항목이 실재하지 않는 걸로 확인돼서 이제
# 비어있다 — 나중에 진짜 X-선검사 항목이 추가되면 그 item_name을 여기 넣으면 된다.
DISABLE_ITEMS = set()

# db.DB_PATH = DATA_DIR(영구디스크)/iqc.db — 상대경로 'iqc.db'는 실행 위치(예: /app)의
# 빈 파일을 봐서 "no such table" 에러가 나는 실제 있었던 사고. 항상 이 경로를 써야 한다.
conn = sqlite3.connect(db.DB_PATH)
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
