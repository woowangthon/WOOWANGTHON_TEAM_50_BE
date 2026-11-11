from typing import Literal
from uuid import UUID

from pydantic import BaseModel

from app.recommend.rank import Source
from app.recommend.vector import Reaction


class OptionOut(BaseModel):
    id: str
    label: str


class CardOut(BaseModel):
    card_id: str
    type: str
    title: str
    body: str
    job_name: str | None
    majors: list[str]            # 직업 카드의 관련 학과. 선택형이거나 정보가 없으면 []
    options: list[OptionOut]


class FeedCard(CardOut):
    impression_id: UUID          # POST /reactions에 그대로 보낸다
    source: Source


class FeedResponse(BaseModel):
    cards: list[FeedCard]        # 비어 있으면 더 보여 줄 카드가 없다


class ReactionCreate(BaseModel):
    impression_id: UUID
    reaction: Reaction
    option_id: str | None = None # reaction이 choose일 때만


class ReactionResponse(BaseModel):
    status: Literal["recorded", "duplicate"]


class OnboardingResponse(BaseModel):
    cards: list[FeedCard]        # 아직 답하지 않은 설문 카드, 보여 줄 순서대로
    total: int                   # 설문 카드 전체 수
    answered: int                # 이미 답한 수 (진행 막대용)
    done: bool                   # true면 설문 완료 → 피드로 이동
