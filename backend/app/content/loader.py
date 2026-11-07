"""정적 콘텐츠 로더.

서버 시작 시 한 번 호출해 data/ 를 검증하고 추천 계산용 행렬을 만든다.
검증에 실패하면 예외를 던져 서버가 뜨지 않게 한다.
"""
import csv
import json
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from .models import N_DIM, Attribute, Card, CardDeck, Job

DATA_DIR = Path(__file__).resolve().parents[2] / "data"


@dataclass(frozen=True)
class ContentStore:
    attributes: list[Attribute]
    jobs: dict[int, Job]
    cards: list[Card]
    card_index: dict[str, int]        # card.id -> 행 번호
    card_matrix: np.ndarray           # (N, 12) L2 정규화. 코사인 유사도 = 내적
    option_vectors: dict[str, np.ndarray]  # option.id -> (12,) 0~1 스케일

    def card(self, card_id: str) -> Card | None:
        i = self.card_index.get(card_id)
        return None if i is None else self.cards[i]

    def option_vector(self, option_id: str) -> np.ndarray | None:
        return self.option_vectors.get(option_id)


def scale_attrs(v: list[int]) -> np.ndarray:
    """0~3 정수 → 0~1 실수."""
    return np.asarray(v, dtype=np.float32) / 3.0


def _l2(m: np.ndarray) -> np.ndarray:
    norm = np.linalg.norm(m, axis=-1, keepdims=True)
    return m / np.where(norm == 0, 1, norm)


def load_attributes(path: Path) -> list[Attribute]:
    with path.open(encoding="utf-8-sig") as f:      # 엑셀이 붙이는 BOM 무시
        attrs = [Attribute(**row) for row in csv.DictReader(f)]
    if [a.idx for a in attrs] != list(range(N_DIM)):
        raise ValueError(f"attributes.csv: idx가 0~{N_DIM - 1} 순서대로 있어야 함")
    if len({a.code for a in attrs}) != N_DIM:
        raise ValueError("attributes.csv: code 중복")
    return attrs


def load_jobs(path: Path) -> dict[int, Job]:
    if not path.exists():
        return {}
    jobs = [Job(**j) for j in json.loads(path.read_text(encoding="utf-8"))]
    return {j.id: j for j in jobs}


def load_content(data_dir: Path = DATA_DIR, reviewed_only: bool = False) -> ContentStore:
    attributes = load_attributes(data_dir / "attributes.csv")
    jobs = load_jobs(data_dir / "jobs.json")
    deck = CardDeck.model_validate_json((data_dir / "cards.json").read_text(encoding="utf-8"))

    if jobs:
        missing = sorted({c.job_id for c in deck.cards if c.job_id is not None} - jobs.keys())
        if missing:
            raise ValueError(f"cards.json: jobs.json에 없는 job_id {missing}")

    cards = [c for c in deck.cards if c.reviewed or not reviewed_only]
    if not cards:
        raise ValueError("사용 가능한 카드가 없음 (reviewed_only 설정 확인)")

    matrix = _l2(np.stack([scale_attrs(c.attrs) for c in cards]))
    options = {o.id: scale_attrs(o.attrs) for c in cards for o in c.options}

    return ContentStore(
        attributes=attributes,
        jobs=jobs,
        cards=cards,
        card_index={c.id: i for i, c in enumerate(cards)},
        card_matrix=matrix,
        option_vectors=options,
    )
