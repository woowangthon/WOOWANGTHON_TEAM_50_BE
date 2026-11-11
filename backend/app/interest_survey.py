"""관심사 설문 (프론트 /interests의 12문항). DB·HTTP 의존 없음.

흥미 유형 R·I·A·S·E·C마다 2문항씩 5점 척도로 묻는다. 답 하나가 그 문항의 흥미 축을
SCORES[답] × STEP 만큼 올리거나 내린다.

    전혀 하고 싶지 않아요 −2 · 별로 −1 · 보통 0 · 조금 +1 · 정말 +2

한 축에 2문항이므로 축마다 최대 ±0.5 움직인다. 결과는 [0, 1]로 자른다
(새 사용자는 0에서 시작하므로 '−'는 이미 쌓인 점수가 있을 때만 효과가 있다).
문항 ID·축 매핑은 프론트 src/lib/interestSurvey.js의 INTEREST_QUESTIONS와 같아야 한다.
"""
from collections.abc import Mapping

import numpy as np

SURVEY_VERSION = 1
STEP = 0.125

SCORES: dict[str, int] = {
    "strongly_dislike": -2,
    "dislike": -1,
    "neutral": 0,
    "like": 1,
    "strongly_like": 2,
}

# 문항 ID → 흥미 축 코드 (attributes.csv의 code)
QUESTIONS: dict[str, str] = {
    "make": "R",
    "repair": "R",
    "experiment": "I",
    "patterns": "I",
    "stories": "A",
    "design": "A",
    "explain": "S",
    "support": "S",
    "invite": "E",
    "coordinate": "E",
    "organize": "C",
    "checklist": "C",
}


def survey_delta(answers: Mapping[str, str], axis_index: Mapping[str, int], n_dim: int) -> np.ndarray:
    """답변 전체가 만드는 축별 증감. 문항이 빠졌거나 모르는 값이면 ValueError."""
    if set(answers) != set(QUESTIONS):
        raise ValueError("모든 문항에 한 번씩 답해야 함")
    delta = np.zeros(n_dim, dtype=np.float32)
    for qid, value in answers.items():
        if value not in SCORES:
            raise ValueError(f"{qid}: 알 수 없는 답 ({value})")
        delta[axis_index[QUESTIONS[qid]]] += SCORES[value] * STEP
    return delta


def apply_survey(user: np.ndarray, delta: np.ndarray) -> np.ndarray:
    return np.clip(user + delta, 0.0, 1.0).astype(np.float32)
