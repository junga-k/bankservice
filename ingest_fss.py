"""금융감독원 FSS 데이터를 Elasticsearch에 직접 인덱싱하는 독립 스크립트.

실행:
    cd ~/myProject/chat
    .venv/bin/python ingest_fss.py --fss-key YOUR_FSS_KEY

또는 secrets.toml에 FSS_API_KEY가 있으면:
    .venv/bin/python ingest_fss.py
"""

from __future__ import annotations

import argparse
import pathlib
import sys

# ── 키 로드 ────────────────────────────────────────────────────────────
def _load_secrets() -> dict:
    secrets_path = pathlib.Path(__file__).parent / ".streamlit" / "secrets.toml"
    if not secrets_path.exists():
        return {}
    try:
        import tomllib
    except ImportError:
        try:
            import tomli as tomllib
        except ImportError:
            return {}
    with open(secrets_path, "rb") as f:
        return tomllib.load(f)


secrets = _load_secrets()

ES_HOST   = "http://localhost:9200"
ES_INDEX  = "rag_documents"
EMBED_DIM = 1536
EMBED_MODEL = "text-embedding-3-small"
CHUNK_SIZE = 900
CHUNK_OVERLAP = 100


# ── ES 인덱스 준비 ────────────────────────────────────────────────────
def _ensure_index(es) -> None:
    if not es.indices.exists(index=ES_INDEX):
        es.indices.create(index=ES_INDEX, mappings={"properties": {
            "text":      {"type": "text", "analyzer": "standard"},
            "embedding": {"type": "dense_vector", "dims": EMBED_DIM,
                          "index": True, "similarity": "cosine"},
            "doc_name":  {"type": "keyword"},
            "chunk_idx": {"type": "integer"},
        }})
        print(f"  인덱스 '{ES_INDEX}' 생성 완료")


# ── 청킹 ──────────────────────────────────────────────────────────────
def _chunk(text: str) -> list[str]:
    chunks, start = [], 0
    while start < len(text):
        chunks.append(text[start:start + CHUNK_SIZE])
        start += CHUNK_SIZE - CHUNK_OVERLAP
    return chunks


# ── 임베딩 ────────────────────────────────────────────────────────────
def _embed(texts: list[str], openai_key: str) -> list[list[float]]:
    from openai import OpenAI
    resp = OpenAI(api_key=openai_key).embeddings.create(
        model=EMBED_MODEL, input=texts
    )
    return [r.embedding for r in resp.data]


# ── 인덱싱 ────────────────────────────────────────────────────────────
def index_docs(docs: list[tuple[str, str]], openai_key: str, es) -> int:
    from elasticsearch.helpers import bulk
    total = 0
    for doc_name, text in docs:
        es.delete_by_query(
            index=ES_INDEX,
            query={"term": {"doc_name": doc_name}},
            refresh=True,
        )
        chunks = _chunk(text)
        if not chunks:
            continue
        embeddings = _embed(chunks, openai_key)
        actions = [
            {
                "_index": ES_INDEX,
                "_id": f"{doc_name}__{i}",
                "_source": {
                    "text": chunk,
                    "embedding": emb,
                    "doc_name": doc_name,
                    "chunk_idx": i,
                },
            }
            for i, (chunk, emb) in enumerate(zip(chunks, embeddings))
        ]
        bulk(es, actions, refresh=True)
        total += len(chunks)
    return total


# ── 메인 ─────────────────────────────────────────────────────────────
def main() -> None:
    parser = argparse.ArgumentParser(description="FSS 금융상품 데이터 → Elasticsearch 인덱싱")
    parser.add_argument("--fss-key",    default=secrets.get("FSS_API_KEY", ""),   help="FSS API 키")
    parser.add_argument("--openai-key", default=secrets.get("OPENAI_API_KEY", ""), help="OpenAI API 키")
    parser.add_argument("--es-host",    default=ES_HOST, help="Elasticsearch 주소")
    args = parser.parse_args()

    if not args.fss_key:
        print("오류: FSS API 키가 필요합니다. --fss-key 옵션 또는 secrets.toml에 FSS_API_KEY를 설정하세요.")
        sys.exit(1)
    if not args.openai_key:
        print("오류: OpenAI API 키가 필요합니다. --openai-key 옵션 또는 secrets.toml에 OPENAI_API_KEY를 설정하세요.")
        sys.exit(1)

    from elasticsearch import Elasticsearch
    import fss_fetcher

    es = Elasticsearch(args.es_host)
    if not es.ping():
        print(f"오류: Elasticsearch({args.es_host})에 연결할 수 없습니다. 실행 중인지 확인하세요.")
        sys.exit(1)

    _ensure_index(es)

    print("\n[FSS 금융상품 데이터 수집 시작]\n")
    grand_total_docs = 0
    grand_total_chunks = 0

    for category in fss_fetcher.PRODUCT_TYPES:
        print(f"  {category} 수집 중...", end=" ", flush=True)
        try:
            docs = fss_fetcher.fetch_category(args.fss_key, category)
            print(f"{len(docs)}개 상품 → 인덱싱 중...", end=" ", flush=True)
            chunks = index_docs(docs, args.openai_key, es)
            print(f"완료 ({chunks}개 청크)")
            grand_total_docs += len(docs)
            grand_total_chunks += chunks
        except Exception as e:
            print(f"실패: {e}")

    print(f"\n완료: 총 {grand_total_docs}개 상품 / {grand_total_chunks}개 청크 인덱싱됨")
    print(f"Elasticsearch 인덱스: {ES_INDEX}")

    # 마지막 수집 시각 기록(관리자 성능관리 'FSS 데이터 현황'에서 조회)
    try:
        import json
        from datetime import datetime
        data_dir = pathlib.Path(__file__).parent / "site" / "data"
        data_dir.mkdir(parents=True, exist_ok=True)
        (data_dir / "fss_ingest.json").write_text(json.dumps({
            "ingested_at": datetime.now().isoformat(timespec="seconds"),
            "product_count": grand_total_docs,
            "chunk_count": grand_total_chunks,
        }, ensure_ascii=False, indent=2), encoding="utf-8")
        print("수집 시각 기록: site/data/fss_ingest.json")
    except Exception as e:
        print(f"(수집 시각 기록 실패: {e})")


if __name__ == "__main__":
    main()
