import json
import shutil
from pathlib import Path

import numpy as np
import pytest
from pydantic import ValidationError

from app.content import N_DIM, Card, load_content

DATA = Path(__file__).resolve().parents[1] / "data"


def v(*xs: int) -> list[int]:
    """앞쪽 축 점수만 적고 나머지는 0으로 채운 벡터."""
    return list(xs) + [0] * (N_DIM - len(xs))


def make_card(**over):
    base = {
        "id": "c0100", "type": "job_day", "job_id": 1,
        "title": "t", "body": "b",
        "attrs": v(3, 2),
        "source": "manual",
    }
    return {**base, **over}


def test_sample_data_loads():
    store = load_content(DATA)
    assert len(store.attributes) == N_DIM
    assert store.card_matrix.shape == (len(store.cards), N_DIM)
    assert np.allclose(np.linalg.norm(store.card_matrix, axis=1), 1.0)
    assert store.option_vector("c0002-a") is not None


@pytest.mark.parametrize("attrs, msg", [
    (v(3), "0이 아닌 축"),          # 1개
    (v(1, 1, 1, 1, 1), "0이 아닌 축"),          # 5개
    (v(3, 3, 3), "3점 축"),
])
def test_attr_rules(attrs, msg):
    with pytest.raises(ValidationError, match=msg):
        Card(**make_card(attrs=attrs))


def test_attr_length_and_range():
    with pytest.raises(ValidationError):
        Card(**make_card(attrs=[1, 1]))
    with pytest.raises(ValidationError):
        Card(**make_card(attrs=v(4, 1)))


def test_job_day_needs_job_id():
    with pytest.raises(ValidationError, match="job_id 필수"):
        Card(**make_card(job_id=None))


def test_a_vs_b_option_count_and_gap():
    opt = lambda i, a: {"id": f"c0100-{i}", "label": "x", "attrs": a}
    v1 = v(2, 1)
    v2 = v(1, 2)
    with pytest.raises(ValidationError, match="정확히 2개"):
        Card(**make_card(type="a_vs_b", job_id=None, options=[opt("a", v1)]))
    with pytest.raises(ValidationError, match="2점 이상"):
        Card(**make_card(type="a_vs_b", job_id=None, options=[opt("a", v1), opt("b", v2)]))


def test_option_id_prefix():
    opts = [
        {"id": "c9999-a", "label": "x", "attrs": v(3, 0, 1)},
        {"id": "c0100-b", "label": "y", "attrs": v(0, 3, 1)},
    ]
    with pytest.raises(ValidationError, match="시작해야 함"):
        Card(**make_card(type="a_vs_b", job_id=None, options=opts))


def test_duplicate_id_rejected(tmp_path):
    shutil.copy(DATA / "attributes.csv", tmp_path)
    deck = json.loads((DATA / "cards.json").read_text(encoding="utf-8"))
    deck["cards"].append(deck["cards"][0])
    (tmp_path / "cards.json").write_text(json.dumps(deck, ensure_ascii=False), encoding="utf-8")
    with pytest.raises(ValidationError, match="중복"):
        load_content(tmp_path)


def test_reviewed_only_keeps_only_reviewed_cards(tmp_path):
    shutil.copy(DATA / "attributes.csv", tmp_path)
    deck = json.loads((DATA / "cards.json").read_text(encoding="utf-8"))
    for i, card in enumerate(deck["cards"]):
        card["reviewed"] = i < 2
    (tmp_path / "cards.json").write_text(json.dumps(deck, ensure_ascii=False), encoding="utf-8")
    store = load_content(tmp_path, reviewed_only=True)
    assert [c.id for c in store.cards] == [c["id"] for c in deck["cards"][:2]]
    assert len(load_content(tmp_path).cards) == len(deck["cards"])


def test_reviewed_only_without_reviewed_cards_fails(tmp_path):
    shutil.copy(DATA / "attributes.csv", tmp_path)
    deck = json.loads((DATA / "cards.json").read_text(encoding="utf-8"))
    for card in deck["cards"]:
        card["reviewed"] = False
    (tmp_path / "cards.json").write_text(json.dumps(deck, ensure_ascii=False), encoding="utf-8")
    with pytest.raises(ValueError, match="사용 가능한 카드가 없음"):
        load_content(tmp_path, reviewed_only=True)


def test_unknown_job_id_rejected(tmp_path):
    shutil.copy(DATA / "attributes.csv", tmp_path)
    shutil.copy(DATA / "cards.json", tmp_path)
    (tmp_path / "jobs.json").write_text('[{"id": 999, "name": "x"}]', encoding="utf-8")
    with pytest.raises(ValueError, match="없는 job_id"):
        load_content(tmp_path)
