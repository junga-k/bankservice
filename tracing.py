"""Arize Phoenix 추적(tracing) 설정.

Phoenix 서버는 앱과 별도 터미널에서 실행해야 합니다:
    .venv/bin/phoenix serve

이 모듈은 Phoenix 서버에 OTEL 트레이스를 전송하도록 설정합니다.
@st.cache_resource 로 감싸져 앱 생애주기 동안 한 번만 실행됩니다.
"""

from __future__ import annotations

import streamlit as st

PHOENIX_URL = "http://localhost:6006"


def _is_phoenix_running(url: str) -> bool:
    """Phoenix 서버가 실행 중인지 확인."""
    try:
        import urllib.request
        urllib.request.urlopen(f"{url}/healthz", timeout=1)
        return True
    except Exception:
        return False


@st.cache_resource
def init_tracing() -> dict:
    """OTEL tracer provider 를 설정하고 계측기를 등록한다.

    Phoenix 서버가 없어도 앱은 정상 동작하며,
    서버가 나중에 뜨면 추적이 자동으로 시작됩니다.

    Returns:
        {"ok": bool, "url": str, "phoenix_up": bool, "error": str | None}
    """
    try:
        from phoenix.otel import register

        register(
            project_name="ai-chat",
            endpoint=f"{PHOENIX_URL}/v1/traces",
            batch=False,
            verbose=False,
        )

        # OpenAI 자동 계측
        try:
            from openinference.instrumentation.openai import OpenAIInstrumentor
            OpenAIInstrumentor().instrument()
        except Exception:
            pass

        phoenix_up = _is_phoenix_running(PHOENIX_URL)
        return {"ok": True, "url": PHOENIX_URL, "phoenix_up": phoenix_up, "error": None}

    except Exception as e:
        return {"ok": False, "url": PHOENIX_URL, "phoenix_up": False, "error": str(e)}
