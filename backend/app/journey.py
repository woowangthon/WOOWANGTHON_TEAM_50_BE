"""매일 다시 오게 만드는 장치: 연속 접속 · 오늘 목표 · 레벨 · 성향 유형 · 오늘의 미션 · 배지 · 반응 직후 피드백.

전부 반응 로그와 성향 벡터에서 계산한다 (DB 스키마 추가 없음, LLM 없음).
날짜는 한국 시간 기준이다 (서버는 UTC로 돌기 때문에 직접 변환).
"""
from datetime import date, datetime, timedelta, timezone

import numpy as np

from app.content import Attribute, ContentStore

KST = timezone(timedelta(hours=9))   # 한국은 서머타임이 없다
DAILY_GOAL = 5                        # 하루 목표 카드 수. 상한이 아니라 목표 (넘겨도 막지 않음)
MIN_GAIN = 0.005

LEVELS = [(0, "새싹 탐험가"), (5, "호기심 탐험가"), (15, "길 찾는 탐험가"),
          (30, "지도 그리는 탐험가"), (60, "나침반 탐험가"), (100, "별자리 탐험가")]

PERSONA_NOUN = {"R": "메이커", "I": "탐구가", "A": "크리에이터", "S": "서포터", "E": "리더", "C": "플래너"}
PERSONA_ADJ = {"R": "손으로 해내는", "I": "끝까지 파고드는", "A": "감각적인", "S": "다정한", "E": "추진력 있는", "C": "꼼꼼한"}
PERSONA_TAGLINE = {
    "R": "생각만 하기보다 직접 만들어 볼 때 눈이 반짝여요.",
    "I": "'왜 그럴까?'가 풀릴 때까지 놓지 않는 편이에요.",
    "A": "정답 없는 문제에서 나만의 답을 만드는 걸 좋아해요.",
    "S": "누군가에게 도움이 됐다고 느낄 때 힘이 나요.",
    "E": "사람들을 모으고 일을 앞으로 끌고 가는 데 끌려요.",
    "C": "흐트러진 걸 정리해 딱 맞아떨어질 때 뿌듯해요.",
}

MISSIONS = {
    "R": ["집에 있는 물건 하나를 골라 어떻게 조립됐는지 1분만 관찰해 보기",
          "오늘 본 물건 중 '내가 만든다면 이렇게 바꾸겠다' 한 가지 떠올려 보기",
          "종이 한 장으로 가장 멀리 나는 비행기 접어 보기"],
    "I": ["오늘 수업에서 '왜?'라는 질문 하나 적어 보기",
          "궁금했던 것 하나를 검색해서 세 문장으로 정리해 보기",
          "오늘 날씨가 왜 이런지 이유를 하나 찾아보기"],
    "A": ["오늘 본 것 중 가장 예쁜 장면을 사진으로 한 장 남기기",
          "좋아하는 노래 가사 한 줄을 내 식으로 바꿔 써 보기",
          "지금 기분을 색깔 하나와 낱말 하나로 표현해 보기"],
    "S": ["오늘 친구 한 명에게 고맙다는 말 구체적으로 전하기",
          "누군가의 이야기를 끊지 않고 끝까지 들어 보기",
          "도움이 필요해 보이는 사람에게 먼저 한마디 건네 보기"],
    "E": ["오늘 모둠이나 친구들 사이에서 의견 하나 먼저 내 보기",
          "이번 주 안에 이루고 싶은 작은 목표 하나 정해 친구에게 말하기",
          "학교에서 바뀌면 좋겠는 점 하나와 설득할 이유 한 줄 적어 보기"],
    "C": ["내일 할 일을 세 가지만 순서대로 적어 보기",
          "가방이나 책상 한 칸을 3분만 정리해 보기",
          "이번 주 용돈이나 시간을 어디에 썼는지 표로 적어 보기"],
}
DEFAULT_MISSIONS = ["오늘 본 직업 카드 중 가장 낯선 직업 하나를 가족이나 친구에게 소개해 보기",
                    "길에서 본 '일하는 사람' 세 명의 직업을 맞혀 보기",
                    "어른 한 명에게 '일하면서 가장 재밌는 순간'을 물어보기"]


def local_day(ts: str) -> date:
    """Supabase timestamptz 문자열 → 한국 날짜."""
    return datetime.fromisoformat(ts.replace("Z", "+00:00")).astimezone(KST).date()


def activity(reactions: list[dict], now: datetime | None = None) -> tuple[int, int]:
    """(연속 접속 일수, 오늘 반응 수). 오늘 아직 안 했어도 어제까지 이어졌으면 연속은 유지된다."""
    today = (now or datetime.now(KST)).astimezone(KST).date()
    days = [local_day(r["created_at"]) for r in reactions]
    active = set(days)
    cursor = today if today in active else today - timedelta(days=1)
    streak = 0
    while cursor in active:
        streak += 1
        cursor -= timedelta(days=1)
    return streak, days.count(today)


def level(n_reactions: int) -> dict:
    idx = max(i for i, (need, _) in enumerate(LEVELS) if n_reactions >= need)
    base = LEVELS[idx][0]
    next_at = LEVELS[idx + 1][0] if idx + 1 < len(LEVELS) else None
    progress = 1.0 if next_at is None else (n_reactions - base) / (next_at - base)
    return {"n": idx + 1, "name": LEVELS[idx][1], "next_at": next_at, "progress": round(progress, 3)}


def top_interests(attributes: list[Attribute], vector: np.ndarray, k: int = 2) -> list[Attribute]:
    picked = [a for a in attributes if a.group == "interest" and vector[a.idx] > 0]
    return sorted(picked, key=lambda a: (-vector[a.idx], a.idx))[:k]


def persona(attributes: list[Attribute], vector: np.ndarray) -> dict | None:
    """흥미 상위 2축으로 만든 성향 유형. 예: I·A → 'IA형 · 감각적인 탐구가'. 흥미 점수가 없으면 None."""
    tops = top_interests(attributes, vector)
    if not tops:
        return None
    first = tops[0].code
    name = PERSONA_NOUN[first] if len(tops) == 1 else f"{PERSONA_ADJ[tops[1].code]} {PERSONA_NOUN[first]}"
    return {
        "code": "".join(a.code for a in tops),
        "name": name,
        "tagline": PERSONA_TAGLINE[first],
        "labels": [a.label for a in tops],
    }


def mission(attributes: list[Attribute], vector: np.ndarray, today: date) -> dict:
    """오늘의 미션. 가장 끌린 흥미 유형에 맞춰 날짜마다 바뀐다 (같은 날에는 같은 미션)."""
    tops = top_interests(attributes, vector, 1)
    code = tops[0].code if tops else None
    pool = MISSIONS[code] if code else DEFAULT_MISSIONS
    return {
        "id": f"{today.isoformat()}-{code or 'start'}",
        "text": pool[today.toordinal() % len(pool)],
        "trait": tops[0].label if tops else None,
    }


def badges(
    content: ContentStore, reactions: list[dict], streak: int, report_ready: bool, n_saved: int = 0
) -> list[dict]:
    """n_saved: 저장한 카드 수 (FeedRepo.list_saved). 저장은 반응이 아니라 reactions에 없다."""
    kinds = [r["reaction"] for r in reactions]
    fields = set()
    for r in reactions:
        card = content.card(r["card_id"])
        job = content.jobs.get(card.job_id) if card and card.job_id is not None else None
        if job and job.field:
            fields.add(job.field)
    rules = [
        ("first_step", "첫 발자국", "첫 카드에 반응했어요", len(reactions) >= 1),
        ("collector", "수집가", "카드를 3장 저장했어요", n_saved >= 3),
        ("decider", "결정 장인", "선택형 카드에 5번 답했어요", kinds.count("choose") >= 5),
        ("explorer", "분야 탐험가", "5개 분야의 직업을 살펴봤어요", len(fields) >= 5),
        ("streak3", "3일 연속", "3일 연속으로 탐색했어요", streak >= 3),
        ("streak7", "일주일 연속", "7일 연속으로 탐색했어요", streak >= 7),
        ("report", "나를 발견", "성향 리포트를 열었어요", report_ready),
    ]
    return [{"id": i, "name": n, "description": d, "earned": bool(e)} for i, n, d, e in rules]


def gains(attributes: list[Attribute], before: np.ndarray, after: np.ndarray, k: int = 2) -> list[dict]:
    """이번 반응으로 가장 많이 오른 성향 (최대 k개). delta는 퍼센트 포인트."""
    diff = after - before
    ups = sorted((a for a in attributes if diff[a.idx] > MIN_GAIN), key=lambda a: -diff[a.idx])[:k]
    return [{"code": a.code, "label": a.label, "delta": max(1, round(float(diff[a.idx]) * 100))} for a in ups]


def card_reason(attributes: list[Attribute], user: np.ndarray, card_vec: np.ndarray, source: str) -> str | None:
    """'왜 이 카드?' 한 줄. 사용자와 카드가 함께 높은 축으로 만든다 (LLM 없음)."""
    if source == "random":
        return "평소와 다른 분야예요. 의외로 잘 맞을지도 몰라요."
    if source != "personalized":
        return None
    overlap = user * card_vec
    best = max(attributes, key=lambda a: overlap[a.idx])
    return f"'{best.label}' 쪽 카드에 자주 끌려서 골랐어요." if overlap[best.idx] > 0 else None
