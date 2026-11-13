"""배포 점검. DB 마이그레이션 5개가 적용됐는지, 배포 서버가 응답하는지 확인한다.

    python -m scripts.check_deploy                                  # DB만
    python -m scripts.check_deploy --api https://findme-api.onrender.com --origin https://<프론트 주소>

DB는 backend/.env(또는 환경 변수)의 SUPABASE_URL·SUPABASE_SERVICE_ROLE_KEY로 접속한다.
데이터는 남기지 않는다: 쓰기 확인은 없는 사용자 ID로 보내 외래 키 오류(23503)가 나는지로 판단한다.
(CHECK 제약은 외래 키보다 먼저 검사되므로, 23503이면 CHECK는 통과한 것이다.)
"""
import argparse
import sys
import uuid

import httpx
from postgrest.exceptions import APIError
from supabase import create_client

from scripts.common import read_env

FK_VIOLATION = "23503"
CHECK_VIOLATION = "23514"
NO_TABLE = "PGRST205"
NO_FUNCTION = "PGRST202"


def _error(fn) -> APIError | None:
    try:
        fn()
    except APIError as e:
        return e
    return None


def check_db() -> list[tuple[str, bool, str]]:
    db = create_client(read_env("SUPABASE_URL"), read_env("SUPABASE_SERVICE_ROLE_KEY"))
    nobody = str(uuid.uuid4())
    results = []

    def table(migration, name):
        e = _error(lambda: db.table(name).select("*", count="exact").limit(0).execute())
        results.append((migration, e is None, f"테이블 {name}" + ("" if e is None else f" 없음 ({e.code})")))

    def rpc(migration, name, params):
        e = _error(lambda: db.rpc(name, params).execute())
        ok = e is None or e.code != NO_FUNCTION
        results.append((migration, ok, f"함수 {name}" + ("" if ok else " 없음")))

    for t in ("profiles", "user_vectors", "impressions", "reactions", "push_subscriptions"):
        table("20261109000000_init_schema", t)

    rpc("20261109010000_record_reaction", "record_reaction", {
        "p_impression_id": str(uuid.uuid4()), "p_user_id": nobody, "p_card_id": "check",
        "p_reaction": "like", "p_option_id": None, "p_vector": [0.0] * 11, "p_expected_version": None,
    })

    e = _error(lambda: db.table("impressions").insert(
        {"user_id": nobody, "card_id": "check", "source": "onboarding"}).execute())
    ok = e is not None and e.code == FK_VIOLATION
    detail = "impressions.source에 'onboarding' 허용"
    if not ok:
        detail += " 안 됨" if e is not None and e.code == CHECK_VIOLATION else f" 확인 실패 ({e and e.code})"
    results.append(("20261111000000_onboarding_source", ok, detail))

    table("20261111010000_interest_survey", "interest_surveys")
    rpc("20261111010000_interest_survey", "record_interest_survey", {
        "p_user_id": nobody, "p_version": 1, "p_answers": {}, "p_vector": [0.0] * 11, "p_expected_version": None,
    })

    table("20261111020000_saved_cards", "saved_cards")
    return results


def check_api(base: str, origin: str | None) -> list[tuple[str, bool, str]]:
    base = base.rstrip("/")
    results = []
    try:
        # 무료 플랜은 잠들어 있으면 첫 요청에 50초 넘게 걸릴 수 있다
        r = httpx.get(f"{base}/health", timeout=90)
        results.append(("GET /health", r.status_code == 200, f"{r.status_code} {r.text[:80]}"))
        r = httpx.get(f"{base}/me", timeout=30)
        results.append(("GET /me (토큰 없음)", r.status_code == 401, f"{r.status_code} (401이어야 정상)"))
        if origin:
            r = httpx.options(f"{base}/me", timeout=30, headers={
                "Origin": origin, "Access-Control-Request-Method": "GET",
                "Access-Control-Request-Headers": "authorization",
            })
            allowed = r.headers.get("access-control-allow-origin")
            results.append((f"CORS {origin}", allowed == origin, f"{r.status_code} allow-origin={allowed}"))
    except httpx.HTTPError as e:
        results.append(("API 접속", False, repr(e)))
    return results


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--api", help="배포 서버 주소 (예: https://findme-api.onrender.com)")
    parser.add_argument("--origin", help="CORS 확인할 프론트 주소")
    parser.add_argument("--skip-db", action="store_true")
    args = parser.parse_args()

    results = [] if args.skip_db else check_db()
    if args.api:
        results += check_api(args.api, args.origin)
    for name, ok, detail in results:
        print(f"{'O' if ok else 'X'}  {name:36} {detail}")
    failed = sum(not ok for _, ok, _ in results)
    print(f"\n{len(results) - failed}/{len(results)} 통과")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
