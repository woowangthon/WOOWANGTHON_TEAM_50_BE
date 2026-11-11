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
    score: float                 # 성향과 직업 카드의 코사인 유사도 (0~1). 정렬용, 화면 표시는 권장하지 않음
    summary: str                 # 직업 설명 첫 문장


class ReportResponse(BaseModel):
    n_reactions: int
    ready: bool                  # false면 아래 분석 항목은 비어 있고 remaining만 의미가 있다
    remaining: int               # 리포트가 열리기까지 더 필요한 좋아요·저장·선택 수 (패스·싫어요는 세지 않음)
    traits: list[Trait]          # GET /me와 같은 11개 축 점수
    top_interests: list[Trait]   # 흥미 유형 상위 2개
    strengths: list[Trait]       # 일하는 방식·가치 상위 2개
    summary: str
    summary_by_ai: bool          # true면 summary를 Gemini가 썼다. false면 점수 기반 기본 문장 (키 없음·실패·잠김)
    study_tips: list[str]
    jobs: list[ReportJob]
    majors: list[str]
    reaction_counts: dict[str, int]  # like·dislike·save·pass·choose 5개 키가 항상 있음


class RoadmapTrait(BaseModel):
    code: str
    label: str
    group: Literal["interest", "style", "value"]
    job_score: int               # 이 직업 카드의 점수 (0~3)
    my_score: float              # 내 성향 점수 (0~1). GET /me traits와 같은 값


class RoadmapItem(BaseModel):
    title: str | None            # 없으면 detail만 보여 준다
    detail: str


class RoadmapStep(BaseModel):
    key: Literal["explore", "grow", "study", "career"]
    title: str                   # 화면에 그대로 써도 되는 단계 이름
    items: list[RoadmapItem]


class RoadmapJob(BaseModel):
    job_id: int
    name: str
    field: str | None
    summary: str                 # 직업 설명 첫 문장
    majors: list[str]
    card_id: str | None          # 이 직업의 직업 카드 (없으면 null)
    saved_at: datetime | None    # 저장한 시각. 저장하지 않은 직업 미리보기면 null
    traits: list[RoadmapTrait]   # 이 직업 카드에서 점수가 있는 축, 직업 점수 높은 순
    steps: list[RoadmapStep]     # 내용이 있는 단계만, 순서대로


class RoadmapResponse(BaseModel):
    jobs: list[RoadmapJob]       # 저장한 직업 카드의 직업, 최근에 저장한 순. 없으면 []
