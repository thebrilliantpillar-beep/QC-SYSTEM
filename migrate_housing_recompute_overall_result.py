"""하우징 pending 성적서 전부의 inspections.overall_result를 새 규칙으로 다시 계산한다.

배경(2026-09-28): _recompute_housing_overall_result()의 규칙을 사용자 피드백으로
바로잡았다 — "검토필요"는 "측정 완료 + 이상(NG) 있음"만 뜻하고, 미완료인데 값이 일부
있으면 "진행중", 아예 없으면 ""(대기중)이어야 한다. 이 함수는 STAGE 저장(full_inspect_save)
시점마다 자동 호출되므로 앞으로는 저절로 맞게 유지되지만, 이미 값이 일부/전부 들어가
있는 기존 건들은 다음에 뭔가 저장할 때까지 예전 값("검토필요"/"합격"/"불합격")에 머물러
있다 — 이 스크립트가 지금 한 번에 다시 계산해서 맞춰준다.

app.py의 실제 함수(_recompute_housing_overall_result)를 그대로 재사용한다(8-1절,
판정로직을 스크립트에 복붙하지 않음). CLAUDE.md 26-2 원칙: db.DB_PATH 그대로 사용.
"""
import sys, io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

import database as db
import app as app_module

housing_map = db.list_housing_materials()
targets = [r for r in db.list_inspections(status="pending") if r["material_no"] in housing_map]

for r in targets:
    before = r["overall_result"]
    after = app_module._recompute_housing_overall_result(r["id"])
    mark = " (변경)" if before != after else ""
    print(f'  id={r["id"]} ({r["material_no"]}) {before!r} -> {after!r}{mark}')

print(f'\n총 {len(targets)}건 재계산 완료. DONE.')
