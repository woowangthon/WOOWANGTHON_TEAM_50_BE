"""커리어넷 직업백과 API → data/jobs.json 수집 배치.

    python -m scripts.fetch_careernet               # 전체 수집
    python -m scripts.fetch_careernet --limit 5     # 앞에서 5개만 (동작 확인용)
    python -m scripts.fetch_careernet --offline     # API 호출 없이 캐시로 jobs.json만 다시 생성

- API 키는 환경 변수 CAREERNET_API_KEY 또는 backend/.env 에서 읽는다.
- 상세 응답 원본은 data/raw/careernet/{직업코드}.json 에 캐시한다 (git 제외).
  카드 생성 스크립트는 이 원본을 재료로 쓰고, jobs.json 은 서버가 쓰는 최소 정보만 담는다.
- 이미 캐시된 직업은 다시 호출하지 않는다. 갱신하려면 --refresh.

API 명세: https://www.career.go.kr/cnet/front/openapi/jobCenter.do
(구 getOpenApi 직업정보 API는 2023-08 종료)
"""
import argparse
import json
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

from scripts.common import BACKEND_DIR, DATA_DIR, read_env

RAW_DIR = DATA_DIR / "raw" / "careernet"
JOBS_PATH = DATA_DIR / "jobs.json"

BASE = "https://www.career.go.kr/cnet/front/openapi"
LIST_URL = f"{BASE}/jobs.json"
DETAIL_URL = f"{BASE}/job.json"

SUMMARY_MAX = 300
MAJORS_MAX = 10


# ---------- HTTP ----------

class CareernetApiError(RuntimeError):
    """API가 에러 코드를 돌려준 경우 (인증키 오류 등). 재시도해도 소용없으므로 즉시 중단."""


def check_api_error(data: dict) -> None:
    # 에러 응답 형식: {"hashMap": {"code": "-2", "message": "인증키 없습니다."}}
    err = data.get("hashMap")
    if isinstance(err, dict) and str(err.get("code", "0")).startswith("-"):
        raise CareernetApiError(f"커리어넷 API 오류 {err.get('code')}: {err.get('message')}")


def get_json(url: str, params: dict[str, Any], retries: int = 3) -> dict:
    full = f"{url}?{urllib.parse.urlencode(params)}"
    last: Exception | None = None
    for attempt in range(retries):
        try:
            req = urllib.request.Request(full, headers={"User-Agent": "find-me-batch/0.1"})
            with urllib.request.urlopen(req, timeout=20) as r:
                body = r.read().decode("utf-8")
            data = json.loads(body)
            if not isinstance(data, dict):
                raise ValueError(f"예상치 못한 응답 형식: {body[:200]}")
            check_api_error(data)
            return data
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, ValueError) as e:
            last = e
            time.sleep(1.5 * (attempt + 1))
    # 키를 로그에 남기지 않는다
    safe = {k: v for k, v in params.items() if k != "apiKey"}
    raise RuntimeError(f"{url} {safe} 요청 실패: {last}")


def fetch_list(api_key: str, sleep: float, limit: int | None) -> list[dict]:
    items: list[dict] = []
    page = 1
    while True:
        data = get_json(LIST_URL, {"apiKey": api_key, "pageIndex": page})
        jobs = data.get("jobs") or []
        if not jobs:
            break
        items.extend(jobs)
        total = int(data.get("count") or 0)
        print(f"  목록 {page}페이지: 누적 {len(items)}/{total}", file=sys.stderr)
        if (limit and len(items) >= limit) or (total and len(items) >= total):
            break
        page += 1
        time.sleep(sleep)
    return items[:limit] if limit else items


def fetch_detail(api_key: str, seq: int) -> dict:
    return get_json(DETAIL_URL, {"apiKey": api_key, "seq": seq})


# ---------- 변환 (순수 함수, 테스트 대상) ----------

def _clean(s: Any) -> str:
    return " ".join(str(s).split()) if s else ""


def _truncate(s: str, n: int) -> str:
    return s if len(s) <= n else s[: n - 1].rstrip() + "…"


def to_job(list_item: dict, detail: dict | None) -> dict:
    """목록 항목 + 상세 응답 → Job 모델 dict (app.content.models.Job)."""
    detail = detail or {}
    base = detail.get("baseInfo") or {}

    job_id = int(list_item.get("job_cd") or base.get("job_cd") or base.get("seq"))
    name = _clean(list_item.get("job_nm") or base.get("job_nm"))

    works = [_clean(w.get("work")) for w in detail.get("workList") or [] if w.get("work")]
    summary = " ".join(works) or _clean(list_item.get("work"))

    majors: list[str] = []
    for d in detail.get("departList") or []:
        m = _clean(d.get("depart_name"))
        if m and m not in majors:
            majors.append(m)

    # 명세 표기는 tob_nm, 예시는 top_nm → 둘 다 허용
    field = _clean(list_item.get("top_nm") or list_item.get("tob_nm")) or None

    return {
        "id": job_id,
        "name": name,
        "field": field,
        "summary": _truncate(summary, SUMMARY_MAX) or None,
        "majors": majors[:MAJORS_MAX],
    }


# ---------- 실행 ----------

def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--limit", type=int, help="앞에서 N개만 수집")
    ap.add_argument("--sleep", type=float, default=0.3, help="요청 간 대기(초)")
    ap.add_argument("--refresh", action="store_true", help="캐시가 있어도 상세를 다시 받음")
    ap.add_argument("--offline", action="store_true", help="API 호출 없이 캐시로만 jobs.json 생성")
    args = ap.parse_args()

    RAW_DIR.mkdir(parents=True, exist_ok=True)
    list_cache = RAW_DIR / "_list.json"

    if args.offline:
        if not list_cache.exists():
            sys.exit(f"{list_cache} 가 없습니다. 먼저 온라인으로 한 번 수집하세요.")
        items = json.loads(list_cache.read_text(encoding="utf-8"))
        if args.limit:
            items = items[: args.limit]
    else:
        api_key = read_env("CAREERNET_API_KEY")
        print("직업 목록 수집 중...", file=sys.stderr)
        items = fetch_list(api_key, args.sleep, args.limit)
        list_cache.write_text(json.dumps(items, ensure_ascii=False, indent=1), encoding="utf-8")

    jobs: dict[int, dict] = {}
    failed: list[int] = []
    for i, item in enumerate(items, 1):
        seq = int(item["job_cd"])
        cache = RAW_DIR / f"{seq}.json"
        detail = None
        if cache.exists() and not args.refresh:
            detail = json.loads(cache.read_text(encoding="utf-8"))
        elif not args.offline:
            try:
                detail = fetch_detail(api_key, seq)
                cache.write_text(json.dumps(detail, ensure_ascii=False, indent=1), encoding="utf-8")
                time.sleep(args.sleep)
            except CareernetApiError:
                raise
            except RuntimeError as e:
                print(f"  ! {e}", file=sys.stderr)
                failed.append(seq)
        jobs[seq] = to_job(item, detail)
        if i % 25 == 0 or i == len(items):
            print(f"  상세 {i}/{len(items)}", file=sys.stderr)

    out = sorted(jobs.values(), key=lambda j: j["id"])

    # 서버 로더와 같은 모델로 검증한 뒤 저장
    sys.path.insert(0, str(BACKEND_DIR))
    from app.content.models import Job
    for j in out:
        Job.model_validate(j)

    JOBS_PATH.write_text(json.dumps(out, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"완료: {JOBS_PATH} ({len(out)}개)", file=sys.stderr)
    if failed:
        print(f"상세 실패 {len(failed)}개 (summary·majors 비어 있음, 재실행하면 이어서 받음): {failed}",
              file=sys.stderr)


if __name__ == "__main__":
    main()
