"""성향 리포트. 사용자 벡터와 카드 점수만으로 만든다 (LLM 없음, 같은 입력이면 같은 결과).

- 흥미 유형: 흥미(RIASEC) 축 상위 2개
- 일하는 방식·가치: style/value 축 상위 2개
- 어울리는 직업: 직업 카드와 코사인 유사도 상위 (싫어요 한 직업 제외)
- 관련 학과: 상위 직업들의 학과를 유사도로 가중 합산 (직업마다 학과 수로 나눔)

리포트는 성향을 만드는 반응(좋아요·저장·선택)이 MIN_SIGNAL개 이상이고 벡터가 0이 아닐 때 열린다.
패스는 벡터를 바꾸지 않고, 0에서 시작한 벡터는 싫어요로도 0에 머물기 때문에 이 둘은 세지 않는다.
"""
import re
from collections import Counter, defaultdict
from collections.abc import Iterable
from dataclasses import dataclass, field

import numpy as np

from app.content import Attribute, ContentStore
from app.recommend.vector import Reaction

MIN_SIGNAL = 5
SIGNAL_REACTIONS = {Reaction.LIKE.value, Reaction.SAVE.value, Reaction.CHOOSE.value}
N_INTERESTS = 2
N_STRENGTHS = 2
N_JOBS = 5
N_MAJORS = 5
MAJOR_POOL = 10          # 학과는 상위 10개 직업에서 모은다

INTEREST_PHRASES = {
    "R": "직접 만들고 고치는 일",
    "I": "원리를 탐구하는 일",
    "A": "생각을 표현하는 일",
    "S": "사람을 돕고 가르치는 일",
    "E": "사람을 이끌고 설득하는 일",
    "C": "규칙대로 꼼꼼히 정리하는 일",
}
STRENGTH_PHRASES = {
    "team": "여럿이 함께 일하는 방식",
    "field": "현장을 누비는 일",
    "novelty": "새롭고 변화가 많은 일",
    "autonomy": "스스로 방법을 정하는 일",
    "impact": "사회에 도움이 되는 일",
}
STUDY_TIPS = {
    "R": "기술·가정 실습이나 만들기 동아리에서 손으로 직접 만들어 보세요.",
    "I": "수학·과학 시간에 '왜 그럴까?'를 끝까지 파고드는 탐구 보고서를 써 보세요.",
    "A": "국어·미술·음악 수행평가에서 내 생각을 담은 작품을 만들어 보세요.",
    "S": "또래 멘토링이나 봉사 활동으로 다른 사람을 돕는 경험을 해 보세요.",
    "E": "학급 회의나 모둠 활동에서 의견을 모으고 이끄는 역할을 맡아 보세요.",
    "C": "정보 시간에 자료를 표로 정리하거나 내 공부 계획표를 꼼꼼히 관리해 보세요.",
}


@dataclass
class JobMatch:
    job_id: int
    name: str
    field: str | None
    majors: list[str]
    score: float
    summary: str = ""


@dataclass
class Report:
    n_reactions: int
    ready: bool
    remaining: int                                  # 리포트가 열리기까지 더 필요한 좋아요·저장·선택 수
    top_interests: list[Attribute] = field(default_factory=list)
    strengths: list[Attribute] = field(default_factory=list)
    summary: str = ""
    study_tips: list[str] = field(default_factory=list)
    jobs: list[JobMatch] = field(default_factory=list)
    majors: list[str] = field(default_factory=list)
    reaction_counts: dict[str, int] = field(default_factory=dict)


def _top(attributes: list[Attribute], vector: np.ndarray, groups: set[str], k: int) -> list[Attribute]:
    picked = [a for a in attributes if a.group in groups and vector[a.idx] > 0]
    return sorted(picked, key=lambda a: (-vector[a.idx], a.idx))[:k]


def _join(words: list[str]) -> str:
    return words[0] if len(words) == 1 else f"{', '.join(words[:-1])}과 {words[-1]}"


def _summary(interests: list[Attribute], strengths: list[Attribute]) -> str:
    if not interests and not strengths:
        return "아직 뚜렷하게 끌리는 쪽이 보이지 않아요. 카드를 더 둘러보면 성향이 드러나요."
    parts = []
    if interests:
        labels = "·".join(a.label for a in interests)
        parts.append(f"{labels} 카드에 자주 끌렸어요. {_join([INTEREST_PHRASES[a.code] for a in interests])}에 관심이 많아 보여요.")
    if strengths:
        parts.append(f"일할 때는 {_join([STRENGTH_PHRASES[a.code] for a in strengths])}을 중요하게 여기는 편이에요.")
    return " ".join(parts)


FIRST_SENTENCE = re.compile(r"^(.+?[.!?])(?:\s|$)")
MAX_SUMMARY = 100     # 첫 문장이 이보다 길면 자른다 (전체 552개 중 56개)


def first_sentence(text: str | None) -> str:
    """직업 설명의 첫 문장. 문장 끝을 못 찾거나 너무 길면 잘라서 말줄임표."""
    text = " ".join((text or "").split())
    match = FIRST_SENTENCE.match(text)
    sentence = match[1] if match else text
    return sentence if len(sentence) <= MAX_SUMMARY else sentence[: MAX_SUMMARY - 1].rstrip() + "…"


def match_jobs(content: ContentStore, vector: np.ndarray, exclude_jobs: Iterable[int] = (), k: int = N_JOBS) -> list[JobMatch]:
    norm = float(np.linalg.norm(vector))
    if norm == 0:
        return []
    exclude = set(exclude_jobs)
    rows = [i for i, c in enumerate(content.cards) if c.job_id is not None and c.job_id not in exclude]
    if not rows:
        return []
    scores = content.card_matrix[rows] @ (vector / norm)
    best: dict[int, float] = {}
    for i, s in zip(rows, scores):
        job_id = content.cards[i].job_id
        best[job_id] = max(best.get(job_id, -1.0), float(s))
    ranked = sorted(best.items(), key=lambda kv: (-kv[1], kv[0]))[:k]
    matches = []
    for job_id, score in ranked:
        job = content.jobs.get(job_id)
        if job is not None:
            matches.append(
                JobMatch(job_id, job.name, job.field, list(job.majors), round(score, 3), first_sentence(job.summary))
            )
    return matches


def rank_majors(jobs: list[JobMatch], k: int = N_MAJORS) -> list[str]:
    """직업마다 유사도를 학과 수로 나눠 준다. 학과를 많이 나열한 직업이 순위를 독차지하지 않게."""
    weight: dict[str, float] = defaultdict(float)
    for job in jobs:
        for major in job.majors:
            weight[major] += job.score / len(job.majors)
    return [m for m, _ in sorted(weight.items(), key=lambda kv: -kv[1])[:k]]


def build_report(
    content: ContentStore,
    vector: np.ndarray,
    n_reactions: int,
    reactions: list[dict],
    *,
    min_signal: int = MIN_SIGNAL,
) -> Report:
    """reactions: [{card_id, reaction}, ...] — 반응 종류를 세고, 싫어요 한 직업을 뺀다."""
    counts = {r.value: 0 for r in Reaction} | Counter(r["reaction"] for r in reactions)
    signal = sum(counts[r] for r in SIGNAL_REACTIONS)
    if signal < min_signal or not np.any(vector > 0):
        # 반응 수는 채웠는데 싫어요로 벡터가 0이 된 경우에도 1개는 더 필요하다고 안내
        return Report(n_reactions, False, max(min_signal - signal, 1), reaction_counts=counts)

    disliked = set()
    for r in reactions:
        card = content.card(r["card_id"])
        if r["reaction"] == "dislike" and card is not None and card.job_id is not None:
            disliked.add(card.job_id)

    interests = _top(content.attributes, vector, {"interest"}, N_INTERESTS)
    strengths = _top(content.attributes, vector, {"style", "value"}, N_STRENGTHS)
    pool = match_jobs(content, vector, disliked, k=MAJOR_POOL)
    return Report(
        n_reactions=n_reactions,
        ready=True,
        remaining=0,
        top_interests=interests,
        strengths=strengths,
        summary=_summary(interests, strengths),
        study_tips=[STUDY_TIPS[a.code] for a in interests],
        jobs=pool[:N_JOBS],
        majors=rank_majors(pool),
        reaction_counts=counts,
    )
