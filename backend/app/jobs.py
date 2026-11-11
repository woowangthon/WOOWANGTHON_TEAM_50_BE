"""직업 탐색: 직업 552개와 내 성향의 적합도, 직업 상세(핵심 성향 비교 · 로드맵 단계 · 비슷한 직업).

직업 벡터는 data/analysis/job_tags.csv (LLM 전수 태깅)를 쓴다. 파일이 없으면 직업 카드의 점수로 대신한다.
적합도(%)는 내 성향 벡터와 직업 벡터의 코사인 유사도 × 100. 반응이 쌓일수록 달라지는 '지금까지의 경향'이다.
준비 단계는 GET /me/roadmap 과 같은 커리어넷 원문(app/roadmap.py)을 쓴다.
"""
import csv
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from app.content import N_DIM, Attribute, ContentStore, scale_attrs
from app.content.loader import DATA_DIR
from app.report import first_sentence
from app.roadmap import build_roadmap

JOB_TAGS = DATA_DIR / "analysis" / "job_tags.csv"
N_SIMILAR = 4

@dataclass(frozen=True)
class JobIndex:
    ids: list[int]
    row: dict[int, int]          # job_id → 행 번호
    raw: np.ndarray              # (N, 11) 0~1 스케일
    unit: np.ndarray             # (N, 11) L2 정규화

    def vector(self, job_id: int) -> np.ndarray | None:
        i = self.row.get(job_id)
        return None if i is None else self.raw[i]


def _unit(m: np.ndarray) -> np.ndarray:
    norm = np.linalg.norm(m, axis=-1, keepdims=True)
    return m / np.where(norm == 0, 1, norm)


def load_job_index(content: ContentStore, path: Path = JOB_TAGS) -> JobIndex:
    vectors: dict[int, np.ndarray] = {}
    if path.exists():
        codes = [a.code for a in content.attributes]
        with path.open(encoding="utf-8-sig", newline="") as f:
            for r in csv.DictReader(f):
                job_id = int(r["job_id"])
                v = scale_attrs([int(r[c] or 0) for c in codes])
                if job_id in content.jobs and v.any():
                    vectors[job_id] = v
    for card in content.cards:   # 태깅 파일에 없는 직업은 카드 점수로 채운다
        if card.job_id is not None and card.job_id in content.jobs and card.job_id not in vectors:
            vectors[card.job_id] = scale_attrs(card.attrs)
    ids = sorted(vectors)
    raw = np.stack([vectors[i] for i in ids]) if ids else np.zeros((0, N_DIM), dtype=np.float32)
    return JobIndex(ids, {j: i for i, j in enumerate(ids)}, raw, _unit(raw))


def match_scores(index: JobIndex, user: np.ndarray) -> np.ndarray | None:
    """직업별 적합도(0~100 정수). 성향이 아직 없으면 None."""
    norm = float(np.linalg.norm(user))
    if norm == 0 or not index.ids:
        return None
    return np.rint(np.clip(index.unit @ (user / norm), 0, 1) * 100).astype(int)


def _summary(content: ContentStore, index: JobIndex, i: int, match: int | None) -> dict:
    job = content.jobs[index.ids[i]]
    return {"job_id": job.id, "name": job.name, "field": job.field,
            "summary": first_sentence(job.summary), "match": match}


def list_fields(content: ContentStore, index: JobIndex) -> list[str]:
    return sorted({content.jobs[j].field for j in index.ids if content.jobs[j].field})


def rank_jobs(content: ContentStore, index: JobIndex, user: np.ndarray,
              field: str | None = None, limit: int = 30) -> list[dict]:
    """적합도 높은 순. 성향이 없으면 직업 코드 순 (match=None)."""
    scores = match_scores(index, user)
    rows = [i for i, j in enumerate(index.ids) if field is None or content.jobs[j].field == field]
    if scores is not None:
        rows.sort(key=lambda i: (-scores[i], index.ids[i]))
    return [_summary(content, index, i, None if scores is None else int(scores[i])) for i in rows[:limit]]


def job_detail(content: ContentStore, index: JobIndex, user: np.ndarray, job_id: int) -> dict | None:
    i = index.row.get(job_id)
    if i is None:
        return None
    job, vec = content.jobs[job_id], index.raw[i]
    scores = match_scores(index, user)
    axes = sorted((a for a in content.attributes if vec[a.idx] > 0), key=lambda a: (-vec[a.idx], a.idx))

    # 왜 맞을까: 이 직업의 핵심 축마다 직업 점수(1~3)와 내 성향(%)
    traits = [{"code": a.code, "label": a.label, "group": a.group, "definition": a.definition,
               "level": round(float(vec[a.idx]) * 3), "mine": round(float(user[a.idx]) * 100)} for a in axes]


    sims = index.unit @ index.unit[i]
    order = [k for k in np.argsort(-sims, kind="stable") if k != i][:N_SIMILAR]
    similar = [_summary(content, index, int(k), None if scores is None else int(scores[k])) for k in order]

    return {
        "job_id": job.id, "name": job.name, "field": job.field,
        "summary": " ".join((job.summary or "").split()), "majors": list(job.majors),
        "match": None if scores is None else int(scores[i]),
        "traits": traits,
        "steps": [s.model_dump() for s in build_roadmap(content, job_id, user).steps],   # 로드맵과 같은 단계
        "similar": similar,
    }
