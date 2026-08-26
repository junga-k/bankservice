"""설정 읽기/쓰기 모듈.

config.json 에 저장하며, 키가 없으면 DEFAULTS 값을 사용한다.

config.json 은 .gitignore 대상이라 배포본(Vercel/Streamlit Cloud)에는 존재하지 않는다.
그래서 API 키는 파일에 값이 없을 때 환경변수(_ENV_FALLBACK)에서 읽는다 — 파일 값이 항상
우선이므로 로컬 동작은 그대로다.
"""
from __future__ import annotations

import json
import os
import pathlib

_CONFIG_PATH = pathlib.Path(__file__).parent / "config.json"

DEFAULTS: dict = {
    "openai_api_key":  "",
    "fss_api_key":     "",
    "provider":        "",
    "default_model":   "",
    "default_style":   "🎯 정확  — 코드·번역·사실 질문",
    "system_prompt":   "",
    "web_search":      False,
    "cache_enabled":   False,
    "rag_top_k":       10,
    "cache_threshold": 0.08,
    "redis_ttl":       1800,
    "es_host":         "http://localhost:9200",
    "redis_host":      "localhost",
    "redis_port":      6379,
    # 이체 정책(관리자 조정 가능) — 상수 fallback 은 backend/app.py 참조
    "transfer_limit":        5_000_000,   # 1회 한도
    "daily_transfer_limit": 10_000_000,   # 1일 누적 한도
    "transfer_fee":                500,   # 타행 이체 수수료
}


# 파일에 값이 없을 때만 참조할 환경변수 (배포 환경용)
_ENV_FALLBACK = {
    "fss_api_key":    "FSS_API_KEY",
    "openai_api_key": "OPENAI_API_KEY",
}


def load() -> dict:
    cfg = dict(DEFAULTS)
    if _CONFIG_PATH.exists():
        with open(_CONFIG_PATH, encoding="utf-8") as f:
            cfg.update(json.load(f))
    for key, env in _ENV_FALLBACK.items():
        if not cfg.get(key):
            cfg[key] = os.environ.get(env, "")
    return cfg


def save(cfg: dict) -> None:
    with open(_CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(cfg, f, indent=2, ensure_ascii=False)
