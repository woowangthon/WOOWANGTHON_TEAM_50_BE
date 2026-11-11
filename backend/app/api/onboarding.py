from typing import Annotated

from fastapi import APIRouter, Depends

from app.api.deps import get_content, get_feed
from app.api.feed import _feed_card
from app.content import ContentStore
from app.core.auth import CurrentUser, get_current_user
from app.db.feed import FeedRepo
from app.db.users import Row
from app.onboarding import onboarding_ids
from app.recommend.rank import Recommendation
from app.schemas.feed import OnboardingResponse

router = APIRouter(tags=["onboarding"])

User = Annotated[CurrentUser, Depends(get_current_user)]
Feed = Annotated[FeedRepo, Depends(get_feed)]
Content = Annotated[ContentStore, Depends(get_content)]


@router.get("/onboarding")
def read_onboarding(user: User, feed: Feed, content: Content) -> OnboardingResponse:
    """아직 답하지 않은 설문 카드를 순서대로. 답은 POST /reactions (choose)로 보낸다."""
    ids = onboarding_ids(content)
    impressions = feed.list_impressions(user.id)
    answered = {i["card_id"] for i in impressions if i["reactions"]}

    pending: dict[str, Row] = {}
    for i in impressions:
        if i["card_id"] in ids and i["card_id"] not in answered:
            pending.setdefault(i["card_id"], i)

    missing = [c for c in ids if c not in answered and c not in pending]
    for row in feed.create_impressions(user.id, [Recommendation(c, "onboarding", None) for c in missing]):
        pending[row["card_id"]] = row

    cards = [_feed_card(pending[c], content) for c in ids if c in pending]
    return OnboardingResponse(cards=cards, total=len(ids), answered=len(ids) - len(cards), done=not cards)
