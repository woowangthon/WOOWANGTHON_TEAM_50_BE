"""카드 검수용 CSV 내보내기 / 되돌려 넣기.

    python -m scripts.review_cards export                     # data/raw/review/cards.csv 로 내보내기
    python -m scripts.review_cards export --unreviewed-only
    python -m scripts.review_cards import data/raw/review/cards.csv --dry-run   # 바뀔 내용만 확인
    python -m scripts.review_cards import data/raw/review/cards.csv

검수자가 고칠 수 있는 칸: reviewed, title, body, attrs, option_x, option_x_attrs, note
- attrs 형식: "R3 field2" (축 코드 + 0~3 점수, 없는 축은 0)
- reviewed: true/false, 1/0, y/n, o/x (엑셀의 TRUE/FALSE도 가능)
- note는 검수 메모로, cards.json에 저장되지 않는다.
- 카드를 빼고 싶으면 reviewed를 false로 둔다. 운영에서는 CONTENT_REVIEWED_ONLY=true로 검수된 카드만 노출한다.

되돌려 넣을 때 카드 전체를 다시 검증하고, 오류가 하나라도 있으면 아무것도 저장하지 않는다.
CSV에 없는 카드는 그대로 둔다. 카드나 선택지의 추가·삭제는 하지 않는다.
"""
import argparse
import csv
import re
import sys
from pathlib import Path

from pydantic import ValidationError

from scripts.common import BACKEND_DIR, DATA_DIR, RAW_DIR
from scripts.generate_cards import CARDS_PATH, load_deck, save_deck

sys.path.insert(0, str(BACKEND_DIR))
from app.content.loader import load_attributes, load_jobs  # noqa: E402
from app.content.models import Attribute, Card, CardDeck, Job  # noqa: E402

DEFAULT_OUT = RAW_DIR / "review" / "cards.csv"
MAX_OPTIONS = 4
OPTION_KEYS = "abcd"
TRUE_VALUES = {"true", "1", "y", "yes", "o"}
FALSE_VALUES = {"false", "0", "n", "no", "x"}
TOKEN = re.compile(r"^([A-Za-z]+)([0-3])$")

COLUMNS = [
    "id", "type", "job", "field", "reviewed", "title", "body", "attrs",
    *[c for k in OPTION_KEYS for c in (f"option_{k}", f"option_{k}_attrs")],
    "note",
]


# ---------- 변환 (순수 함수, 테스트 대상) ----------

def format_attrs(vector: list[int], attributes: list[Attribute]) -> str:
    return " ".join(f"{a.code}{vector[a.idx]}" for a in attributes if vector[a.idx])


def parse_attrs(text: str, attributes: list[Attribute]) -> list[int]:
    by_code = {a.code: a for a in attributes}
    vector = [0] * len(attributes)
    seen: set[str] = set()
    for token in text.replace(",", " ").split():
        match = TOKEN.match(token)
        if not match or match[1] not in by_code:
            raise ValueError(f"점수 형식 오류: '{token}' (예: R3 field2)")
        if match[1] in seen:
            raise ValueError(f"같은 축이 두 번 있음: {match[1]}")
        seen.add(match[1])
        vector[by_code[match[1]].idx] = int(match[2])
    return vector


def parse_reviewed(text: str) -> bool:
    value = text.strip().lower()
    if value in TRUE_VALUES:
        return True
    if value in FALSE_VALUES:
        return False
    raise ValueError(f"reviewed 값 오류: '{text}' (true 또는 false)")


def card_to_row(card: Card, jobs: dict[int, Job], attributes: list[Attribute]) -> dict[str, str]:
    job = jobs.get(card.job_id) if card.job_id is not None else None
    row = {column: "" for column in COLUMNS}
    row.update(
        id=card.id,
        type=card.type,
        job=job.name if job else "",
        field=(job.field or "") if job else "",
        reviewed="true" if card.reviewed else "false",
        title=card.title,
        body=card.body,
        attrs=format_attrs(card.attrs, attributes),
    )
    for key, option in zip(OPTION_KEYS, card.options):
        row[f"option_{key}"] = option.label
        row[f"option_{key}_attrs"] = format_attrs(option.attrs, attributes)
    return row


def apply_row(card: Card, row: dict[str, str], attributes: list[Attribute]) -> Card:
    """CSV 행을 반영한 새 Card. 규칙을 어기면 ValueError."""
    data = card.model_dump()
    data.update(
        reviewed=parse_reviewed(row["reviewed"]),
        title=row["title"].strip(),
        body=" ".join(row["body"].split()),
        attrs=parse_attrs(row["attrs"], attributes),
    )
    for i, key in enumerate(OPTION_KEYS):
        label = (row.get(f"option_{key}") or "").strip()
        if i >= len(card.options):
            if label:
                raise ValueError(f"선택지는 추가할 수 없음: option_{key}")
            continue
        if not label:
            raise ValueError(f"선택지는 비울 수 없음: option_{key}")
        data["options"][i].update(
            label=label, attrs=parse_attrs(row.get(f"option_{key}_attrs") or "", attributes)
        )
    return Card.model_validate(data)


def _describe(error: ValueError) -> str:
    if isinstance(error, ValidationError):
        return "; ".join(f"{'.'.join(map(str, e['loc'])) or '카드'}: {e['msg']}" for e in error.errors())
    return str(error)


def merge_rows(
    deck: CardDeck, rows: list[dict[str, str]], attributes: list[Attribute]
) -> tuple[CardDeck | None, list[str], list[str]]:
    """→ (새 덱 또는 오류 시 None, 오류 목록, 바뀐 카드 id). 입력 덱은 바꾸지 않는다."""
    index = {c.id: i for i, c in enumerate(deck.cards)}
    cards = list(deck.cards)
    errors: list[str] = []
    changed: list[str] = []
    seen: set[str] = set()

    for line, row in enumerate(rows, 2):      # 1행은 머리글
        card_id = (row.get("id") or "").strip()
        if card_id not in index:
            errors.append(f"{line}행: 없는 카드 id '{card_id}'")
            continue
        if card_id in seen:
            errors.append(f"{line}행: id 중복 '{card_id}'")
            continue
        seen.add(card_id)
        try:
            updated = apply_row(cards[index[card_id]], row, attributes)
        except (ValueError, KeyError) as e:
            detail = f"필수 칸 없음: {e}" if isinstance(e, KeyError) else _describe(e)
            errors.append(f"{line}행 {card_id}: {detail}")
            continue
        if updated != cards[index[card_id]]:
            cards[index[card_id]] = updated
            changed.append(card_id)

    if errors:
        return None, errors, []
    return CardDeck(schema_version=deck.schema_version, cards=cards), [], changed


# ---------- 파일 ----------

def write_csv(path: Path, rows: list[dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as f:      # BOM: 엑셀에서 한글이 깨지지 않게
        writer = csv.DictWriter(f, fieldnames=COLUMNS)
        writer.writeheader()
        writer.writerows(rows)


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8-sig", newline="") as f:
        return [{k: v or "" for k, v in row.items() if k} for row in csv.DictReader(f)]


# ---------- 실행 ----------

def cmd_export(args: argparse.Namespace) -> None:
    attributes = load_attributes(DATA_DIR / "attributes.csv")
    jobs = load_jobs(DATA_DIR / "jobs.json")
    cards = [c for c in load_deck().cards if not (args.unreviewed_only and c.reviewed)]
    write_csv(args.out, [card_to_row(c, jobs, attributes) for c in cards])
    print(f"{len(cards)}장 내보냄 → {args.out}", file=sys.stderr)


def cmd_import(args: argparse.Namespace) -> None:
    attributes = load_attributes(DATA_DIR / "attributes.csv")
    deck = load_deck()
    if not args.path.exists():
        sys.exit(f"파일이 없습니다: {args.path}")
    new_deck, errors, changed = merge_rows(deck, read_csv(args.path), attributes)
    if new_deck is None:
        for error in errors:
            print(f"  ! {error}", file=sys.stderr)
        sys.exit(f"오류 {len(errors)}건. 아무것도 저장하지 않았습니다.")

    reviewed = sum(c.reviewed for c in new_deck.cards)
    print(f"바뀐 카드 {len(changed)}장, 검수 완료 {reviewed}/{len(new_deck.cards)}장", file=sys.stderr)
    if changed:
        print("  " + ", ".join(changed[:30]) + (" …" if len(changed) > 30 else ""), file=sys.stderr)
    if args.dry_run:
        print("--dry-run: 저장하지 않았습니다.", file=sys.stderr)
        return
    if changed:
        save_deck(new_deck, CARDS_PATH)
        print(f"저장 → {CARDS_PATH}", file=sys.stderr)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="command", required=True)

    export = sub.add_parser("export", help="cards.json → 검수용 CSV")
    export.add_argument("--out", type=Path, default=DEFAULT_OUT)
    export.add_argument("--unreviewed-only", action="store_true", help="아직 검수하지 않은 카드만")
    export.set_defaults(run=cmd_export)

    imp = sub.add_parser("import", help="검수한 CSV → cards.json")
    imp.add_argument("path", type=Path)
    imp.add_argument("--dry-run", action="store_true", help="저장하지 않고 바뀔 내용만 출력")
    imp.set_defaults(run=cmd_import)

    args = ap.parse_args()
    args.run(args)


if __name__ == "__main__":
    main()
