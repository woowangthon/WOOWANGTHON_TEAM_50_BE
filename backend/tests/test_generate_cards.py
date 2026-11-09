import json

import pytest

from app.content import N_DIM, CardDeck, Job
from app.content.loader import DATA_DIR, load_attributes
from scripts.generate_cards import (
    batch_model,
    build_prompt,
    dump_deck,
    generate_batch,
    load_deck,
    next_card_id,
    parse_batch,
    place_card,
    select_jobs,
    to_card,
)

ATTRS = load_attributes(DATA_DIR / "attributes.csv")
CODES = [a.code for a in ATTRS]
JOB = Job(id=68, name="목공", field="건설·채굴직")
JOB2 = Job(id=15, name="수의사", field="보건·의료직")
RAW = {
    "workList": [{"work": "목재를 깎고 조립해요."}, {"work": "도면에 따라 치수를 재요."}],
    "interestList": [{"interest": "만드는 걸 좋아하는 사람"}],
    "performList": {"environment": [
        {"environment": "고지대 작업", "importance": 50},
        {"environment": "위험한 장비 노출", "importance": 90},
    ]},
}


def response(title="목공의 하루", body="아침엔 나무를 재요. 오후엔 지붕틀을 조립해요.", **scores) -> str:
    attrs = {c: 0 for c in CODES}
    attrs.update(scores or {"R": 3, "field": 2, "C": 1})
    return json.dumps({"title": title, "body": body, "attrs": attrs}, ensure_ascii=False)


def batch(*pairs) -> str:
    """batch((직업코드, response(...)), ...) → 배치 응답 JSON."""
    return json.dumps({"cards": [{"job_id": j, **json.loads(r)} for j, r in pairs]}, ensure_ascii=False)


# ---------- 변환·검증 ----------

def test_to_card_maps_codes_to_vector():
    card = to_card(response(), "c0010", JOB, ATTRS)
    assert card.attrs[CODES.index("R")] == 3
    assert card.attrs[CODES.index("field")] == 2
    assert (card.source, card.reviewed, card.type, card.job_id) == ("llm", False, "job_day", 68)


@pytest.mark.parametrize("text, msg", [
    ("not json", "JSON"),
    (response(R=3, I=3, A=3), "3점"),                 # 3점 축 3개
    (response(R=1), "0이 아닌 축"),                     # 축 1개
    (response(R=3, field=5), "field"),                 # 범위 밖 점수
    (response(title="가" * 31), "title"),              # 제목 30자 초과
    (json.dumps({"title": "t", "body": "b"}), "attrs"),  # attrs 누락
])
def test_to_card_rejects_rule_violations(text, msg):
    with pytest.raises(ValueError, match=msg):
        to_card(text, "c0010", JOB, ATTRS)


def test_prompt_contains_rubric_and_material():
    p = build_prompt([(JOB, RAW), (JOB2, {"workList": [{"work": "동물을 치료해요."}]})], ATTRS, rubric="RUBRIC-TEXT")
    assert "RUBRIC-TEXT" in p and "목재를 깎고" in p and "동물을 치료해요" in p
    assert '<job id="68">' in p and '<job id="15">' in p
    assert ", ".join(CODES) in p
    # 업무 환경은 중요도 순
    assert p.index("위험한 장비 노출") < p.index("고지대 작업")


# ---------- 생성 루프 ----------

ITEMS = [(JOB, RAW), (JOB2, RAW)]


def test_batch_makes_one_request_and_caches_each_job(tmp_path):
    calls = []

    def fake(prompt: str) -> str:
        calls.append(prompt)
        return batch((68, response()), (15, response(title="수의사의 하루")))

    cards, errors = generate_batch(fake, ITEMS, ATTRS, "rubric", tmp_path)
    assert len(calls) == 1 and errors == {}
    assert (cards[68].job_id, cards[15].title) == (68, "수의사의 하루")
    assert (tmp_path / "68.json").exists() and (tmp_path / "15.json").exists()


def test_retries_only_failed_jobs_with_error_feedback(tmp_path):
    prompts = []
    replies = iter([batch((68, response()), (15, response(R=1))), batch((15, response()))])

    def fake(prompt: str) -> str:
        prompts.append(prompt)
        return next(replies)

    cards, errors = generate_batch(fake, ITEMS, ATTRS, "rubric", tmp_path)
    assert set(cards) == {68, 15} and errors == {}
    assert '<job id="68">' not in prompts[1] and '<job id="15">' in prompts[1]
    assert "이전 답변이 규칙을 어겼다" in prompts[1] and "0이 아닌 축" in prompts[1]


def test_job_missing_from_response_is_retried(tmp_path):
    replies = iter([batch((68, response())), batch((15, response()))])
    cards, errors = generate_batch(lambda p: next(replies), ITEMS, ATTRS, "rubric", tmp_path)
    assert set(cards) == {68, 15} and errors == {}


def test_unrequested_job_in_response_is_ignored(tmp_path):
    fake = lambda p: batch((68, response()), (15, response()), (999, response()))  # noqa: E731
    cards, _ = generate_batch(fake, ITEMS, ATTRS, "rubric", tmp_path)
    assert set(cards) == {68, 15}
    assert not (tmp_path / "999.json").exists()


def test_cache_skips_api_and_only_uncached_jobs_are_requested(tmp_path):
    (tmp_path / "68.json").write_text(json.dumps({"job_id": 68, "response": response()}), encoding="utf-8")
    prompts = []

    def fake(prompt: str) -> str:
        prompts.append(prompt)
        return batch((15, response()))

    cards, _ = generate_batch(fake, ITEMS, ATTRS, "rubric", tmp_path)
    assert set(cards) == {68, 15}
    assert '<job id="68">' not in prompts[0]


def test_fully_cached_batch_makes_no_request(tmp_path):
    for job, _ in ITEMS:
        (tmp_path / f"{job.id}.json").write_text(json.dumps({"job_id": job.id, "response": response()}), encoding="utf-8")

    def boom(prompt: str) -> str:
        raise AssertionError("캐시가 있으면 호출하면 안 됨")

    cards, errors = generate_batch(boom, ITEMS, ATTRS, "rubric", tmp_path)
    assert set(cards) == {68, 15} and errors == {}


def test_invalid_cache_is_regenerated(tmp_path):
    (tmp_path / "68.json").write_text(json.dumps({"job_id": 68, "response": response(R=1)}), encoding="utf-8")
    cards, _ = generate_batch(lambda p: batch((68, response())), [(JOB, RAW)], ATTRS, "rubric", tmp_path)
    assert cards[68].attrs[CODES.index("R")] == 3


def test_gives_up_after_max_attempts(tmp_path):
    calls = []

    def fake(prompt: str) -> str:
        calls.append(prompt)
        return batch((68, response(R=1)))

    cards, errors = generate_batch(fake, [(JOB, RAW)], ATTRS, "rubric", tmp_path)
    assert cards == {} and "0이 아닌 축" in errors[68] and len(calls) == 3
    assert not (tmp_path / "68.json").exists()


def test_invalid_json_response_is_retried(tmp_path):
    replies = iter(["not json", json.dumps({"wrong": []}), batch((68, response()))])
    cards, errors = generate_batch(lambda p: next(replies), [(JOB, RAW)], ATTRS, "rubric", tmp_path)
    assert set(cards) == {68} and errors == {}


def test_api_error_propagates_but_keeps_earlier_cache(tmp_path):
    replies = iter([batch((68, response()), (15, response(R=1)))])

    def fake(prompt: str) -> str:
        try:
            return next(replies)
        except StopIteration:
            raise RuntimeError("quota") from None

    with pytest.raises(RuntimeError):
        generate_batch(fake, ITEMS, ATTRS, "rubric", tmp_path)
    assert (tmp_path / "68.json").exists() and not (tmp_path / "15.json").exists()


def test_parse_batch_keeps_first_duplicate():
    text = batch((68, response(title="첫째")), (68, response(title="둘째")))
    assert parse_batch(text)[68]["title"] == "첫째"


def test_batch_schema_requires_job_id():
    model = batch_model(ATTRS)
    assert model.model_validate_json(batch((68, response()))).cards[0].job_id == 68
    with pytest.raises(ValueError):
        model.model_validate_json(json.dumps({"cards": [json.loads(response())]}))


# ---------- 대상 선택·파일 ----------

def jobs_fixture():
    return {
        1: Job(id=1, name="a", field="F1"), 2: Job(id=2, name="b", field="F1"),
        3: Job(id=3, name="c", field="F1"), 4: Job(id=4, name="d", field="F2"),
    }


def deck_with(job_ids):
    return CardDeck(schema_version=1, cards=[
        {"id": f"c{i:04d}", "type": "job_day", "job_id": j, "title": "t", "body": "b",
         "attrs": [3, 1] + [0] * (N_DIM - 2), "source": "manual"}
        for i, j in enumerate(job_ids, 1)
    ])


def test_select_per_field_fills_up_to_n_by_popularity():
    picked = select_jobs(jobs_fixture(), deck_with([1]), {2: 10, 3: 99}, per_field=2)
    assert [j.id for j in picked] == [3, 4]          # F1은 1장 있으니 1장만, 조회수 높은 3번


def test_select_explicit_skips_existing_and_rejects_unknown():
    assert [j.id for j in select_jobs(jobs_fixture(), deck_with([1]), {}, job_ids=[1, 4])] == [4]
    with pytest.raises(ValueError, match="99"):
        select_jobs(jobs_fixture(), deck_with([]), {}, job_ids=[99])


def test_refresh_includes_unreviewed_llm_card_but_not_manual_or_reviewed():
    llm, manual, reviewed = deck_with([1, 2, 3]).cards
    deck = CardDeck(schema_version=1, cards=[
        llm.model_copy(update={"source": "llm"}),
        manual,
        reviewed.model_copy(update={"source": "llm", "reviewed": True}),
    ])
    picked = select_jobs(jobs_fixture(), deck, {}, job_ids=[1, 2, 3, 4], refresh=True)
    assert [j.id for j in picked] == [1, 4]


def test_place_card_replaces_same_job_keeping_id_and_position():
    deck = deck_with([1, 2])
    card = deck.cards[1].model_copy(update={"id": "c0000", "title": "새 카드"})
    placed = place_card(deck, card)
    assert (placed.id, [c.title for c in deck.cards]) == ("c0002", ["t", "새 카드"])


def test_place_card_appends_new_job_with_next_id():
    deck = deck_with([1])
    new = deck.cards[0].model_copy(update={"id": "c0000", "job_id": 4})
    assert place_card(deck, new).id == "c0002"
    assert len(deck.cards) == 2


def test_next_card_id():
    assert next_card_id(deck_with([1, 2])) == "c0003"
    assert next_card_id(CardDeck(schema_version=1, cards=[])) == "c0001"


def test_dump_keeps_existing_cards_json_format():
    path = DATA_DIR / "cards.json"
    assert dump_deck(load_deck(path)) == path.read_text(encoding="utf-8")


def test_material_tolerates_null_entries():
    # 커리어넷이 일부 직업에서 [None]을 돌려줌
    raw = {"workList": [None, {"work": "일"}], "performList": {"environment": [None]}}
    text = build_prompt([(JOB, raw)], ATTRS, "r")
    assert "- 일" in text and "[업무 환경 (중요도 순)]" not in text
