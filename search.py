"""웹 검색 모듈 (DuckDuckGo — API 키 불필요)."""

from __future__ import annotations


def web_search(query: str, max_results: int = 5) -> list[dict]:
    """DuckDuckGo로 텍스트 검색. 실패하면 빈 리스트 반환."""
    try:
        from ddgs import DDGS
        with DDGS() as ddgs:
            return list(ddgs.text(query, max_results=max_results))
    except Exception:
        return []


def format_for_llm(results: list[dict]) -> str:
    """검색 결과를 LLM 컨텍스트 문자열로 변환."""
    if not results:
        return ""
    lines = ["[웹 검색 결과 — 아래 내용을 참고하여 답변하세요]\n"]
    for i, r in enumerate(results, 1):
        title = r.get("title", "")
        url = r.get("href", "")
        body = (r.get("body", "") or "")[:400]
        lines.append(f"[{i}] {title}")
        if url:
            lines.append(f"출처: {url}")
        if body:
            lines.append(body)
        lines.append("")
    return "\n".join(lines)
