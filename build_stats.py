"""홈페이지 대시보드용 데이터 생성 스크립트.

입력:
  - batch_test_results.json : AI 응답 품질 벤치마크 (999문항)
  - conversations/*.json    : 실제 사용자 대화 (인기/최근 질문)
  - app.py._BANK_URLS       : 은행명 → 바로가기 URL (ast로 안전 추출, 앱 미실행)

출력:
  - site/data/stats.json    : 대시보드 집계 지표
  - site/data/banks.json    : 은행 바로가기 목록

실행:  .venv/bin/python build_stats.py
"""
from __future__ import annotations

import ast
import glob
import json
import pathlib
from collections import Counter

_ROOT = pathlib.Path(__file__).parent
_DATA_DIR = _ROOT / "site" / "data"


def _extract_bank_urls() -> dict[str, str]:
    """app.py를 실행하지 않고 _BANK_URLS 딕셔너리 리터럴만 파싱한다."""
    source = (_ROOT / "app.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    for node in ast.walk(tree):
        # 일반 대입: _BANK_URLS = {...}
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id == "_BANK_URLS":
                    return ast.literal_eval(node.value)
        # 타입 주석 대입: _BANK_URLS: dict[str, str] = {...}
        if isinstance(node, ast.AnnAssign) and node.value is not None:
            if isinstance(node.target, ast.Name) and node.target.id == "_BANK_URLS":
                return ast.literal_eval(node.value)
    return {}


def _build_quality_stats() -> dict:
    """batch_test_results.json → 품질 지표 집계."""
    path = _ROOT / "batch_test_results.json"
    if not path.exists():
        return {}
    data = json.loads(path.read_text(encoding="utf-8"))
    meta = data.get("meta", {})
    results = data.get("results", [])

    total = len(results)
    success = sum(1 for r in results if r.get("success"))
    latencies = [r.get("latency_ms", 0) for r in results if r.get("latency_ms")]
    categories = Counter(r.get("category", "기타") for r in results)

    return {
        "total": total,
        "success": success,
        "success_rate": round(success / total * 100, 1) if total else 0,
        "avg_latency_ms": round(sum(latencies) / len(latencies)) if latencies else 0,
        "provider": meta.get("provider", ""),
        "model": meta.get("model", ""),
        "tested_at": meta.get("finished_at", ""),
        "categories": [
            {"name": name, "count": count}
            for name, count in categories.most_common()
        ],
    }


def _collect_questions() -> list[str]:
    """conversations/*.json → 사용자 질문(대화 제목) 목록."""
    titles: list[str] = []
    for fp in sorted(glob.glob(str(_ROOT / "conversations" / "*.json"))):
        try:
            conv = json.loads(pathlib.Path(fp).read_text(encoding="utf-8"))
        except Exception:
            continue
        title = (conv.get("title") or "").strip()
        if title:
            titles.append(title)
    return titles


def main() -> None:
    _DATA_DIR.mkdir(parents=True, exist_ok=True)

    quality = _build_quality_stats()
    questions = _collect_questions()

    stats = {
        "quality": quality,
        "recent_questions": questions[:8],
        "conversation_count": len(questions),
    }

    banks = [{"name": name, "url": url} for name, url in _extract_bank_urls().items()]

    (_DATA_DIR / "stats.json").write_text(
        json.dumps(stats, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (_DATA_DIR / "banks.json").write_text(
        json.dumps(banks, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    print(f"✅ stats.json  — 품질 {quality.get('total', 0)}문항, "
          f"성공률 {quality.get('success_rate', 0)}%, "
          f"대화 {len(questions)}건")
    print(f"✅ banks.json  — 은행 {len(banks)}개")
    print(f"   → {_DATA_DIR}")


if __name__ == "__main__":
    main()
