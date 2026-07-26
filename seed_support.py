"""고객센터 데모 데이터 시드: 공지사항 / FAQ / 서식·약관·설명서 / 이벤트 / 특별상품 / 배너.

FAQ는 기존 index.html에 하드코딩돼 있던 4개를 그대로 옮긴 것이다.
테이블별로 독립적으로 멱등 체크한다(하나가 이미 시드돼 있어도 나머지 테이블은 채운다).
실행: .venv/bin/python seed_support.py
"""
from __future__ import annotations

import time

from backend import db

NOTICES = [
    ("[공지] 시스템 정기점검 안내",
     "보다 안정적인 서비스 제공을 위해 7월 5일 02:00~04:00 정기점검이 진행됩니다. "
     "점검 시간 동안 일부 서비스 이용이 제한될 수 있습니다."),
    ("[공지] 신규 적금 상품 출시 안내",
     "고금리 자유적립식 적금 상품이 새롭게 출시되었습니다. 상품안내 탭에서 자세한 조건을 확인하세요."),
    ("[공지] 개인정보 처리방침 개정 안내",
     "관련 법령 개정에 따라 개인정보 처리방침 일부 조항이 개정되었습니다. 서식·약관·설명서 메뉴에서 "
     "전문을 확인하실 수 있습니다."),
    ("[공지] AI은행원 서비스 업데이트 안내",
     "AI은행원의 금융상품 검색 정확도가 개선되었습니다. 더 빠르고 정확한 답변을 받아보세요."),
]

FAQS = [
    ("AI은행원은 어떻게 사용하나요?",
     "상단 메뉴의 AI은행원을 누른 뒤, 입력창에 궁금한 금융상품이나 업무를 질문하면 됩니다."),
    ("답변의 금리 정보는 정확한가요?",
     "금융감독원(FSS) 데이터를 기반으로 안내하지만, 실제 가입 전 각 은행 공식 페이지에서 최신 조건을 "
     "반드시 확인하세요."),
    ("어떤 은행을 지원하나요?",
     "국민·신한·우리·하나·농협 등 주요 시중은행과 인터넷전문은행, 지방은행을 포함합니다. 상품안내 탭에서 "
     "바로가기를 확인하세요."),
    ("이용 요금이 있나요?",
     "검색·상담 서비스는 무료로 제공됩니다."),
]

# (제목, 카테고리, 설명)
DOCUMENTS = [
    ("예금거래 기본약관", "약관", "예금 계좌 개설 및 거래 전반에 적용되는 기본 약관입니다."),
    ("전자금융거래 이용약관", "약관", "인터넷·모바일 등 전자금융거래 이용 시 적용되는 약관입니다."),
    ("개인정보 수집·이용 동의서", "서식", "회원가입 및 서비스 이용 시 개인정보 수집·이용에 대한 동의 서식입니다."),
    ("예금자보호법 안내문", "설명서", "예금보험공사의 예금자보호 제도와 보호 한도를 안내하는 문서입니다."),
    ("적금 상품설명서", "설명서", "적금 상품의 가입 조건, 금리, 중도해지 시 유의사항을 안내하는 문서입니다."),
]


def seed_support_content() -> None:
    if db.count_faqs() > 0:
        print("이미 시드됨 (faqs 존재) — 건너뜀")
        return
    for title, content in NOTICES:
        db.create_notice(title, content)
    for question, answer in FAQS:
        db.create_faq(question, answer)
    for title, category, description in DOCUMENTS:
        db.create_document(title, category, description)
    print(f"시드 완료: 공지사항 {len(NOTICES)}개, FAQ {len(FAQS)}개, 서식·약관·설명서 {len(DOCUMENTS)}개")


def seed_special_products() -> int | None:
    """특판 특별상품 1건을 시드하고 id를 반환(이미 있으면 기존 id 조회).

    매치뱅크는 실제 은행이 아니라 FSS API 기반 비교 사이트이므로, 특별상품도 가상 상품이 아니라
    실제 FSS 예금 데이터 중 상품명에 "특판"이 들어간 부산은행 "더(The) 특판 정기예금"을 그대로 옮겨왔다
    (2026.07.20 공시 기준 최고금리 3.55%, 12·24개월). FSS API에는 "특판/이벤트성 상품" 전용 필드가
    없어 상품명 키워드로 찾아낸 것 — 실시간 연동은 아니라 금리 갱신 시 수동 반영 필요.
    (이전에는 청년 전용 상품(농협은행 NH1934월복리적금)을 썼으나, 청년미래적금류 정부정책 상품은
    FSS가 아니라 서민금융진흥원이 관리해 FSS 데이터에 없다는 걸 확인 후 청년 한정이 아닌
    일반 특판상품으로 교체함.)
    """
    existing = db.list_special_products(limit=50)
    match = next((p for p in existing if p["title"] == "더(The) 특판 정기예금"), None)
    if match:
        return match["id"]
    if db.count_special_products() > 0:
        return None  # 다른 특별상품은 있는데 이 상품만 없는 특이 상태 — 배너 연결은 건너뜀
    product_id = db.create_special_product(
        title="더(The) 특판 정기예금", bank_name="부산은행",
        rate_text="연 최고 3.55%(12·24개월 기준)",
        description="신규 고객 우대 등 조건 충족 시 최대 1.35%p 우대금리가 적용되는 특판 정기예금",
        badge="특판", sort_order=0,
    )
    print("시드 완료: 특별상품 1개")
    return product_id


def seed_events() -> int | None:
    """오픈 기념 추첨 이벤트 1건 + 정보성 이벤트 1건을 시드하고 추첨 이벤트 id를 반환."""
    if db.count_events() > 0:
        existing = db.list_events(limit=50)
        match = next((e for e in existing if e["title"] == "매치뱅크 오픈 기념 이벤트"), None)
        return match["id"] if match else None
    now = time.time()
    draw_event_id = db.create_event(
        title="매치뱅크 오픈 기념 이벤트",
        content="매치뱅크 오픈을 기념해 신규 가입 고객 중 추첨을 통해 10명께 축하 상품을 드립니다.",
        start_at=now, end_at=now + 30 * 86400,
        is_drawing=1, winner_count=10,
    )
    db.create_event(
        title="AI은행원 업데이트 후기 이벤트",
        content="AI은행원 업데이트 이용 후기를 남겨주신 모든 분께 감사 인사를 전합니다.",
        start_at=now, end_at=now + 60 * 86400,
        is_drawing=0, winner_count=0,
    )
    print("시드 완료: 이벤트 2개")
    return draw_event_id


def seed_banners(event_id: int | None, special_product_id: int | None) -> None:
    if db.count_banners() > 0:
        print("이미 시드됨 (banners 존재) — 건너뜀")
        return
    notices = db.list_notices(limit=50)
    update_notice = next((n for n in notices if n["title"] == "[공지] AI은행원 서비스 업데이트 안내"), None)

    # 실제 디자인 파일이 없어 기존 3가지 톤을 살린 샘플 SVG(1080x360, site/img/banners/seed-N.svg)를 사용.
    # 관리자가 실제 업로드하는 배너는 같은 폴더에 저장되지만 git에는 커밋하지 않는다(.gitignore 참고).
    db.create_banner(
        title="매치뱅크 오픈 기념", subtitle="지금 가입하면 계좌 개설 바로 가능해요",
        image_path="/img/banners/seed-1.svg",
        link_type="event" if event_id else "none", link_id=event_id,
        sort_order=1, is_active=1,
    )
    db.create_banner(
        title="AI은행원 업데이트", subtitle="더 정확해진 금융상품 비교를 경험해보세요",
        image_path="/img/banners/seed-2.svg",
        link_type="notice" if update_notice else "none",
        link_id=update_notice["id"] if update_notice else None,
        sort_order=2, is_active=1,
    )
    db.create_banner(
        title="부산은행 특판예금", subtitle="지금 가입 가능한 특판 상품을 확인해보세요",
        image_path="/img/banners/seed-3.svg",
        link_type="special_product" if special_product_id else "none",
        link_id=special_product_id,
        sort_order=3, is_active=1,
    )
    print("시드 완료: 배너 3개")


def main() -> None:
    db.init_db()
    seed_support_content()
    special_product_id = seed_special_products()
    event_id = seed_events()
    seed_banners(event_id, special_product_id)


if __name__ == "__main__":
    main()
