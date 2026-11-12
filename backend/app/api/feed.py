from typing import Annotated

import numpy as np
from fastapi import APIRouter, Depends, HTTPException, Query, status

from app.api.deps import get_content, get_feed, get_users
from app.content import Card, ContentStore, scale_attrs
from app.core.auth import CurrentUser, get_current_user
from app.db.feed import FeedRepo
from app.db.users import Row, UserRepo
from app.journey import DAILY_GOAL, activity, card_reason, gains
from app.recommend.rank import DEFAULT_N, recommend
from app.recommend.vector import apply_reaction, new_user_vector
from app.schemas.feed import FeedCard, FeedResponse, OptionOut, ReactionCreate, ReactionResponse

router = APIRouter(tags=["feed"])

MAX_ATTEMPTS = 3   # 동시 반응으로 벡터 버전이 어긋났을 때 다시 읽는 횟수

User = Annotated[CurrentUser, Depends(get_current_user)]
Users = Annotated[UserRepo, Depends(get_users)]
Feed = Annotated[FeedRepo, Depends(get_feed)]
Content = Annotated[ContentStore, Depends(get_content)]


def _user_vector(row: Row | None) -> np.ndarray:
    return np.asarray(row["vector"], dtype=np.float32) if row else new_user_vector()


def card_fields(card: Card, content: ContentStore) -> dict:
    """화면에 보여 줄 카드 필드 (CardOut). 점수(attrs)는 내보내지 않는다."""
    job = content.jobs.get(card.job_id) if card.job_id is not None else None
    return {
        "card_id": card.id,
        "type": card.type,
        "title": card.title,
        "body": card.body,
        "job_id": job.id if job else None,
        "job_name": job.name if job else None,
        "majors": job.majors if job else [],
        "options": [OptionOut(id=o.id, label=o.label) for o in card.options],
    }


def _feed_card(impression: Row, content: ContentStore, user: np.ndarray | None = None) -> FeedCard:
    """user(성향 벡터)를 주면 '왜 이 카드?' 한 줄을 붙인다. 가입 설문처럼 이유가 필요 없으면 생략."""
    card = content.card(impression["card_id"])
    reason = None if user is None else card_reason(content.attributes, user, scale_attrs(card.attrs), impression["source"])
    return FeedCard(
        impression_id=impression["id"], source=impression["source"], reason=reason, **card_fields(card, content)
    )


@router.get("/feed")
def read_feed(
    user: User,
    users: Users,
    feed: Feed,
    content: Content,
    n: Annotated[int, Query(ge=1, le=20)] = DEFAULT_N,
) -> FeedResponse:
    impressions = feed.list_impressions(user.id)
    reacted = {i["card_id"] for i in impressions if i["reactions"]}

    # 받았지만 반응하지 않은 카드는 새로 뽑지 않고 같은 노출로 다시 내려준다
    pending: dict[str, Row] = {}
    for i in impressions:
        if i["card_id"] not in reacted and content.card(i["card_id"]) is not None:
            pending.setdefault(i["card_id"], i)
    items = list(pending.values())[:n]

    row = users.get_vector(user.id)
    vector = _user_vector(row)
    if len(items) < n:
        recs = recommend(
            content,
            vector,
            row["n_reactions"] if row else 0,
            seen={i["card_id"] for i in impressions},
            n=n - len(items),
        )
        items += feed.create_impressions(user.id, recs)

    return FeedResponse(cards=[_feed_card(i, content, vector) for i in items])


@router.post("/reactions")
def create_reaction(
    body: ReactionCreate, user: User, users: Users, feed: Feed, content: Content
) -> ReactionResponse:
    impression = feed.get_impression(body.impression_id, user.id)
    if impression is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "impression_not_found")
    card_id = impression["card_id"]

    for _ in range(MAX_ATTEMPTS):
        row = users.get_vector(user.id)
        before = _user_vector(row)
        try:
            vector = apply_reaction(content, before, card_id, body.reaction, body.option_id)
        except ValueError:
            raise HTTPException(422, "invalid_reaction") from None

        result = feed.record_reaction(
            body.impression_id,
            user.id,
            card_id,
            body.reaction.value,
            body.option_id,
            vector.tolist(),
            row["version"] if row else None,
        )
        if result != "conflict":
            # ponytail: 오늘 반응 수를 세려고 반응 로그를 통째로 읽는다. 사용자당 수천 건이 넘으면 날짜 조건 쿼리로
            streak, today_count = activity(feed.list_reactions(user.id))
            recorded = result == "recorded"
            return ReactionResponse(
                status=result,
                gains=gains(content.attributes, before, vector) if recorded else [],
                today_count=today_count,
                daily_goal=DAILY_GOAL,
                streak=streak,
                goal_reached=recorded and today_count == DAILY_GOAL,
            )

    raise HTTPException(status.HTTP_409_CONFLICT, "reaction_conflict")
