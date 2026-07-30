"""이체 이벤트 컨슈머 워커.

Kafka transfer-requests 토픽을 소비해 각 이체를 SQLite에서 원자적으로 처리한다.
실행: .venv/bin/python transfer_consumer.py  (별도 터미널, 상시 실행)
"""
from __future__ import annotations

import functools

from backend import db, kafka_io

# 로그가 즉시 보이도록 stdout 자동 flush
print = functools.partial(print, flush=True)  # noqa: A001


def main() -> None:
    db.init_db()
    consumer = kafka_io.make_consumer()
    print(f"[consumer] 대기 중… (topic={kafka_io.TOPIC_TRANSFER}, broker={kafka_io.BROKER})")
    try:
        for msg in consumer:
            evt = msg.value
            transfer_id = evt.get("transfer_id")
            if transfer_id is None:
                continue
            # 메시지 하나 처리하는 동안 db 연결을 하나로 공유하고 확실히 닫는다
            # (app.py 예약이체 폴러와 동일한 패턴 — 연결을 연 쪽이 닫을 책임을 명시적으로 진다).
            with db.request_scope():
                try:
                    status = db.process_transfer(transfer_id)
                    print(f"[consumer] transfer {transfer_id} → {status} "
                          f"({evt.get('from_account')} → {evt.get('to_account')}, "
                          f"{evt.get('amount'):,}원)")
                except Exception as e:
                    db.fail_transfer(transfer_id, f"처리 오류: {e}")
                    print(f"[consumer] transfer {transfer_id} 처리 오류: {e}")
    except KeyboardInterrupt:
        print("\n[consumer] 종료")
    finally:
        consumer.close()


if __name__ == "__main__":
    main()
