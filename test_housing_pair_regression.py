import os
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("SECRET_KEY", "housing-pair-regression-test-only")

import app


PAIR_SPEC = {
    "stage_group": 2,
    "judge_type": "numeric_pair",
    "item_name": "F",
    "spec_display": "고저항 CT SIDE (기존값)",
    "lower_limit": 1.0,
    "upper_limit": 2.0,
    "inspect_method": "절연저항계",
}


class HousingPairRegressionTests(unittest.TestCase):
    def _column_for(self, voltage):
        with (
            patch.object(app.db, "get_specs_by_material", return_value=[PAIR_SPEC]),
            patch.object(app.db, "get_material", return_value={"material_name": "HOUSING"}),
            patch.object(app.db, "get_full_inspect_config", return_value={"voltage": voltage}),
            patch.object(app, "_gauge_expiry_by_method", return_value={}),
        ):
            return app._housing_columns_by_stage("TEST")["stage2_cols"][0]

    def test_voltage_specific_high_resistance_labels_and_ranges(self):
        col_15 = self._column_for("15KV")
        self.assertEqual((col_15["header"], col_15["lo"], col_15["hi"]),
                         ("고저항 (40Ω) ±1%", 39.6, 40.4))

        for voltage in ("27KV", "38KV"):
            col = self._column_for(voltage)
            self.assertEqual((col["header"], col["lo"], col["hi"]),
                             ("고저항 (100Ω) ±1%", 99, 101))
            self.assertEqual(f"{col['lo']}~{col['hi']}", "99~101")

        unknown = self._column_for("기타")
        self.assertEqual((unknown["header"], unknown["lo"], unknown["hi"]),
                         ("고저항 CT SIDE (기존값)", 1.0, 2.0))

        self.assertEqual(app._fi_auto_result(
            {"F": "40.5/40"}, [col_15], {"F"}), "NG")
        col_27 = self._column_for("27KV")
        self.assertEqual(app._fi_auto_result(
            {"F": "100/100"}, [col_27], {"F"}), "OK")

    def test_pair_must_have_both_channels_before_result(self):
        columns = [{"key": "F", "type": "num_pair", "lo": 99.0, "hi": 101.0}]
        self.assertEqual(app._fi_auto_result({"F": "100/"}, columns, {"F"}), "")
        self.assertEqual(app._fi_auto_result({"F": "/100"}, columns, {"F"}), "")
        self.assertEqual(app._fi_auto_result({"F": "abc/100"}, columns, {"F"}), "")
        self.assertEqual(app._fi_auto_result({"F": "NaN/100"}, columns, {"F"}), "")
        self.assertEqual(app._fi_auto_result({"F": "inf/100"}, columns, {"F"}), "")
        self.assertEqual(app._fi_auto_result({"F": "100/100,"}, columns, {"F"}), "")
        self.assertEqual(app._fi_auto_result({"F": "99/101"}, columns, {"F"}), "OK")
        self.assertEqual(app._fi_auto_result({"F": "99/101,100/100"}, columns, {"F"}), "OK")
        self.assertEqual(app._fi_auto_result({"F": "98.9/100"}, columns, {"F"}), "NG")

    def test_template_saves_whole_pair_and_protects_newer_dirty_value(self):
        source = Path("templates/full_inspect_housing.html").read_text(encoding="utf-8")
        self.assertIn("? pairFor(+inp.dataset.unit, inp.dataset.key)", source)
        self.assertIn("if (dirtyCells[k] === sentCells[k]) delete dirtyCells[k]", source)
        self.assertIn("_savePromise = drainSaves().finally", source)
        self.assertIn("while (_savePending || Object.keys(dirtyCells).length)", source)
        self.assertIn("clearTimeout(_timer);\n  const saved = await doSave()", source)
        self.assertIn("{% set rod_val = pair_val[1] if pair_val|length >= 2 else '' %}", source)


if __name__ == "__main__":
    unittest.main()
