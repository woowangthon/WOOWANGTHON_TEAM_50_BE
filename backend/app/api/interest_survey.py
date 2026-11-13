from datetime import datetime
from typing import Annotated, Literal

import numpy as np
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel

from app.api.deps import get_content, get_users
from app.content import N_DIM, ContentStore
from app.core.auth import CurrentUser, get_current_user
from app.db.users import UserRepo
from app.interest_survey import SURVEY_VERSION, apply_survey, survey_delta

router = APIRouter(tags=["interest-survey"])

MAX_ATTEMPTS = 3   # 동시 반응으로 벡터 버전이 어긋났을 때 다시 읽는 횟수

User = Annotated[CurrentUser, Depends(get_current_user)]
Users = Annotated[UserRepo, Depends(get_users)]
Content = Annotated[ContentStore, Depends(get_content)]


class InterestSurveyCreate(BaseModel):
    version: int
    answers: dict[str, str]      # { 문항 ID: strongly_dislike | dislike | neutral | like | strongly_like }


class InterestSurveyStatus(BaseModel):
    done: bool                   # true면 이미 설문을 했다 → 프론트는 설문 화면을 건너뛴다
    version: int | None
    answers: dict[str, str] | None
    created_at: datetime | None


class InterestSurveyResponse(BaseModel):
    status: Literal["recorded", "duplicate"]   # duplicate: 이미 반영한 사용자 (벡터 그대로)


@router.get("/interest-survey")
def read_interest_survey(user: User, users: Users) -> InterestSurveyStatus:
    """설문을 했는지. 다른 브라우저·기기에서 로그인해도 설문을 다시 보여 주지 않게 프론트가 확인한다."""
    row = users.get_interest_survey(user.id)
    if row is None:
        return InterestSurveyStatus(done=False, version=None, answers=None, created_at=None)
    return InterestSurveyStatus(done=True, **row)


@router.post("/interest-survey")
def create_interest_survey(
    body: InterestSurveyCreate, user: User, users: Users, content: Content
) -> InterestSurveyResponse:
    """관심사 설문 12문항 답을 흥미 축(R·I·A·S·E·C) 점수에 ± 반영한다. 사용자당 한 번."""
    if body.version != SURVEY_VERSION:
        raise HTTPException(422, "invalid_survey")
    axis = {a.code: a.idx for a in content.attributes}
    try:
        delta = survey_delta(body.answers, axis, N_DIM)
    except ValueError:
        raise HTTPException(422, "invalid_survey") from None

    for _ in range(MAX_ATTEMPTS):
        row = users.get_vector(user.id)
        current = np.asarray(row["vector"], dtype=np.float32) if row else np.zeros(N_DIM, dtype=np.float32)
        result = users.record_interest_survey(
            user.id,
            body.version,
            body.answers,
            apply_survey(current, delta).tolist(),
            row["version"] if row else None,
        )
        if result != "conflict":
            return InterestSurveyResponse(status=result)

    raise HTTPException(status.HTTP_409_CONFLICT, "survey_conflict")
