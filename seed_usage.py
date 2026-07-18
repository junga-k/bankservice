"""이용 통계 검증용 테스트 데이터 시드.

usage_events에 view(상품안내 클릭) + search(은행원 검색) 이벤트를 다양하게 넣어
은행 순위 병합 집계와 카테고리별 Top 5 절삭을 검증한다.

실행:
  .venv/bin/python seed_usage.py         # 테스트 이벤트 적재
  .venv/bin/python seed_usage.py clear   # usage_events 전부 삭제
"""
from __future__ import annotations

import sys

from backend import db

# 은행 순위 검증: 은행별 (view횟수, search횟수). 합산이 순위가 되어야 함.
BANK_EVENTS = {
    "KB국민은행": (8, 4),   # 합 12 → 1위
    "카카오뱅크": (5, 5),   # 합 10 → 2위
    "신한은행":   (3, 5),   # 합 8  → 3위
    "토스뱅크":   (4, 2),   # 합 6  → 4위
    "우리은행":   (2, 2),   # 합 4  → 5위
    "하나은행":   (1, 1),   # 합 2  → 6위
}

# 카테고리별 Top 5 검증: 카테고리마다 6개 상품(5개 초과) → Top 5 절삭 확인.
# (상품명, view횟수, search횟수). 합산 카운트로 정렬.
CATEGORY_PRODUCTS = {
    "예금": [
        ("KB Star 정기예금", 6, 4),   # 10
        ("카카오 정기예금",   5, 3),   # 8
        ("신한 쏠편한 예금",  4, 3),   # 7
        ("토스 굴비적금예금", 3, 2),   # 5
        ("우리 WON예금",      2, 2),   # 4
        ("하나 정기예금",     1, 1),   # 2  ← 6위, Top5에서 제외되어야 함
    ],
    "적금": [
        ("카카오 26주적금",   7, 3),   # 10
        ("신한 한달적금",     4, 4),   # 8
        ("KB 특별한적금",     3, 3),   # 6
        ("토스 자유적금",     2, 3),   # 5
        ("우리 스무살적금",   2, 1),   # 3
        ("하나 주거래적금",   1, 0),   # 1  ← 6위, 제외
    ],
}


def clear() -> None:
    with db.get_conn() as conn:
        conn.execute("DELETE FROM usage_events")
    print("usage_events 전부 삭제 완료")


def seed() -> None:
    db.init_db()
    n = 0
    # 은행 순위용 이벤트
    for bank, (views, searches) in BANK_EVENTS.items():
        for _ in range(views):
            db.add_usage_event("view", bank=bank); n += 1
        for _ in range(searches):
            db.add_usage_event("search", bank=bank); n += 1
    # 카테고리별 상품 이벤트 (상품에는 은행도 함께 기록)
    for category, products in CATEGORY_PRODUCTS.items():
        for product, views, searches in products:
            bank = product.split()[0]  # 상품명 앞단어를 은행 힌트로(순위엔 위 BANK_EVENTS가 주도)
            for _ in range(views):
                db.add_usage_event("view", product=product, category=category); n += 1
            for _ in range(searches):
                db.add_usage_event("search", product=product, category=category); n += 1
    print(f"테스트 이벤트 {n}건 적재 완료")


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "clear":
        clear()
    else:
        seed()
