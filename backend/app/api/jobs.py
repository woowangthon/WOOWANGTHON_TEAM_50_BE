from typing import Annotated

import numpy as np
from fastapi import APIRouter, Depends, HTTPException, Query, status

from app.api.deps import get_coach, get_content, get_job_index, get_users
from app.content import N_DIM, ContentStore
from app.core.auth import CurrentUser, get_current_user
from app.db.users import UserRepo
from app.jobs import JobIndex, job_detail, list_fields, rank_jobs
from app.llm.coach import Coach, fallback_answer, job_questions
from app.schemas.coach import AskIn, AskOut
from app.schemas.jobs import JobDetailResponse, JobsResponse

router = APIRouter(prefix="/jobs", tags=["jobs"])

User = Annotated[CurrentUser, Depends(get_current_user)]
Users = Annotated[UserRepo, Depends(get_users)]
Content = Annotated[ContentStore, Depends(get_content)]
Index = Annotated[JobIndex, Depends(get_job_index)]


def _vector(users: UserRepo, user: CurrentUser) -> np.ndarray:
    row = users.get_vector(user.id)
    return np.asarray(row["vector"] if row else [0.0] * N_DIM, dtype=np.float32)


@router.get("")
def read_jobs(
    user: User, users: Users, content: Content, index: Index,
    field: str | None = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 30,
) -> JobsResponse:
    """내 성향과 잘 맞는 순서의 직업 목록. field로 분야를 거를 수 있다."""
    return JobsResponse(
        fields=list_fields(content, index),
        jobs=rank_jobs(content, index, _vector(users, user), field, limit),
    )


@router.get("/{job_id}")
def read_job(job_id: int, user: User, users: Users, content: Content, index: Index) -> JobDetailResponse:
    detail = job_detail(content, index, _vector(users, user), job_id)
    if detail is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "job_not_found")
    return JobDetailResponse(questions=job_questions(detail), **detail)


@router.post("/{job_id}/ask")
def ask_job(
    job_id: int, body: AskIn, user: User, users: Users, content: Content, index: Index,
    coach: Annotated[Coach | None, Depends(get_coach)],
) -> AskOut:
    """직업에 대한 정해진 질문에 답한다. AI가 못 쓰면 자료만으로 만든 답을 준다."""
    row = users.get_vector(user.id)
    vector = np.asarray(row["vector"] if row else [0.0] * N_DIM, dtype=np.float32)
    detail = job_detail(content, index, vector, job_id)
    if detail is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "job_not_found")
    if body.question_id == "why_match" and detail["match"] is None:
        raise HTTPException(422, "no_match_yet")
    ai = coach.answer_job(user.id, row["n_reactions"] if row else 0, detail, body.question_id) if coach else None
    return AskOut(answer=ai or fallback_answer(detail, body.question_id), by_ai=ai is not None)
