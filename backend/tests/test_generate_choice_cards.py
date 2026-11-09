import json

import pytest

from app.content import CardDeck
from app.content.loader import DATA_DIR, load_attributes
from scripts.generate_cards import dump_deck
from scripts.generate_choice_cards import (
    AXIS_BRIEFS,
    batch_model,
    build_prompt,
    covers,
    generate_for_axis,
    needs,
    parse_cards,
    to_choice_card,
    with_id,
)

ATTRS = load_attributes(DATA_DIR / "attributes.csv")
BY_CODE = {a.code: a for a in ATTRS}
TEAM = BY_CODE["team"]
AUTONOMY = BY_CODE["autonomy"]


def scores(**values) -> dict[str, int]:
    return {a.code: values.get(a.code, 0) for a in ATTRS}


def entry(title="모둠 발표 준비", kind="a_vs_b", card=None, options=None) -> dict:
    return {
        "type": kind,
        "title": title,
        "body": "발표를 준비해요.",
        "attrs": scores(**(card or {"team": 3, "S": 1})),
        "options": options or [
            {"label": "친구들과 나눠서", "attrs": scores(team=3, S=1)},
            {"label": "혼자 끝까지", "attrs": scores(I=2, autonomy=1)},
        ],
    }


def reply(*entries) -> str:
    return json.dumps({"cards": list(entries)}, ensure_ascii=False)


def test_all_briefs_are_real_axes():
    assert set(AXIS_BRIEFS) <= set(BY_CODE)


def test_valid_entry_becomes_unreviewed_llm_card():
    card = to_choice_card(entry(), TEAM, ATTRS)
    assert (card.type, card.source, card.reviewed, card.job_id) == ("a_vs_b", "llm", False, None)
    assert card.attrs[TEAM.idx] == 3
    assert covers(card, TEAM.idx)


def test_option_order_is_stable_for_same_title():
    labels = [[o.label for o in to_choice_card(entry(), TEAM, ATTRS).options] for _ in range(3)]
    assert labels[0] == labels[1] == labels[2]


def test_option_order_is_not_always_as_generated():
    first = {to_choice_card(entry(title=f"제목{i}"), TEAM, ATTRS).options[0].label for i in range(20)}
    assert len(first) == 2


@pytest.mark.parametrize("bad, msg", [
    (entry(card={"S": 2, "I": 2}), "team"),                                     # 카드가 그 축을 안 묻는다
    (entry(card={"team": 1, "S": 2}), "team"),                                  # 묻는 정도가 약하다
    (entry(options=[
        {"label": "a", "attrs": scores(team=2, S=3)},
        {"label": "b", "attrs": scores(team=1, I=3)},
    ]), "team"),                                                                # 다른 축에서만 갈린다
    (entry(kind="a_vs_b", options=[
        {"label": "a", "attrs": scores(team=3, S=1)},
        {"label": "b", "attrs": scores(I=2, C=1)},
        {"label": "c", "attrs": scores(A=2, C=1)},
    ]), "정확히 2개"),
    (entry(card={"team": 3, "A": 2}, options=[
        {"label": "a", "attrs": scores(team=3, A=2)},
        {"label": "b", "attrs": scores(A=2, C=2)},
    ]), "A"),                                                                   # 선택지가 같은 점수인 축을 묻는다
    (entry(kind="job_day"), "job_id"),
    (entry(title="가" * 31), "title"),
    (entry(options=[
        {"label": "a", "attrs": scores(team=3)},                                # 0이 아닌 축 1개
        {"label": "b", "attrs": scores(I=2, C=1)},
    ]), "0이 아닌 축"),
])
def test_rule_violations_are_rejected(bad, msg):
    with pytest.raises(ValueError, match=msg):
        to_choice_card(bad, TEAM, ATTRS)


def test_flat_axis_error_names_the_axis_for_retry():
    bad = entry(card={"team": 3, "A": 2}, options=[
        {"label": "a", "attrs": scores(team=3, A=2)},
        {"label": "b", "attrs": scores(A=2, C=2)},
    ])
    with pytest.raises(ValueError, match=r"\['A'\].*0점"):
        to_choice_card(bad, TEAM, ATTRS)


def test_axis_shared_by_only_some_options_is_allowed():
    card = to_choice_card(entry(kind="situation", card={"team": 3, "A": 1}, options=[
        {"label": "a", "attrs": scores(team=3, A=2)},
        {"label": "b", "attrs": scores(A=2, C=2)},
        {"label": "c", "attrs": scores(I=2, C=1)},
    ]), TEAM, ATTRS)
    assert card.type == "situation"


def test_prompt_guards_against_observed_mistagging():
    p = build_prompt(TEAM, 1, ATTRS, "r", [], [])
    assert "혼자 한다는 것만으로 autonomy를 주지 않는다" in p
    assert "완성도를 높이는 것만으로 C를 주지 않는다" in p
    assert "같은 점수인 축은 카드 attrs에서 0점" in p


def test_with_id_renames_options_and_keeps_deck_valid():
    card = with_id(to_choice_card(entry(), TEAM, ATTRS), "c0123")
    assert card.id == "c0123"
    assert [o.id for o in card.options] == ["c0123-a", "c0123-b"]
    deck = CardDeck(schema_version=2, cards=[card])
    assert CardDeck.model_validate_json(dump_deck(deck)).cards[0].options[1].id == "c0123-b"


def make_deck(*cards) -> CardDeck:
    return CardDeck(schema_version=2, cards=[with_id(c, f"c{i:04d}") for i, c in enumerate(cards, 1)])


def test_needs_counts_only_cards_that_really_ask_the_axis():
    deck = make_deck(to_choice_card(entry(), TEAM, ATTRS))
    assert needs(deck, [TEAM, AUTONOMY], per_axis=2) == [(TEAM, 1), (AUTONOMY, 2)]


def test_needs_skips_satisfied_axis():
    deck = make_deck(to_choice_card(entry(), TEAM, ATTRS))
    assert needs(deck, [TEAM], per_axis=1) == []


def test_prompt_contains_axis_brief_used_titles_and_rubric():
    p = build_prompt(AUTONOMY, 3, ATTRS, "RUBRIC-TEXT", ["기존 제목"], [])
    assert AXIS_BRIEFS["autonomy"] in p and "기존 제목" in p and "RUBRIC-TEXT" in p and "3장" in p
    assert "이전 답변이 규칙을 어겼다" not in p


def test_generates_requested_count_in_one_request():
    calls = []

    def fake(prompt):
        calls.append(prompt)
        return reply(entry(title="가"), entry(title="나"))

    cards, errors = generate_for_axis(fake, TEAM, 2, ATTRS, "r", set())
    assert [c.title for c in cards] == ["가", "나"] and len(calls) == 1 and errors == []


def test_extra_cards_are_dropped():
    cards, _ = generate_for_axis(lambda p: reply(entry(title="가"), entry(title="나")), TEAM, 1, ATTRS, "r", set())
    assert len(cards) == 1


def test_requests_only_shortfall_with_errors_after_invalid_card():
    prompts = []
    replies = iter([reply(entry(title="가"), entry(title="나", card={"S": 2, "I": 2})), reply(entry(title="다"))])

    def fake(prompt):
        prompts.append(prompt)
        return next(replies)

    cards, errors = generate_for_axis(fake, TEAM, 2, ATTRS, "r", set())
    assert [c.title for c in cards] == ["가", "다"] and errors == []
    assert "1장" in prompts[1] and "이전 답변이 규칙을 어겼다" in prompts[1] and "- 가" in prompts[1]


def test_existing_and_repeated_titles_are_rejected():
    cards, errors = generate_for_axis(
        lambda p: reply(entry(title="기존"), entry(title="새것"), entry(title="새것")),
        TEAM, 3, ATTRS, "r", {"기존"},
    )
    assert [c.title for c in cards] == ["새것"]
    assert any("이미 있는 제목" in e for e in errors)


def test_stops_after_max_attempts():
    calls = []

    def fake(prompt):
        calls.append(prompt)
        return "not json"

    cards, errors = generate_for_axis(fake, TEAM, 2, ATTRS, "r", set())
    assert cards == [] and len(calls) == 3 and "JSON" in errors[0]


@pytest.mark.parametrize("text", ['{"wrong": []}', '{"cards": 3}', "[]"])
def test_parse_rejects_wrong_shape(text):
    with pytest.raises(ValueError):
        parse_cards(text)


def test_batch_schema_accepts_generated_shape():
    assert len(batch_model(ATTRS).model_validate_json(reply(entry())).cards) == 1
