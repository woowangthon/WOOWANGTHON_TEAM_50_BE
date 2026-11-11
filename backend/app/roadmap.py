"""직업 로드맵. 커리어넷 원문(data/job_guides.json)을 단계별로 묶는다 (LLM 없음).

단계: 지금 해 볼 수 있는 일 → 키워 볼 능력 → 진학 준비 → 자격과 진출
내용이 없는 단계는 뺀다.
"""
from collections.abc import Sequence
from datetime import datetime

from app.content import ContentStore, JobGuide
from app.report import first_sentence, josa
from app.schemas.me import RoadmapItem, RoadmapJob, RoadmapStep, RoadmapTrait

STEP_TITLES = {
    "explore": "지금 해 볼 수 있는 일",
    "grow": "키워 볼 능력",
    "study": "진학 준비",
    "career": "자격과 진출",
}


def _grow_items(guide: JobGuide) -> list[RoadmapItem]:
    """능력 이름마다 그 능력을 설명하는 적성 문장을 붙인다. 짝이 없는 적성 문장은 따로 넣는다."""
    items, used = [], set()
    for ability in guide.abilities:
        sentence = next((s for s in guide.aptitudes if ability in s and s not in used), None)
        if sentence:
            used.add(sentence)
        items.append(RoadmapItem(title=ability, detail=sentence or f"{josa(ability, '이', '가')} 필요해요."))
    items += [RoadmapItem(title=None, detail=s) for s in guide.aptitudes if s not in used]
    return items


def _steps(guide: JobGuide | None, majors: Sequence[str]) -> list[RoadmapStep]:
    study = [RoadmapItem(title=None, detail=s) for s in guide.education] if guide else []
    if majors:
        study.append(RoadmapItem(title="관련 학과", detail=", ".join(majors)))
    items = {
        "explore": [RoadmapItem(title=a.title, detail=a.detail) for a in guide.activities] if guide else [],
        "grow": _grow_items(guide) if guide else [],
        "study": study,
        "career": [RoadmapItem(title="자격증", detail=s) for s in guide.certificates]
        + [RoadmapItem(title="진출 분야", detail=s) for s in guide.careers] if guide else [],
    }
    return [RoadmapStep(key=k, title=STEP_TITLES[k], items=v) for k, v in items.items() if v]


def build_roadmap(content: ContentStore, job_id: int, vector, saved_at: datetime | None = None) -> RoadmapJob | None:
    """job_id의 로드맵. 콘텐츠에 없는 직업이면 None."""
    job = content.jobs.get(job_id)
    if job is None:
        return None
    card = next((c for c in content.cards if c.job_id == job_id), None)
    traits = []
    if card is not None:
        scored = sorted((a for a in content.attributes if card.attrs[a.idx]), key=lambda a: (-card.attrs[a.idx], a.idx))
        traits = [
            RoadmapTrait(code=a.code, label=a.label, group=a.group,
                         job_score=card.attrs[a.idx], my_score=float(vector[a.idx]))
            for a in scored
        ]
    return RoadmapJob(
        job_id=job.id,
        name=job.name,
        field=job.field,
        summary=first_sentence(job.summary),
        majors=job.majors,
        card_id=card.id if card else None,
        saved_at=saved_at,
        traits=traits,
        steps=_steps(content.guides.get(job_id), job.majors),
    )


def saved_job_ids(content: ContentStore, reactions: list[dict]) -> list[tuple[int, str]]:
    """reactions(최신 순)에서 저장한 직업 카드의 (job_id, 저장 시각). 같은 직업은 한 번만."""
    out: dict[int, str] = {}
    for r in reactions:
        card = content.card(r["card_id"])
        if r["reaction"] == "save" and card is not None and card.job_id is not None:
            out.setdefault(card.job_id, r["created_at"])
    return list(out.items())
