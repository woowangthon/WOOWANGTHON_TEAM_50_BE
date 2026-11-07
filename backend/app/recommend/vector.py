"""사용자 성향 벡터 갱신. DB·HTTP 의존 없음.

사용자 벡터는 카드 속성과 같은 12차원, 0~1 스케일이다 (0~3 루브릭 / 3).

- 긍정 반응(좋아요·저장·선택): 대상 벡터 쪽으로 지수이동평균
      u ← u + α·w·(t − u)
  대상에 없는 축은 조금씩 줄어든다 (오래된 취향을 서서히 잊음).
- 부정 반응(싫어요): 대상 벡터가 가진 축만 깎는다
      u ← u − α·|w|·t
- 패스: 벡터는 그대로 두고 노출 로그에만 남긴다.

결과는 항상 [0, 1]로 자른다.
"""
from enum import Enum

import numpy as np

from app.content import N_DIM, ContentStore, scale_attrs

ALPHA = 0.2   # 학습률. 좋아요 한 번에 대상 쪽으로 20% 이동


class Reaction(str, Enum):
    LIKE = "like"
    DISLIKE = "dislike"
    SAVE = "save"
    PASS = "pass"
    CHOOSE = "choose"     # 선택형 카드(a_vs_b, situation)에서 선택지 고르기


WEIGHTS: dict[Reaction, float] = {
    Reaction.SAVE: 1.5,
    Reaction.LIKE: 1.0,
    Reaction.CHOOSE: 1.0,
    Reaction.PASS: 0.0,
    Reaction.DISLIKE: -0.5,
}


def new_user_vector() -> np.ndarray:
    return np.zeros(N_DIM, dtype=np.float32)


def target_vector(
    store: ContentStore, card_id: str, reaction: Reaction, option_id: str | None = None
) -> np.ndarray:
    """반응이 가리키는 벡터. 선택이면 고른 선택지, 그 외엔 카드 자체.

    잘못된 조합(없는 카드, 선택지 없는 카드에 선택, 다른 카드의 선택지 등)은 ValueError.
    """
    card = store.card(card_id)
    if card is None:
        raise ValueError(f"없는 카드: {card_id}")

    if reaction is Reaction.CHOOSE:
        if not card.options:
            raise ValueError(f"{card_id}: 선택지가 없는 카드에는 choose 불가")
        vec = store.option_vector(option_id) if option_id else None
        if vec is None or not option_id.startswith(f"{card_id}-"):
            raise ValueError(f"{card_id}: 이 카드의 선택지가 아님 ({option_id})")
        return vec

    if option_id is not None:
        raise ValueError(f"{reaction.value} 반응에는 option_id를 넣지 않음")
    return scale_attrs(card.attrs)


def update_vector(
    user: np.ndarray, target: np.ndarray, reaction: Reaction, alpha: float = ALPHA
) -> np.ndarray:
    """새 사용자 벡터를 돌려준다 (입력은 바꾸지 않음)."""
    w = WEIGHTS[reaction]
    if w > 0:
        out = user + min(alpha * w, 1.0) * (target - user)
    elif w < 0:
        out = user - alpha * -w * target
    else:
        out = user.copy()
    return np.clip(out, 0.0, 1.0).astype(np.float32)


def apply_reaction(
    store: ContentStore,
    user: np.ndarray,
    card_id: str,
    reaction: Reaction,
    option_id: str | None = None,
    alpha: float = ALPHA,
) -> np.ndarray:
    """target_vector + update_vector. API 계층은 이것만 부르면 된다."""
    return update_vector(user, target_vector(store, card_id, reaction, option_id), reaction, alpha)
