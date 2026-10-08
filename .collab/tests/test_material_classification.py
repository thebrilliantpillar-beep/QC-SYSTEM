# -*- coding: utf-8 -*-
import json
import os
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor

import database as db


ASSET_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))),
                          "data", "material_classifications.json")


class MaterialClassificationTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.old_db_path = db.DB_PATH
        db.DB_PATH = os.path.join(self.tmp.name, "test.db")
        db.init_db()
        db.ensure_ncr_columns_migration()

    def tearDown(self):
        db.DB_PATH = self.old_db_path
        self.tmp.cleanup()

    def _asset(self):
        with open(ASSET_PATH, encoding="utf-8") as f:
            return json.load(f)

    def test_asset_bootstrap_is_idempotent_and_keeps_material_name(self):
        sample = self._asset()["mappings"][0]
        db.upsert_material(sample["material_no"], "운영 자재명")

        first = db.ensure_material_classification_asset(ASSET_PATH)
        second = db.ensure_material_classification_asset(ASSET_PATH)

        self.assertTrue(first["applied"])
        self.assertFalse(second["applied"])
        self.assertEqual(first["count"], 914)
        material = db.get_material(sample["material_no"])
        self.assertEqual(material["material_name"], "운영 자재명")
        self.assertEqual(material["major_category"], sample["major_category"])
        self.assertEqual(material["middle_category"], sample["middle_category"])

    def test_concurrent_bootstrap_is_serialized(self):
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda _: db.ensure_material_classification_asset(ASSET_PATH),
                                    range(2)))
        self.assertEqual(sorted(r["applied"] for r in results), [False, True])
        conn = db.get_conn()
        count = conn.execute("SELECT COUNT(*) FROM material_classification_map").fetchone()[0]
        conn.close()
        self.assertEqual(count, 914)

    def test_conflicting_or_blank_asset_is_rejected_without_partial_write(self):
        db.update_material_classification("MANUAL-1", "수동대분류", "수동중분류")
        before = [dict(r) for r in db.list_material_classification_pairs()]
        bad_path = os.path.join(self.tmp.name, "bad.json")
        with open(bad_path, "w", encoding="utf-8") as f:
            json.dump({"mapping_count": 2, "mappings": [
                {"material_no": "A", "major_category": "대1", "middle_category": "중1"},
                {"material_no": "A", "major_category": "대2", "middle_category": "중2"},
            ]}, f, ensure_ascii=False)
        with self.assertRaisesRegex(ValueError, "충돌"):
            db.ensure_material_classification_asset(bad_path)
        self.assertEqual([dict(r) for r in db.list_material_classification_pairs()], before)
        self.assertIsNone(db.get_setting("material_classification_asset_sha256"))

        with open(bad_path, "w", encoding="utf-8") as f:
            json.dump({"mapping_count": 1, "mappings": [
                {"material_no": "B", "major_category": "", "middle_category": "중"},
            ]}, f, ensure_ascii=False)
        with self.assertRaisesRegex(ValueError, "필수값 공백"):
            db.ensure_material_classification_asset(bad_path)

    def test_future_registration_and_missing_material_are_resolved_by_join(self):
        sample = self._asset()["mappings"][0]
        db.ensure_material_classification_asset(ASSET_PATH)
        db.upsert_material(sample["material_no"], "나중에 등록")
        db.upsert_material("FILE-MISSING", "파일 미포함")

        matched = db.get_material(sample["material_no"])
        missing = db.get_material("FILE-MISSING")
        self.assertEqual((matched["major_category"], matched["middle_category"]),
                         (sample["major_category"], sample["middle_category"]))
        self.assertIsNone(missing["major_category"])
        self.assertIsNone(missing["middle_category"])
        rows = db.search_materials(major_category=db.UNASSIGNED_CLASSIFICATION)
        self.assertEqual([r["material_no"] for r in rows], ["FILE-MISSING"])

    def test_major_and_middle_filters_use_and_semantics(self):
        for no in ("A", "B", "C"):
            db.upsert_material(no, no)
        db.update_material_classification("A", "전기", "CT")
        db.update_material_classification("B", "전기", "PCB")
        db.update_material_classification("C", "기구", "CT")

        rows = db.search_materials(major_category="전기", middle_category="CT")
        self.assertEqual([r["material_no"] for r in rows], ["A"])
        self.assertEqual([r["material_no"] for r in db.search_materials(major_category="전기")],
                         ["A", "B"])

    def test_new_asset_version_removes_only_old_asset_mappings(self):
        first_path = os.path.join(self.tmp.name, "first.json")
        second_path = os.path.join(self.tmp.name, "second.json")
        with open(first_path, "w", encoding="utf-8") as f:
            json.dump({"mapping_count": 2, "mappings": [
                {"material_no": "A", "major_category": "대1", "middle_category": "중1"},
                {"material_no": "B", "major_category": "대2", "middle_category": "중2"},
            ]}, f, ensure_ascii=False)
        db.ensure_material_classification_asset(first_path)
        db.update_material_classification("MANUAL", "수동대", "수동중")
        for no in ("A", "B", "MANUAL"):
            db.upsert_material(no, no)

        with open(second_path, "w", encoding="utf-8") as f:
            json.dump({"mapping_count": 1, "mappings": [
                {"material_no": "A", "major_category": "대1", "middle_category": "중1"},
            ]}, f, ensure_ascii=False)
        db.ensure_material_classification_asset(second_path)

        self.assertEqual(db.get_material("A")["major_category"], "대1")
        self.assertIsNone(db.get_material("B")["major_category"])
        self.assertEqual(db.get_material("MANUAL")["major_category"], "수동대")

    def test_bom_list_includes_classification_even_before_material_registration(self):
        db.update_material_classification("BOM-1", "기구", "하우징")
        conn = db.get_conn()
        conn.execute("""INSERT INTO material_bom_links
                      (material_no, model_name, level, bom_name) VALUES (?, ?, ?, ?)""",
                     ("BOM-1", "MODEL", 1, "BOM 이름"))
        conn.commit()
        conn.close()

        rows = db.search_bom_materials(major_category="기구", middle_category="하우징")
        self.assertEqual(len(rows), 1)
        self.assertIsNone(rows[0]["material_name"])
        self.assertEqual(rows[0]["major_category"], "기구")
        self.assertEqual(rows[0]["middle_category"], "하우징")

    def test_dashboard_report_filter_and_rows_include_both_categories(self):
        for no, middle in (("A", "CT"), ("B", "PCB")):
            db.upsert_material(no, no)
            db.update_material_classification(no, "전기", middle)
        conn = db.get_conn()
        for no in ("A", "B"):
            conn.execute("""INSERT INTO inspections
                          (material_no, material_name, supplier, po_number, inspect_date,
                           quantity, overall_result, status, approval_type)
                          VALUES (?, ?, '업체', 'LOT', '2026-10-01', 1, '합격', 'approved', 'normal')""",
                         (no, no))
        conn.commit()
        conn.close()

        report = db.quality_report("2026-10-01", "2026-10-31",
                                   major_category="전기", middle_category="CT")
        self.assertEqual([r["material_no"] for r in report["성적서목록"]], ["A"])
        self.assertEqual(report["자재별"][0]["대분류"], "전기")
        self.assertEqual(report["자재별"][0]["중분류"], "CT")
        self.assertEqual(report["필터"]["대분류"], "전기")
        self.assertEqual(report["필터"]["중분류"], "CT")

    def test_rename_moves_mapping_and_old_reregistration_stays_unassigned(self):
        db.upsert_material("OLD", "기존 자재")
        db.update_material_classification("OLD", "전기", "CT")

        success, error = db.rename_material("OLD", "NEW", "변경 자재")

        self.assertTrue(success, error)
        self.assertIsNone(db.get_material("OLD"))
        renamed = db.get_material("NEW")
        self.assertEqual(renamed["material_name"], "변경 자재")
        self.assertEqual((renamed["major_category"], renamed["middle_category"]),
                         ("전기", "CT"))
        conn = db.get_conn()
        try:
            self.assertIsNone(conn.execute(
                "SELECT 1 FROM material_classification_map WHERE material_no = 'OLD'"
            ).fetchone())
        finally:
            conn.close()

        db.upsert_material("OLD", "옛 번호 재등록")
        restored_old = db.get_material("OLD")
        self.assertIsNone(restored_old["major_category"])
        self.assertIsNone(restored_old["middle_category"])

    def test_rename_classification_conflict_rolls_back_all_changes(self):
        db.upsert_material("OLD", "기존 자재")
        db.update_material_classification("OLD", "전기", "CT")
        # 분류 매핑은 자재 등록 전에도 존재할 수 있으므로 NEW 자재 없이 충돌을 재현한다.
        db.update_material_classification("NEW", "기구", "하우징")
        db.add_spec("OLD", "기존 자재", "A", "10±1", "numeric", 9, 11, "측정", "4", 1)

        success, error = db.rename_material("OLD", "NEW", "변경 자재")

        self.assertFalse(success)
        self.assertIn("서로 다른 대분류/중분류", error)
        self.assertEqual(db.get_material("OLD")["material_name"], "기존 자재")
        self.assertIsNone(db.get_material("NEW"))
        self.assertEqual(len(db.get_specs_by_material("OLD")), 1)
        self.assertEqual(db.get_specs_by_material("NEW"), [])
        conn = db.get_conn()
        try:
            old_mapping = conn.execute("""
                SELECT major_category, middle_category FROM material_classification_map
                 WHERE material_no = 'OLD'
            """).fetchone()
            new_mapping = conn.execute("""
                SELECT major_category, middle_category FROM material_classification_map
                 WHERE material_no = 'NEW'
            """).fetchone()
        finally:
            conn.close()
        self.assertEqual(tuple(old_mapping), ("전기", "CT"))
        self.assertEqual(tuple(new_mapping), ("기구", "하우징"))

    def test_new_classification_surfaces_ignore_legacy_category_query(self):
        for no, legacy, major, middle in (
                ("A", "기존분류-A", "전기", "CT"),
                ("B", "기존분류-B", "기구", "하우징")):
            db.upsert_material(no, no)
            db.update_material_category(no, legacy)
            db.update_material_classification(no, major, middle)

        conn = db.get_conn()
        for no in ("A", "B"):
            conn.execute("""INSERT INTO material_bom_links
                          (material_no, model_name, level, bom_name) VALUES (?, 'MODEL', 1, ?)""",
                         (no, no))
            conn.execute("""INSERT INTO inspections
                          (material_no, material_name, supplier, po_number, inspect_date,
                           quantity, overall_result, status, approval_type)
                          VALUES (?, ?, '업체', 'LOT', '2026-10-01', 1, '합격', 'approved', 'normal')""",
                         (no, no))
        conn.commit()
        conn.close()

        # app import 시 초기화가 현재 테스트 임시 DB에서만 실행되도록 setUp 뒤에 지연 import한다.
        os.environ.setdefault("SECRET_KEY", "material-classification-test-only")
        import app as qms_app

        with qms_app.app.test_request_context("/spec?category=기존분류-A"):
            rows, filters = qms_app._spec_list_materials()
        self.assertEqual([r["material_no"] for r in rows], ["A", "B"])
        self.assertNotIn("category", filters)
        with qms_app.app.test_request_context(
                "/spec?category=기존분류-B&major_category=전기&middle_category=CT"):
            rows, _ = qms_app._spec_list_materials()
        self.assertEqual([r["material_no"] for r in rows], ["A"])

        with qms_app.app.test_request_context("/materials/find?category=기존분류-A"):
            rows, filters = qms_app._material_find_rows()
        self.assertEqual([r["material_no"] for r in rows], ["A", "B"])
        self.assertNotIn("category", filters)
        with qms_app.app.test_request_context(
                "/materials/find?category=기존분류-B&major_category=전기&middle_category=CT"):
            rows, _ = qms_app._material_find_rows()
        self.assertEqual([r["material_no"] for r in rows], ["A"])

        dashboard_base = ("/dashboard?period=custom&start=2026-10-01&end=2026-10-31"
                          "&category=기존분류-A")
        with qms_app.app.test_request_context(dashboard_base):
            params = qms_app._dashboard_params()
            report = qms_app._build_quality_report(params)
            query_string = qms_app._dashboard_query(params)
        self.assertNotIn("category", params)
        self.assertNotIn("category=", query_string)
        self.assertEqual(sorted(r["material_no"] for r in report["성적서목록"]), ["A", "B"])

        with qms_app.app.test_request_context(
                dashboard_base + "&major_category=전기&middle_category=CT"):
            params = qms_app._dashboard_params()
            report = qms_app._build_quality_report(params)
        self.assertEqual([r["material_no"] for r in report["성적서목록"]], ["A"])


if __name__ == "__main__":
    unittest.main()
