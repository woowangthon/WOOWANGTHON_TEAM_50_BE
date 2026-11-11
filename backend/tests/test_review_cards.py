import copy

import pytest

from app.content import N_DIM, CardDeck, Job
from app.content.loader import DATA_DIR, load_attributes
from scripts.generate_cards import load_deck
from scripts.review_cards import (
    COLUMNS,
    card_to_row,
    format_attrs,
    merge_rows,
    parse_attrs,
    parse_reviewed,
    read_csv,
    write_csv,
)

ATTRS = load_attributes(DATA_DIR / "attributes.csv")
CODES = [a.code for a in ATTRS]
JOBS = {68: Job(id=68, name="목공", field="건설·채굴직")}


def vector(**scores) -> list[int]:
    return [scores.get(c, 0) for c in CODES]


def make_deck() -> CardDeck:
    return CardDeck(schema_version=2, cards=[
        {"id": "c0001", "type": "job_day", "job_id": 68, "title": "목공의 하루", "body": "나무를 재요.",
         "attrs": vector(R=3, field=2), "source": "llm"},
        {"id": "c0002", "type": "a_vs_b", "title": "주말", "body": "더 끌리는 쪽은?",
         "attrs": vector(team=3, S=1), "source": "llm",
         "options": [
             {"id": "c0002-a", "label": "같이", "attrs": vector(team=3, S=1)},
             {"id": "c0002-b", "label": "혼자", "attrs": vector(I=2, C=1)},
         ]},
    ])


def rows_of(deck: CardDeck) -> list[dict[str, str]]:
    return [card_to_row(c, JOBS, ATTRS) for c in deck.cards]


def merge(deck, rows):
    return merge_rows(deck, rows, ATTRS)


# ---------- 점수 형식 ----------

def test_format_and_parse_round_trip():
    v = vector(R=3, field=2, impact=1)
    assert format_attrs(v, ATTRS) == "R3 field2 impact1"
    assert parse_attrs("R3 field2 impact1", ATTRS) == v


def test_parse_allows_commas_and_extra_spaces():
    assert parse_attrs(" R3,  field2 ", ATTRS) == vector(R=3, field=2)


@pytest.mark.parametrize("text", ["R4", "X2", "R", "R3 R2", "3R"])
def test_parse_rejects_bad_tokens(text):
    with pytest.raises(ValueError):
        parse_attrs(text, ATTRS)


@pytest.mark.parametrize("text, expected", [
    ("true", True), ("TRUE", True), ("1", True), ("o", True), ("Y", True),
    ("false", False), ("FALSE", False), ("0", False), ("x", False), ("n", False),
])
def test_parse_reviewed(text, expected):
    assert parse_reviewed(text) is expected


@pytest.mark.parametrize("text", ["", "maybe"])
def test_parse_reviewed_rejects_unknown(text):
    with pytest.raises(ValueError):
        parse_reviewed(text)


# ---------- 내보내기 ----------

def test_export_shows_job_and_options():
    job, choice = rows_of(make_deck())
    assert (job["job"], job["field"], job["attrs"], job["option_a"]) == ("목공", "건설·채굴직", "R3 field2", "")
    assert (choice["job"], choice["option_b"], choice["option_b_attrs"]) == ("", "혼자", "I2 C1")
    assert set(job) == set(COLUMNS)


def test_unedited_csv_changes_nothing():
    deck = make_deck()
    new_deck, errors, changed = merge(deck, rows_of(deck))
    assert errors == [] and changed == [] and new_deck == deck


def test_real_deck_survives_csv_round_trip(tmp_path):
    from app.content.loader import load_jobs

    deck = load_deck()
    path = tmp_path / "cards.csv"
    write_csv(path, [card_to_row(c, load_jobs(DATA_DIR / "jobs.json"), ATTRS) for c in deck.cards])
    new_deck, errors, changed = merge(deck, read_csv(path))
    assert errors == [] and changed == [] and new_deck == deck


def test_csv_starts_with_bom_for_excel(tmp_path):
    path = tmp_path / "cards.csv"
    write_csv(path, rows_of(make_deck()))
    assert path.read_bytes().startswith(b"\xef\xbb\xbf")


def test_csv_preserves_commas_quotes_and_newlines(tmp_path):
    rows = rows_of(make_deck())
    rows[0]["body"] = '쉼표, "따옴표"\n줄바꿈'
    path = tmp_path / "cards.csv"
    write_csv(path, rows)
    assert read_csv(path)[0]["body"] == rows[0]["body"]


# ---------- 되돌려 넣기 ----------

def test_edits_are_applied_and_reported():
    deck = make_deck()
    rows = rows_of(deck)
    rows[0].update(reviewed="TRUE", title="고친 제목", body="고친  본문", attrs="R2 field3")
    new_deck, errors, changed = merge(deck, rows)
    card = new_deck.cards[0]
    assert errors == [] and changed == ["c0001"]
    assert (card.reviewed, card.title, card.body, card.attrs) == (True, "고친 제목", "고친 본문", vector(R=2, field=3))


def test_option_edits_keep_option_ids():
    deck = make_deck()
    rows = rows_of(deck)
    rows[1].update(option_b="혼자 집중", option_b_attrs="I3 C1")
    option = merge(deck, rows)[0].cards[1].options[1]
    assert (option.id, option.label, option.attrs) == ("c0002-b", "혼자 집중", vector(I=3, C=1))


def test_uneditable_fields_stay_the_same():
    deck = make_deck()
    rows = rows_of(deck)
    rows[0].update(type="a_vs_b", job="다른 직업", field="다른 분야", note="메모")
    new_deck, errors, changed = merge(deck, rows)
    assert errors == [] and changed == [] and new_deck.cards[0].type == "job_day"


def test_rows_missing_from_csv_are_left_alone():
    deck = make_deck()
    rows = rows_of(deck)[:1]
    rows[0]["reviewed"] = "true"
    new_deck, _, changed = merge(deck, rows)
    assert changed == ["c0001"] and new_deck.cards[1] == deck.cards[1]


def test_input_deck_is_not_mutated():
    deck = make_deck()
    before = copy.deepcopy(deck)
    rows = rows_of(deck)
    rows[1].update(option_a="바뀜", reviewed="true")
    merge(deck, rows)
    assert deck == before


@pytest.mark.parametrize("edit, msg", [
    ({"attrs": "R3"}, "0이 아닌 축"),
    ({"attrs": "R3 I3 A3"}, "3점"),
    ({"attrs": "R9 field2"}, "점수 형식"),
    ({"title": "가" * 31}, "title"),
    ({"reviewed": "모름"}, "reviewed"),
    ({"option_a": "새 선택지", "option_a_attrs": "R3 I1"}, "추가할 수 없음"),
])
def test_rule_violations_are_reported_with_row_and_id(edit, msg):
    deck = make_deck()
    rows = rows_of(deck)
    rows[0].update(edit)
    new_deck, errors, changed = merge(deck, rows)
    assert new_deck is None and changed == []
    assert errors[0].startswith("2행 c0001") and msg in errors[0]


@pytest.mark.parametrize("edit, msg", [
    ({"option_a": ""}, "비울 수 없음"),
    ({"option_a_attrs": "I2 C1"}, "차이"),                     # 선택지끼리 점수가 같아짐
])
def test_option_rule_violations_are_rejected(edit, msg):
    deck = make_deck()
    rows = rows_of(deck)
    if "option_a_attrs" in edit:
        rows[1]["option_a_attrs"], rows[1]["option_b_attrs"] = "I2 C1", "I2 C1"
    else:
        rows[1].update(edit)
    new_deck, errors, _ = merge(deck, rows)
    assert new_deck is None and msg in errors[0]


def test_one_bad_row_blocks_every_change():
    deck = make_deck()
    rows = rows_of(deck)
    rows[0]["reviewed"] = "true"
    rows[1]["attrs"] = "오류"
    new_deck, errors, changed = merge(deck, rows)
    assert new_deck is None and changed == [] and len(errors) == 1


def test_collects_all_errors():
    deck = make_deck()
    rows = rows_of(deck)
    rows[0]["attrs"] = "오류"
    rows[1]["attrs"] = "오류"
    assert len(merge(deck, rows)[1]) == 2


def test_unknown_and_duplicate_ids_are_rejected():
    deck = make_deck()
    unknown = rows_of(deck)
    unknown[0]["id"] = "c9999"
    assert "없는 카드" in merge(deck, unknown)[1][0]
    duplicate = rows_of(deck) + rows_of(deck)[:1]
    assert "id 중복" in merge(deck, duplicate)[1][0]


def test_missing_required_column_is_reported():
    deck = make_deck()
    rows = rows_of(deck)
    del rows[0]["title"]
    assert "필수 칸" in merge(deck, rows)[1][0]


def test_vector_length_matches_dimensions():
    assert len(parse_attrs("R3", ATTRS)) == N_DIM
