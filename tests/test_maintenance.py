"""정기점검 시간대와 KST 날짜 경계.

시계를 기다리지 않고 확인할 수 있도록 두 헬퍼 모두 now 를 인자로 받게 만들어뒀다.
점검창이 23:50~00:10 으로 **자정을 넘기기 때문에** 경계가 특히 틀리기 쉬워서 고정해 둔다.
"""
import datetime as dt

from backend.app import KST, kst_today_start, maintenance_state


def at(h: int, m: int, day: int = 15) -> float:
    """2026-10-{day} {h}:{m} KST 의 epoch."""
    return dt.datetime(2026, 10, day, h, m, tzinfo=KST).timestamp()


def test_점검시간이_아닌_때는_비활성():
    for h, m in [(0, 10), (9, 0), (12, 0), (23, 49)]:
        active, resume_at = maintenance_state(at(h, m))
        assert active is False, f"{h:02d}:{m:02d} 는 점검시간이 아니어야 한다"
        assert resume_at == 0.0


def test_점검_시작_직후부터_자정_전까지_활성():
    for h, m in [(23, 50), (23, 55), (23, 59)]:
        active, resume_at = maintenance_state(at(h, m))
        assert active is True, f"{h:02d}:{m:02d} 는 점검시간이어야 한다"
        # 자정을 넘기므로 재개 시각은 '다음 날' 00:10
        assert dt.datetime.fromtimestamp(resume_at, KST) == dt.datetime(2026, 10, 16, 0, 10, tzinfo=KST)


def test_자정_직후부터_점검_종료_전까지_활성():
    for h, m in [(0, 0), (0, 5), (0, 9)]:
        active, resume_at = maintenance_state(at(h, m))
        assert active is True, f"{h:02d}:{m:02d} 는 점검시간이어야 한다"
        # 어제 23:50 에 시작된 점검이 이어지는 중이므로 재개는 '같은 날' 00:10
        assert dt.datetime.fromtimestamp(resume_at, KST) == dt.datetime(2026, 10, 15, 0, 10, tzinfo=KST)


def test_점검_종료_시각은_이미_정상():
    active, _ = maintenance_state(at(0, 10))
    assert active is False


def test_일일한도_기준일은_KST_자정():
    """예전에는 서버 로컬(UTC) 자정 기준이라 한국 사용자에겐 오전 9시에 리셋됐다."""
    expected = dt.datetime(2026, 10, 15, 0, 0, tzinfo=KST)
    for h, m in [(0, 0), (8, 59), (9, 0), (23, 59)]:
        got = dt.datetime.fromtimestamp(kst_today_start(at(h, m)), KST)
        assert got == expected, f"{h:02d}:{m:02d} 기준일이 KST 자정이 아니다"


def test_자정을_넘지_않는_창도_재개시각이_같은_날(monkeypatch):
    """운영 기본값은 자정을 넘지만(23:50~00:10), 점검 시각을 옮기면 같은 날 안에서 끝난다.
    자정 분기만 두면 그때 재개 시각이 하루 뒤로 잡힌다(실제로 임시 창 확인 중 발견)."""
    import backend.app as app
    monkeypatch.setattr(app, "MAINTENANCE_START", (3, 0))
    monkeypatch.setattr(app, "MAINTENANCE_END", (3, 20))

    assert app.maintenance_state(at(2, 59))[0] is False
    active, resume_at = app.maintenance_state(at(3, 10))
    assert active is True
    assert dt.datetime.fromtimestamp(resume_at, KST) == dt.datetime(2026, 10, 15, 3, 20, tzinfo=KST)
    assert app.maintenance_state(at(3, 20))[0] is False
    assert app.maintenance_state(at(23, 55))[0] is False   # 옮긴 창 밖


def test_KST는_UTC보다_9시간_빠르다():
    assert KST.utcoffset(None) == dt.timedelta(hours=9)
