from typing import Literal
from uuid import UUID

from pydantic import BaseModel

from app.recommend.rank import Source
from app.recommend.vector import Reaction
from app.schemas.journey import Gain


class OptionOut(BaseModel):
    id: str
    label: str


class CardOut(BaseModel):
    card_id: str
    type: str
    title: str
    body: str
    job_id: int | None           # 직업 카드면 GET /jobs/{job_id} 로 상세를 볼 수 있다
    job_name: str | None
    majors: list[str]            # 직업 카드의 관련 학과. 선택형이거나 정보가 없으면 []
    options: list[OptionOut]


class FeedCard(CardOut):
    impression_id: UUID          # POST /reactions에 그대로 보낸다
    source: Source
    reason: str | None = None    # '왜 이 카드?' 한 줄. 콜드 스타트는 null
    saved: bool = False          # 이미 저장한 카드인지 (PUT·DELETE /me/saved/{card_id})


class FeedResponse(BaseModel):
    cards: list[FeedCard]        # 비어 있으면 더 보여 줄 카드가 없다


class ReactionCreate(BaseModel):
    impression_id: UUID
    reaction: Reaction           # save는 받지 않는다 (422). 저장은 PUT /me/saved/{card_id}
    option_id: str | None = None # reaction이 choose일 때만


class ReactionResponse(BaseModel):
    status: Literal["recorded", "duplicate"]
    gains: list[Gain] = []       # 이번 반응으로 오른 성향 (최대 2개)
    today_count: int = 0         # 오늘 반응한 카드 수 (한국 시간)
    daily_goal: int = 0
    streak: int = 0
    goal_reached: bool = False   # 이번 반응으로 오늘 목표를 막 채웠으면 true
