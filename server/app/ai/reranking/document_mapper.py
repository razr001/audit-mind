import math
from collections.abc import Sequence
from typing import cast

from langchain_core.documents import Document

from app.ai.regulation_qa.state import RetrievedRegulationChunk
from app.ai.reranking.contract import RerankerError


def chunks_to_documents(
    chunks: Sequence[RetrievedRegulationChunk],
) -> list[Document]:
    """Expose stable LangChain Documents without leaking AuditMind TypedDicts."""
    return [
        Document(
            # Most rerank APIs only inspect page_content, so include the title as a
            # useful scope signal instead of leaving it exclusively in metadata.
            page_content=(
                f"Regulation title: {chunk['title']}\nContent:\n{chunk['content']}"
            ),
            metadata={
                key: value
                for key, value in chunk.items()
                if key not in {"content", "rerank_score"}
            },
        )
        for chunk in chunks
    ]


def documents_to_chunks(
    documents: Sequence[Document],
    original_chunks: Sequence[RetrievedRegulationChunk],
) -> list[RetrievedRegulationChunk]:
    """Map provider output back by immutable chunk_id and validate its contract."""
    originals = {chunk["chunk_id"]: chunk for chunk in original_chunks}
    if len(originals) != len(original_chunks):
        raise RerankerError("rerank input contains duplicate chunk_id values")

    mapped: list[RetrievedRegulationChunk] = []
    seen_ids: set[str] = set()
    for document in documents:
        chunk_id = document.metadata.get("chunk_id")
        if not isinstance(chunk_id, str) or chunk_id not in originals:
            raise RerankerError("reranker returned a document without a valid chunk_id")
        if chunk_id in seen_ids:
            raise RerankerError("reranker returned a duplicate document")
        seen_ids.add(chunk_id)

        item = dict(originals[chunk_id])
        # AuditMind providers use rerank_score. Several native LangChain
        # integrations use relevance_score, so normalize both at this boundary.
        score = document.metadata.get("rerank_score")
        if score is None:
            score = document.metadata.get("relevance_score")
        if score is not None:
            if isinstance(score, bool) or not isinstance(score, (int, float)):
                raise RerankerError("reranker returned a non-numeric relevance score")
            score_value = float(score)
            if not math.isfinite(score_value):
                raise RerankerError("reranker returned a non-finite relevance score")
            item["rerank_score"] = score_value
        mapped.append(cast(RetrievedRegulationChunk, item))
    return mapped
