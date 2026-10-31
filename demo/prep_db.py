# 녹화 전 데모 데이터 준비: 백오피스 프롬프트 관리의 '변경 이력'이 비어 있으면 3건을 넣는다.
# API(PUT /api/admin/chatbot-config)로 저장하면 실제 AI 설정(config.json)까지 바뀌므로 DB 에만 직접 넣는다.
# 마지막 이력은 지금 config.json 의 프롬프트와 같게 해서 '현재 버전'과 어긋나지 않게 한다.
# 녹화 후 bank.db 를 백업으로 되돌리면 함께 사라진다(README 순서 2·5).
# 사용법: ../.venv/bin/python prep_db.py   (demo/ 에서)
import json
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
KST = timezone(timedelta(hours=9))

cfg = json.loads((ROOT / "config.json").read_text(encoding="utf-8"))
current = cfg.get("system_prompt", "")
sections = current.split("\n### ")
# v1: 비교표(2단계)와 포트폴리오(3단계) 없이 추천만 → v2: 비교표 추가 → v3: 지금 프롬프트
v1 = sections[0].replace("아래 3가지 단계로", "아래 형식으로") + "\n### " + sections[1] if len(sections) >= 3 else current
v2 = "\n### ".join(sections[:3]).replace("아래 3가지 단계로", "아래 2가지 단계로") if len(sections) >= 3 else current
versions = [
    (v1, datetime(2026, 10, 20, 14, 10, tzinfo=KST)),
    (v2, datetime(2026, 10, 24, 11, 30, tzinfo=KST)),
    (current, datetime(2026, 10, 29, 16, 45, tzinfo=KST)),
]

con = sqlite3.connect(ROOT / "bank.db")
if con.execute("SELECT COUNT(*) FROM chatbot_config_history").fetchone()[0]:
    print("프롬프트 이력이 이미 있어 건너뜀")
else:
    name = (con.execute("SELECT name FROM users WHERE role='admin' ORDER BY id LIMIT 1").fetchone() or ["관리자"])[0]
    for prompt, at in versions:
        con.execute(
            "INSERT INTO chatbot_config_history "
            "(provider, default_model, default_style, system_prompt, web_search, changed_by, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (cfg.get("provider", "OpenAI"), cfg.get("default_model", ""), "정확", prompt,
             int(bool(cfg.get("web_search", False))), name, at.timestamp()),
        )
    con.commit()
    print(f"프롬프트 이력 {len(versions)}건 추가")
con.close()
