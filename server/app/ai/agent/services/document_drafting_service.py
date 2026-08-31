from app.schemas.regulation_qa import RegulationAnswerResponse
from app.services.regulation_qa_service import RegulationQaService


class DocumentDraftingService:
    """A deterministic facade around grounded regulation QA for first-stage drafting."""

    def __init__(self, regulation_qa_service: RegulationQaService) -> None:
        self.regulation_qa_service = regulation_qa_service

    async def draft_contract(
        self,
        *,
        user_id,
        requirements: str,
        history: list[dict[str, str]] | None = None,
    ) -> RegulationAnswerResponse:
        question = (
            "请依据当前法规知识起草合同草案。必须区分法规强制要求与商业建议；"
            "用户未提供的主体、金额、期限、地址、账号、日期和签署信息一律使用"
            "【待填写】占位符，并在末尾列出缺失字段。不得编造法规依据。\n\n"
            f"合同需求：{requirements}"
        )
        return await self.regulation_qa_service.ask(
            user_id=user_id,
            question=question,
            top_k=8,
            history=history,
        )

    async def review_contract(
        self,
        *,
        user_id,
        content: str,
        history: list[dict[str, str]] | None = None,
    ) -> RegulationAnswerResponse:
        question = (
            "请依据当前法规知识审查以下合同或协议。逐项列出风险、法规依据和修改建议；"
            "证据不足时明确说明，不得把一般商业建议表述为法规强制要求。\n\n"
            f"待审查内容：{content}"
        )
        return await self.regulation_qa_service.ask(
            user_id=user_id,
            question=question,
            top_k=8,
            history=history,
        )
