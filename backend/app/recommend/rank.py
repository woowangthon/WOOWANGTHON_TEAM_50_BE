"""피드 카드 고르기. DB·HTTP 의존 없음.

- 콜드 스타트: 반응이 적거나 벡터가 비어 있으면 분야별로 돌아가며 고르게 뽑는다.
- 맞춤: 사용자 벡터와 카드 벡터의 코사인 유사도 순.
- 탐색: 일부 슬롯은 맞춤 결과에 없는 분야에서 무작위로 뽑아 섞는다.

이미 본 카드는 제외한다. 남은 카드가 n개보다 적으면 있는 만큼만 돌려준다.
각 결과에는 출처(source)를 붙여 노출 로그에 저장한다 → 맞춤 vs 무작위 반응률 비교용.
"""
from collections import defaultdict
from collections.abc import Collection
from dataclasses import dataclass
from typing import Literal

import numpy as np

from app.content import Card, ContentStore

Source = Literal["personalized", "random", "cold_start"]

EXPLORE_RATIO = 0.2          # 무작위 슬롯 비율
COLD_START_REACTIONS = 5     # 이 개수 미만으로 반응했으면 콜드 스타트
DEFAULT_N = 10


@dataclass(frozen=True)
class Recommendation:
    card_id: str
    source: Source
    score: float | None      # 코사인 유사도. 콜드 스타트는 None


def bucket_of(store: ContentStore, card: Card) -> str:
    """분야 묶음. 직업 카드는 커리어넷 대분류, 선택형 카드는 카드 유형."""
    if card.job_id is not None:
        job = store.jobs.get(card.job_id)
        return f"field:{job.field}" if job and job.field else "field:기타"
    return f"type:{card.type}"


def _stratified(
    store: ContentStore,
    idxs: list[int],
    k: int,
    rng: np.random.Generator,
    avoid: Collection[str] = (),
) -> list[int]:
    """분야별로 한 장씩 돌아가며 k장. avoid에 있는 분야는 뒤로 미룬다."""
    groups: dict[str, list[int]] = defaultdict(list)
    for i in idxs:
        groups[bucket_of(store, store.cards[i])].append(i)
    for g in groups.values():
        rng.shuffle(g)
    order = list(groups)
    rng.shuffle(order)
    order.sort(key=lambda b: b in avoid)     # 안정 정렬: 섞인 순서 유지

    picks: list[int] = []
    while len(picks) < k and any(groups[b] for b in order):
        for b in order:
            if groups[b] and len(picks) < k:
                picks.append(groups[b].pop())
    return picks


def recommend(
    store: ContentStore,
    user: np.ndarray,
    n_reactions: int,
    seen: Collection[str] = (),
    n: int = DEFAULT_N,
    *,
    explore_ratio: float = EXPLORE_RATIO,
    cold_start_reactions: int = COLD_START_REACTIONS,
    rng: np.random.Generator | None = None,
) -> list[Recommendation]:
    rng = rng if rng is not None else np.random.default_rng()
    seen = set(seen)
    candidates = [i for i, c in enumerate(store.cards) if c.id not in seen]
    n = min(n, len(candidates))
    if n <= 0:
        return []

    norm = float(np.linalg.norm(user))
    if n_reactions < cold_start_reactions or norm == 0:
        return [
            Recommendation(store.cards[i].id, "cold_start", None)
            for i in _stratified(store, candidates, n, rng)
        ]

    scores = store.card_matrix[candidates] @ (user / norm)
    score_of = {i: float(s) for i, s in zip(candidates, scores)}
    ranked = [candidates[j] for j in np.argsort(-scores, kind="stable")]

    n_random = min(int(round(n * explore_ratio)), n - 1)   # 맞춤 1장은 항상 남김
    personal = ranked[: n - n_random]
    taken = set(personal)
    rest = [i for i in candidates if i not in taken]
    avoid = {bucket_of(store, store.cards[i]) for i in personal}
    randoms = _stratified(store, rest, n_random, rng, avoid)

    result = [Recommendation(store.cards[i].id, "personalized", score_of[i]) for i in personal]
    for i in randoms:
        # 첫 장은 맞춤 카드로 두고, 나머지 위치 중 아무 데나 끼운다
        pos = int(rng.integers(1, len(result) + 1))
        result.insert(pos, Recommendation(store.cards[i].id, "random", score_of[i]))
    return result
