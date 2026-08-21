# -*- coding: utf-8 -*-
"""판정 로직 — 규격(DB에서 조회)과 측정값을 비교해 항목별/전체 판정."""

def judge_all(spec_rows, measurements, visuals):
    """
    spec_rows: database.get_spec()의 결과 리스트
    measurements: {"A":[108.4,108.6], ...}  (수치 항목)
    visuals: {"G":"ok"} 또는 {"G":"ng"}      (육안 항목)
    반환: (결과리스트, 전체판정 '합격'/'불합격')
    """
    results = []
    overall_pass = True

    for row in spec_rows:
        item = row["item"]
        kind = row["kind"]

        if kind == "육안":
            v = visuals.get(item)
            if v not in ("ok", "ng"):
                verdict, mx, mn = "", None, None
                overall_pass = False  # 미입력 항목 있으면 미완료 취급
            else:
                verdict = "합격" if v == "ok" else "불합격"
                mx = mn = None
                if verdict == "불합격":
                    overall_pass = False
        else:
            vals = [v for v in measurements.get(item, []) if v is not None]
            need = row["sample_n"]
            if len(vals) < need:
                verdict, mx, mn = "", None, None
                overall_pass = False
            else:
                mx, mn = max(vals), min(vals)
                ok = all(row["lo"] <= v <= row["hi"] for v in vals)
                verdict = "합격" if ok else "불합격"
                if not ok:
                    overall_pass = False

        results.append({
            "item": item, "label": row["label"], "kind": kind,
            "lo": row["lo"], "hi": row["hi"], "method": row["method"],
            "sample_n": row["sample_n"],
            "values": measurements.get(item, []) if kind == "수치" else [],
            "visual": visuals.get(item),
            "max": mx, "min": mn, "verdict": verdict,
        })

    all_judged = all(r["verdict"] for r in results)
    overall = "합격" if (overall_pass and all_judged) else ("불합격" if any(r["verdict"]=="불합격" for r in results) else "미완료")
    return results, overall
