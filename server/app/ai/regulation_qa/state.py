from typing import Any

from typing_extensions import NotRequired, TypedDict


class RetrievedRegulationChunk(TypedDict):
    chunk_id: str
    regulation_id: str
    title: str
    page_number: int | None
    page_start: int | None
    page_end: int | None
    content: str
    score: float
    # 保留 RRF 原始 score，便于排障；启用 Rerank 后单独记录模型分数。
    rerank_score: NotRequired[float]


class RegulationQaInputState(TypedDict):
    """服务入口创建的初始状态；这些字段在每个节点中都必然存在。"""

    user_id: str
    question: str
    top_k: int
    category: str | None
    source_type: str | None
    jurisdiction: str | None
    history: list[dict[str, str]]


class RegulationQaState(RegulationQaInputState, total=False):
    """节点逐步补充的法规问答状态；这里只列出阶段性可选字段。"""

    guardrail_decision: str
    guardrail_reason: str
    standalone_question: str
    search_query: str
    query_intent: str
    needs_clarification: bool
    clarification_question: str | None

    chunks: list[RetrievedRegulationChunk]
    model_output: dict[str, Any]
    result: dict[str, Any]
