"""가입 설문. 정해진 선택형 카드 5장에 답하면 그 반응으로 첫 성향 벡터가 생긴다.

설문 답도 일반 반응(POST /reactions, choose)으로 저장한다. 5장에 모두 답하면 반응 수가
콜드 스타트 기준(COLD_START_REACTIONS)을 채우므로, 첫 피드부터 맞춤 추천 + 탐색 카드로 나간다.
설문을 건너뛴 사용자는 기존처럼 무작위 콜드 스타트를 받는다.
"""
from app.content import ContentStore

# 흥미 유형 R·I·A·S·E·C를 고르게 묻는 카드. 보여 줄 순서대로.
ONBOARDING_CARD_IDS = ("c0160", "c0161", "c0162", "c0163", "c0164")


def onboarding_ids(content: ContentStore) -> list[str]:
    """콘텐츠에 실제로 있는 설문 카드 (검수 전용 모드에서 빠진 카드는 건너뛴다)."""
    return [i for i in ONBOARDING_CARD_IDS if content.card(i) is not None]
