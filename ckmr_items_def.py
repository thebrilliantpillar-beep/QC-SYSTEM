"""CKMR4000(15kV 일반수평 3상, 비155V)과 155V 변형(CKMR4020 등)의 제품검사 항목 정의 — 정의의 단일 출처.

기준값은 사내 양식(14개 검사 항목) 초안이며 현장 기준서 대조 전이다. 27kV·38kV 등 다른 전압 모델은 여기에 없다
(항목 설정 화면의 "다른 모델에서 복사" 후 기준값만 고친다).
register_ckmr4000_items.py가 이 정의를 모델에 저장한다.
"""

PH = "A-r, B-s, C-t"          # 상(A-r·B-s·C-t) 칸 이름
ABC = "A, B, C"
SPEED_STEP = "스피드"


def _num(key, name, step, upper=None, lower=None, n=3, digits=2, labels=PH, **kw):
    d = {"key": key, "name": name, "input_type": "numeric", "display_digits": str(digits),
         "required_n": str(n), "slot_labels": labels, "step": step}
    if lower is not None:
        d["lower"] = str(lower)
    if upper is not None:
        d["upper"] = str(upper)
    d.update(kw)
    return d


def _okng(key, name, step):
    return {"key": key, "name": name, "input_type": "ok_ng", "required_n": "1", "step": step}


def _angle(key, name, step, lower, upper, n=3, labels=PH):
    return {"key": key, "name": name, "input_type": "angle", "lower": str(lower), "upper": str(upper),
            "display_digits": "1", "required_n": str(n), "slot_labels": labels, "step": step}


def ckmr4000_items(with_speed=True):
    """with_speed=True: 비155V(스피드 검사 4항목+1차 극간이 MA 연결로 자동 채워짐). False: 155V 변형(스피드 4개 없음,
    1차 극간은 직접 입력)."""
    s_ma = SPEED_STEP if with_speed else "MA 연결"
    it = [{"key": "ma_no", "name": "MA 번호", "input_type": "ma_link", "required_n": "3", "step": s_ma}]
    if with_speed:
        it += [
            _num("sp_open", "스피드 개방시간(ms)", SPEED_STEP, 15, speed_field="open_ms", source="speed"),
            _num("sp_open_skew", "스피드 개방 괴리차(ms)", SPEED_STEP, 6, speed_field="open_skew_ms", source="speed"),
            _num("sp_close", "스피드 투입시간(ms)", SPEED_STEP, 50, speed_field="close_ms", source="speed"),
            _num("sp_close_skew", "스피드 투입 괴리차(ms)", SPEED_STEP, 6, speed_field="close_skew_ms", source="speed"),
        ]
    s1, s2, s3 = "1 탱크", "2 본체부", "3 구조 및 외관"
    it.append(_okng("ck_tank", "탱크 내 먼지·금속파편 등 이물질 부착이 없을 것", s1))
    it.append(_okng("ck_body", "본체부 점검(배선·부품·수동동작·흡습제 등 9개 확인)", s2))
    if with_speed:
        it.append({"key": "sp_gap1", "name": "VI 극간거리 1차(mm, 스피드 검사 데이터)", "input_type": "record_num",
                   "display_digits": "2", "required_n": "3", "slot_labels": ABC, "step": s2,
                   "speed_field": "primary_gap", "source": "speed"})
    else:
        it.append({"key": "gap1", "name": "VI 극간거리 1차(mm, 직접 입력)", "input_type": "record_num",
                   "display_digits": "2", "required_n": "3", "slot_labels": ABC, "step": s2})
    it.append(_num("vi_gap", "VI 극간거리 2차(mm)", s2, 10.2, 10, labels=ABC))
    it.append(_okng("ck_look", "도장·용접·스티커/로고 부착 확인", s3))
    s4 = "4 주회로 저항"
    for k, ph in (("A", "A-r"), ("B", "B-s"), ("C", "C-t")):  # 각 측정값도 70 이하 판정(사용자 확정)
        it.append(_num(f"r_first_{k}", f"{ph} 주회로 저항 3회 연속(μΩ)", s4, 70, digits=1, labels="1회, 2회, 3회"))
    it.append(_okng("ck_ops", "무전압 개폐시험(25회 투·개방 후 확인)", "5 무전압 개폐시험"))
    s6 = "6 주회로 저항 측정"
    rm = _num("r_meas", "주회로 저항 측정(μΩ, 3회 평균 자동)", s6, 70, digits=0)
    rm["derived"] = {"op": "avg_floor", "from": ["r_first_A", "r_first_B", "r_first_C"]}
    it += [rm, _num("r_after25", "25회 후 주회로 저항(μΩ)", s6, 70, digits=1),
           _num("r_final", "출하 전 주회로 저항(μΩ)", s6, 70, digits=1)]
    s7 = "7 오실로스코프"
    it += [_num("osc_open", "오실로 개방시간(ms)", s7, 15, labels=ABC),
           _num("osc_open_skew", "오실로 개방 괴리차(ms)", s7, 6, labels=ABC),
           _num("osc_close", "오실로 투입시간(ms)", s7, 50, labels=ABC),
           _num("osc_close_skew", "오실로 투입 괴리차(ms)", s7, 6, labels=ABC)]
    it += [_okng("im_g", "충격파 내전압 대지간(110kV, 각 3회)", "8 충격파 내전압"),
           _okng("im_p", "충격파 내전압 상간(110kV, 각 3회)", "8 충격파 내전압"),
           _okng("im_o", "충격파 내전압 극간(110kV, 각 3회)", "8 충격파 내전압")]
    it += [_okng("pf_g", "상용주파 내전압 대지간(50kV, 60초)", "9 상용주파 내전압"),
           _okng("pf_p", "상용주파 내전압 상간(50kV, 60초)", "9 상용주파 내전압"),
           _okng("pf_o", "상용주파 내전압 극간(50kV, 60초)", "9 상용주파 내전압")]
    it.append(_num("pd", "부분방전(pC) (17.5kV 60초 후 9.8kV 60초 측정)", "10 부분방전시험", 10, n=4, digits=1,
                   labels="A r, B s, C t, ABC rst"))
    s11 = "11 전압계측 정밀도"
    it += [_num("volt", "전압(V)", s11, 4.12, 3.88), _angle("volt_ph", "전압 위상(°)", s11, 357, 3)]
    s12 = "12 전류극성·전류계측 정밀도"
    it.append(_okng("ck_pol", "전류 CT 극성 확인", s12))
    it += [_num("cur_45", "전류 45A(A)", s12, 45.68, 44.33, digits=0),   # 양식 인쇄값(사용자 확정)
           _num("cur_180", "전류 180A(A)", s12, 182.7, 177.3, digits=0),
           _num("cur_900", "전류 900A(A)", s12, 909, 891, digits=0),
           _num("cur_1080", "전류 1080A(A)", s12, 1090.8, 1069.2, digits=0)]
    s13 = "13 전류계측 위상시험"
    it += [_angle("curph_45", "전류 위상 45A(°)", s13, 357, 3),
           _angle("curph_180", "전류 위상 180A(°)", s13, 358.5, 1.5),
           _angle("curph_900", "전류 위상 900A(°)", s13, 359, 1),
           _angle("curph_1080", "전류 위상 1080A(°)", s13, 359, 1)]
    it.append(_okng("ck_ind", "인디게이터 CLOSE(주황색) 확인", "14 인디게이터 확인"))
    return it
