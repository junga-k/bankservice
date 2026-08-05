"""대화 영구 저장 모듈 (로컬 JSON).

대화 하나당 파일 하나: conversations/<id>.json
구조: {"id", "title", "created_at", "updated_at", "messages": [...]}
messages: [{"role": "user"|"assistant", "content": str}, ...]

나중에 SQLite 등으로 바꾸려면 이 파일의 함수들만 교체하면 된다
(app.py 는 아래 공개 함수들만 사용한다).
"""

from __future__ import annotations

import json
import time
import uuid
from pathlib import Path

# 이 파일이 있는 폴더 기준 conversations/ (실행 위치와 무관하게 동작)
CONV_DIR = Path(__file__).resolve().parent / "conversations"


def _ensure_dir() -> None:
    CONV_DIR.mkdir(parents=True, exist_ok=True)


def _path(conv_id: str) -> Path:
    return CONV_DIR / f"{conv_id}.json"


def new_conversation() -> dict:
    """새 빈 대화 객체를 만든다(아직 디스크에 저장하지 않음)."""
    now = time.time()
    return {
        "id": uuid.uuid4().hex,
        "title": "새 대화",
        "created_at": now,
        "updated_at": now,
        "messages": [],
    }


def _make_title(messages: list[dict]) -> str:
    """첫 사용자 메시지 앞부분으로 제목을 자동 생성."""
    for m in messages:
        if m["role"] == "user":
            text = m["content"].strip().replace("\n", " ")
            return text[:30] + ("…" if len(text) > 30 else "")
    return "새 대화"


def save_conversation(conv: dict) -> None:
    """대화를 디스크에 저장. 메시지가 없으면 저장하지 않는다.

    이미 저장된 messages와 완전히 같으면 updated_at을 건드리지 않는다 — 사이드바에서
    다른 대화로 전환하기 직전 "혹시 몰라서" 현재 대화를 방어적으로 저장하는 호출이
    있는데(app.py), 실제로는 매 메시지 전송 직후 이미 저장돼 있어 내용 변경이 없다.
    그런데도 매번 updated_at을 지금 시각으로 덮어써서, 대화를 그냥 열람만 해도
    "최근순" 사이드바 목록 순서가 흔들리는 버그가 있었다(2026-08-06 발견).
    """
    if not conv.get("messages"):
        return
    _ensure_dir()
    path = _path(conv["id"])
    if path.exists():
        try:
            with open(path, encoding="utf-8") as f:
                existing = json.load(f)
            if existing.get("messages") == conv["messages"]:
                return  # 내용 변경 없음 — updated_at 그대로 두고 재저장도 생략
        except (json.JSONDecodeError, KeyError):
            pass  # 기존 파일이 손상됐으면 그냥 새로 저장
    conv["updated_at"] = time.time()
    if conv.get("title", "새 대화") == "새 대화":
        conv["title"] = _make_title(conv["messages"])
    with open(path, "w", encoding="utf-8") as f:
        json.dump(conv, f, ensure_ascii=False, indent=2)


def load_conversation(conv_id: str) -> dict | None:
    p = _path(conv_id)
    if not p.exists():
        return None
    with open(p, encoding="utf-8") as f:
        return json.load(f)


def list_conversations(query: str = "") -> list[dict]:
    """최신순 대화 메타 목록 [{"id", "title", "updated_at"}, ...].

    query가 있으면 제목 또는 메시지 본문에 포함된 대화만 반환한다.
    """
    _ensure_dir()
    q = query.strip().lower()
    items = []
    for p in CONV_DIR.glob("*.json"):
        try:
            with open(p, encoding="utf-8") as f:
                conv = json.load(f)
            if q:
                title = conv.get("title", "").lower()
                hit = q in title or any(
                    q in m.get("content", "").lower() for m in conv.get("messages", [])
                )
                if not hit:
                    continue
            items.append(
                {
                    "id": conv["id"],
                    "title": conv.get("title", "새 대화"),
                    "updated_at": conv.get("updated_at", 0),
                }
            )
        except (json.JSONDecodeError, KeyError):
            continue
    items.sort(key=lambda x: x["updated_at"], reverse=True)
    return items


def delete_conversation(conv_id: str) -> None:
    p = _path(conv_id)
    if p.exists():
        p.unlink()
