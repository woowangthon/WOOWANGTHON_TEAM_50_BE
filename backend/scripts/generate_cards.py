"""커리어넷 원본 캐시 → LLM(Gemini) → 직업 카드(job_day) 초안 → data/cards.json.

    python -m scripts.generate_cards --per-field 3          # 분야마다 3개씩 (조회수 높은 직업부터)
    python -m scripts.generate_cards --jobs 15,5            # 직업코드 지정
    python -m scripts.generate_cards --jobs 68,206 --refresh  # 미검수 LLM 카드를 같은 id로 다시 생성
    python -m scripts.generate_cards --per-field 3 --dry-run  # API 호출 없이 첫 프롬프트만 출력

흐름
1. 아직 카드가 없는 직업을 고른다 (분야별 균등, 커리어넷 조회수 순).
2. --batch 개씩 묶어 직업 원본(data/raw/careernet/{코드}.json) + 차원 정의 + 루브릭(docs/dimensions.md)으로 프롬프트를 만든다.
3. LLM이 직업마다 본문을 먼저 쓰고, 그 본문만 보고 성향 축을 태깅한다 (루브릭 규칙 3).
4. app.content.Card 규칙으로 직업별로 검증한다. 실패하거나 응답에서 빠진 직업만 오류와 함께 다시 요청한다.
5. 통과한 카드만 source="llm", reviewed=false 로 cards.json 에 넣는다. 묶음마다 저장하므로 중간에 끊겨도 이어서 돌리면 된다.

- 응답 원본은 data/raw/llm/{코드}.json 에 직업별로 캐시한다 (git 제외). 캐시가 있으면 다시 호출하지 않는다.
- --refresh 는 --jobs 와 함께 쓴다. 수동 작성 카드와 검수 끝난 카드는 교체하지 않는다.
- 키: GEMINI_API_KEY, 모델: GEMINI_MODEL (기본값 DEFAULT_MODEL). 환경 변수 또는 backend/.env.
"""
import argparse
import json
import sys
import time
from collections import defaultdict
from collections.abc import Callable
from pathlib import Path

from pydantic import BaseModel, Field, ValidationError, create_model

from scripts.common import BACKEND_DIR, DATA_DIR, RAW_DIR, read_env

sys.path.insert(0, str(BACKEND_DIR))
from app.content.loader import load_attributes, load_jobs  # noqa: E402
from app.content.models import Attribute, Card, CardDeck, Job  # noqa: E402

CARDS_PATH = DATA_DIR / "cards.json"
CAREERNET_DIR = RAW_DIR / "careernet"
LLM_CACHE_DIR = RAW_DIR / "llm"
RUBRIC_PATH = BACKEND_DIR.parent / "docs" / "dimensions.md"

DEFAULT_MODEL = "gemini-3.8-flash"
TEMPERATURE = 0.7
MAX_ATTEMPTS = 3          # 첫 시도 + 검증 실패 시 재요청
BATCH_SIZE = 10           # 한 요청에 넣을 직업 수 (무료 등급은 요청 수로 제한됨)
PLACEHOLDER_ID = "c0000"  # 검증용 임시 id. 실제 id는 place_card가 붙인다
ITEMS_PER_SECTION = 5     # 원본에서 섹션마다 넣을 문장 수
TRANSIENT_CODES = {429, 500, 503}   # 할당량·서버 과부하: 기다렸다 재시도
API_RETRIES = 4
API_BACKOFF = 5.0         # 초. 5 → 10 → 20 → 40

# prompt → JSON 문자열. 테스트에서는 가짜 함수를 넣는다.
Generator = Callable[[str], str]


# ---------- LLM 응답 형식 ----------

def response_model(attributes: list[Attribute]) -> type[BaseModel]:
    """{title, body, attrs: {R: 0~3, I: ..., impact: ...}} 형태.

    LLM은 12칸 배열보다 이름 붙은 필드를 더 정확히 채우므로 축 코드를 키로 쓴다.
    """
    Attrs = create_model("Attrs", **{a.code: (int, ...) for a in attributes})
    return create_model(
        "GeneratedCard",
        title=(str, Field(description="카드 제목, 30자 이내")),
        body=(str, Field(description="카드 본문, 150자 이내")),
        attrs=(Attrs, ...),
    )


def batch_model(attributes: list[Attribute]) -> type[BaseModel]:
    """{cards: [{job_id, title, body, attrs}, ...]} 형태. LLM 응답 스키마로 쓴다."""
    Item = create_model("GeneratedJobCard", __base__=response_model(attributes), job_id=(int, ...))
    return create_model("GeneratedCards", cards=(list[Item], ...))


# ---------- 프롬프트 ----------

def _texts(items: list | None, key: str) -> list[str]:
    out = []
    for it in items or []:
        t = " ".join(str(it.get(key, "")).split()) if isinstance(it, dict) else ""
        if t:
            out.append(t)
    return out[:ITEMS_PER_SECTION]


def job_material(job: Job, raw: dict) -> str:
    """원본 상세 응답에서 카드 재료가 될 부분만 뽑는다."""
    perform = raw.get("performList") or {}
    # 일부 직업은 environment가 [None]으로 온다 → dict만 남김
    env = [e for e in perform.get("environment") or [] if isinstance(e, dict)]
    env.sort(key=lambda e: -(e.get("importance") or 0))
    sections = [
        ("하는 일", _texts(raw.get("workList"), "work")),
        ("이런 사람에게 맞아요", _texts(raw.get("interestList"), "interest")),
        ("필요한 적성", _texts(raw.get("aptitudeList"), "aptitude")),
        ("업무 환경 (중요도 순)", _texts(env, "environment")),
    ]
    lines = [f"직업명: {job.name}", f"분야: {job.field or '미분류'}"]
    for title, items in sections:
        if items:
            lines.append(f"\n[{title}]")
            lines += [f"- {t}" for t in items]
    return "\n".join(lines)


def build_prompt(items: list[tuple[Job, dict]], attributes: list[Attribute], rubric: str) -> str:
    codes = ", ".join(a.code for a in attributes)
    dims = "\n".join(
        f"- {a.code} ({a.label}): {a.definition} / 아닌 경우: {a.not_this}" for a in attributes
    )
    jobs = "\n\n".join(f'<job id="{j.id}">\n{job_material(j, raw)}\n</job>' for j, raw in items)
    return f"""너는 중학생용 진로 탐색 앱의 카드 작가이자 태깅 담당자다.
아래 직업 {len(items)}개 각각에 대해 "직업명의 하루" 카드를 한 장씩 만들고, {len(attributes)}개 축 점수를 매겨라.
직업마다 자기 자료만 사용하고, 카드끼리 문장 구조가 똑같아지지 않게 쓴다.

## 1단계: 카드 쓰기
- title: "직업명의 하루" 형식. 30자를 넘으면 직업명을 줄여라.
- body: 이 직업인의 하루를 아침·오후·저녁 같은 장면 2~3개로 보여준다.
  - 중학생이 바로 떠올릴 수 있는 구체적인 행동으로 쓴다 ("~을 분석한다"보다 "~을 현미경으로 들여다봐요").
  - 장면 중 하나는 **무엇을 하는지뿐 아니라 어떻게 일하는지**를 보여 준다. 자료(특히 [업무 환경])에 근거가 있는 것만 고른다.
    - 누구와: 동료들과 함께 하는지, 혼자 집중하는지 (team)
    - 얼마나 달라지는지: 날마다 상황이 다르고 새로 배워야 하는지 (novelty)
    - 누가 정하는지: 순서·방법을 스스로 정하는지, 정해진 절차를 따르는지 (autonomy)
    - 누구에게 도움이 되는지: 일의 결과로 누가 안전해지거나 편해지는지 (impact)
    - 이끄는지: 사람들을 설득하거나 이끄는 장면이 있는지 (E)
  - 해요체, 150자 이내, 2~3문장.
  - 연봉·학력·성별·외모·지역 언급 금지. 직업을 미화하거나 깎아내리지 않는다.
  - 자료에 없는 사실을 지어내지 않는다.

## 2단계: 태깅
- 1단계에서 쓴 **body에 나온 내용만** 보고 점수를 매긴다. 직업 자료나 배경지식으로 점수를 올리지 않는다.
- 0이 아닌 축은 2~4개, 3점은 최대 2개.
- 흥미 축(R I A S E C)만 채우고 끝내지 말 것. body에 드러난 방식·가치 축(team field novelty autonomy impact)도 빠짐없이 반영한다.
- attrs의 키는 정확히 이 {len(attributes)}개: {codes}

차원 정의:
{dims}

태깅 루브릭 전문:
<rubric>
{rubric}
</rubric>

## 직업 자료 (커리어넷 직업백과)
{jobs}

모든 직업에 대해 빠짐없이 답하고, job_id는 <job id="...">의 값을 그대로 쓴다.
JSON으로만 답하라: {{"cards": [{{"job_id": 0, "title": "...", "body": "...", "attrs": {{"R": 0, ...}}}}, ...]}}
"""


def retry_prompt(prompt: str, previous: dict[int, str], errors: dict[int, str]) -> str:
    details = "\n".join(
        f"- job_id {job_id}: {error}\n  이전 답변: {previous.get(job_id, '없음')}"
        for job_id, error in errors.items()
    )
    return f"{prompt}\n\n## 이전 답변이 규칙을 어겼다\n{details}\n\n오류를 고친 JSON을 다시 답하라."


# ---------- 변환·검증 (순수 함수, 테스트 대상) ----------

def to_card(text: str, card_id: str, job: Job, attributes: list[Attribute]) -> Card:
    """LLM 응답 JSON → 검증된 Card. 규칙 위반 시 ValueError (오류 문구는 재요청에 그대로 씀)."""
    try:
        data = json.loads(text)
    except json.JSONDecodeError as e:
        raise ValueError(f"JSON 파싱 실패: {e}") from e
    try:
        out = response_model(attributes).model_validate(data)
        attrs = out.attrs.model_dump()
        return Card(
            id=card_id,
            type="job_day",
            job_id=job.id,
            title=out.title.strip(),
            body=" ".join(out.body.split()),
            attrs=[attrs[a.code] for a in attributes],
            source="llm",
            reviewed=False,
        )
    except ValidationError as e:
        codes = [a.code for a in attributes]

        def where(loc: tuple) -> str:
            # Card의 attrs.7 → attrs.field (LLM에게 돌려줄 때 축 이름이 더 알아듣기 쉬움)
            if len(loc) == 2 and loc[0] == "attrs" and isinstance(loc[1], int):
                return f"attrs.{codes[loc[1]]}"
            return ".".join(map(str, loc)) or "카드"

        raise ValueError("; ".join(f"{where(err['loc'])}: {err['msg']}" for err in e.errors())) from e


def parse_batch(text: str) -> dict[int, dict]:
    """배치 응답 JSON → {job_id: 카드 항목}. 형식이 틀리면 ValueError."""
    try:
        entries = json.loads(text)["cards"]
        return {entry["job_id"]: entry for entry in reversed(entries)}   # 중복이면 먼저 나온 항목
    except json.JSONDecodeError as e:
        raise ValueError(f"JSON 파싱 실패: {e}") from e
    except (KeyError, TypeError) as e:
        raise ValueError(f"배치 응답 형식 오류: {e!r}") from e


def next_card_id(deck: CardDeck) -> str:
    nums = [int(c.id[1:]) for c in deck.cards]
    return f"c{max(nums, default=0) + 1:04d}"


def place_card(deck: CardDeck, card: Card) -> Card:
    """같은 직업 카드가 있으면 그 id로 교체하고, 없으면 새 id로 추가한다."""
    for i, existing in enumerate(deck.cards):
        if existing.job_id == card.job_id:
            deck.cards[i] = card.model_copy(update={"id": existing.id})
            return deck.cards[i]
    deck.cards.append(card.model_copy(update={"id": next_card_id(deck)}))
    return deck.cards[-1]


def select_jobs(
    jobs: dict[int, Job],
    deck: CardDeck,
    popularity: dict[int, int],
    per_field: int | None = None,
    job_ids: list[int] | None = None,
    limit: int | None = None,
    refresh: bool = False,
) -> list[Job]:
    """카드가 아직 없는 직업 중에서 고른다. per_field는 '분야별 카드 총수'를 이 값까지 채운다.

    refresh면 job_ids 중 미검수 LLM 카드도 다시 만든다.
    """
    has_card = {c.job_id for c in deck.cards if c.job_id is not None}
    if job_ids:
        missing = [i for i in job_ids if i not in jobs]
        if missing:
            raise ValueError(f"jobs.json에 없는 직업코드: {missing}")
        locked = {c.job_id for c in deck.cards if c.source != "llm" or c.reviewed} if refresh else has_card
        picked = [jobs[i] for i in job_ids if i not in locked]
    else:
        existing: dict[str, int] = defaultdict(int)
        for jid in has_card:
            if jid in jobs:
                existing[jobs[jid].field or "미분류"] += 1
        by_field: dict[str, list[Job]] = defaultdict(list)
        for j in sorted(jobs.values(), key=lambda j: (-popularity.get(j.id, 0), j.id)):
            if j.id not in has_card:
                by_field[j.field or "미분류"].append(j)
        picked = []
        for f in sorted(by_field):
            need = max(0, (per_field or 0) - existing[f])
            picked += by_field[f][:need]
    return picked[:limit] if limit else picked


# ---------- 파일 ----------

def load_deck(path: Path = CARDS_PATH) -> CardDeck:
    return CardDeck.model_validate_json(path.read_text(encoding="utf-8"))


def dump_deck(deck: CardDeck) -> str:
    """사람이 diff로 검수하기 쉽게: 카드는 여러 줄, attrs 배열과 선택지는 한 줄."""
    def val(v) -> str:
        return json.dumps(v, ensure_ascii=False)

    cards = []
    for card in deck.cards:
        c = card.model_dump(mode="json")
        # 기본값인 필드는 생략 (손으로 쓴 기존 카드 형식과 맞춤)
        if c["job_id"] is None:
            del c["job_id"]
        if c["version"] == 1:
            del c["version"]
        lines = []
        for k, v in c.items():
            if k == "options":
                if not v:
                    continue
                opts = ",\n".join(f"        {{ {val(o)[1:-1]} }}" for o in v)
                lines.append(f'      "options": [\n{opts}\n      ]')
            else:
                lines.append(f"      {val(k)}: {val(v)}")
        cards.append("    {\n" + ",\n".join(lines) + "\n    }")
    return (
        "{\n"
        f'  "schema_version": {deck.schema_version},\n'
        '  "cards": [\n' + ",\n".join(cards) + "\n  ]\n}\n"
    )


def save_deck(deck: CardDeck, path: Path = CARDS_PATH) -> None:
    CardDeck.model_validate(deck.model_dump())       # 저장 전 전체 재검증 (id 중복 등)
    path.write_text(dump_deck(deck), encoding="utf-8")


def load_popularity() -> dict[int, int]:
    p = CAREERNET_DIR / "_list.json"
    if not p.exists():
        return {}
    return {int(it["job_cd"]): int(it.get("views") or 0) for it in json.loads(p.read_text(encoding="utf-8"))}


# ---------- Gemini ----------

def gemini_generator(
    api_key: str, model: str, schema: type[BaseModel], temperature: float = TEMPERATURE
) -> Generator:
    """프롬프트 → JSON 문자열. 429·5xx 일시 오류는 지수 백오프로 재시도."""
    try:
        from google import genai
        from google.genai import types
    except ImportError:
        sys.exit("google-genai 가 없습니다: pip install -r requirements.txt")

    client = genai.Client(api_key=api_key)
    config = types.GenerateContentConfig(
        temperature=temperature,
        response_mime_type="application/json",
        response_schema=schema,
    )

    def generate(prompt: str) -> str:
        for attempt in range(API_RETRIES + 1):
            try:
                resp = client.models.generate_content(model=model, contents=prompt, config=config)
                break
            except Exception as e:
                code = getattr(e, "code", None)
                # 하루 한도(PerDay)는 기다려도 안 풀림 → 바로 중단 (분당 한도만 재시도)
                if code not in TRANSIENT_CODES or "PerDay" in str(e) or attempt == API_RETRIES:
                    raise
                wait = API_BACKOFF * 2 ** attempt
                print(f"    … {code} 일시 오류, {wait:.0f}초 후 재시도 ({attempt + 1}/{API_RETRIES})",
                      file=sys.stderr)
                time.sleep(wait)
        if not resp.text:
            raise ValueError("빈 응답 (안전 필터 차단 가능성)")
        return resp.text

    return generate


# ---------- 생성 루프 ----------

def _from_cache(path: Path, job: Job, attributes: list[Attribute]) -> Card | None:
    if not path.exists():
        return None
    try:
        return to_card(json.loads(path.read_text(encoding="utf-8"))["response"], PLACEHOLDER_ID, job, attributes)
    except (ValueError, KeyError):
        return None           # 규칙이 바뀌어 무효가 된 캐시는 다시 생성


def generate_batch(
    generate: Generator, items: list[tuple[Job, dict]],
    attributes: list[Attribute], rubric: str, cache_dir: Path,
) -> tuple[dict[int, Card], dict[int, str]]:
    """→ (직업별 카드, 끝까지 실패한 직업의 마지막 오류). 카드 id는 임시값이다."""
    done: dict[int, Card] = {}
    todo: list[tuple[Job, dict]] = []
    for job, raw in items:
        card = _from_cache(cache_dir / f"{job.id}.json", job, attributes)
        if card:
            done[job.id] = card
        else:
            todo.append((job, raw))

    errors: dict[int, str] = {}
    previous: dict[int, str] = {}
    for _ in range(MAX_ATTEMPTS):
        if not todo:
            break
        base = build_prompt(todo, attributes, rubric)
        prompt = retry_prompt(base, previous, errors) if errors else base
        errors, previous = {}, {}
        try:
            entries = parse_batch(generate(prompt))
        except ValueError as e:
            errors = {job.id: str(e) for job, _ in todo}
            continue

        still: list[tuple[Job, dict]] = []
        for job, raw in todo:
            if job.id not in entries:
                errors[job.id] = "응답에서 빠짐"
                still.append((job, raw))
                continue
            text = json.dumps(entries[job.id], ensure_ascii=False)
            try:
                done[job.id] = to_card(text, PLACEHOLDER_ID, job, attributes)
            except ValueError as e:
                errors[job.id], previous[job.id] = str(e), text
                still.append((job, raw))
                continue
            (cache_dir / f"{job.id}.json").write_text(
                json.dumps({"job_id": job.id, "response": text}, ensure_ascii=False, indent=1),
                encoding="utf-8",
            )
        todo = still
    return done, errors


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    pick = ap.add_mutually_exclusive_group(required=True)
    pick.add_argument("--per-field", type=int, help="분야마다 직업 카드가 N장이 되도록 채움")
    pick.add_argument("--jobs", help="직업코드 쉼표 구분 (예: 15,5)")
    ap.add_argument("--limit", type=int, help="이번 실행에서 최대 N장")
    ap.add_argument("--batch", type=int, default=BATCH_SIZE, help="한 요청에 넣을 직업 수")
    ap.add_argument("--sleep", type=float, default=4.0, help="요청 간 대기(초). 무료 등급 분당 한도 대비")
    ap.add_argument("--refresh", action="store_true", help="--jobs의 미검수 LLM 카드를 같은 id로 다시 생성")
    ap.add_argument("--dry-run", action="store_true", help="API 호출 없이 대상 목록과 첫 프롬프트만 출력")
    args = ap.parse_args()
    if args.refresh and not args.jobs:
        ap.error("--refresh 는 --jobs 와 함께 쓰세요")
    if args.batch < 1:
        ap.error("--batch 는 1 이상이어야 합니다")

    attributes = load_attributes(DATA_DIR / "attributes.csv")
    jobs = load_jobs(DATA_DIR / "jobs.json")
    if not jobs:
        sys.exit("data/jobs.json 이 없습니다. 먼저 python -m scripts.fetch_careernet")
    rubric = RUBRIC_PATH.read_text(encoding="utf-8")
    deck = load_deck()

    job_ids = [int(x) for x in args.jobs.split(",")] if args.jobs else None
    try:
        targets = select_jobs(jobs, deck, load_popularity(), args.per_field, job_ids, args.limit, args.refresh)
    except ValueError as e:
        sys.exit(str(e))
    no_raw = [j for j in targets if not (CAREERNET_DIR / f"{j.id}.json").exists()]
    for j in no_raw:
        print(f"  ! {j.name}({j.id}): 커리어넷 원본 캐시 없음, 건너뜀", file=sys.stderr)
    targets = [j for j in targets if j not in no_raw]
    if not targets:
        print("새로 만들 카드가 없습니다.", file=sys.stderr)
        return

    print(f"대상 {len(targets)}개: " + ", ".join(f"{j.name}({j.id})" for j in targets[:10])
          + (" …" if len(targets) > 10 else ""), file=sys.stderr)

    def load_item(job: Job) -> tuple[Job, dict]:
        return job, json.loads((CAREERNET_DIR / f"{job.id}.json").read_text(encoding="utf-8"))

    if args.dry_run:
        print(build_prompt([load_item(j) for j in targets[:args.batch]], attributes, rubric))
        return

    model = read_env("GEMINI_MODEL", required=False, default=DEFAULT_MODEL)
    generate = gemini_generator(read_env("GEMINI_API_KEY"), model, batch_model(attributes))
    LLM_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    print(f"모델: {model}", file=sys.stderr)

    if args.refresh:
        for job in targets:
            (LLM_CACHE_DIR / f"{job.id}.json").unlink(missing_ok=True)

    made, failed = 0, []
    chunks = [targets[i:i + args.batch] for i in range(0, len(targets), args.batch)]
    for b, chunk in enumerate(chunks, 1):
        used_api = any(not (LLM_CACHE_DIR / f"{j.id}.json").exists() for j in chunk)
        try:
            cards, errors = generate_batch(generate, [load_item(j) for j in chunk], attributes, rubric, LLM_CACHE_DIR)
        except Exception as e:          # 네트워크·할당량 오류 등: 통과한 응답은 캐시에 남아 다음 실행에 쓰인다
            print(f"  ! 묶음 {b}/{len(chunks)} API 오류로 중단: {type(e).__name__}: {e}", file=sys.stderr)
            break
        for job in chunk:
            if job.id in cards:
                card = place_card(deck, cards[job.id])
                made += 1
                print(f"  [{b}/{len(chunks)}] {card.id} {card.title} {card.attrs}", file=sys.stderr)
            else:
                failed.append(job.id)
                print(f"  [{b}/{len(chunks)}] 실패 {job.name}({job.id}): {errors.get(job.id)}", file=sys.stderr)
        save_deck(deck)
        if used_api and b < len(chunks):
            time.sleep(args.sleep)

    print(f"완료: {made}장 저장 → {CARDS_PATH} (모두 reviewed=false, 검수 필요)", file=sys.stderr)
    if failed:
        print(f"실패 {len(failed)}개 (다시 돌리면 재시도): {failed}", file=sys.stderr)


if __name__ == "__main__":
    main()
