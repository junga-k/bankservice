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


# ── 2026-10-06 라이브 확인에서 나온 버그 2건 ──────────────────────────

def test_점검창_안으로_예약한_실행시각은_점검중으로_판정된다():
    """23:59 에 '10/7 00:00' 으로 예약하면 그 시각은 아직 점검 창(23:50~00:10) 안이다.

    예전에는 즉시 이체만 막고 예약은 실행 시각을 보지 않아서 그대로 통과했다. 폴러가
    점검 중엔 쉬므로 돈이 점검 중에 움직이진 않았지만, 사용자에게는 '00:00에 보낸다'고
    약속해놓고 실제로는 00:10 이후에 나가는 셈이었다.
    """
    # 예약하려는 실행 시각들 — 전부 점검 창 안이라 '점검 중'이어야 한다
    for h, m in [(23, 50), (23, 59), (0, 0), (0, 9)]:
        day = 15 if h == 23 else 16
        assert maintenance_state(at(h, m, day))[0] is True, \
            f"{h:02d}:{m:02d} 로 예약한 실행 시각이 점검 창 밖으로 판정됐다"

    # 창 밖(= 받아도 되는 시각)
    for h, m, day in [(0, 10, 16), (0, 30, 16), (23, 49, 15)]:
        assert maintenance_state(at(h, m, day))[0] is False, \
            f"{h:02d}:{m:02d} 는 예약이 허용돼야 한다"


def test_점검중_예약의_재개시각은_다음_00시10분():
    """거절 문구에 넣는 '이 시각 이후로 설정하세요' 값이 맞는지."""
    _, resume = maintenance_state(at(23, 59, 15))
    assert dt.datetime.fromtimestamp(resume, KST) == dt.datetime(2026, 10, 16, 0, 10, tzinfo=KST)

    _, resume = maintenance_state(at(0, 5, 16))
    assert dt.datetime.fromtimestamp(resume, KST) == dt.datetime(2026, 10, 16, 0, 10, tzinfo=KST)


def test_예약시각_변환이_KST_기준이어야_한다():
    """챗봇이 고른 '10/7 00:00' 을 naive 로 다루면 배포(UTC)에서 9시간 뒤로 저장된다.

    실제로 마이페이지 예약이체에 '오전 9시'로 떴던 버그. app.py 는 이제 KST 를 명시한다.
    """
    picked_date = dt.date(2026, 10, 16)
    picked_time = dt.time(0, 0)

    kst_epoch = dt.datetime.combine(picked_date, picked_time, tzinfo=KST).timestamp()
    assert dt.datetime.fromtimestamp(kst_epoch, KST) == dt.datetime(2026, 10, 16, 0, 0, tzinfo=KST)

    # 같은 값을 UTC 로 해석하면(예전 동작) KST 로 09:00 이 된다 — 사용자가 본 그 숫자다
    utc_epoch = dt.datetime.combine(picked_date, picked_time, tzinfo=dt.timezone.utc).timestamp()
    assert dt.datetime.fromtimestamp(utc_epoch, KST).hour == 9
    assert utc_epoch - kst_epoch == 9 * 3600
