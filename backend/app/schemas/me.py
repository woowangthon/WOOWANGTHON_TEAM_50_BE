from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, Field, StringConstraints

from app.schemas.feed import CardOut

Nickname = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=20)]
Grade = Annotated[int, Field(ge=1, le=3)]


class ProfileCreate(BaseModel):
    nickname: Nickname
    grade: Grade | None = None


class ProfileUpdate(BaseModel):
    """보낸 필드만 수정한다. grade는 null로 지울 수 있고, nickname은 null을 받지 않는다."""

    nickname: Nickname = None  # 기본값은 검증하지 않으므로 생략은 되고 명시적 null은 422
    grade: Grade | None = None


class Trait(BaseModel):
    code: str
    label: str
    group: Literal["interest", "style", "value"]
    score: float


class MeResponse(BaseModel):
    user_id: UUID
    email: str | None
    nickname: str
    grade: int | None
    created_at: datetime
    updated_at: datetime
    n_reactions: int
    traits: list[Trait]


class SavedCard(CardOut):
    saved_at: datetime


class SavedResponse(BaseModel):
    cards: list[SavedCard]       # 최근에 저장한 순


class ReportJob(BaseModel):
    job_id: int
    name: str
    field: str | None
    majors: list[str]
    score: float                 # 성향과 직업 카드의 코사인 유사도 (0~1)


class ReportResponse(BaseModel):
    n_reactions: int
    ready: bool                  # false면 아래 분석 항목은 비어 있고 remaining만 의미가 있다
    remaining: int               # 리포트가 열리기까지 더 반응해야 하는 카드 수
    traits: list[Trait]          # GET /me와 같은 11개 축 점수
    top_interests: list[Trait]   # 흥미 유형 상위 2개
    strengths: list[Trait]       # 일하는 방식·가치 상위 2개
    summary: str
    study_tips: list[str]
    jobs: list[ReportJob]
    majors: list[str]
    reaction_counts: dict[str, int]
