from typing import Literal
from uuid import UUID

from pydantic import BaseModel

from app.recommend.rank import Source
from app.recommend.vector import Reaction


class OptionOut(BaseModel):
    id: str
    label: str


class FeedCard(BaseModel):
    impression_id: UUID          # POST /reactions에 그대로 보낸다
    source: Source
    card_id: str
    type: str
    title: str
    body: str
    job_name: str | None
    options: list[OptionOut]


class FeedResponse(BaseModel):
    cards: list[FeedCard]        # 비어 있으면 더 보여 줄 카드가 없다


class ReactionCreate(BaseModel):
    impression_id: UUID
    reaction: Reaction
    option_id: str | None = None # reaction이 choose일 때만


class ReactionResponse(BaseModel):
    status: Literal["recorded", "duplicate"]
