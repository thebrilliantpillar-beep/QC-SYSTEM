# -*- coding: utf-8 -*-
"""
602106P103 규격 데이터 — 성적서_양식.xlsx(602106P103 시트)에서 직접 추출한 실제 값.
A~G 7항목이며, 항목명 대신 A/B/C.. 기호만 존재 (원본 양식 그대로).
E항목 '63 - 0.2'는 한쪽 공차로 확인됨 -> 하한 62.8 / 상한 63.0.
"""
from database import get_conn, add_spec


def already_seeded():
    conn = get_conn()
    row = conn.execute(
        "SELECT COUNT(*) as cnt FROM specs WHERE material_no = ?",
        ("602106P103",)
    ).fetchone()
    conn.close()
    return row["cnt"] > 0


def seed_specs():
    if already_seeded():
        print("이미 시드 데이터가 있어. 중복 삽입 방지를 위해 건너뜀.")
        return

    material_no = "602106P103"
    material_name = "COIL PART (CKMR7317)"

    # (항목기호, 표기, 판정유형, 하한, 상한, 검사방법, AQL, 순서)
    items = [
        ("A", "108.5 ± 0.8",                "numeric", 107.7, 109.3, "버니어캘리퍼스", 4,    1),
        ("B", "77.5 ± 0.5",                 "numeric", 77.0,  78.0,  "버니어캘리퍼스", 4,    2),
        ("C", "97.5 ± 0.5",                 "numeric", 97.0,  98.0,  "버니어캘리퍼스", 4,    3),
        ("D", "93.5 ± 0.5",                 "numeric", 93.0,  94.0,  "버니어캘리퍼스", 4,    4),
        ("E", "63 - 0.2",                   "numeric", 62.8,  63.0,  "버니어캘리퍼스", 4,    5),
        ("F", "저항 19옴 ± 10% (17.1~20.9)", "numeric", 17.1,  20.9,  "LCR측정기",     0.65, 6),
        ("G", "외관",                        "visual",  None,  None,  "육안",           1.5,  7),
    ]

    for name, disp, jtype, lo, hi, method, aql, order in items:
        add_spec(material_no, material_name, name, disp, jtype, lo, hi, method, aql, order)

    print(f"{material_no} 규격 {len(items)}항목 등록 완료 (실제 성적서 양식 기준)")


if __name__ == "__main__":
    from database import init_db
    init_db()
    seed_specs()
