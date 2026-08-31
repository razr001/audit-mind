from typing import Any

RRF_RANK_CONSTANT = 60
BM25_RRF_WEIGHT = 2.0
VECTOR_RRF_WEIGHT = 1.0


def fuse_regulation_results(
    *,
    bm25_hits: list[dict[str, Any]],
    knn_hits: list[dict[str, Any]],
    top_k: int,
) -> list[dict[str, Any]]:
    """使用加权 RRF 融合关键词与向量排名。

    法规查询中的专有名词和条款原文很重要，因此 BM25 权重
    略高于向量召回；向量路径仍负责召回不同表述的语义结果。
    """
    fused: dict[str, dict[str, Any]] = {}

    for hits, weight in (
        (bm25_hits, BM25_RRF_WEIGHT),
        (knn_hits, VECTOR_RRF_WEIGHT),
    ):
        seen_chunks: set[str] = set()
        for rank, hit in enumerate(hits, start=1):
            chunk_id = str(hit["_source"].get("chunk_id") or hit["_id"])
            # 同一完整表格可能有多个 ES 片段。每一路排名只允许一个
            # 片段贡献 RRF 分数，避免大表格凭片段数量获得不公平加权。
            if chunk_id in seen_chunks:
                continue
            seen_chunks.add(chunk_id)
            contribution = weight / (RRF_RANK_CONSTANT + rank)
            entry = fused.setdefault(
                chunk_id,
                {
                    "source": hit["_source"],
                    "score": 0.0,
                    "best_contribution": contribution,
                },
            )
            if contribution > entry["best_contribution"]:
                entry["source"] = hit["_source"]
                entry["best_contribution"] = contribution
            entry["score"] += contribution

    ordered = sorted(
        fused.items(),
        key=lambda item: item[1]["score"],
        reverse=True,
    )[:top_k]

    return [
        {
            **entry["source"],
            "chunk_id": chunk_id,
            "score": entry["score"],
        }
        for chunk_id, entry in ordered
    ]
