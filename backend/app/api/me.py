from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status

import numpy as np

from app.api.deps import get_content, get_feed, get_report_writer, get_users
from app.api.feed import card_fields
from app.content import N_DIM, Attribute, ContentStore
from app.core.auth import CurrentUser, get_current_user
from app.db.feed import FeedRepo
from app.db.users import ProfileExists, Row, UserRepo
from app.llm.report_writer import ReportWriter
from app.journey import DAILY_GOAL, KST, activity, badges, level, mission, persona
from app.report import build_report
from app.roadmap import build_roadmap, saved_job_ids
from app.schemas.journey import JourneyResponse
from app.schemas.me import (
    MeResponse,
    ProfileCreate,
    ProfileUpdate,
    ReportJob,
    ReportResponse,
    RoadmapJob,
    RoadmapResponse,
    SavedCard,
    SavedResponse,
    Trait,
)

router = APIRouter(prefix="/me", tags=["me"])

User = Annotated[CurrentUser, Depends(get_current_user)]
Users = Annotated[UserRepo, Depends(get_users)]
Content = Annotated[ContentStore, Depends(get_content)]
Feed = Annotated[FeedRepo, Depends(get_feed)]
Writer = Annotated[ReportWriter | None, Depends(get_report_writer)]


def _not_found() -> HTTPException:
    return HTTPException(status.HTTP_404_NOT_FOUND, "profile_not_found")


def _trait(a: Attribute, vector) -> Trait:
    return Trait(code=a.code, label=a.label, group=a.group, score=float(vector[a.idx]))


def _me(user: CurrentUser, profile: Row, users: UserRepo, content: ContentStore) -> MeResponse:
    row = users.get_vector(user.id)
    vector = row["vector"] if row else [0.0] * N_DIM
    return MeResponse(
        user_id=user.id,
        email=user.email,
        nickname=profile["nickname"],
        grade=profile["grade"],
        created_at=profile["created_at"],
        updated_at=profile["updated_at"],
        n_reactions=row["n_reactions"] if row else 0,
        traits=[_trait(a, vector) for a in content.attributes],
    )


@router.post("", status_code=status.HTTP_201_CREATED)
def create_me(body: ProfileCreate, user: User, users: Users, content: Content) -> MeResponse:
    try:
        profile = users.create_profile(user.id, body.model_dump())
    except ProfileExists:
        raise HTTPException(status.HTTP_409_CONFLICT, "profile_exists") from None
    return _me(user, profile, users, content)


@router.get("")
def read_me(user: User, users: Users, content: Content) -> MeResponse:
    profile = users.get_profile(user.id)
    if profile is None:
        raise _not_found()
    return _me(user, profile, users, content)


@router.patch("")
def update_me(body: ProfileUpdate, user: User, users: Users, content: Content) -> MeResponse:
    fields = body.model_dump(exclude_unset=True)
    profile = users.update_profile(user.id, fields) if fields else users.get_profile(user.id)
    if profile is None:
        raise _not_found()
    return _me(user, profile, users, content)


@router.get("/saved")
def read_saved(user: User, feed: Feed, content: Content) -> SavedResponse:
    """저장한 카드. 같은 카드는 한 번만, 콘텐츠에서 빠진 카드는 건너뛴다."""
    cards: dict[str, SavedCard] = {}
    for r in feed.list_reactions(user.id):
        card = content.card(r["card_id"])
        if r["reaction"] == "save" and card is not None and card.id not in cards:
            cards[card.id] = SavedCard(saved_at=r["created_at"], **card_fields(card, content))
    return SavedResponse(cards=list(cards.values()))


@router.get("/report")
def read_report(user: User, users: Users, feed: Feed, content: Content, writer: Writer) -> ReportResponse:
    row = users.get_vector(user.id)
    vector = np.asarray(row["vector"] if row else [0.0] * N_DIM, dtype=np.float32)
    report = build_report(content, vector, row["n_reactions"] if row else 0, feed.list_reactions(user.id))
    ai_summary = writer.summarize(user.id, report) if report.ready and writer else None
    return ReportResponse(
        n_reactions=report.n_reactions,
        ready=report.ready,
        remaining=report.remaining,
        traits=[_trait(a, vector) for a in content.attributes],
        top_interests=[_trait(a, vector) for a in report.top_interests],
        strengths=[_trait(a, vector) for a in report.strengths],
        summary=ai_summary or report.summary,
        summary_by_ai=ai_summary is not None,
        study_tips=report.study_tips,
        jobs=[ReportJob(match=round(j.score * 100), **vars(j)) for j in report.jobs],
        majors=report.majors,
        reaction_counts=report.reaction_counts,
    )


def _vector(users: UserRepo, user: CurrentUser) -> np.ndarray:
    row = users.get_vector(user.id)
    return np.asarray(row["vector"] if row else [0.0] * N_DIM, dtype=np.float32)


@router.get("/roadmap")
def read_roadmaps(user: User, users: Users, feed: Feed, content: Content) -> RoadmapResponse:
    """저장한 직업 카드마다 로드맵. 최근에 저장한 순."""
    vector = _vector(users, user)
    jobs = [
        build_roadmap(content, job_id, vector, saved_at)
        for job_id, saved_at in saved_job_ids(content, feed.list_reactions(user.id))
    ]
    return RoadmapResponse(jobs=[j for j in jobs if j is not None])


@router.get("/roadmap/{job_id}")
def read_roadmap(job_id: int, user: User, users: Users, feed: Feed, content: Content) -> RoadmapJob:
    """직업 하나의 로드맵. 리포트의 추천 직업처럼 저장하지 않은 직업도 미리 볼 수 있다."""
    vector = _vector(users, user)
    saved = dict(saved_job_ids(content, feed.list_reactions(user.id)))
    roadmap = build_roadmap(content, job_id, vector, saved.get(job_id))
    if roadmap is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "job_not_found")
    return roadmap


@router.get("/journey")
def read_journey(user: User, users: Users, feed: Feed, content: Content) -> JourneyResponse:
    """홈 화면용: 연속 접속 · 오늘 목표 · 레벨 · 성향 유형 · 오늘의 미션 · 배지."""
    row = users.get_vector(user.id)
    vector = np.asarray(row["vector"] if row else [0.0] * N_DIM, dtype=np.float32)
    n_reactions = row["n_reactions"] if row else 0
    reactions = feed.list_reactions(user.id)
    now = datetime.now(KST)
    streak, today_count = activity(reactions, now)
    report = build_report(content, vector, n_reactions, reactions)
    return JourneyResponse(
        streak=streak,
        today_count=today_count,
        daily_goal=DAILY_GOAL,
        n_reactions=n_reactions,
        level=level(n_reactions),
        persona=persona(content.attributes, vector),
        report_ready=report.ready,
        report_remaining=report.remaining,
        mission=mission(content.attributes, vector, now.date()),
        badges=badges(content, reactions, streak, report.ready),
    )
