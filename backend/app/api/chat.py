from typing import Annotated

import numpy as np
from fastapi import APIRouter, Depends, HTTPException, status

from app.api.deps import get_coach, get_content, get_feed, get_job_index, get_users
from app.content import N_DIM, ContentStore
from app.core.auth import CurrentUser, get_current_user
from app.db.feed import FeedRepo
from app.db.users import UserRepo
from app.jobs import JobIndex, rank_jobs
from app.journey import persona, top_interests
from app.llm.coach import CRISIS_REPLY, Coach, fallback_chat, is_crisis
from app.schemas.coach import ChatIn, ChatOut

router = APIRouter(tags=["chat"])
MAX_USER_CHARS = 300


def build_context(content: ContentStore, index: JobIndex, vector: np.ndarray, n_reactions: int, saved_rows: list[dict]) -> dict:
    """대화에 쓸 학생 기록. 닉네임·이메일은 넣지 않는다. saved_rows: FeedRepo.list_saved (최신 순)."""
    others = sorted((a for a in content.attributes if a.group != "interest" and vector[a.idx] > 0),
                    key=lambda a: -vector[a.idx])[:2]
    saved = []
    for s in saved_rows:
        card = content.card(s["card_id"])
        if card is not None and card.title not in saved:
            saved.append(card.title)
    jobs = [j for j in rank_jobs(content, index, vector, limit=5) if j["match"] is not None]
    return {
        "n_reactions": n_reactions,
        "persona": persona(content.attributes, vector),
        "interests": [a.label for a in top_interests(content.attributes, vector)],
        "strengths": [a.label for a in others],
        "jobs": [{"name": j["name"], "match": j["match"]} for j in jobs],
        "saved": saved[:5],
    }


@router.post("/chat")
def chat(
    body: ChatIn,
    user: Annotated[CurrentUser, Depends(get_current_user)],
    users: Annotated[UserRepo, Depends(get_users)],
    feed: Annotated[FeedRepo, Depends(get_feed)],
    content: Annotated[ContentStore, Depends(get_content)],
    index: Annotated[JobIndex, Depends(get_job_index)],
    coach: Annotated[Coach | None, Depends(get_coach)],
) -> ChatOut:
    """AI 진로 동료와 대화. 내 성향·저장한 카드·맞는 직업을 근거로 답한다. 대화는 저장하지 않는다."""
    last = body.messages[-1]
    if last.role != "user" or len(last.text) > MAX_USER_CHARS:
        raise HTTPException(422, "invalid_message")
    if is_crisis(last.text):     # 위기 표현은 LLM을 거치지 않고 정해진 안내로 답한다
        return ChatOut(reply=CRISIS_REPLY, suggestions=[], by_ai=False)

    row = users.get_vector(user.id)
    vector = np.asarray(row["vector"] if row else [0.0] * N_DIM, dtype=np.float32)
    ctx = build_context(content, index, vector, row["n_reactions"] if row else 0, feed.list_saved(user.id))

    if coach is not None:
        if not coach.allow(user.id):
            raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, "chat_limit")
        draft = coach.chat(ctx, [m.model_dump() for m in body.messages])
        if draft is not None:
            return ChatOut(**draft, by_ai=True)
    return ChatOut(**fallback_chat(ctx), by_ai=False)
