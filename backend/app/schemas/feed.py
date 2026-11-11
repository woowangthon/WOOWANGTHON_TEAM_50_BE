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
