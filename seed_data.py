# -*- coding: utf-8 -*-
"""초기 규격 데이터 시딩 — 처음 한 번만 실행. 다시 실행해도 안전(덮어쓰기)."""
from database import init_db, upsert_material, upsert_spec_item

init_db()

upsert_material("602106P103", "COIL PART (CKMR7317)")

items = [
    ("A", 1, "108.5 ± 0.8", 107.7, 109.3, "수치", "버니어캘리퍼스", 2),
    ("B", 2, "77.5 ± 0.5", 77.0, 78.0, "수치", "버니어캘리퍼스", 2),
    ("C", 3, "97.5 ± 0.5", 97.0, 98.0, "수치", "버니어캘리퍼스", 2),
    ("D", 4, "93.5 ± 0.5", 93.0, 94.0, "수치", "버니어캘리퍼스", 2),
    ("E", 5, "63 - 0.2", 62.8, 63.0, "수치", "버니어캘리퍼스", 2),
    ("F", 6, "저항 19Ω ± 10% (17.1~20.9)", 17.1, 20.9, "수치", "LCR측정기", 3),
    ("G", 7, "외관", None, None, "육안", "육안", 1),
]
for item, order, label, lo, hi, kind, method, n in items:
    upsert_spec_item("602106P103", item, order, label, lo, hi, kind, method, n)

print("시딩 완료: 602106P103 (7개 항목)")
