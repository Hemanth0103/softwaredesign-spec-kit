"""Public student chat; response validation remains mandatory."""

from typing import Annotated

from fastapi import APIRouter, Depends, Request

from app.api.schemas.chat import ChatResponse, StudentQuestion
from app.services.chat_service import ChatService

router = APIRouter()


def get_chat_service(request: Request) -> ChatService:
    return ChatService(
        session_factory=request.app.state.session_factory,
        settings=request.app.state.settings,
        providers=getattr(request.app.state, "ai_providers", {}),
    )


@router.post("/api/v1/chat/answers", response_model=ChatResponse, response_model_exclude_none=True)
def answer_question(
    question: StudentQuestion, service: Annotated[ChatService, Depends(get_chat_service)]
) -> object:
    return service.answer(question)
