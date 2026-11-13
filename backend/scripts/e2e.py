"""배포 서버 E2E. 실제 Supabase(인증·DB)와 배포된 API로 사용자 흐름 전체를 확인한다.

    python -m scripts.e2e --api https://findme-api-rxg3.onrender.com
    python -m scripts.e2e --api http://localhost:8000 --keep      # 테스트 계정을 지우지 않고 남김

흐름: 가입(계정 → 프로필) → 관심사 설문 → 피드 → 반응(좋아요·선택·싫어요) → 저장 → 리포트 → 로드맵
     → 저장 취소 → 여정 → 중복·잘못된 요청 확인

- 테스트 계정은 서비스 키(backend/.env)로 만든다 (e2e-<랜덤>@example.com, 이메일 확인 생략).
- 끝나면 계정을 지운다. auth.users 삭제가 profiles·user_vectors·impressions·reactions·saved_cards·
  interest_surveys로 cascade 되어 이 계정이 만든 데이터가 모두 지워진다. 실패해도 지운다 (--keep 제외).
"""
import argparse
import secrets
import sys
import time
from collections.abc import Callable

import httpx
from supabase import create_client

from scripts.common import read_env

SURVEY_ANSWERS = {   # 탐구(I)·예술(A)에 끌리고 관습(C)은 싫어하는 학생
    "make": "neutral", "repair": "dislike", "experiment": "strongly_like", "patterns": "like",
    "stories": "strongly_like", "design": "like", "explain": "neutral", "support": "like",
    "invite": "dislike", "coordinate": "neutral", "organize": "strongly_dislike", "checklist": "dislike",
}
INTEREST_CODES = ("R", "I", "A", "S", "E", "C")


class Failed(Exception):
    pass


class Runner:
    def __init__(self, api: str, token: str):
        self.http = httpx.Client(base_url=api.rstrip("/"), headers={"Authorization": f"Bearer {token}"}, timeout=90)
        self.passed = 0

    def call(self, method: str, path: str, expect: int = 200, **kw) -> httpx.Response:
        t = time.perf_counter()
        r = self.http.request(method, path, **kw)
        ms = (time.perf_counter() - t) * 1000
        if r.status_code != expect:
            raise Failed(f"{method} {path}: {r.status_code} (기대 {expect}) {r.text[:300]}")
        print(f"   {method:6} {path:32} {r.status_code}  {ms:6.0f}ms")
        return r

    def step(self, name: str, fn: Callable[[], str | None]) -> None:
        print(f"▶ {name}")
        note = fn()
        self.passed += 1
        print(f"O {name}" + (f" — {note}" if note else ""))


def check(cond: bool, msg: str) -> None:
    if not cond:
        raise Failed(msg)


def interests(me: dict) -> dict[str, float]:
    return {t["code"]: t["score"] for t in me["traits"] if t["code"] in INTEREST_CODES}


def run(api: str, keep: bool) -> int:
    admin = create_client(read_env("SUPABASE_URL"), read_env("SUPABASE_SERVICE_ROLE_KEY"))
    email, password = f"e2e-{secrets.token_hex(4)}@example.com", secrets.token_urlsafe(16)
    user = admin.auth.admin.create_user({"email": email, "password": password, "email_confirm": True}).user
    print(f"테스트 계정 {email} ({user.id})")
    s: dict = {}
    r: Runner | None = None
    try:
        session = create_client(read_env("SUPABASE_URL"), read_env("SUPABASE_SERVICE_ROLE_KEY")) \
            .auth.sign_in_with_password({"email": email, "password": password}).session
        r = Runner(api, session.access_token)

        def health():
            body = r.call("GET", "/health").json()
            return f"카드 {body.get('cards')}장 · 직업 {body.get('jobs')}개"

        def signup():
            r.call("GET", "/me", 404)
            me = r.call("POST", "/me", 201, json={"nickname": "E2E 학생", "grade": 2}).json()
            check(me["email"] == email and me["nickname"] == "E2E 학생", f"프로필 응답 이상: {me}")
            check(all(v == 0 for v in interests(me).values()), "새 계정인데 성향 점수가 0이 아님")
            r.call("POST", "/me", 409, json={"nickname": "또", "grade": 1})
            return "프로필 생성 · 중복 가입 409"

        def survey():
            body = {"version": 1, "answers": SURVEY_ANSWERS}
            check(r.call("POST", "/interest-survey", json=body).json()["status"] == "recorded", "설문 recorded 아님")
            check(r.call("POST", "/interest-survey", json=body).json()["status"] == "duplicate", "재전송이 duplicate 아님")
            score = interests(r.call("GET", "/me").json())
            check(score["I"] > 0 and score["A"] > 0 and score["C"] == 0, f"설문 점수 반영 이상: {score}")
            s["after_survey"] = score
            return "흥미 점수 " + " ".join(f"{k}={v:.2f}" for k, v in score.items())

        def feed():
            cards = r.call("GET", "/feed", params={"n": 10}).json()["cards"]
            check(len(cards) == 10, f"피드 카드 {len(cards)}장")
            again = r.call("GET", "/feed", params={"n": 10}).json()["cards"]
            check([c["impression_id"] for c in again] == [c["impression_id"] for c in cards], "반응 안 한 카드가 재사용되지 않음")
            s["cards"] = cards
            return f"10장 (출처 {sorted({c['source'] for c in cards})}) · 다시 받아도 같은 노출"

        def react():
            cards = s["cards"]
            jobs = [c for c in cards if c["job_id"] is not None and not c["options"]]
            choices = [c for c in cards if c["options"]]
            check(len(jobs) >= 3, f"직업 카드가 {len(jobs)}장뿐 (피드 구성 확인 필요)")
            for c in jobs[:2]:
                check(r.call("POST", "/reactions", json={"impression_id": c["impression_id"], "reaction": "like"})
                      .json()["status"] == "recorded", "좋아요 recorded 아님")
            r.call("POST", "/reactions", json={"impression_id": jobs[2]["impression_id"], "reaction": "dislike"})
            dup = r.call("POST", "/reactions", json={"impression_id": jobs[0]["impression_id"], "reaction": "like"}).json()
            check(dup["status"] == "duplicate", "같은 노출에 두 번 반응했는데 duplicate 아님")
            note = "좋아요 2 · 싫어요 1 · 중복 반응 duplicate"
            if choices:
                c = choices[0]
                r.call("POST", "/reactions", json={"impression_id": c["impression_id"], "reaction": "choose",
                                                   "option_id": c["options"][0]["id"]})
                note += " · 선택 1"
            r.call("POST", "/reactions", 422, json={"impression_id": jobs[3 if len(jobs) > 3 else 2]["impression_id"],
                                                    "reaction": "save"})
            s["liked_job"], s["disliked_job"] = jobs[0], jobs[2]
            reacted = {c["impression_id"] for c in jobs[:3]} | ({choices[0]["impression_id"]} if choices else set())
            left = r.call("GET", "/feed", params={"n": 10}).json()["cards"]
            check(not reacted & {c["impression_id"] for c in left}, "반응한 카드가 피드에 다시 나옴")
            me = r.call("GET", "/me").json()
            check(me["n_reactions"] == (4 if choices else 3), f"n_reactions {me['n_reactions']}")
            return note + " · save 반응 422 · 반응한 카드 피드에서 빠짐"

        def save():
            job = s["liked_job"]
            r.call("PUT", f"/me/saved/{job['card_id']}", 204)
            r.call("PUT", f"/me/saved/{job['card_id']}", 204)
            r.call("PUT", "/me/saved/c9999", 404)
            saved = r.call("GET", "/me/saved").json()["cards"]
            check([c["card_id"] for c in saved] == [job["card_id"]], f"저장 목록 이상: {saved}")
            flags = {c["card_id"]: c["saved"] for c in r.call("GET", "/feed", params={"n": 10}).json()["cards"]}
            check(flags.get(job["card_id"]) in (True, None), "피드의 saved 표시 이상")
            return f"{job['job_name']} 저장 · 두 번 저장해도 1개 · 없는 카드 404"

        def report():
            body = r.call("GET", "/me/report").json()
            check(body["ready"] and body["remaining"] == 0, f"리포트가 안 열림: ready={body['ready']} remaining={body['remaining']}")
            check(body["top_interests"] and body["jobs"] and body["majors"] and body["summary"], "리포트 항목 비어 있음")
            check(s["disliked_job"]["job_id"] not in {j["job_id"] for j in body["jobs"]}, "싫어요 한 직업이 추천에 나옴")
            return (f"흥미 {[t['label'] for t in body['top_interests']]} · 직업 {[j['name'] for j in body['jobs'][:3]]} · "
                    f"AI 요약 {'O' if body['summary_by_ai'] else 'X (점수 기반 문장)'}")

        def roadmap():
            job = s["liked_job"]
            jobs = r.call("GET", "/me/roadmap").json()["jobs"]
            check([j["job_id"] for j in jobs] == [job["job_id"]], f"로드맵 목록 이상: {[j['job_id'] for j in jobs]}")
            check(jobs[0]["steps"] and jobs[0]["saved_at"], "로드맵 단계·저장 시각 없음")
            one = r.call("GET", f"/me/roadmap/{job['job_id']}").json()
            check(one["saved_at"] is not None, "저장한 직업인데 saved_at 없음")
            preview = r.call("GET", f"/me/roadmap/{s['disliked_job']['job_id']}").json()
            check(preview["saved_at"] is None, "저장 안 한 직업 미리보기에 saved_at 있음")
            r.call("GET", "/me/roadmap/999999", 404)
            return f"{jobs[0]['name']}: {[st['title'] for st in jobs[0]['steps']]}"

        def unsave():
            job = s["liked_job"]
            r.call("DELETE", f"/me/saved/{job['card_id']}", 204)
            r.call("DELETE", f"/me/saved/{job['card_id']}", 204)
            check(r.call("GET", "/me/saved").json()["cards"] == [], "저장 취소 후에도 목록에 남음")
            check(r.call("GET", "/me/roadmap").json()["jobs"] == [], "저장 취소 후에도 로드맵에 남음")
            return "저장 목록 · 로드맵에서 빠짐 · 두 번 취소해도 204"

        def journey():
            body = r.call("GET", "/me/journey").json()
            check(body["today_count"] >= 3 and body["streak"] >= 1 and body["report_ready"], f"여정 이상: {body}")
            return f"오늘 {body['today_count']}/{body['daily_goal']} · 연속 {body['streak']}일 · {body['level']['name']}"

        for name, fn in [("헬스 체크", health), ("가입", signup), ("관심사 설문", survey), ("피드", feed),
                         ("반응", react), ("저장", save), ("리포트", report), ("로드맵", roadmap),
                         ("저장 취소", unsave), ("여정", journey)]:
            r.step(name, fn)
        print(f"\n전부 통과 ({r.passed}단계)")
        return 0
    except Failed as e:
        print(f"X {e}")
        print(f"\n실패 ({r.passed if r else 0}단계 통과 후)")
        return 1
    finally:
        if keep:
            print(f"--keep: 테스트 계정을 남김 {email} / {user.id}")
        else:
            admin.auth.admin.delete_user(user.id)
            left = admin.table("profiles").select("user_id").eq("user_id", user.id).execute().data
            print("테스트 계정 삭제" + (" (데이터 정리 확인)" if not left else " — 프로필이 남음! 확인 필요"))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--api", required=True, help="API 주소 (예: https://findme-api-rxg3.onrender.com)")
    parser.add_argument("--keep", action="store_true", help="끝나도 테스트 계정을 지우지 않음")
    args = parser.parse_args()
    sys.exit(run(args.api, args.keep))


if __name__ == "__main__":
    main()
