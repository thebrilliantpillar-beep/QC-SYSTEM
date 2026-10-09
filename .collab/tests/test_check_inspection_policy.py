# -*- coding: utf-8 -*-
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import database as db


class CheckInspectionPolicyTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        os.environ.setdefault("SECRET_KEY", "check-inspection-policy-test")

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.old_db_path = db.DB_PATH
        db.DB_PATH = os.path.join(self.tmp.name, "test.db")
        db.init_db()

        # app은 database 모듈 객체를 공유하므로 import 전/후 모두 위 임시 DB_PATH를 쓴다.
        import app as qms_app
        self.qms = qms_app
        self.qms.app.config.update(TESTING=True, WTF_CSRF_ENABLED=False)

    def tearDown(self):
        db.DB_PATH = self.old_db_path
        self.tmp.cleanup()

    def _material(self, no, major, middle, aql="4", spec_count=1):
        db.upsert_material(no, no)
        db.update_material_classification(no, major, middle)
        for idx in range(spec_count):
            db.add_spec(no, no, chr(65 + idx), "10±1", "numeric", 9, 11,
                        "버니어", aql, idx + 1)

    def _intake(self, material_no, quantity):
        conn = db.get_conn()
        cur = conn.execute("""
            INSERT INTO intake_list
                (material_no, quantity, supplier, receive_date, po_number, status)
            VALUES (?, ?, '업체', '2026-10-09', ?, '대기')
        """, (material_no, quantity, f"PO-{material_no}"))
        intake_id = cur.lastrowid
        conn.commit()
        conn.close()
        return intake_id

    def _login_session(self, client):
        db.create_user("tester", "pw", "검사자", "inspect_input,users,approve")
        user = db.get_user_by_username("tester")
        with client.session_transaction() as sess:
            sess["user_id"] = user["id"]
            sess["last_seen"] = 9999999999
        return user

    def _login_material_user(self, client, username="material_editor", can_edit=True):
        permissions = "material_view,material_edit" if can_edit else "material_view"
        db.create_user(username, "pw", "자재 담당자", permissions)
        user = db.get_user_by_username(username)
        with client.session_transaction() as sess:
            sess["user_id"] = user["id"]
            sess["last_seen"] = 9999999999
        return user

    def _clear_check_migration_flag(self):
        conn = db.get_conn()
        conn.execute("DELETE FROM app_settings WHERE key = ?",
                     (db.CHECK_INSPECTION_AQL_MIGRATION_KEY,))
        conn.commit()
        conn.close()

    def _snapshot_inspection(self, material_no, quantity, aql, sample_qty, ac,
                             result="합격", actual_time_sec=10, created_by_user_id=None):
        spec = db.get_specs_by_material(material_no)[0]
        return db.create_inspection(
            {"material_no": material_no, "material_name": material_no, "supplier": "업체",
             "po_number": f"PO-{material_no}", "receive_date": "2026-10-09",
             "inspect_date": "2026-10-09", "inspector": "검사자", "quantity": quantity},
            [{"item_name": spec["item_name"], "measured_value": "12" if result == "불합격" else "10",
              "result": result, "part_material_no": material_no,
              "aql_snapshot": aql, "sample_qty_snapshot": sample_qty,
              "ac_allowance_snapshot": ac}],
            "합격" if result == "합격" else "검토필요",
            actual_time_sec=actual_time_sec, created_by_user_id=created_by_user_id)

    def test_runtime_uses_saved_aql_and_never_forces_by_classification(self):
        self._material("TARGET_RAW", "기타 소자류", "IC", aql="4", spec_count=3)
        built = self.qms.build_specs_with_sample(
            db.get_specs_by_material("TARGET_RAW"), 1000)
        self.assertTrue(all(s["aql"] == "4" for s in built))
        self.assertTrue(all(s["sample_qty"] == 32 for s in built))

        for spec in db.get_specs_by_material("TARGET_RAW"):
            db.update_spec_item(spec["id"], spec["item_name"], spec["spec_display"],
                                spec["judge_type"], spec["lower_limit"], spec["upper_limit"],
                                spec["inspect_method"], "체크검사", spec["item_order"])
        built = self.qms.build_specs_with_sample(
            db.get_specs_by_material("TARGET_RAW"), 1000)
        self.assertTrue(all(s["aql"] == "체크검사" for s in built))
        self.assertTrue(all(s["sample_qty"] == 5 for s in built))
        self.assertTrue(all(s["ac_allowance"] == 0 for s in built))

    def test_check_sample_quantity_is_min_of_intake_and_five(self):
        self._material("A", "상품", "다른 중분류", aql="체크검사", spec_count=1)
        specs = db.get_specs_by_material("A")
        for quantity, expected in ((1, 1), (4, 4), (5, 5), (6, 5), (99999, 5)):
            built = self.qms.build_specs_with_sample(specs, quantity)
            self.assertEqual(built[0]["sample_qty"], expected)
            self.assertEqual(built[0]["ac_allowance"], 0)

    def test_historical_snapshot_survives_category_and_spec_changes(self):
        self._material("A", "기타 소자류", "중", aql="체크검사", spec_count=1)
        spec = self.qms.build_specs_with_sample(db.get_specs_by_material("A"), 100)[0]
        inspection_id = db.create_inspection(
            {"material_no": "A", "material_name": "A", "supplier": "업체",
             "po_number": "PO", "receive_date": "2026-10-09", "inspect_date": "2026-10-09",
             "inspector": "검사자", "quantity": 100},
            [{"item_name": spec["item_name"], "measured_value": "10", "result": "합격",
              "part_material_no": "A", **self.qms._snapshot_fields(spec)}],
            "합격")

        db.update_material_classification("A", "상품", "다른 중분류")
        db.update_spec_item(spec["id"], "A", "10±1", "numeric", 9, 11, "버니어", "0.65", 1)
        _header, items = db.get_inspection(inspection_id)
        self.assertEqual((items[0]["aql"], items[0]["sample_qty"], items[0]["ac_allowance"]),
                         ("체크검사", 5, 0))
        rebuilt = self.qms.build_specs_with_sample(
            db.get_specs_by_material("A"), 100, snapshot_items=items)
        self.assertEqual((rebuilt[0]["aql"], rebuilt[0]["sample_qty"]), ("체크검사", 5))

    def test_legacy_null_snapshot_is_frozen_and_new_reinspection_uses_current_policy(self):
        self._material("A", "상품", "다른 중분류", aql=None, spec_count=1)
        spec = db.get_specs_by_material("A")[0]
        inspection_id = db.create_inspection(
            {"material_no": "A", "material_name": "A", "supplier": "업체",
             "po_number": "PO", "receive_date": "2026-10-09", "inspect_date": "2026-10-09",
             "inspector": "검사자", "quantity": 8},
            [{"item_name": "A", "measured_value": "10", "result": "합격",
              "part_material_no": "A"}], "합격")
        self.assertEqual(self.qms.ensure_inspection_aql_snapshot_migration(), 1)

        db.update_material_classification("A", "상품", "엘보/부싱 액세서리")
        db.update_spec_item(spec["id"], "A", "10±1", "numeric", 9, 11, "버니어", "체크검사", 1)
        _header, old_items = db.get_inspection(inspection_id)
        self.assertEqual(old_items[0]["aql_snapshot_set"], 1)
        self.assertIsNone(old_items[0]["aql"])
        self.assertIsNone(old_items[0]["sample_qty"])

        new_specs = self.qms.build_specs_with_sample(db.get_specs_by_material("A"), 8)
        self.assertEqual((new_specs[0]["aql"], new_specs[0]["sample_qty"],
                          new_specs[0]["ac_allowance"]), ("체크검사", 5, 0))

    def test_normal_form_and_auto_batch_share_policy_and_render_fixed_label(self):
        self._material("NORMAL", "기타 소자류", "중", aql="체크검사", spec_count=1)
        self._material("AUTO", "조립부품류 (소모품)", "중", aql="체크검사", spec_count=1)
        normal_id = self._intake("NORMAL", 6)
        auto_id = self._intake("AUTO", 4)
        client = self.qms.app.test_client()
        self._login_session(client)

        page = client.get(f"/inspect/start/{normal_id}")
        self.assertEqual(page.status_code, 200)
        html = page.get_data(as_text=True)
        self.assertIn("체크검사", html)
        self.assertNotIn('class="aql-fixed"', html)
        self.assertIn('id="aql_', html)
        self.assertIn('<option value="체크검사"', html)
        self.assertIn(f'id="sqty_{db.get_specs_by_material("NORMAL")[0]["id"]}"><b>5</b>', html)

        spec = db.get_specs_by_material("NORMAL")[0]
        form = {"inspect_date": "2026-10-09", "actual_time_sec": "10"}
        for idx in range(1, 6):
            form[f"item_{spec['id']}_{idx}"] = "10"
        response = client.post(f"/inspect/start/{normal_id}", data=form)
        self.assertEqual(response.status_code, 302)

        response = client.post("/inspect/auto-batch", data={
            "intake_ids": str(auto_id), "inspect_date": "2026-10-09",
            "time_min_m": "0", "time_min_s": "1", "time_max_m": "0", "time_max_s": "1",
        })
        self.assertEqual(response.status_code, 302)

        conn = db.get_conn()
        rows = conn.execute("SELECT id, material_no FROM inspections ORDER BY id").fetchall()
        conn.close()
        self.assertEqual({r["material_no"] for r in rows}, {"NORMAL", "AUTO"})
        snapshots = {}
        for row in rows:
            _header, items = db.get_inspection(row["id"])
            snapshots[row["material_no"]] = (items[0]["aql"], items[0]["sample_qty"],
                                               items[0]["ac_allowance"])
        self.assertEqual(snapshots["NORMAL"], ("체크검사", 5, 0))
        self.assertEqual(snapshots["AUTO"], ("체크검사", 4, 0))

        normal_inspection_id = next(r["id"] for r in rows if r["material_no"] == "NORMAL")
        detail = client.get(f"/inspection/{normal_inspection_id}")
        approval = client.get(f"/approve/{normal_inspection_id}")
        self.assertEqual(detail.status_code, 200)
        self.assertEqual(approval.status_code, 200)
        for rendered in (detail.get_data(as_text=True), approval.get_data(as_text=True)):
            self.assertIn("체크검사", rendered)
            self.assertIn("Ac0/Re1", rendered)

    def test_report_sheet_shows_check_label_five_and_zero_defect(self):
        from openpyxl import Workbook
        import report_builder

        ws = Workbook().active
        result = {
            "item_name": "A", "aql": "체크검사", "sample_qty": 5,
            "ac_allowance": 0, "values": [10] * 5, "max": 10, "min": 10,
            "verdict": "합격", "gauge_expiry": None, "spec_display": "10±1",
            "inspect_method": "버니어", "lower_limit": 9, "upper_limit": 11,
        }
        report_builder._fill_sheet(ws, "A", "자재", {"qty": 100}, [result], "합격")
        self.assertEqual(ws["C9"].value, "[체크검사]\n무결점")
        self.assertEqual(ws["D9"].value, 5)

    def test_edit_total_time_uses_stored_snapshot_not_current_classification(self):
        self._material("EDIT", "상품", "다른 중분류", aql="4", spec_count=1)
        client = self.qms.app.test_client()
        user = self._login_session(client)
        inspection_id = self._snapshot_inspection(
            "EDIT", 100, "4", 8, 1, created_by_user_id=user["id"])
        db.update_material_classification("EDIT", "기타 소자류", "중")
        spec = db.get_specs_by_material("EDIT")[0]

        response = client.post(f"/inspection/{inspection_id}/edit", data={
            "inspect_date": "2026-10-09", "actual_time_sec": "10",
            f"item_{spec['id']}_1": "10",
        })
        self.assertEqual(response.status_code, 302)
        header, items = db.get_inspection(inspection_id)
        self.assertEqual(header["total_time_sec"], 80)  # 저장 당시 8개 × 10초
        self.assertEqual((items[0]["aql"], items[0]["sample_qty"]), ("4", 8))

    def test_approved_history_backfill_uses_snapshot_for_time_and_pass_quantity(self):
        self._material("HISTORY", "상품", "다른 중분류", aql="4", spec_count=1)
        inspection_id = self._snapshot_inspection("HISTORY", 100, "4", 8, 1)
        conn = db.get_conn()
        conn.execute("""UPDATE inspections
                           SET status='approved', approval_type='normal', total_time_sec=NULL,
                               remark_inspector='검사 수량 8개 중 1개 불량'
                         WHERE id=?""", (inspection_id,))
        conn.commit()
        conn.close()
        db.update_material_classification("HISTORY", "기타 소자류", "중")

        with self.qms.app.test_request_context("/history/approved"):
            rows, _filters = self.qms._collect_approval_history()
        row = next(r for r in rows if r["id"] == inspection_id)
        self.assertEqual(row["max_sample"], 8)
        self.assertEqual(row["pass_count"], 7)
        self.assertEqual(row["total_time_sec"], 80)

    def test_ncr_auto_sample_prefers_snapshot_and_only_legacy_falls_back(self):
        self._material("NCR", "기타 소자류", "중", aql="4", spec_count=1)
        client = self.qms.app.test_client()
        self._login_session(client)
        inspection_id = self._snapshot_inspection("NCR", 100, "4", 3, 0, result="불합격")

        response = client.get(f"/ncr/new/{inspection_id}")
        self.assertEqual(response.status_code, 200)
        html = response.get_data(as_text=True)
        self.assertIn('name="sample_qty" min="0" value="3"', html)

        # 스냅샷 도입 전 구형 행만 AQL 표에 따른 8개로 계산한다.
        conn = db.get_conn()
        conn.execute("""UPDATE inspection_items
                           SET aql_snapshot_set=0, aql_snapshot=NULL,
                               sample_qty_snapshot=NULL, ac_allowance_snapshot=NULL
                         WHERE inspection_id=?""", (inspection_id,))
        conn.commit()
        conn.close()
        response = client.get(f"/ncr/new/{inspection_id}")
        self.assertEqual(response.status_code, 200)
        self.assertIn('name="sample_qty" min="0" value="8"', response.get_data(as_text=True))

    def test_one_time_migration_updates_only_existing_specs_for_canonical_241(self):
        import json
        payload = json.loads(Path("data/material_classifications.json").read_text(encoding="utf-8"))
        targets = [r["material_no"] for r in payload["mappings"]
                   if r["major_category"] in ("기타 소자류", "조립부품류 (소모품)")
                   or (r["major_category"] == "상품"
                       and r["middle_category"] == "엘보/부싱 액세서리")]
        non_target = next(r["material_no"] for r in payload["mappings"]
                          if r["material_no"] not in set(targets))
        self.assertEqual(len(set(targets)), 241)
        target_with_specs, target_without_specs = targets[:2]
        db.add_spec(target_with_specs, "대상", "A", "10±1", "numeric", 9, 11, "버니어", "4", 1)
        db.add_spec(target_with_specs, "대상", "B", "양호", "ok_ng", None, None, "육안", "0.65", 2)
        db.upsert_material(target_without_specs, "규격 없는 대상")
        non_target_id = db.add_spec(non_target, "비대상", "A", "10±1", "numeric", 9, 11,
                                    "버니어", "1.5", 1)
        self._clear_check_migration_flag()

        result = db.ensure_check_inspection_aql_migration()
        self.assertEqual(result, {"applied": True, "target_count": 241,
                                  "updated_spec_count": 2})
        self.assertTrue(all(s["aql"] == "체크검사"
                            for s in db.get_specs_by_material(target_with_specs)))
        self.assertEqual(str(db.get_spec_item(non_target_id)["aql"]), "1.5")
        self.assertEqual(db.get_specs_by_material(target_without_specs), [])

        first_target_spec = db.get_specs_by_material(target_with_specs)[0]
        db.update_spec_item(first_target_spec["id"], first_target_spec["item_name"],
                            first_target_spec["spec_display"], first_target_spec["judge_type"],
                            first_target_spec["lower_limit"], first_target_spec["upper_limit"],
                            first_target_spec["inspect_method"], "1.5", first_target_spec["item_order"])
        second = db.ensure_check_inspection_aql_migration(
            str(Path(self.tmp.name) / "asset-removed-after-first-run.json"))
        self.assertEqual(second["applied"], False)
        self.assertEqual(str(db.get_spec_item(first_target_spec["id"])["aql"]), "1.5")

    def test_completed_migration_skips_before_missing_invalid_or_240_asset_access(self):
        import json
        db.set_setting(db.CHECK_INSPECTION_AQL_MIGRATION_KEY, "1")
        invalid_asset = Path(self.tmp.name) / "invalid.json"
        invalid_asset.write_text("not-json", encoding="utf-8")
        source = json.loads(Path("data/material_classifications.json").read_text(encoding="utf-8"))
        source["mappings"].remove(next(
            r for r in source["mappings"]
            if r["major_category"] in ("기타 소자류", "조립부품류 (소모품)")
            or (r["major_category"] == "상품"
                and r["middle_category"] == "엘보/부싱 액세서리")))
        asset_240 = Path(self.tmp.name) / "240.json"
        asset_240.write_text(json.dumps(source, ensure_ascii=False), encoding="utf-8")

        for asset_path in (Path(self.tmp.name) / "missing.json", invalid_asset, asset_240):
            result = db.ensure_check_inspection_aql_migration(str(asset_path))
            self.assertEqual(result, {"applied": False, "target_count": 241,
                                      "updated_spec_count": 0})

    def test_migration_rolls_back_specs_and_flag_together(self):
        target_no = "600007P051"
        spec_id = db.add_spec(target_no, "IC", "A", "10±1", "numeric", 9, 11,
                              "버니어", "4", 1)
        self._clear_check_migration_flag()
        conn = db.get_conn()
        conn.execute("""CREATE TRIGGER fail_check_aql_migration BEFORE UPDATE OF aql ON specs
                        WHEN NEW.aql = '체크검사'
                        BEGIN SELECT RAISE(ABORT, 'forced rollback'); END""")
        conn.commit()
        conn.close()

        with self.assertRaises(Exception):
            db.ensure_check_inspection_aql_migration()
        self.assertEqual(str(db.get_spec_item(spec_id)["aql"]), "4")
        self.assertIsNone(db.get_setting(db.CHECK_INSPECTION_AQL_MIGRATION_KEY))

    def test_migration_rejects_asset_when_target_count_is_not_241(self):
        import json
        source = json.loads(Path("data/material_classifications.json").read_text(encoding="utf-8"))
        removed = next(r for r in source["mappings"]
                       if r["major_category"] in ("기타 소자류", "조립부품류 (소모품)")
                       or (r["major_category"] == "상품"
                           and r["middle_category"] == "엘보/부싱 액세서리"))
        source["mappings"].remove(removed)
        bad_asset = Path(self.tmp.name) / "bad-classifications.json"
        bad_asset.write_text(json.dumps(source, ensure_ascii=False), encoding="utf-8")
        spec_id = db.add_spec(removed["material_no"], "대상", "A", "10±1", "numeric",
                              9, 11, "버니어", "4", 1)
        self._clear_check_migration_flag()

        with self.assertRaisesRegex(ValueError, "정확히 241개"):
            db.ensure_check_inspection_aql_migration(str(bad_asset))
        self.assertEqual(str(db.get_spec_item(spec_id)["aql"]), "4")
        self.assertIsNone(db.get_setting(db.CHECK_INSPECTION_AQL_MIGRATION_KEY))

    def test_unfinished_migration_invalid_json_fails_without_mutation(self):
        target_no = "600007P051"
        spec_id = db.add_spec(target_no, "IC", "A", "10±1", "numeric", 9, 11,
                              "버니어", "4", 1)
        self._clear_check_migration_flag()
        invalid_asset = Path(self.tmp.name) / "invalid.json"
        invalid_asset.write_text("{broken", encoding="utf-8")

        with self.assertRaises(Exception):
            db.ensure_check_inspection_aql_migration(str(invalid_asset))
        self.assertEqual(str(db.get_spec_item(spec_id)["aql"]), "4")
        self.assertIsNone(db.get_setting(db.CHECK_INSPECTION_AQL_MIGRATION_KEY))

    def test_transaction_recheck_skips_when_other_worker_finishes_after_early_check(self):
        target_no = "600007P051"
        spec_id = db.add_spec(target_no, "IC", "A", "10±1", "numeric", 9, 11,
                              "버니어", "4", 1)
        self._clear_check_migration_flag()
        real_open = open

        def finish_other_worker_then_open(*args, **kwargs):
            db.set_setting(db.CHECK_INSPECTION_AQL_MIGRATION_KEY, "1")
            return real_open(*args, **kwargs)

        with mock.patch("builtins.open", side_effect=finish_other_worker_then_open):
            result = db.ensure_check_inspection_aql_migration()
        self.assertEqual(result["applied"], False)
        self.assertEqual(str(db.get_spec_item(spec_id)["aql"]), "4")

    def test_snapshot_migration_runs_before_master_aql_conversion(self):
        target_no = "600007P051"
        self._material(target_no, "기타 소자류", "IC", aql="4", spec_count=1)
        inspection_id = db.create_inspection(
            {"material_no": target_no, "material_name": "IC", "supplier": "업체",
             "po_number": "PO", "receive_date": "2026-10-09", "inspect_date": "2026-10-09",
             "inspector": "검사자", "quantity": 100},
            [{"item_name": "A", "measured_value": "10", "result": "합격",
              "part_material_no": target_no}], "합격")
        self._clear_check_migration_flag()

        self.qms.ensure_inspection_aql_snapshot_migration()
        db.ensure_check_inspection_aql_migration()
        _header, old_items = db.get_inspection(inspection_id)
        self.assertEqual((old_items[0]["aql"], old_items[0]["sample_qty"],
                          old_items[0]["ac_allowance"]), ("4", 8, 1))
        self.assertEqual(db.get_specs_by_material(target_no)[0]["aql"], "체크검사")

    def test_detail_and_quick_add_offer_real_check_option_and_allow_change_away(self):
        material_no = "600007P051"
        self._material(material_no, "기타 소자류", "IC", aql="체크검사")
        spec_id = db.get_specs_by_material(material_no)[0]["id"]
        client = self.qms.app.test_client()
        self._login_material_user(client)

        html = client.get(f"/spec/{material_no}").get_data(as_text=True)
        self.assertIn('<option value="체크검사" selected>체크검사</option>', html)
        self.assertNotIn("적용 검사 정책: 체크검사", html)
        self.assertNotIn("원본 AQL(보존·편집)", html)
        response = client.post(f"/spec/{material_no}/item/{spec_id}/update", data={
            "item_name": "A", "spec_display": "10±1", "judge_type": "numeric",
            "lower_limit": "9", "upper_limit": "11", "inspect_method": "버니어",
            "aql_select": "0.65", "aql_percent": "", "item_order": "1",
        })
        self.assertEqual(response.status_code, 302)
        self.assertEqual(str(db.get_spec_item(spec_id)["aql"]), "0.65")

        response = client.post(f"/spec/{material_no}/item/add", data={
            "item_name": "B", "spec_display": "양호", "judge_type": "ok_ng",
            "lower_limit": "", "upper_limit": "", "inspect_method": "육안",
            "aql_select": "1.5", "aql_percent": "", "item_order": "2",
        }, headers={"X-Requested-With": "XMLHttpRequest"})
        self.assertEqual(response.status_code, 200)
        new_id = response.get_json()["spec"]["id"]
        self.assertEqual(str(db.get_spec_item(new_id)["aql"]), "1.5")

        quick_html = client.get("/spec/quick_add").get_data(as_text=True)
        self.assertIn("<option value=\"체크검사\"", quick_html)

    def test_excel_import_preserves_check_inspection_literal(self):
        from openpyxl import Workbook
        import spec_import

        ws = Workbook().active
        ws["A1"] = "자재번호：IMPORT-CHECK"
        ws["B1"] = "품명：가져오기"
        ws.append(["번호", "검사 항목", "AQL", "검사 방법"])
        ws.append(["A", "10 ± 1", "체크검사", "버니어"])
        material_no, _name, items, _warnings, error = spec_import._parse_sheet(ws, "test.xlsx")
        self.assertIsNone(error)
        self.assertEqual(material_no, "IMPORT-CHECK")
        self.assertEqual(items[0]["aql"], "체크검사")


if __name__ == "__main__":
    unittest.main()
