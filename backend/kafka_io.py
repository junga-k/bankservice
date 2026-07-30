"""Kafka 프로듀서/컨슈머 공통 설정 (이체 이벤트).

브로커: localhost:9092 (docker compose 의 apache/kafka)
토픽:   transfer-requests
메시지: JSON {transfer_id, from_account, to_account, amount}
"""
from __future__ import annotations

import json
import os

BROKER = os.environ.get("KAFKA_BROKER", "localhost:9092")
TOPIC_TRANSFER = "transfer-requests"
# Kafka를 아예 붙이지 않은 환경(예: 현재 Vercel 배포)에서 매 요청마다 부트스트랩
# 재시도 비용을 치르지 않도록 명시적으로 건너뛰는 스위치.
KAFKA_DISABLED = os.environ.get("KAFKA_DISABLED", "").strip().lower() in ("1", "true", "yes")


def make_producer():
    """JSON 직렬화 프로듀서. Kafka 미기동 시 예외를 던진다(호출측에서 처리).
    bootstrap_timeout_ms 기본값(30000ms)이 브로커가 없을 때 응답을 그만큼
    묶어두는 원인이라 짧게 줄임 — request_timeout_ms/max_block_ms는 생성자의
    즉시(eager) 부트스트랩 단계엔 적용되지 않는다."""
    from kafka import KafkaProducer

    return KafkaProducer(
        bootstrap_servers=BROKER,
        value_serializer=lambda v: json.dumps(v).encode("utf-8"),
        acks="all",
        retries=3,
        request_timeout_ms=5000,
        max_block_ms=5000,
        bootstrap_timeout_ms=3000,
    )


def make_consumer(group_id: str = "transfer-worker"):
    from kafka import KafkaConsumer

    return KafkaConsumer(
        TOPIC_TRANSFER,
        bootstrap_servers=BROKER,
        group_id=group_id,
        value_deserializer=lambda v: json.loads(v.decode("utf-8")),
        auto_offset_reset="earliest",
        enable_auto_commit=True,
    )
