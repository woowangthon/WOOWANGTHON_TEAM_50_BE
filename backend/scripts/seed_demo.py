"""심사위원·시연용 계정 만들기. 배포 API로 설문·반응·저장을 미리 쌓아 리포트·로드맵이 바로 열리게 한다.

    python -m scripts.seed_demo --api https://findme-api-rxg3.onrender.com
    python -m scripts.seed_demo --api ... --email demo-judge@example.com --reset   # 같은 계정을 지우고 다시 만듦

- 계정은 서비스 키(backend/.env)로 만든다. 이메일 확인 없이 바로 로그인할 수 있다.
- 비밀번호는 --password 또는 환경 변수 DEMO_PASSWORD. 없으면 새로 만들어 한 번만 출력한다 (레포에 저장하지 않음).
- 이메일은 demo- 로 시작해야 한다. 분석 쿼리(supabase/queries/source_reaction_rates.sql)가 demo-·e2e- 계정을
  빼고 세기 때문에, 시드 반응이 '실제 결과'에 섞이지 않는다.
- 반응은 정해진 학생 유형(탐구·예술에 끌리고 정리·반복은 덜 좋아함)대로 고른다. 카드 점수는 API로 내려오지
  않으므로 레포의 data/cards.json을 읽는다 → 배포 서버와 같은 커밋에서 돌릴 것.
- 저장은 리포트가 추천한 직업 카드 몇 장 (리포트 → 로드맵이 이어지게).
"""
import argparse
import os
import secrets
import sys

import httpx
import numpy as np
from supabase import create_client

from app.content import load_content, scale_attrs
from scripts.common import read_env

SURVEY = {   # 탐구(I)·예술(A) 높음, 사회(S) 조금, 관습(C)·현실(R) 낮음
    "make": "neutral", "repair": "dislike", "experiment": "strongly_like", "patterns": "like",
    "stories": "strongly_like", "design": "like", "explain": "like", "support": "neutral",
    "invite": "neutral", "coordinate": "dislike", "organize": "dislike", "checklist": "strongly_dislike",
}
TASTE = {"I": 1.0, "A": 1.0, "S": 0.3, "novelty": 0.6, "autonomy": 0.4, "impact": 0.3, "R": -0.4, "C": -0.8}
LIKE_AT = 0.5       # 취향 가중 합이 이 이상이고
CORE = ("I", "A")   # 이 축 중 하나가 2점 이상인 직업 카드만 끌려요. 나머지는 별로예요


def taste_vector(content) -> np.ndarray:
    v = np.zeros(len(content.attributes), dtype=np.float32)
    for a in content.attributes:
        v[a.idx] = TASTE.get(a.code, 0.0)
    return v


def find_user(admin, email: str):
    page = 1
    while True:
        users = admin.auth.admin.list_users(page=page, per_page=200)
        for u in users:
            if (u.email or "").lower() == email.lower():
                return u
        if len(users) < 200:
            return None
        page += 1


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--api", required=True)
    ap.add_argument("--email", default="demo-judge@example.com")
    ap.add_argument("--password", default=os.environ.get("DEMO_PASSWORD"))
    ap.add_argument("--nickname", default="심사위원")
    ap.add_argument("--reactions", type=int, default=15, help="쌓을 반응 수 (끌려요·별로예요·선택)")
    ap.add_argument("--saves", type=int, default=3, help="저장할 직업 카드 수 (로드맵)")
    ap.add_argument("--reset", action="store_true", help="같은 이메일 계정이 있으면 지우고 다시 만듦")
    args = ap.parse_args()
    if not args.email.startswith("demo-"):
        sys.exit("이메일은 demo- 로 시작해야 합니다 (분석 쿼리에서 빼기 위해).")

    content = load_content()
    taste = taste_vector(content)
    core = [a.idx for a in content.attributes if a.code in CORE]
    admin = create_client(read_env("SUPABASE_URL"), read_env("SUPABASE_SERVICE_ROLE_KEY"))

    old = find_user(admin, args.email)
    if old and not args.reset:
        sys.exit(f"{args.email} 계정이 이미 있습니다. 다시 만들려면 --reset")
    if old:
        admin.auth.admin.delete_user(old.id)     # 프로필·반응·저장 등은 cascade로 함께 지워짐
        print(f"기존 계정 삭제 {old.id}")

    password = args.password or secrets.token_urlsafe(9)
    user = admin.auth.admin.create_user({"email": args.email, "password": password, "email_confirm": True}).user
    token = create_client(read_env("SUPABASE_URL"), read_env("SUPABASE_SERVICE_ROLE_KEY")) \
        .auth.sign_in_with_password({"email": args.email, "password": password}).session.access_token
    api = httpx.Client(base_url=args.api.rstrip("/"), headers={"Authorization": f"Bearer {token}"}, timeout=90)

    def call(method, path, expect=(200,), **kw):
        r = api.request(method, path, **kw)
        if r.status_code not in expect:
            sys.exit(f"{method} {path}: {r.status_code} {r.text[:300]}")
        return r

    print("서버 깨우는 중 (잠들어 있으면 1분쯤 걸림)...")
    call("GET", "/health")
    call("POST", "/me", (201,), json={"nickname": args.nickname, "grade": 2})
    call("POST", "/interest-survey", json={"version": 1, "answers": SURVEY})

    counts = {"like": 0, "dislike": 0, "choose": 0}
    while sum(counts.values()) < args.reactions:
        cards = call("GET", "/feed", params={"n": 10}).json()["cards"]
        if not cards:
            break
        for c in cards:
            if sum(counts.values()) >= args.reactions:
                break
            card = content.card(c["card_id"])
            if card.options:
                best = max(card.options, key=lambda o: float(taste @ scale_attrs(o.attrs)))
                body = {"impression_id": c["impression_id"], "reaction": "choose", "option_id": best.id}
            else:
                like = float(taste @ scale_attrs(card.attrs)) >= LIKE_AT and max(card.attrs[i] for i in core) >= 2
                body = {"impression_id": c["impression_id"], "reaction": "like" if like else "dislike"}
            call("POST", "/reactions", json=body)
            counts[body["reaction"]] += 1
    if counts["like"] + counts["choose"] == 0:
        sys.exit("성향을 만드는 반응(끌려요·선택)이 하나도 없습니다. LIKE_AT을 낮춰 다시 돌리세요.")

    # 로드맵용: 리포트가 추천한 직업을 저장해 리포트 → 로드맵이 이어지게 한다
    report = call("GET", "/me/report").json()
    card_of = {k.job_id: k.id for k in content.cards if k.job_id is not None}
    saved = []
    for j in report["jobs"]:
        if j["job_id"] in card_of and len(saved) < args.saves:
            call("PUT", f"/me/saved/{card_of[j['job_id']]}", (204,))
            saved.append(j["name"])

    roadmap = call("GET", "/me/roadmap").json()["jobs"]
    journey = call("GET", "/me/journey").json()
    r = call("GET", "/interest-survey", (200, 405))     # 405: 설문 상태 API가 아직 배포 전
    survey_done = r.json()["done"] if r.status_code == 200 else None
    ok = report["ready"] and len(roadmap) == len(saved) and survey_done is not False

    print()
    print(f"{'완료' if ok else '확인 필요'}: {args.email}")
    print(f"  비밀번호      {password}" + ("" if args.password else "   ← 새로 만든 값. 제출 자료에만 적고 레포에는 넣지 말 것"))
    print(f"  반응          끌려요 {counts['like']} · 별로예요 {counts['dislike']} · 선택 {counts['choose']}")
    print(f"  리포트        {'열림' if report['ready'] else '잠김'} · 흥미 {[t['label'] for t in report['top_interests']]}"
          f" · 추천 직업 {[j['name'] for j in report['jobs'][:3]]}")
    print(f"  저장 · 로드맵 {saved}")
    print(f"  설문          {'완료' if survey_done else '확인 불가 (GET /interest-survey 배포 전)' if survey_done is None else '안 됨'}")
    print(f"  여정          {journey['level']['name']} · 오늘 {journey['today_count']}/{journey['daily_goal']}"
          f" · 성향 유형 {(journey['persona'] or {}).get('name')}")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
