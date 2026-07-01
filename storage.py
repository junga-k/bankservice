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
    """대화를 디스크에 저장. 메시지가 없으면 저장하지 않는다."""
    if not conv.get("messages"):
        return
    _ensure_dir()
    conv["updated_at"] = time.time()
    if conv.get("title", "새 대화") == "새 대화":
        conv["title"] = _make_title(conv["messages"])
    with open(_path(conv["id"]), "w", encoding="utf-8") as f:
        json.dump(conv, f, ensure_ascii=False, indent=2)


def load_conversation(conv_id: str) -> dict | None:
    p = _path(conv_id)
    if not p.exists():
        return None
    with open(p, encoding="utf-8") as f:
        return json.load(f)


def list_conversations() -> list[dict]:
    """최신순 대화 메타 목록 [{"id", "title", "updated_at"}, ...]."""
    _ensure_dir()
    items = []
    for p in CONV_DIR.glob("*.json"):
        try:
            with open(p, encoding="utf-8") as f:
                conv = json.load(f)
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
