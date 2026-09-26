"""하우징 3종 자재 — 3차 절연내력시험 'A'(충격파내전압, 파두장/파미장 규격) 항목을
숫자입력(judge_type='numeric')에서 O/X 판정(judge_type='ok_ng')으로 변경.

실사용 피드백(2026-09-27): "...각3회파두장±3%(0.84~1.56)파미장±20%..." 항목은 숫자를
재는 게 아니라 파형이 규격 범위 안에 드는지 육안/장비로 O/X 판정하는 항목이라, 다른
O/X 항목(V=VD쇼트 등)처럼 드롭다운으로 바꿔달라는 요청. _housing_columns_by_stage()
(app.py)가 judge_type만 보고 num/pf를 가르므로 이 필드 하나만 바꾸면 STAGE 입력화면·
report_builder 양쪽에 자동 반영된다(8-1절 원칙 — 별도 화면단 분기 안 만듦).
lower_limit/upper_limit(0.84/1.56)은 과거 규격 참고용으로 남겨두고 지우지 않는다
(judge_type이 numeric/numeric_pair가 아니면 애초에 안 쓰임 — app.py 5237~5238행).

CLAUDE.md 26-2 원칙: db.DB_PATH(절대경로) 사용, 상대경로 'iqc.db' 금지.
"""
import sys, io, sqlite3
import database as db

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

HOUSING = ['602013P015', '604013P000', '608013P011']

conn = sqlite3.connect(db.DB_PATH)
conn.row_factory = sqlite3.Row
cur = conn.cursor()

for mn in HOUSING:
    row = cur.execute(
        "SELECT id, judge_type, spec_display FROM specs WHERE material_no=? AND item_name='A'",
        (mn,)).fetchone()
    if row is None:
        print(f'{mn}: item A 없음 — 스킵')
        continue
    if row['judge_type'] == 'ok_ng':
        print(f'{mn}: 이미 ok_ng — 스킵 ({row["spec_display"]!r})')
        continue
    cur.execute("UPDATE specs SET judge_type='ok_ng' WHERE id=?", (row['id'],))
    print(f'{mn}: judge_type {row["judge_type"]!r} -> \'ok_ng\' ({row["spec_display"]!r})')

conn.commit()
conn.close()
print('\nDONE.')
