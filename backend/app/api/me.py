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
from app.report import build_report
from app.schemas.me import (
    MeResponse,
    ProfileCreate,
    ProfileUpdate,
    ReportJob,
    ReportResponse,
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
        jobs=[ReportJob(**vars(j)) for j in report.jobs],
        majors=report.majors,
        reaction_counts=report.reaction_counts,
    )
