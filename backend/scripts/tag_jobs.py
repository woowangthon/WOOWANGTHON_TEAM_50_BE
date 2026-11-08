"""직업 552개를 성향 축(attributes.csv)으로 직접 태깅해 분포를 분석한다 (분석용, 카드 아님).

    python -m scripts.tag_jobs                # 전체 태깅 (캐시된 직업은 건너뜀)
    python -m scripts.tag_jobs --limit 20     # 앞에서 20개만
    python -m scripts.tag_jobs --report       # API 호출 없이 결과 분석만 출력

카드 태깅(generate_cards)과의 차이
- 카드는 "카드 본문에 쓰인 내용만" 보고 태깅한다 (루브릭 규칙 3).
- 여기서는 커리어넷 직업 자료 전체(하는 일·적성·흥미·업무 환경)를 보고 직업 자체를 태깅한다.
  → "직업 자료에는 가치·방식 정보가 있는데 카드 본문이 못 담는 것인지" 를 확인하는 용도.
- 0이 아닌 축 2~4개, 3점 최대 2개 규칙은 카드와 같게 둔다 (카드 벡터 공간과 비교 가능하도록).

결과
- 직업별 응답: data/raw/llm_jobs/{코드}.json (git 제외, 재실행 시 재사용)
- 모아 놓은 표: data/analysis/job_tags.csv
"""
import argparse
import csv
import json
import sys
import time
from collections import Counter, defaultdict

from pydantic import BaseModel, Field, ValidationError, create_model

from scripts.common import DATA_DIR, RAW_DIR, read_env
from scripts.generate_cards import (
    CAREERNET_DIR, DEFAULT_MODEL, RUBRIC_PATH, Generator, gemini_generator, job_material,
)
from app.content.loader import load_attributes, load_jobs
from app.content.models import Attribute, Job, check_attrs

CACHE_DIR = RAW_DIR / "llm_jobs"
OUT_DIR = DATA_DIR / "analysis"
CSV_PATH = OUT_DIR / "job_tags.csv"

BATCH = 10
TEMPERATURE = 0.2       # 분석용이라 일관성 우선
MAX_ROUNDS = 3          # 배치에서 빠지거나 규칙을 어긴 직업은 다음 라운드에 다시


# ---------- 응답 형식 ----------

def batch_model(attributes: list[Attribute]) -> type[BaseModel]:
    Attrs = create_model("Attrs", **{a.code: (int, ...) for a in attributes})
    Tag = create_model(
        "JobTag",
        job_id=(int, ...),
        attrs=(Attrs, ...),
        reason=(str, Field(description="점수 근거 한 문장, 60자 이내")),
    )
    return create_model("JobTags", tags=(list[Tag], ...))


# ---------- 프롬프트 ----------

def build_batch_prompt(items: list[tuple[Job, dict]], attributes: list[Attribute], rubric: str) -> str:
    codes = ", ".join(a.code for a in attributes)
    dims = "\n".join(
        f"- {a.code} ({a.label}): {a.definition} / 아닌 경우: {a.not_this}" for a in attributes
    )
    jobs = "\n\n".join(f'<job id="{j.id}">\n{job_material(j, raw)}\n</job>' for j, raw in items)
    return f"""너는 진로 탐색 서비스의 직업 분류 담당자다.
아래 직업 {len(items)}개를 각각 {len(attributes)}개 축으로 점수 매겨라.

규칙
- 각 직업의 <job> 자료(하는 일·흥미·적성·업무 환경)에 근거해서 매긴다. 자료에 없는 배경지식으로 점수를 올리지 않는다.
- 흥미 6축(R I A S E C)만 보지 말고, 일하는 방식(team, field)과 가치(novelty, autonomy, impact)도
  자료에 근거가 있으면 반드시 반영한다. 특히 [업무 환경] 항목을 참고한다.
- 0이 아닌 축은 2~4개, 3점은 최대 2개. 가장 두드러진 특성만 고른다.
- attrs의 키는 정확히 이 {len(attributes)}개: {codes}
- reason: 높은 점수를 준 축의 근거를 한 문장(60자 이내)으로.
- 모든 직업에 대해 빠짐없이 답하고, job_id는 <job id="...">의 값을 그대로 쓴다.

차원 정의:
{dims}

태깅 루브릭 (카드용으로 쓰였지만 점수 기준과 헷갈리는 쌍은 그대로 적용한다):
<rubric>
{rubric}
</rubric>

직업 자료:
{jobs}

JSON으로만 답하라: {{"tags": [{{"job_id": 0, "attrs": {{"R": 0, ...}}, "reason": "..."}}, ...]}}
"""


# ---------- 파싱·검증 (순수 함수) ----------

def parse_batch(
    text: str, expected: set[int], attributes: list[Attribute]
) -> tuple[dict[int, dict], dict[int, str]]:
    """→ (통과한 직업 {id: {"attrs": [...], "reason": str}}, 실패한 직업 {id: 사유})."""
    codes = [a.code for a in attributes]
    try:
        data = batch_model(attributes).model_validate(json.loads(text))
    except (json.JSONDecodeError, ValidationError) as e:
        return {}, {i: f"배치 응답 형식 오류: {str(e)[:200]}" for i in expected}

    ok: dict[int, dict] = {}
    bad: dict[int, str] = {}
    for t in data.tags:
        if t.job_id not in expected or t.job_id in ok:
            continue
        a = t.attrs.model_dump()
        vec = [a[c] for c in codes]
        try:
            if any(not 0 <= x <= 3 for x in vec):
                raise ValueError("점수는 0~3")
            check_attrs(vec, str(t.job_id))
        except ValueError as e:
            bad[t.job_id] = str(e)
            continue
        ok[t.job_id] = {"attrs": vec, "reason": " ".join(t.reason.split())}
    for i in expected - ok.keys() - bad.keys():
        bad[i] = "응답에서 빠짐"
    return ok, bad


# ---------- 실행 ----------

def cached_valid(job_id: int, n_dim: int) -> bool:
    """캐시가 있고 지금 규칙에도 맞는지. 축 구성이 바뀌어 규칙을 어기게 된 결과는 다시 태깅한다."""
    p = CACHE_DIR / f"{job_id}.json"
    if not p.exists():
        return False
    attrs = json.loads(p.read_text(encoding="utf-8")).get("attrs") or []
    try:
        if len(attrs) != n_dim:
            return False
        check_attrs(attrs, str(job_id))
    except ValueError:
        return False
    return True


def tag_all(
    generate: Generator, jobs: list[Job], attributes: list[Attribute], rubric: str,
    batch: int = BATCH, sleep: float = 4.0,
) -> dict[int, str]:
    """캐시에 없는 직업을 배치로 태깅해 캐시에 저장. 끝까지 실패한 직업 {id: 사유} 반환."""
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    pending = [j for j in jobs if not cached_valid(j.id, len(attributes))]
    print(f"태깅 대상 {len(pending)}개 (캐시 {len(jobs) - len(pending)}개)", file=sys.stderr)
    failed: dict[int, str] = {}

    for rnd in range(1, MAX_ROUNDS + 1):
        if not pending:
            break
        if rnd > 1:
            print(f"라운드 {rnd}: {len(pending)}개 재시도", file=sys.stderr)
        failed = {}
        n_batches = (len(pending) + batch - 1) // batch
        for b in range(n_batches):
            chunk = pending[b * batch:(b + 1) * batch]
            items = [(j, json.loads((CAREERNET_DIR / f"{j.id}.json").read_text(encoding="utf-8")))
                     for j in chunk]
            text = generate(build_batch_prompt(items, attributes, rubric))
            ok, bad = parse_batch(text, {j.id for j in chunk}, attributes)
            for jid, tag in ok.items():
                (CACHE_DIR / f"{jid}.json").write_text(
                    json.dumps({"job_id": jid, **tag}, ensure_ascii=False), encoding="utf-8")
            failed.update(bad)
            print(f"  [{b + 1}/{n_batches}] 통과 {len(ok)} / 실패 {len(bad)}", file=sys.stderr)
            if b < n_batches - 1:
                time.sleep(sleep)
        pending = [j for j in pending if j.id in failed]
    return failed


def write_csv(jobs: dict[int, Job], attributes: list[Attribute]) -> list[dict]:
    rows = []
    for p in sorted(CACHE_DIR.glob("*.json"), key=lambda p: int(p.stem)):
        t = json.loads(p.read_text(encoding="utf-8"))
        j = jobs.get(t["job_id"])
        if not j:
            continue
        rows.append({"job_id": j.id, "name": j.name, "category": j.field or "",   # 축 코드 field와 겹치지 않게
                     **{a.code: v for a, v in zip(attributes, t["attrs"])}, "reason": t["reason"]})
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    cols = ["job_id", "name", "category", *[a.code for a in attributes], "reason"]
    with CSV_PATH.open("w", encoding="utf-8-sig", newline="") as f:   # 엑셀에서 한글 깨지지 않게 BOM
        w = csv.DictWriter(f, fieldnames=cols)
        w.writeheader()
        w.writerows(rows)
    return rows


# ---------- 분석 ----------

def report(rows: list[dict], attributes: list[Attribute], top: int = 5) -> str:
    codes = [a.code for a in attributes]
    label = {a.code: a.label for a in attributes}
    n = len(rows)
    if not n:
        return "결과 없음"
    out = [f"# 직업 {n}개 {len(codes)}축 태깅 분석", ""]

    out += ["## 1. 축별 사용 빈도", "", "| 축 | 이름 | 0 아님 | 비율 | 3점 | 평균 |", "|---|---|---|---|---|---|"]
    for c in codes:
        vals = [int(r[c]) for r in rows]
        nz = sum(v > 0 for v in vals)
        out.append(f"| {c} | {label[c]} | {nz} | {nz / n:.0%} | {sum(v == 3 for v in vals)} | {sum(vals) / n:.2f} |")

    nz_counts = Counter(sum(int(r[c]) > 0 for c in codes) for r in rows)
    out += ["", "0이 아닌 축 개수 분포: " + ", ".join(f"{k}개 {v}" for k, v in sorted(nz_counts.items()))]

    vecs = Counter(tuple(int(r[c]) for c in codes) for r in rows)
    dup = sum(v for v in vecs.values() if v > 1)
    out += [f"서로 다른 벡터 {len(vecs)}개 / 다른 직업과 벡터가 똑같은 직업 {dup}개", ""]

    by_field: dict[str, list[dict]] = defaultdict(list)
    for r in rows:
        by_field[r["category"] or "미분류"].append(r)
    out += ["## 2. 분야별 평균 (0~3)", "", "| 분야 | 수 | " + " | ".join(codes) + " |",
            "|---|---|" + "---|" * len(codes)]
    for f, rs in sorted(by_field.items(), key=lambda x: -len(x[1])):
        means = [sum(int(r[c]) for r in rs) / len(rs) for c in codes]
        out.append(f"| {f} | {len(rs)} | " + " | ".join(f"{m:.1f}" for m in means) + " |")

    out += ["", f"## 3. 축별 대표 직업 (점수 높은 순 {top}개)", ""]
    for c in codes:
        best = sorted((r for r in rows if int(r[c]) > 0), key=lambda r: -int(r[c]))[:top]
        names = ", ".join(f"{r['name']}({r[c]})" for r in best) or "없음"
        out.append(f"- **{c} {label[c]}**: {names}")
    return "\n".join(out) + "\n"


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--limit", type=int, help="앞에서 N개만")
    ap.add_argument("--batch", type=int, default=BATCH, help="한 요청에 넣을 직업 수")
    ap.add_argument("--sleep", type=float, default=4.0, help="요청 간 대기(초)")
    ap.add_argument("--report", action="store_true", help="API 호출 없이 캐시로 CSV·분석만")
    args = ap.parse_args()

    attributes = load_attributes(DATA_DIR / "attributes.csv")
    jobs = load_jobs(DATA_DIR / "jobs.json")
    targets = [j for j in sorted(jobs.values(), key=lambda j: j.id)
               if (CAREERNET_DIR / f"{j.id}.json").exists()]
    if args.limit:
        targets = targets[: args.limit]

    if not args.report:
        model = read_env("GEMINI_MODEL", required=False, default=DEFAULT_MODEL)
        batch_schema = batch_model(attributes)
        generate = gemini_generator(read_env("GEMINI_API_KEY"), model, batch_schema, TEMPERATURE)
        print(f"모델: {model}, 배치 {args.batch}", file=sys.stderr)
        try:
            failed = tag_all(generate, targets, attributes, RUBRIC_PATH.read_text(encoding="utf-8"),
                             args.batch, args.sleep)
        except Exception as e:      # 할당량 소진 등: 캐시는 남아 있으니 나중에 이어서
            print(f"! API 오류로 중단: {type(e).__name__}: {e}", file=sys.stderr)
            failed = {}
        if failed:
            print(f"끝까지 실패 {len(failed)}개: " + "; ".join(f"{k}: {v}" for k, v in list(failed.items())[:10]),
                  file=sys.stderr)

    rows = write_csv(jobs, attributes)
    print(f"CSV: {CSV_PATH} ({len(rows)}개)", file=sys.stderr)
    print(report(rows, attributes))


if __name__ == "__main__":
    main()
