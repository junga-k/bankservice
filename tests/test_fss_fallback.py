"""금감원(FSS) 서버 장애 시 상품안내 동작.

2026-10-23 FSS 장애 때 상품안내가 통째로 비었다. 조회 실패를 삼켜 빈 목록을 "정상 0건"으로
돌려주고 그걸 1시간 캐시했기 때문이다. 지금은 실패를 드러내고, 마지막 정상 결과(db 보관본)로
대체하며, 빈 결과는 캐시하지 않는다. FSS 를 실제로 끊을 수 없으니 조회 함수를 바꿔 끼워 확인한다.
"""
import pytest
from fastapi import HTTPException

import fss_fetcher
from backend import app as app_mod
from backend import db

PRODUCTS = [{"bank": "테스트은행", "product_name": "정기예금", "category": "예금", "best_rate": 3.5}]


@pytest.fixture
def env(monkeypatch, tmp_path):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "t.db")
    monkeypatch.setattr(app_mod.config, "load", lambda: {"fss_api_key": "k"})
    monkeypatch.setattr(app_mod, "_PRODUCT_CACHE", {})
    monkeypatch.setattr(app_mod, "_fss_down_since", None)
    state = {"mode": "ok"}

    def fake_live(_key, _cat):
        if state["mode"] == "down":
            raise ConnectionError("Empty reply from server")
        if state["mode"] == "empty":
            return []
        return list(PRODUCTS)

    monkeypatch.setattr(app_mod, "_fetch_products_live", fake_live)
    return state


def test_정상_조회는_보관본을_남긴다(env):
    assert app_mod.get_products("예금") == {"products": PRODUCTS}
    saved = db.load_fss_products("예금")
    assert saved and saved[0] == PRODUCTS


def test_장애인데_보관본이_있으면_stale_로_대체(env):
    app_mod.get_products("예금")
    app_mod._PRODUCT_CACHE.clear()
    env["mode"] = "down"
    res = app_mod.get_products("예금")
    assert res["products"] == PRODUCTS
    assert res["stale"] is True and res["fetched_at"] > 0


def test_장애인데_보관본이_없으면_502(env):
    env["mode"] = "down"
    with pytest.raises(HTTPException) as e:
        app_mod.get_products("예금")
    assert e.value.status_code == 502


def test_빈_결과는_캐시하지_않고_정상으로_보지_않는다(env):
    env["mode"] = "empty"
    with pytest.raises(HTTPException):
        app_mod.get_products("예금")
    assert "예금" not in app_mod._PRODUCT_CACHE
    # 복구 대기시간이 지나면 다시 시도해 실시간 데이터로 돌아온다
    env["mode"] = "ok"
    app_mod._fss_down_since = 0.0
    assert app_mod.get_products("예금") == {"products": PRODUCTS}


def test_전_권역_실패면_예외(monkeypatch):
    def boom(*_a, **_k):
        raise ConnectionError("down")
    monkeypatch.setattr(fss_fetcher, "_fetch_pages", boom)
    with pytest.raises(ConnectionError):
        fss_fetcher.fetch_category_structured("k", "예금")
    with pytest.raises(ConnectionError):
        fss_fetcher.fetch_category_structured("k", "신용대출")


def test_일부_권역만_실패하면_나머지로_진행(monkeypatch):
    def partial(_auth, _endpoint, grp):
        if grp == "050000":
            raise ValueError("미지원")
        return [], []
    monkeypatch.setattr(fss_fetcher, "_fetch_pages", partial)
    assert fss_fetcher.fetch_category_structured("k", "예금", ["020000", "050000"]) == []
