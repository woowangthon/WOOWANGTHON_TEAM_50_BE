from typing import Annotated, Literal

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from app.api.deps import get_content
from app.content import ContentStore

router = APIRouter(tags=["health"])


class HealthResponse(BaseModel):
    status: Literal["ok"] = "ok"
    cards: int
    jobs: int


@router.get("/health")
def health(content: Annotated[ContentStore, Depends(get_content)]) -> HealthResponse:
    return HealthResponse(cards=len(content.cards), jobs=len(content.jobs))
