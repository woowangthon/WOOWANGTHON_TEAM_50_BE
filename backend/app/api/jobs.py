from typing import Annotated

import numpy as np
from fastapi import APIRouter, Depends, HTTPException, Query, status

from app.api.deps import get_content, get_job_index, get_users
from app.content import N_DIM, ContentStore
from app.core.auth import CurrentUser, get_current_user
from app.db.users import UserRepo
from app.jobs import JobIndex, job_detail, list_fields, rank_jobs
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
    return JobDetailResponse(**detail)
