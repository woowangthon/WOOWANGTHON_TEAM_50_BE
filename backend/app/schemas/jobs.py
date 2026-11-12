from typing import Literal

from pydantic import BaseModel

from app.schemas.me import RoadmapStep


class JobSummary(BaseModel):
    job_id: int
    name: str
    field: str | None
    summary: str
    match: int | None            # 적합도 0~100. 성향이 아직 없으면 null


class JobsResponse(BaseModel):
    fields: list[str]
    jobs: list[JobSummary]       # 적합도 높은 순


class JobTrait(BaseModel):
    code: str
    label: str
    group: Literal["interest", "style", "value"]
    definition: str
    level: int                   # 이 직업에서의 비중 1~3
    mine: int                    # 내 성향 0~100


class JobDetailResponse(BaseModel):
    job_id: int
    name: str
    field: str | None
    summary: str
    majors: list[str]
    match: int | None
    traits: list[JobTrait]
    steps: list[RoadmapStep]     # GET /me/roadmap/{job_id} 의 steps 와 같다 (커리어넷 원문)
    similar: list[JobSummary]
