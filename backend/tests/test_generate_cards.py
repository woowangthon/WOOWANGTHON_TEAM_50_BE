import json

import pytest

from app.content import N_DIM, CardDeck, Job
from app.content.loader import DATA_DIR, load_attributes
from scripts.generate_cards import (
    build_prompt,
    dump_deck,
    generate_card,
    load_deck,
    next_card_id,
    select_jobs,
    to_card,
)

ATTRS = load_attributes(DATA_DIR / "attributes.csv")
CODES = [a.code for a in ATTRS]
JOB = Job(id=68, name="목공", field="건설·채굴직")
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
    p = build_prompt(JOB, RAW, ATTRS, rubric="RUBRIC-TEXT")
    assert "RUBRIC-TEXT" in p and "목공의 하루" in p and "목재를 깎고" in p
    assert ", ".join(CODES) in p
    # 업무 환경은 중요도 순
    assert p.index("위험한 장비 노출") < p.index("고지대 작업")


# ---------- 생성 루프 ----------

def test_retries_with_error_feedback(tmp_path):
    prompts = []
    replies = iter([response(R=1), response()])     # 첫 응답은 규칙 위반

    def fake(prompt: str) -> str:
        prompts.append(prompt)
        return next(replies)

    cache = tmp_path / "68.json"
    card, errors = generate_card(fake, JOB, RAW, "c0010", ATTRS, "rubric", cache)
    assert card is not None and len(errors) == 1
    assert "이전 답변이 규칙을 어겼다" in prompts[1] and "0이 아닌 축" in prompts[1]
    assert cache.exists()


def test_cache_skips_api(tmp_path):
    cache = tmp_path / "68.json"
    cache.write_text(json.dumps({"job_id": 68, "response": response()}), encoding="utf-8")

    def boom(prompt: str) -> str:
        raise AssertionError("캐시가 있으면 호출하면 안 됨")

    card, _ = generate_card(boom, JOB, RAW, "c0010", ATTRS, "rubric", cache)
    assert card.title == "목공의 하루"


def test_gives_up_after_max_attempts():
    card, errors = generate_card(lambda p: response(R=1), JOB, RAW, "c0010", ATTRS, "rubric")
    assert card is None and len(errors) == 3


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


def test_next_card_id():
    assert next_card_id(deck_with([1, 2])) == "c0003"
    assert next_card_id(CardDeck(schema_version=1, cards=[])) == "c0001"


def test_dump_keeps_existing_cards_json_format():
    path = DATA_DIR / "cards.json"
    assert dump_deck(load_deck(path)) == path.read_text(encoding="utf-8")


def test_material_tolerates_null_entries():
    # 커리어넷이 일부 직업에서 [None]을 돌려줌
    raw = {"workList": [None, {"work": "일"}], "performList": {"environment": [None]}}
    text = build_prompt(JOB, raw, ATTRS, "r")
    assert "- 일" in text and "[업무 환경 (중요도 순)]" not in text
