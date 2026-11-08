import json
import shutil

import numpy as np
import pytest

from app.content import N_DIM, load_content
from app.content.loader import DATA_DIR
from app.recommend import (
    Reaction,
    apply_reaction,
    bucket_of,
    new_user_vector,
    recommend,
    target_vector,
    update_vector,
)

# 분야 → 주로 쓰는 축 두 개
FIELDS = {"A": (0, 1), "B": (2, 3), "C": (4, 5)}
PER_FIELD = 4


def attrs(hi: int, lo: int) -> list[int]:
    v = [0] * N_DIM
    v[hi], v[lo] = 3, 1
    return v


@pytest.fixture(scope="module")
def store(tmp_path_factory):
    d = tmp_path_factory.mktemp("data")
    shutil.copy(DATA_DIR / "attributes.csv", d / "attributes.csv")
    jobs, cards = [], []
    for f, (a, b) in FIELDS.items():
        for k in range(PER_FIELD):
            jid = len(jobs) + 1
            jobs.append({"id": jid, "name": f"{f}{k}", "field": f})
            cards.append({
                "id": f"c{jid:04d}", "type": "job_day", "job_id": jid,
                "title": f"{f}{k}", "body": "본문",
                # 같은 분야 안에서도 순위가 갈리도록 앞 2장은 a축, 뒤 2장은 b축이 핵심
                "attrs": attrs(a, b) if k < 2 else attrs(b, a),
                "source": "manual",
            })
    cards.append({
        "id": "c0100", "type": "a_vs_b", "title": "둘 중?", "body": "본문",
        "attrs": [2, 0, 2] + [0] * (N_DIM - 3),
        "options": [
            {"id": "c0100-a", "label": "가", "attrs": [3, 1] + [0] * (N_DIM - 2)},
            {"id": "c0100-b", "label": "나", "attrs": [0, 0, 3, 1] + [0] * (N_DIM - 4)},
        ],
        "source": "manual",
    })
    (d / "jobs.json").write_text(json.dumps(jobs, ensure_ascii=False), encoding="utf-8")
    (d / "cards.json").write_text(
        json.dumps({"schema_version": 1, "cards": cards}, ensure_ascii=False), encoding="utf-8"
    )
    return load_content(d)


def user_toward(*axes: int) -> np.ndarray:
    u = new_user_vector()
    u[list(axes)] = 1.0
    return u


# ---------- 벡터 갱신 ----------

def test_like_moves_toward_card(store):
    u = new_user_vector()
    t = target_vector(store, "c0001", Reaction.LIKE)
    after = update_vector(u, t, Reaction.LIKE)
    assert np.linalg.norm(after - t) < np.linalg.norm(u - t)
    assert after[0] > 0 and after[5] == 0


def test_save_stronger_than_like(store):
    u = new_user_vector()
    like = apply_reaction(store, u, "c0001", Reaction.LIKE)
    save = apply_reaction(store, u, "c0001", Reaction.SAVE)
    assert save[0] > like[0]


def test_dislike_lowers_only_card_axes_and_clips(store):
    u = np.full(N_DIM, 0.05, dtype=np.float32)
    after = apply_reaction(store, u, "c0001", Reaction.DISLIKE)   # 축 0(3점), 1(1점)
    assert after[0] == 0.0                     # 0 아래로 내려가지 않음
    assert after[1] < u[1]
    assert after[6] == pytest.approx(u[6])     # 카드에 없는 축은 그대로


def test_pass_keeps_vector(store):
    u = user_toward(2)
    assert np.array_equal(apply_reaction(store, u, "c0001", Reaction.PASS), u)


def test_update_does_not_mutate_input(store):
    u = user_toward(2)
    before = u.copy()
    apply_reaction(store, u, "c0001", Reaction.LIKE)
    assert np.array_equal(u, before)


def test_choose_uses_option_vector(store):
    after = apply_reaction(store, new_user_vector(), "c0100", Reaction.CHOOSE, "c0100-b")
    assert after[2] > 0 and after[0] == 0


@pytest.mark.parametrize("card_id, reaction, option_id", [
    ("c0001", Reaction.CHOOSE, "c0001-a"),      # 선택지 없는 카드에 choose
    ("c0100", Reaction.CHOOSE, None),           # choose인데 선택지 없음
    ("c0100", Reaction.CHOOSE, "c0100-z"),      # 없는 선택지
    ("c0100", Reaction.LIKE, "c0100-a"),        # like에 선택지
    ("c9999", Reaction.LIKE, None),             # 없는 카드
])
def test_invalid_reactions(store, card_id, reaction, option_id):
    with pytest.raises(ValueError):
        target_vector(store, card_id, reaction, option_id)


# ---------- 추천 ----------

def rng():
    return np.random.default_rng(0)


def test_cold_start_spreads_across_buckets(store):
    recs = recommend(store, new_user_vector(), n_reactions=0, n=4, rng=rng())
    assert {r.source for r in recs} == {"cold_start"}
    buckets = {bucket_of(store, store.card(r.card_id)) for r in recs}
    assert len(buckets) == 4                    # 분야 A·B·C + 선택형, 겹치지 않음


def test_cold_start_when_vector_empty_even_with_reactions(store):
    recs = recommend(store, new_user_vector(), n_reactions=50, n=3, rng=rng())
    assert {r.source for r in recs} == {"cold_start"}


def test_personalized_ranks_by_similarity(store):
    recs = recommend(store, user_toward(2), n_reactions=10, n=5, explore_ratio=0, rng=rng())
    assert {r.source for r in recs} == {"personalized"}
    top = [store.card(r.card_id) for r in recs[:2]]
    assert all(c.attrs[2] == 3 for c in top)    # 축 2가 핵심인 B분야 카드가 먼저
    scores = [r.score for r in recs]
    assert scores == sorted(scores, reverse=True)


def test_explore_slots_come_from_other_buckets(store):
    recs = recommend(store, user_toward(2), n_reactions=10, n=5, explore_ratio=0.4, rng=rng())
    personal = [r for r in recs if r.source == "personalized"]
    randoms = [r for r in recs if r.source == "random"]
    assert len(randoms) == 2 and len(personal) == 3
    assert recs[0].source == "personalized"
    personal_buckets = {bucket_of(store, store.card(r.card_id)) for r in personal}
    assert all(bucket_of(store, store.card(r.card_id)) not in personal_buckets for r in randoms)


def test_seen_cards_excluded(store):
    seen = {"c0005", "c0006"}                  # B분야 축 2 핵심 카드
    recs = recommend(store, user_toward(2), n_reactions=10, n=13, seen=seen, rng=rng())
    ids = [r.card_id for r in recs]
    assert not seen & set(ids)
    assert len(ids) == len(set(ids)) == 11      # 전체 13장 중 2장 제외


def test_returns_fewer_when_exhausted(store):
    all_ids = {c.id for c in store.cards}
    assert recommend(store, user_toward(2), 10, seen=all_ids, rng=rng()) == []
    left = recommend(store, user_toward(2), 10, seen=all_ids - {"c0001"}, n=10, rng=rng())
    assert [r.card_id for r in left] == ["c0001"]


def test_deterministic_with_seed(store):
    a = recommend(store, user_toward(0, 4), 10, n=6, rng=np.random.default_rng(7))
    b = recommend(store, user_toward(0, 4), 10, n=6, rng=np.random.default_rng(7))
    assert a == b


def test_works_with_real_content():
    real = load_content()
    u = apply_reaction(real, new_user_vector(), "c0001", Reaction.LIKE)
    recs = recommend(real, u, n_reactions=10, seen={"c0001"}, rng=rng())
    ids = [r.card_id for r in recs]
    assert "c0001" not in ids
    assert len(ids) == len(set(ids)) == min(10, len(real.cards) - 1)
