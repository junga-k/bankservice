#!/bin/bash
# Kafka·Elasticsearch·Phoenix 일괄 기동. 이미 떠 있는 서비스는 건드리지 않는다.
set -e
cd "$(dirname "$0")"

echo "▶ Docker Desktop 확인..."
if ! docker info >/dev/null 2>&1; then
  # "docker desktop start"는 앱이 이미 떠 있으면 일시정지(paused) 상태를 풀지 못한다
  # (Whale 메뉴로 직접 재개해야 함) — restart를 쓰면 CLI만으로 paused도 함께 해소된다.
  if docker desktop status 2>/dev/null | grep -qi paused; then
    echo "  Docker Desktop이 일시정지 상태 — 재시작 중... (최대 60초 대기)"
    docker desktop restart
  else
    echo "  Docker Desktop 시작 중... (최대 60초 대기)"
    docker desktop start
  fi
  for i in $(seq 1 30); do
    docker info >/dev/null 2>&1 && break
    sleep 2
  done
fi
if docker info >/dev/null 2>&1; then
  echo "  ✓ Docker 준비됨"
else
  echo "  ✗ Docker Desktop을 시작하지 못했습니다 — Whale 메뉴에서 직접 확인하세요."
fi

echo "▶ Kafka (docker compose)..."
docker compose up -d

echo "▶ Elasticsearch..."
if curl -s -o /dev/null http://localhost:9200; then
  echo "  ✓ 이미 실행 중"
else
  ~/elasticsearch-8.13.4/bin/elasticsearch -d -p ~/elasticsearch.pid
  echo "  시작함 (완전히 뜨기까지 몇 초 걸릴 수 있음)"
fi

echo "▶ Phoenix..."
if curl -s -o /dev/null http://localhost:6006/healthz; then
  echo "  ✓ 이미 실행 중"
else
  nohup .venv/bin/phoenix serve > /tmp/phoenix.log 2>&1 &
  disown
  echo "  시작함"
fi

echo ""
echo "완료. 상태 확인:"
echo "  curl -s -o /dev/null -w '%{http_code}\n' http://localhost:9200"
echo "  curl -s -o /dev/null -w '%{http_code}\n' http://localhost:6006/healthz"
echo "  docker compose ps"
