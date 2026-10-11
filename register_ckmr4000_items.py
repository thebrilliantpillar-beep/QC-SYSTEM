"""CKMR4000 / CKMR4020 등 모델에 제품검사 항목(ckmr_items_def.py)을 저장하는 일회성 스크립트.

기본은 dry-run(검증만, 저장 안 함). 저장하려면 --apply. 모델이 없으면 안내만 하고 만들지 않는다.
같은 정의가 이미 저장돼 있으면 "변경 사항이 없어요"로 정상 종료한다(멱등).

  python register_ckmr4000_items.py                       # CKMR4000 dry-run (스피드 포함, 비155V)
  python register_ckmr4000_items.py CKMR4020              # 155V 변형 dry-run (스피드 4개 없음)
  python register_ckmr4000_items.py CKMR4000 --apply      # 실제 저장 (Render Shell에서 사용자가 실행)

두 번째 버전부터 항목 변경에는 사유가 필요해서 --apply 시 사유를 자동으로 넣는다.
"""
import sys, io
import database as db
from ckmr_items_def import ckmr4000_items

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
REASON = "CKMR4000 양식 14개 항목 기준 정의(칸 이름·단계·주회로 저항 자동 계산) 등록"
ACTOR = "시드 스크립트"


def main(argv):
    apply_ = "--apply" in argv
    names = [a for a in argv if not a.startswith("--")]
    model_no = names[0] if names else "CKMR4000"
    print(f"DB: {db.DB_PATH}")
    model = next((m for m in db.list_product_models() if m["model_no"] == model_no), None)
    if model is None:
        print(f"모델 {model_no}이(가) 없어요. 모델 관리에서 먼저 등록해줘 (이 스크립트는 모델을 만들지 않아요).")
        return 1
    with_speed = not model["pcode"]  # 155V(P코드 있음)는 스피드 검사 단계가 없다
    items = ckmr4000_items(with_speed=with_speed)
    try:
        norm = db._norm_items(items)
        db.validate_items_for_model(norm, model)
    except ValueError as e:
        print(f"정의 검증 실패: {e}")
        return 1
    print(f"{model_no}: {'스피드 포함(비155V)' if with_speed else '155V 변형(스피드 4개 없음)'} · 항목 {len(norm)}개 검증 통과")
    cur = db.get_current_model_items(model["id"])
    print(f"현재 저장된 버전: {cur['version'] if cur else '없음'}")
    if not apply_:
        print("dry-run이라 저장하지 않았어요. 저장하려면 --apply를 붙여줘.")
        return 0
    try:
        ver = db.save_model_items(model["id"], items, REASON, ACTOR)
    except ValueError as e:
        if "변경 사항이 없어요" in str(e):
            print("변경 사항이 없어요 (이미 같은 정의가 저장돼 있어요).")
            return 0
        print(f"저장 실패: {e}")
        return 1
    print(f"저장 완료: 버전 {ver}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
