"""설정 읽기/쓰기 모듈.

config.json 에 저장하며, 키가 없으면 DEFAULTS 값을 사용한다.
"""
from __future__ import annotations

import json
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
}


def load() -> dict:
    if _CONFIG_PATH.exists():
        with open(_CONFIG_PATH, encoding="utf-8") as f:
            return {**DEFAULTS, **json.load(f)}
    return dict(DEFAULTS)


def save(cfg: dict) -> None:
    with open(_CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(cfg, f, indent=2, ensure_ascii=False)
