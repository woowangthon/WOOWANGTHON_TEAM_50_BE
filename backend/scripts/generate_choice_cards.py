"""방식·가치 축을 직접 묻는 선택형 카드(a_vs_b, situation) 초안 → data/cards.json.

    python -m scripts.generate_choice_cards --per-axis 8             # 축마다 8장이 되도록 채움
    python -m scripts.generate_choice_cards --per-axis 2 --axes team,impact
    python -m scripts.generate_choice_cards --per-axis 8 --dry-run   # API 호출 없이 첫 프롬프트만 출력

직업 자료에는 방식·가치 정보가 적어 직업 카드만으로는 이 축들이 비기 때문에, 중학생의 일상 상황으로 직접 묻는다.
축마다 한 요청으로 만들고, 규칙을 어긴 카드는 버린 뒤 모자란 만큼 오류와 함께 다시 요청한다.
축별 수량은 cards.json에서 세므로 끊겨도 이어서 돌리면 된다. 결과는 모두 source="llm", reviewed=false.
"""
import argparse
import json
import random
import sys
import time

from pydantic import BaseModel, Field, create_model

from scripts.common import BACKEND_DIR, DATA_DIR, read_env
from scripts.generate_cards import (
    CARDS_PATH,
    DEFAULT_MODEL,
    MAX_ATTEMPTS,
    PLACEHOLDER_ID,
    RUBRIC_PATH,
    Generator,
    gemini_generator,
    load_deck,
    next_card_id,
    save_deck,
)

sys.path.insert(0, str(BACKEND_DIR))
from app.content.loader import load_attributes  # noqa: E402
from app.content.models import MIN_OPTION_GAP, Attribute, Card, CardDeck  # noqa: E402

MIN_FOCUS_SCORE = 2       # 카드가 이 축을 묻는다고 볼 최소 점수

# 묻는 축 → 두 선택지가 갈리는 방향
AXIS_BRIEFS = {
    "team": "여럿이 함께 해내기 vs 혼자 집중해서 해내기",
    "field": "밖에서 몸을 움직이며 하기 vs 실내에서 앉아서 하기",
    "novelty": "처음 해 보는 새로운 방식에 도전하기 vs 익숙한 방식을 더 잘하게 다듬기",
    "autonomy": "순서와 방법을 스스로 정하기 vs 정해진 순서와 안내를 따르기",
    "impact": "결과가 다른 사람이나 사회에 도움이 되는 쪽 vs 내 실력이나 작품의 완성도가 높아지는 쪽",
}


# ---------- LLM 응답 형식 ----------

def item_model(attributes: list[Attribute]) -> type[BaseModel]:
    Attrs = create_model("Attrs", **{a.code: (int, ...) for a in attributes})
    Option = create_model("GeneratedOption", label=(str, Field(description="선택지, 40자 이내")), attrs=(Attrs, ...))
    return create_model(
        "GeneratedChoiceCard",
        type=(str, Field(description="a_vs_b 또는 situation")),
        title=(str, Field(description="카드 제목, 30자 이내")),
        body=(str, Field(description="상황 설명, 100자 이내")),
        attrs=(Attrs, ...),
        options=(list[Option], ...),
    )


def batch_model(attributes: list[Attribute]) -> type[BaseModel]:
    return create_model("GeneratedChoiceCards", cards=(list[item_model(attributes)], ...))


# ---------- 프롬프트 ----------

def build_prompt(
    axis: Attribute, count: int, attributes: list[Attribute], rubric: str,
    used_titles: list[str], errors: list[str],
) -> str:
    codes = ", ".join(a.code for a in attributes)
    dims = "\n".join(f"- {a.code} ({a.label}): {a.definition} / 아닌 경우: {a.not_this}" for a in attributes)
    used = "\n".join(f"- {t}" for t in used_titles) or "(없음)"
    feedback = ""
    if errors:
        details = "\n".join(f"- {e}" for e in errors)
        feedback = f"\n## 이전 답변이 규칙을 어겼다\n{details}\n위 오류를 반복하지 말고 새 카드로 답하라.\n"
    return f"""너는 중학생용 진로 탐색 앱의 카드 작가이자 태깅 담당자다.
학생의 "{axis.label}"({axis.code}) 성향을 알아보는 선택형 카드 {count}장을 만들어라.

이 축의 정의: {axis.definition}
선택지가 갈리는 방향: {AXIS_BRIEFS[axis.code]}

## 카드 쓰기
- 학교생활·동아리·방과후·주말처럼 중학생이 실제로 겪는 상황으로 쓴다. 직업 이름이나 진로 용어는 쓰지 않는다.
- 카드마다 상황과 소재를 다르게 한다.
- type: a_vs_b(선택지 정확히 2개) 또는 situation(선택지 2~4개, 구체적인 상황을 body에 설명). 두 유형을 고르게 섞는다.
- title: 30자 이내. body: 해요체, 100자 이내. 선택지 label: 40자 이내.
- 모든 선택지가 똑같이 매력적이어야 한다. 한쪽이 더 착하거나 더 멋져 보이거나 정답처럼 보이면 안 된다.
- 성별·외모·지역·가정 형편과 엮지 않는다.
- 아래 제목과 겹치지 않게 쓴다.
{used}

## 태깅
- 카드 attrs: 이 질문이 어떤 축을 묻는지. {axis.code}는 반드시 {MIN_FOCUS_SCORE}점 이상.
  선택지끼리 점수가 실제로 다른 축만 넣는다. 모든 선택지가 같은 점수인 축은 카드 attrs에서 0점으로 둔다.
- 선택지 attrs: 그 선택지를 고른 학생의 성향. 선택지에 쓰인 내용만 보고 매긴다.
- 선택지들은 {axis.code}에서 최소 {MIN_OPTION_GAP}점 이상 차이 나야 한다.
- 혼자 한다는 것만으로 autonomy를 주지 않는다. autonomy는 순서나 방법을 스스로 정하는 내용이 선택지에 있을 때만 준다.
  혼자 하는 선택지는 그 활동의 성격(탐구면 I, 표현이면 A, 만들기면 R)으로 점수를 준다.
- C는 규칙과 절차를 따라 정확히 정리·관리하는 내용일 때만 준다. 익숙한 일을 연습하거나 완성도를 높이는 것만으로 C를 주지 않는다.
- 카드와 각 선택지 모두 0이 아닌 축은 2~4개, 3점은 최대 2개.
  {axis.code}가 0점인 선택지도 그 선택지의 성향을 나타내는 다른 축 2개 이상에 점수를 준다.
- attrs의 키는 정확히 이 {len(attributes)}개: {codes}

차원 정의:
{dims}

태깅 루브릭 전문:
<rubric>
{rubric}
</rubric>
{feedback}
JSON으로만 답하라: {{"cards": [{{"type": "a_vs_b", "title": "...", "body": "...", "attrs": {{"R": 0, ...}}, "options": [{{"label": "...", "attrs": {{"R": 0, ...}}}}, ...]}}, ...]}}
"""


# ---------- 변환·검증 (순수 함수, 테스트 대상) ----------

def option_gap(card: Card, idx: int) -> int:
    scores = [o.attrs[idx] for o in card.options]
    return max(scores, default=0) - min(scores, default=0)


def covers(card: Card, idx: int) -> bool:
    """이 카드가 해당 축을 실제로 묻고, 선택지로 구분할 수 있는가."""
    return bool(card.options) and card.attrs[idx] >= MIN_FOCUS_SCORE and option_gap(card, idx) >= MIN_OPTION_GAP


def flat_axes(card: Card, attributes: list[Attribute]) -> list[str]:
    """카드가 묻는다고 표시했지만 선택지로 구분되지 않는 축."""
    return [a.code for a in attributes if card.attrs[a.idx] > 0 and option_gap(card, a.idx) == 0]


def with_id(card: Card, card_id: str) -> Card:
    options = [
        o.model_copy(update={"id": f"{card_id}-{chr(ord('a') + i)}"}) for i, o in enumerate(card.options)
    ]
    return card.model_copy(update={"id": card_id, "options": options})


def to_choice_card(entry: dict, axis: Attribute, attributes: list[Attribute]) -> Card:
    """LLM 응답 항목 → 검증된 Card. 규칙 위반 시 ValueError (오류 문구는 재요청에 그대로 씀)."""
    out = item_model(attributes).model_validate(entry)

    def vector(attrs: BaseModel) -> list[int]:
        scores = attrs.model_dump()
        return [scores[a.code] for a in attributes]

    options = [{"label": o.label.strip(), "attrs": vector(o.attrs)} for o in out.options]
    random.Random(out.title).shuffle(options)    # 성향이 강한 쪽이 항상 앞에 오지 않도록
    card = Card(
        id=PLACEHOLDER_ID,
        type=out.type,
        title=out.title.strip(),
        body=" ".join(out.body.split()),
        attrs=vector(out.attrs),
        options=[
            {"id": f"{PLACEHOLDER_ID}-{chr(ord('a') + i)}", **o} for i, o in enumerate(options)
        ],
        source="llm",
        reviewed=False,
    )
    if not covers(card, axis.idx):
        raise ValueError(
            f"'{card.title}': 카드의 {axis.code}가 {MIN_FOCUS_SCORE}점 이상이고 "
            f"선택지끼리 {axis.code}에서 {MIN_OPTION_GAP}점 이상 차이 나야 함"
        )
    flat = flat_axes(card, attributes)
    if flat:
        raise ValueError(f"'{card.title}': 카드 attrs의 {flat}는 선택지끼리 점수가 같으므로 0점으로 둘 것")
    return card


def parse_cards(text: str) -> list[dict]:
    try:
        cards = json.loads(text)["cards"]
    except json.JSONDecodeError as e:
        raise ValueError(f"JSON 파싱 실패: {e}") from e
    except (KeyError, TypeError) as e:
        raise ValueError(f"응답 형식 오류: {e!r}") from e
    if not isinstance(cards, list):
        raise ValueError("응답 형식 오류: cards는 배열이어야 함")
    return cards


def needs(deck: CardDeck, axes: list[Attribute], per_axis: int) -> list[tuple[Attribute, int]]:
    """(축, 더 만들 장수). 이미 충분한 축은 뺀다."""
    out = []
    for axis in axes:
        have = sum(covers(c, axis.idx) for c in deck.cards)
        if have < per_axis:
            out.append((axis, per_axis - have))
    return out


# ---------- 생성 루프 ----------

def generate_for_axis(
    generate: Generator, axis: Attribute, count: int, attributes: list[Attribute],
    rubric: str, used_titles: set[str],
) -> tuple[list[Card], list[str]]:
    """→ (임시 id의 카드들, 마지막 시도의 오류)."""
    cards: list[Card] = []
    titles = set(used_titles)
    errors: list[str] = []
    for _ in range(MAX_ATTEMPTS):
        if len(cards) >= count:
            break
        prompt = build_prompt(axis, count - len(cards), attributes, rubric, sorted(titles), errors)
        errors = []
        try:
            entries = parse_cards(generate(prompt))
        except ValueError as e:
            errors = [str(e)]
            continue
        for entry in entries:
            if len(cards) >= count:
                break
            try:
                card = to_choice_card(entry, axis, attributes)
            except ValueError as e:
                errors.append(str(e)[:300])
                continue
            if card.title in titles:
                errors.append(f"'{card.title}': 이미 있는 제목")
                continue
            titles.add(card.title)
            cards.append(card)
    return cards, errors


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--per-axis", type=int, required=True, help="축마다 카드가 N장이 되도록 채움")
    ap.add_argument("--axes", default=",".join(AXIS_BRIEFS), help="쉼표 구분 축 코드")
    ap.add_argument("--sleep", type=float, default=4.0, help="요청 간 대기(초)")
    ap.add_argument("--dry-run", action="store_true", help="API 호출 없이 대상과 첫 프롬프트만 출력")
    args = ap.parse_args()

    codes = [c.strip() for c in args.axes.split(",") if c.strip()]
    unknown = [c for c in codes if c not in AXIS_BRIEFS]
    if unknown:
        ap.error(f"지원하지 않는 축: {unknown} (가능: {list(AXIS_BRIEFS)})")

    attributes = load_attributes(DATA_DIR / "attributes.csv")
    by_code = {a.code: a for a in attributes}
    rubric = RUBRIC_PATH.read_text(encoding="utf-8")
    deck = load_deck()
    targets = needs(deck, [by_code[c] for c in codes], args.per_axis)
    if not targets:
        print("새로 만들 카드가 없습니다.", file=sys.stderr)
        return
    print("대상: " + ", ".join(f"{a.code} {n}장" for a, n in targets), file=sys.stderr)

    titles = {c.title for c in deck.cards if c.options}
    if args.dry_run:
        axis, count = targets[0]
        print(build_prompt(axis, count, attributes, rubric, sorted(titles), []))
        return

    model = read_env("GEMINI_MODEL", required=False, default=DEFAULT_MODEL)
    generate = gemini_generator(read_env("GEMINI_API_KEY"), model, batch_model(attributes))
    print(f"모델: {model}", file=sys.stderr)

    made = 0
    for i, (axis, count) in enumerate(targets, 1):
        try:
            cards, errors = generate_for_axis(generate, axis, count, attributes, rubric, titles)
        except Exception as e:          # 네트워크·할당량 오류 등: 앞 축의 카드는 이미 저장됨
            print(f"  ! {axis.code} API 오류로 중단: {type(e).__name__}: {e}", file=sys.stderr)
            break
        for card in cards:
            placed = with_id(card, next_card_id(deck))
            deck.cards.append(placed)
            titles.add(placed.title)
            made += 1
            print(f"  [{axis.code}] {placed.id} {placed.type} {placed.title}", file=sys.stderr)
        save_deck(deck)
        if len(cards) < count:
            print(f"  ! {axis.code}: {count}장 중 {len(cards)}장만 생성. 마지막 오류: {errors[:3]}", file=sys.stderr)
        if i < len(targets):
            time.sleep(args.sleep)

    print(f"완료: {made}장 추가 → {CARDS_PATH} (모두 reviewed=false, 검수 필요)", file=sys.stderr)


if __name__ == "__main__":
    main()
