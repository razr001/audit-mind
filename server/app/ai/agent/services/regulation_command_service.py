from uuid import UUID

from fastapi import Depends

from app.ai.agent.services.command_outcome import CommandOutcome
from app.models.regulation import Regulation
from app.schemas.regulation import RegulationTextCreateRequest
from app.services.regulation_pipeline_dispatch_service import schedule_regulation_pipeline
from app.services.regulation_pipeline_service import get_regulation_pipeline_state
from app.services.regulation_text_service import RegulationTextService, get_regulation_text_service


class RegulationCommandService:
    """High-level regulation commands; internal pipeline stages remain hidden."""

    def __init__(self, text_service: RegulationTextService) -> None:
        self.text_service = text_service

    async def create_text_and_process(
        self,
        *,
        request: RegulationTextCreateRequest,
        user_id: UUID,
        request_id: str,
        agent_tool_call_id: UUID | None = None,
    ) -> CommandOutcome[Regulation]:
        regulation = await self.text_service.create(
            request=request,
            user_id=user_id,
            request_id=request_id,
            agent_tool_call_id=agent_tool_call_id,
        )
        scheduled = await self._schedule(
            regulation=regulation,
            user_id=user_id,
            request_id=request_id,
        )
        return CommandOutcome(regulation, "SUCCEEDED" if scheduled else "DISPATCH_FAILED")

    async def process(
        self,
        *,
        regulation_id: UUID,
        user_id: UUID,
        request_id: str,
    ) -> CommandOutcome[Regulation]:
        regulation = await get_regulation_pipeline_state(
            regulation_id=regulation_id,
            user_id=user_id,
        )
        scheduled = await self._schedule(
            regulation=regulation,
            user_id=user_id,
            request_id=request_id,
        )
        return CommandOutcome(regulation, "SUCCEEDED" if scheduled else "DISPATCH_FAILED")

    @staticmethod
    async def _schedule(
        *,
        regulation: Regulation,
        user_id: UUID,
        request_id: str,
    ) -> bool:
        try:
            await schedule_regulation_pipeline(
                regulation=regulation,
                user_id=user_id,
                request_id=request_id,
            )
        except Exception:
            return False
        return True


def get_regulation_command_service(
    text_service: RegulationTextService = Depends(get_regulation_text_service),
) -> RegulationCommandService:
    return RegulationCommandService(text_service)
