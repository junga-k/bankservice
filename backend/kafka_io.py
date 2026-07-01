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


def make_producer():
    """JSON 직렬화 프로듀서. Kafka 미기동 시 예외를 던진다(호출측에서 처리)."""
    from kafka import KafkaProducer

    return KafkaProducer(
        bootstrap_servers=BROKER,
        value_serializer=lambda v: json.dumps(v).encode("utf-8"),
        acks="all",
        retries=3,
        request_timeout_ms=5000,
        max_block_ms=5000,
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
