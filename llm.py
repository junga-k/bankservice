"""LLM 호출 추상화 모듈 (멀티 제공자, 멀티모달 지원).

새 제공자 추가 방법:
  1) PROVIDERS 에 항목 추가
  2) _stream_<provider>() 함수 작성
  3) stream_chat() 분기에 연결
app.py 는 수정 불필요.

attachments 형식:
  {"type": "text",  "name": str, "content": str}   # 텍스트·PDF
  {"type": "image", "name": str, "data": bytes, "mime_type": str}  # 이미지
"""

from __future__ import annotations
import base64

PROVIDERS: dict[str, dict] = {
    "OpenAI": {
        "models": [
            "gpt-4o-mini",
            "gpt-4o",
            "gpt-4.1-mini",
        ],
        "secret_key": "OPENAI_API_KEY",
    },
}

DEFAULT_PROVIDER = "OpenAI"


def models_for(provider: str) -> list[str]:
    return PROVIDERS[provider]["models"]


def secret_key_for(provider: str) -> str:
    return PROVIDERS[provider]["secret_key"]


def _text_context(attachments: list[dict]) -> str:
    parts = [a for a in attachments if a["type"] == "text"]
    return "\n\n".join(f"[첨부파일: {a['name']}]\n{a['content']}" for a in parts)


# ──────────────────────────────────────────────
# Gemini
# ──────────────────────────────────────────────
def _to_gemini_contents(messages: list[dict], attachments: list[dict]) -> list:
    text_ctx = _text_context(attachments)
    image_atts = [a for a in attachments if a["type"] == "image"]
    contents = []

    for i, m in enumerate(messages):
        role = "model" if m["role"] == "assistant" else "user"
        is_last = i == len(messages) - 1

        if is_last and m["role"] == "user" and (text_ctx or image_atts):
            parts = []
            for img in image_atts:
                b64 = base64.b64encode(img["data"]).decode()
                parts.append({"inline_data": {"data": b64, "mime_type": img["mime_type"]}})
            text = (text_ctx + "\n\n" + m["content"]) if text_ctx else m["content"]
            parts.append({"text": text})
            contents.append({"role": role, "parts": parts})
        else:
            contents.append({"role": role, "parts": [{"text": m["content"]}]})

    return contents


def _stream_gemini(api_key, model, messages, temperature, system_prompt, attachments):
    import json
    from google import genai
    from google.genai import types

    client = genai.Client(api_key=api_key)
    config = types.GenerateContentConfig(temperature=temperature)
    if system_prompt:
        config.system_instruction = system_prompt

    # OTEL 수동 스팬 (google-genai 신규 SDK는 자동 계측 미지원)
    try:
        from opentelemetry import trace as otel_trace
        from opentelemetry.trace import SpanKind, StatusCode
        from openinference.semconv.trace import SpanAttributes
        tracer = otel_trace.get_tracer("llm.gemini")
        span_ctx = tracer.start_span("gemini.generate_content", kind=SpanKind.CLIENT)
        span_ctx.set_attribute(SpanAttributes.LLM_PROVIDER, "google")
        span_ctx.set_attribute(SpanAttributes.LLM_MODEL_NAME, model)
        span_ctx.set_attribute("llm.invocation_parameters", json.dumps({"temperature": temperature}))
        span_ctx.set_attribute(SpanAttributes.INPUT_VALUE, messages[-1]["content"] if messages else "")
    except Exception:
        span_ctx = None

    stream = client.models.generate_content_stream(
        model=model,
        contents=_to_gemini_contents(messages, attachments),
        config=config,
    )
    chunks: list[str] = []
    try:
        for chunk in stream:
            text = getattr(chunk, "text", None)
            if text:
                chunks.append(text)
                yield text
        if span_ctx:
            span_ctx.set_attribute(SpanAttributes.OUTPUT_VALUE, "".join(chunks))
            span_ctx.set_status(StatusCode.OK)
    except Exception as e:
        if span_ctx:
            span_ctx.record_exception(e)
            span_ctx.set_status(StatusCode.ERROR)
        raise
    finally:
        if span_ctx:
            span_ctx.end()


# ──────────────────────────────────────────────
# OpenAI
# ──────────────────────────────────────────────
def _stream_openai(api_key, model, messages, temperature, system_prompt, attachments):
    from openai import OpenAI

    client = OpenAI(api_key=api_key)
    text_ctx = _text_context(attachments)
    image_atts = [a for a in attachments if a["type"] == "image"]

    oai_messages = []
    if system_prompt:
        oai_messages.append({"role": "system", "content": system_prompt})

    for i, m in enumerate(messages):
        is_last = i == len(messages) - 1
        if is_last and m["role"] == "user" and (text_ctx or image_atts):
            content = []
            text = (text_ctx + "\n\n" + m["content"]) if text_ctx else m["content"]
            content.append({"type": "text", "text": text})
            for img in image_atts:
                b64 = base64.b64encode(img["data"]).decode()
                content.append({
                    "type": "image_url",
                    "image_url": {"url": f"data:{img['mime_type']};base64,{b64}"},
                })
            oai_messages.append({"role": "user", "content": content})
        else:
            oai_messages.append({"role": m["role"], "content": m["content"]})

    stream = client.chat.completions.create(
        model=model,
        messages=oai_messages,
        temperature=temperature,
        stream=True,
    )
    for chunk in stream:
        delta = chunk.choices[0].delta.content
        if delta:
            yield delta


# ──────────────────────────────────────────────
# 공개 API
# ──────────────────────────────────────────────
def stream_chat(
    provider: str,
    api_key: str,
    model: str,
    messages: list[dict],
    temperature: float = 0.7,
    system_prompt: str | None = None,
    attachments: list[dict] | None = None,
):
    """대화 메시지를 받아 응답을 청크 단위로 스트리밍한다."""
    atts = attachments or []
    if provider == "Gemini":
        yield from _stream_gemini(api_key, model, messages, temperature, system_prompt, atts)
    elif provider == "OpenAI":
        yield from _stream_openai(api_key, model, messages, temperature, system_prompt, atts)
    else:
        raise ValueError(f"알 수 없는 제공자: {provider}")
