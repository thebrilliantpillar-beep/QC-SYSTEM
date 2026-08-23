"""
자동출력/성적서 폴더의 모든 xlsx 파일에서 규격(specs)을 일괄 파싱해서 DB에 등록.

사용법:
  python batch_import_specs.py          -- 미리보기(dry-run, DB 변경 없음)
  python batch_import_specs.py --commit -- 실제 등록

기존 자재에 같은 항목기호(item_name)의 규격이 이미 있으면 SKIP(덮어쓰지 않음).
새 자재 + 새 항목만 추가한다.
"""
import os, sys
sys.path.insert(0, os.path.dirname(__file__))

import database as db
import spec_import as si
from datetime import datetime as _dt

FOLDER = r"C:\Users\Jaiden\Desktop\iqc-app\자동출력\성적서"
COMMIT = "--commit" in sys.argv

db.init_db()

# 현재 DB에 있는 specs (자재번호+항목기호 조합)
import sqlite3
conn = sqlite3.connect(db.DB_PATH)
existing = set(
    (r[0], r[1])
    for r in conn.execute("SELECT material_no, item_name FROM specs").fetchall()
)
existing_materials = set(
    r[0] for r in conn.execute("SELECT material_no FROM materials").fetchall()
)
conn.close()

print(f"현재 DB: 자재 {len(existing_materials)}개, 규격항목 {len(existing)}개\n")
print(f"모드: {'실제 등록 (--commit)' if COMMIT else '미리보기 (dry-run)'}\n")

# 결과 집계
stats = {
    "files": 0, "sheets_total": 0, "sheets_ok": 0, "sheets_skip": 0,
    "new_materials": 0, "new_specs": 0, "skipped_specs": 0, "parse_fail": 0,
    "warn_sheets": [],
}

xlsx_files = sorted(f for f in os.listdir(FOLDER) if f.lower().endswith(".xlsx"))
print(f"파일 {len(xlsx_files)}개 처리 시작...\n")

for fname in xlsx_files:
    fpath = os.path.join(FOLDER, fname)
    stats["files"] += 1
    try:
        results = si.parse_spec_file(fpath, source_name=fname)
    except Exception as e:
        print(f"[파일 오류] {fname}: {e}")
        stats["parse_fail"] += 1
        continue

    for material_no, material_name, items, warnings, fail_reason in results:
        stats["sheets_total"] += 1

        if fail_reason or not material_no:
            stats["sheets_skip"] += 1
            continue

        if not items:
            stats["sheets_skip"] += 1
            continue

        stats["sheets_ok"] += 1
        is_new_material = material_no not in existing_materials

        # 새 자재는 materials 테이블에 추가
        if is_new_material:
            stats["new_materials"] += 1
            if COMMIT:
                conn2 = sqlite3.connect(db.DB_PATH)
                today = _dt.now().strftime("%Y-%m-%d")
                conn2.execute("""
                    INSERT OR IGNORE INTO materials (material_no, material_name, revision_date)
                    VALUES (?, ?, ?)
                """, (material_no, material_name or "", today))
                conn2.commit()
                conn2.close()
                existing_materials.add(material_no)
            print(f"  [신규자재] {material_no} — {material_name or '(자재명 없음)'} ({len(items)}개 항목)")

        # 항목별 등록
        for order, item in enumerate(items):
            key = (material_no, item["item_name"])
            if key in existing:
                stats["skipped_specs"] += 1
                continue

            stats["new_specs"] += 1
            if COMMIT:
                db.add_spec(
                    material_no=material_no,
                    material_name=material_name or "",
                    item_name=item["item_name"],
                    spec_display=item.get("spec_display", ""),
                    judge_type=item.get("judge_type", "numeric"),
                    lower_limit=item.get("lower_limit"),
                    upper_limit=item.get("upper_limit"),
                    inspect_method=item.get("inspect_method", ""),
                    aql=item.get("aql"),
                    item_order=order,
                )
                existing.add(key)

        if warnings:
            stats["warn_sheets"].append((material_no, warnings))

print("\n" + "=" * 60)
print(f"처리 완료")
print(f"  파일: {stats['files']}개")
print(f"  시트: {stats['sheets_total']}개 (성공 {stats['sheets_ok']}, 건너뜀 {stats['sheets_skip']})")
print(f"  신규 자재: {stats['new_materials']}개")
print(f"  신규 규격 항목: {stats['new_specs']}개")
print(f"  이미 있어서 SKIP: {stats['skipped_specs']}개")
print(f"  파일 오류: {stats['parse_fail']}개")

if stats["warn_sheets"]:
    print(f"\n주의가 필요한 시트 ({len(stats['warn_sheets'])}개):")
    for mn, warns in stats["warn_sheets"][:20]:
        print(f"  {mn}: {warns[0]}")

if not COMMIT:
    print(f"\n→ 실제 등록하려면: python batch_import_specs.py --commit")
else:
    print(f"\n→ DB 등록 완료")
