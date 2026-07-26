"""은행 바로가기(`site/data/banks.json`) 링크 상태 점검.

등록된 은행 URL이 실제로 응답하는지(200대/300대) 확인한다. 별명(같은 은행,
다른 표기명)은 URL 기준으로 한 번만 검사한다. 실행: .venv/bin/python check_bank_links.py

정기 스케줄러(cron 등)는 따로 없다 — 필요할 때 수동으로 돌려보는 점검 스크립트다.
실패가 있으면 종료 코드 1을 반환한다.
"""
from __future__ import annotations

import json
import ssl
import sys
import urllib.request
from pathlib import Path

BANKS_JSON = Path(__file__).parent / "site" / "data" / "banks.json"
TIMEOUT_SECONDS = 8


def check_url(url: str) -> tuple[bool, str]:
    """(성공 여부, 상세 메시지)를 반환한다."""
    ctx = ssl.create_default_context()
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT_SECONDS, context=ctx) as resp:
            final_url = resp.geturl()
            detail = f"{resp.status}" + (f" -> {final_url}" if final_url != url else "")
            return True, detail
    except Exception as e:  # noqa: BLE001 - 점검 스크립트라 모든 실패를 한 번에 잡는다
        return False, str(e)


def main() -> int:
    banks = json.loads(BANKS_JSON.read_text(encoding="utf-8"))

    seen_urls: dict[str, list[str]] = {}
    for b in banks:
        seen_urls.setdefault(b["url"], []).append(b["name"])

    failures = []
    print(f"은행 바로가기 링크 {len(seen_urls)}건 점검 중...\n")
    for url, names in seen_urls.items():
        label = "/".join(names)
        ok, detail = check_url(url)
        status = "OK " if ok else "ERR"
        print(f"[{status}] {label}\t{url}\t{detail}")
        if not ok:
            failures.append((label, url, detail))

    print(f"\n{len(seen_urls) - len(failures)}/{len(seen_urls)} 정상")
    if failures:
        print("\n실패한 링크:")
        for label, url, detail in failures:
            print(f"  - {label}: {url} ({detail})")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
