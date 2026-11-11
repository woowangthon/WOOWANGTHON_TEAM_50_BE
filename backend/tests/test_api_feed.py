import time
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from uuid import uuid4

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import ec
from fastapi.testclient import TestClient

from app.content import N_DIM, load_content
from app.core.auth import TokenVerifier
from app.core.config import Settings
from app.main import create_app

URL = "https://test.supabase.co"
PRIVATE = ec.generate_private_key(ec.SECP256R1())
USER_ID = uuid4()
CONTENT = load_content()
JOB_CARD = next(c for c in CONTENT.cards if c.type == "job_day")
CHOICE_CARD = next(c for c in CONTENT.cards if c.options)


class FakeJwks:
    def get_signing_key_from_jwt(self, token):
        return SimpleNamespace(key=PRIVATE.public_key())


class FakeStore:
    """UserRepo.get_vector와 FeedRepo를 함께 흉내 낸다."""

    def __init__(self):
        self.impressions: list[dict] = []
        self.reactions: dict[str, dict] = {}
        self.vectors: dict = {}
        self.conflicts_left = 0
        self.log: list[dict] = []

    def list_reactions(self, user_id):
        return [r for r in reversed(self.log) if r["user_id"] == user_id]

    def get_vector(self, user_id):
        return self.vectors.get(user_id)

    def list_impressions(self, user_id):
        return [
            {**i, "reactions": [{"id": "r"}] if i["id"] in self.reactions else []}
            for i in self.impressions
            if i["user_id"] == user_id
        ]

    def create_impressions(self, user_id, recs):
        rows = [
            {"id": str(uuid4()), "user_id": user_id, "card_id": r.card_id, "source": r.source}
            for r in recs
        ]
        self.impressions += rows
        return rows

    def get_impression(self, impression_id, user_id):
        return next(
            (i for i in self.impressions if i["id"] == str(impression_id) and i["user_id"] == user_id),
            None,
        )

    def record_reaction(self, impression_id, user_id, card_id, reaction, option_id, vector, expected_version):
        if self.conflicts_left:
            self.conflicts_left -= 1
            return "conflict"
        if str(impression_id) in self.reactions:
            return "duplicate"
        self.reactions[str(impression_id)] = {"reaction": reaction, "option_id": option_id}
        self.log.append({"user_id": user_id, "card_id": card_id, "reaction": reaction,
                         "created_at": (datetime(2026, 11, 10, tzinfo=UTC) + timedelta(seconds=len(self.log))).isoformat()})
        row = self.vectors.get(user_id, {"n_reactions": 0, "version": 0})
        assert expected_version == (row["version"] if user_id in self.vectors else None)
        self.vectors[user_id] = {
            "vector": vector,
            "n_reactions": row["n_reactions"] + 1,
            "version": row["version"] + 1,
        }
        return "recorded"


def headers(sub=USER_ID) -> dict[str, str]:
    claims = {"sub": str(sub), "aud": "authenticated", "iss": f"{URL}/auth/v1", "exp": int(time.time()) + 600}
    return {"Authorization": f"Bearer {jwt.encode(claims, PRIVATE, algorithm='ES256')}"}


@pytest.fixture
def store():
    return FakeStore()


@pytest.fixture
def client(store):
    settings = Settings(_env_file=None, supabase_url=URL, supabase_service_role_key="test-key")
    app = create_app(settings)
    with TestClient(app) as c:
        app.state.verifier = TokenVerifier(URL, FakeJwks())
        app.state.users = store
        app.state.feed = store
        yield c


def impression_for(store, card, user_id=USER_ID) -> str:
    rec = SimpleNamespace(card_id=card.id, source="cold_start")
    return store.create_impressions(user_id, [rec])[0]["id"]


def post_reaction(client, impression_id, reaction, option_id=None):
    payload = {"impression_id": impression_id, "reaction": reaction}
    if option_id:
        payload["option_id"] = option_id
    return client.post("/reactions", headers=headers(), json=payload)


def feed(client, n=5) -> list[dict]:
    return client.get("/feed", params={"n": n}, headers=headers()).json()["cards"]


def drain(client) -> set[str]:
    """피드가 빌 때까지 모든 카드에 반응하고, 내려온 카드 id를 돌려준다."""
    served: set[str] = set()
    while cards := feed(client, n=20):
        for c in cards:
            served.add(c["card_id"])
            reaction, option = ("choose", c["options"][0]["id"]) if c["options"] else ("pass", None)
            assert post_reaction(client, c["impression_id"], reaction, option).status_code == 200
    return served


@pytest.mark.parametrize("request_args", [("get", "/feed"), ("post", "/reactions")])
def test_requires_token(client, request_args):
    method, path = request_args
    assert getattr(client, method)(path).status_code == 401


@pytest.mark.parametrize("n", [0, 21])
def test_feed_rejects_out_of_range_n(client, n):
    assert client.get("/feed", params={"n": n}, headers=headers()).status_code == 422


def test_new_user_gets_cold_start_cards_and_impressions_are_logged(client, store):
    cards = feed(client)
    assert len(cards) == 5
    assert {c["source"] for c in cards} == {"cold_start"}
    assert {c["impression_id"] for c in cards} == {i["id"] for i in store.impressions}


def test_feed_exposes_display_fields_but_not_attrs(client, store):
    impression_for(store, JOB_CARD)
    impression_for(store, CHOICE_CARD)
    cards = {c["card_id"]: c for c in feed(client, n=2)}
    job = cards[JOB_CARD.id]
    choice = cards[CHOICE_CARD.id]
    assert job["job_name"] == CONTENT.jobs[JOB_CARD.job_id].name
    assert job["majors"] == CONTENT.jobs[JOB_CARD.job_id].majors
    assert choice["majors"] == []
    assert job["options"] == []
    assert choice["job_name"] is None
    assert choice["options"][0].keys() == {"id", "label"}
    assert "attrs" not in job


def test_refetch_reuses_unreacted_impressions(client, store):
    first = feed(client)
    second = feed(client)
    assert [c["impression_id"] for c in second] == [c["impression_id"] for c in first]
    assert len(store.impressions) == 5


def test_reacted_cards_leave_feed_and_are_replaced(client, store):
    first = feed(client)
    assert post_reaction(client, first[0]["impression_id"], "pass").status_code == 200
    second = feed(client)
    assert first[0]["card_id"] not in {c["card_id"] for c in second}
    assert len(second) == 5
    assert len({c["card_id"] for c in second}) == 5


def test_feed_is_empty_after_every_card_is_reacted(client):
    assert drain(client) == {c.id for c in CONTENT.cards}
    assert feed(client) == []


def test_duplicate_impressions_of_reacted_card_are_not_served_again(client, store):
    first = impression_for(store, JOB_CARD)
    impression_for(store, JOB_CARD)
    post_reaction(client, first, "like")
    assert JOB_CARD.id not in drain(client)


def test_impression_of_removed_card_is_skipped(client, store):
    store.create_impressions(USER_ID, [SimpleNamespace(card_id="c9999", source="random")])
    assert "c9999" not in {c["card_id"] for c in feed(client)}


def test_feed_turns_personalized_after_enough_reactions(client, store):
    store.vectors[USER_ID] = {"vector": [0.5] * N_DIM, "n_reactions": 5, "version": 5}
    assert "personalized" in {c["source"] for c in feed(client)}


def test_like_creates_vector_from_card(client, store):
    res = post_reaction(client, impression_for(store, JOB_CARD), "like")
    row = store.vectors[USER_ID]
    assert res.json()["status"] == "recorded"
    assert row["n_reactions"] == 1
    assert any(v > 0 for v in row["vector"])


def test_choose_records_option(client, store):
    option = CHOICE_CARD.options[0].id
    res = post_reaction(client, impression_for(store, CHOICE_CARD), "choose", option)
    assert res.status_code == 200
    assert next(iter(store.reactions.values())) == {"reaction": "choose", "option_id": option}


def test_second_reaction_passes_current_version(client, store):
    post_reaction(client, impression_for(store, JOB_CARD), "like")
    res = post_reaction(client, impression_for(store, CHOICE_CARD), "pass")
    assert res.json()["status"] == "recorded"
    assert store.vectors[USER_ID]["version"] == 2


def test_duplicate_reaction_does_not_change_vector(client, store):
    impression = impression_for(store, JOB_CARD)
    post_reaction(client, impression, "like")
    before = dict(store.vectors[USER_ID])
    res = post_reaction(client, impression, "like")
    assert res.status_code == 200
    assert res.json()["status"] == "duplicate"
    assert store.vectors[USER_ID] == before


def test_conflict_is_retried(client, store):
    store.conflicts_left = 2
    assert post_reaction(client, impression_for(store, JOB_CARD), "like").json()["status"] == "recorded"


def test_gives_up_after_repeated_conflicts(client, store):
    store.conflicts_left = 3
    res = post_reaction(client, impression_for(store, JOB_CARD), "like")
    assert res.status_code == 409
    assert res.json() == {"detail": "reaction_conflict"}
    assert store.reactions == {}


def test_other_users_impression_is_404(client, store):
    res = post_reaction(client, impression_for(store, JOB_CARD, user_id=uuid4()), "like")
    assert res.status_code == 404
    assert res.json() == {"detail": "impression_not_found"}


@pytest.mark.parametrize(
    "card, reaction, option",
    [
        pytest.param(JOB_CARD, "choose", "c0001-a", id="choose-on-card-without-options"),
        pytest.param(CHOICE_CARD, "choose", None, id="choose-without-option"),
        pytest.param(CHOICE_CARD, "choose", "c9999-a", id="option-of-other-card"),
        pytest.param(CHOICE_CARD, "like", CHOICE_CARD.options[0].id, id="option-on-like"),
    ],
)
def test_invalid_reaction_is_422_and_not_stored(client, store, card, reaction, option):
    res = post_reaction(client, impression_for(store, card), reaction, option)
    assert res.status_code == 422
    assert res.json() == {"detail": "invalid_reaction"}
    assert store.reactions == {}


def test_unknown_reaction_type_is_422(client, store):
    assert post_reaction(client, impression_for(store, JOB_CARD), "love").status_code == 422


# ---------- 저장 목록 · 리포트 ----------

JOB_CARDS = [c for c in CONTENT.cards if c.type == "job_day"]


def react(client, store, card, reaction):
    assert post_reaction(client, impression_for(store, card), reaction).status_code == 200


def saved(client) -> list[dict]:
    res = client.get("/me/saved", headers=headers())
    assert res.status_code == 200
    return res.json()["cards"]


def report(client) -> dict:
    res = client.get("/me/report", headers=headers())
    assert res.status_code == 200
    return res.json()


@pytest.mark.parametrize("path", ["/me/saved", "/me/report"])
def test_saved_and_report_require_token(client, path):
    assert client.get(path).status_code == 401


def test_saved_lists_only_saved_cards_newest_first(client, store):
    a, b, c = JOB_CARDS[:3]
    react(client, store, a, "save")
    react(client, store, b, "like")
    react(client, store, c, "save")
    cards = saved(client)
    assert [x["card_id"] for x in cards] == [c.id, a.id]
    assert cards[0]["job_name"] == CONTENT.jobs[c.job_id].name
    assert cards[0]["majors"] == CONTENT.jobs[c.job_id].majors
    assert "saved_at" in cards[0] and "attrs" not in cards[0]


def test_saved_skips_other_users_and_removed_cards(client, store):
    store.log.append({"user_id": uuid4(), "card_id": JOB_CARD.id, "reaction": "save", "created_at": "2026-11-10T00:00:00+00:00"})
    store.log.append({"user_id": USER_ID, "card_id": "c9999", "reaction": "save", "created_at": "2026-11-10T00:00:00+00:00"})
    assert saved(client) == []


def test_report_is_locked_until_enough_reactions(client, store):
    react(client, store, JOB_CARDS[0], "like")
    body = report(client)
    assert (body["ready"], body["n_reactions"], body["remaining"]) == (False, 1, 4)
    assert body["jobs"] == [] and body["majors"] == [] and body["summary"] == ""
    assert len(body["traits"]) == N_DIM


def test_report_stays_locked_after_only_passes(client, store):
    for card in JOB_CARDS[:5]:
        react(client, store, card, "pass")
    body = report(client)
    assert (body["ready"], body["n_reactions"], body["remaining"]) == (False, 5, 5)


def test_report_for_new_user(client):
    body = report(client)
    assert (body["ready"], body["n_reactions"], body["remaining"]) == (False, 0, 5)


def test_report_after_reactions(client, store):
    for card in JOB_CARDS[:5]:
        react(client, store, card, "like")
    react(client, store, JOB_CARDS[5], "dislike")
    body = report(client)
    assert body["ready"] and body["remaining"] == 0
    assert body["reaction_counts"] == {"like": 5, "dislike": 1, "save": 0, "pass": 0, "choose": 0}
    assert all(j["summary"] for j in body["jobs"])
    assert 1 <= len(body["top_interests"]) <= 2
    assert {t["group"] for t in body["top_interests"]} == {"interest"}
    assert body["summary"] and len(body["study_tips"]) == len(body["top_interests"])
    assert 0 < len(body["jobs"]) <= 5
    assert JOB_CARDS[5].job_id not in {j["job_id"] for j in body["jobs"]}
    scores = [j["score"] for j in body["jobs"]]
    assert scores == sorted(scores, reverse=True)


# ---------- 가입 설문 ----------

from app.onboarding import ONBOARDING_CARD_IDS  # noqa: E402


def onboarding(client) -> dict:
    res = client.get("/onboarding", headers=headers())
    assert res.status_code == 200
    return res.json()


def answer(client, card):
    return post_reaction(client, card["impression_id"], "choose", card["options"][0]["id"])


def test_onboarding_requires_token(client):
    assert client.get("/onboarding").status_code == 401


def test_onboarding_cards_exist_and_cover_every_interest_type():
    cards = [CONTENT.card(i) for i in ONBOARDING_CARD_IDS]
    assert all(c is not None and c.options for c in cards)
    for a in CONTENT.attributes:
        if a.group == "interest":
            assert any(o.attrs[a.idx] == 3 for c in cards for o in c.options), a.code


def test_onboarding_serves_survey_cards_in_order(client, store):
    body = onboarding(client)
    assert [c["card_id"] for c in body["cards"]] == list(ONBOARDING_CARD_IDS)
    assert {c["source"] for c in body["cards"]} == {"onboarding"}
    assert (body["total"], body["answered"], body["done"]) == (5, 0, False)


def test_onboarding_refetch_reuses_impressions(client, store):
    first = onboarding(client)["cards"]
    second = onboarding(client)["cards"]
    assert [c["impression_id"] for c in second] == [c["impression_id"] for c in first]
    assert len(store.impressions) == 5


def test_answered_survey_cards_leave_and_progress_counts(client, store):
    cards = onboarding(client)["cards"]
    assert answer(client, cards[0]).status_code == 200
    body = onboarding(client)
    assert [c["card_id"] for c in body["cards"]] == list(ONBOARDING_CARD_IDS[1:])
    assert (body["answered"], body["done"]) == (1, False)


def test_finished_survey_makes_first_feed_personalized(client, store):
    for card in onboarding(client)["cards"]:
        answer(client, card)
    assert onboarding(client) == {"cards": [], "total": 5, "answered": 5, "done": True}
    sources = {c["source"] for c in feed(client, n=10)}
    assert "personalized" in sources and "cold_start" not in sources
    assert not set(ONBOARDING_CARD_IDS) & {c["card_id"] for c in feed(client, n=10)}


def test_skipped_survey_falls_back_to_cold_start(client):
    assert {c["source"] for c in feed(client)} == {"cold_start"}


def test_survey_answers_do_not_open_report(client, store):
    for card in onboarding(client)["cards"]:
        answer(client, card)
    body = report(client)
    assert (body["ready"], body["n_reactions"], body["remaining"]) == (False, 5, 5)
    assert body["reaction_counts"]["choose"] == 5


class FakeWriter:
    def __init__(self, text):
        self.text, self.calls = text, 0

    def summarize(self, user_id, report):
        self.calls += 1
        return self.text


def test_report_uses_ai_summary_when_available(client, store):
    client.app.state.report_writer = writer = FakeWriter("AI가 쓴 설명이에요.")
    for card in JOB_CARDS[:5]:
        react(client, store, card, "like")
    body = report(client)
    assert (body["summary"], body["summary_by_ai"]) == ("AI가 쓴 설명이에요.", True)
    assert writer.calls == 1


def test_report_falls_back_when_ai_fails(client, store):
    client.app.state.report_writer = FakeWriter(None)
    for card in JOB_CARDS[:5]:
        react(client, store, card, "like")
    body = report(client)
    assert body["summary"] and body["summary_by_ai"] is False


def test_locked_report_does_not_call_ai(client, store):
    client.app.state.report_writer = writer = FakeWriter("x")
    react(client, store, JOB_CARDS[0], "like")
    assert report(client)["summary_by_ai"] is False and writer.calls == 0


# ---------- 로드맵 ----------

def roadmaps(client) -> list[dict]:
    res = client.get("/me/roadmap", headers=headers())
    assert res.status_code == 200
    return res.json()["jobs"]


@pytest.mark.parametrize("path", ["/me/roadmap", f"/me/roadmap/{JOB_CARD.job_id}"])
def test_roadmap_requires_token(client, path):
    assert client.get(path).status_code == 401


def test_roadmap_is_empty_without_saved_job_cards(client, store):
    react(client, store, JOB_CARDS[0], "like")
    react(client, store, CHOICE_CARD, "save")
    assert roadmaps(client) == []


def test_roadmap_lists_saved_jobs_newest_first(client, store):
    a, b = JOB_CARDS[:2]
    react(client, store, a, "save")
    react(client, store, b, "save")
    jobs = roadmaps(client)
    assert [j["job_id"] for j in jobs] == [b.job_id, a.job_id]
    assert jobs[0]["card_id"] == b.id and jobs[0]["saved_at"]
    assert jobs[0]["steps"]
    assert all(t["my_score"] >= 0 for t in jobs[0]["traits"])


def test_single_roadmap_previews_unsaved_job(client, store):
    res = client.get(f"/me/roadmap/{JOB_CARD.job_id}", headers=headers())
    assert res.status_code == 200
    body = res.json()
    assert body["job_id"] == JOB_CARD.job_id and body["saved_at"] is None


def test_single_roadmap_marks_saved_job(client, store):
    react(client, store, JOB_CARD, "save")
    body = client.get(f"/me/roadmap/{JOB_CARD.job_id}", headers=headers()).json()
    assert body["saved_at"] is not None


def test_unknown_job_roadmap_is_404(client):
    res = client.get("/me/roadmap/999999", headers=headers())
    assert (res.status_code, res.json()) == (404, {"detail": "job_not_found"})


# ---------- 여정 · 직업 탐색 ----------

def test_reaction_returns_gains_and_daily_progress(client, store):
    body = post_reaction(client, impression_for(store, JOB_CARD), "like").json()
    assert body["status"] == "recorded" and body["daily_goal"] == 5
    assert body["gains"] and all(g["delta"] >= 1 for g in body["gains"])
    again = post_reaction(client, store.impressions[-1]["id"], "like").json()
    assert again["status"] == "duplicate" and again["gains"] == []


def test_feed_cards_carry_job_id_and_reason_field(client, store):
    cards = feed(client, 10)
    job_card = next(c for c in cards if c["type"] == "job_day")
    assert job_card["job_id"] in CONTENT.jobs and job_card["reason"] is None    # 콜드 스타트는 이유 없음


def journey(client) -> dict:
    return client.get("/me/journey", headers=headers()).json()


def test_journey_for_new_and_active_user(client, store):
    body = journey(client)
    assert (body["streak"], body["today_count"], body["daily_goal"]) == (0, 0, 5)
    assert body["persona"] is None and body["level"]["n"] == 1 and body["mission"]["text"]
    assert not body["report_ready"] and body["report_remaining"] == 5
    for card in JOB_CARDS[:5]:
        react(client, store, card, "like")
    body = journey(client)
    assert body["report_ready"] and body["level"]["n"] == 2 and body["persona"]["name"]
    assert {b["id"] for b in body["badges"] if b["earned"]} >= {"first_step", "report"}


def test_jobs_list_and_detail(client, store):
    res = client.get("/jobs", headers=headers(), params={"limit": 5}).json()
    assert len(res["jobs"]) == 5 and len(res["fields"]) == 10 and res["jobs"][0]["match"] is None
    for card in JOB_CARDS[:5]:
        react(client, store, card, "like")
    jobs = client.get("/jobs", headers=headers(), params={"limit": 5}).json()["jobs"]
    assert jobs[0]["match"] >= jobs[-1]["match"] > 0
    detail = client.get(f"/jobs/{jobs[0]['job_id']}", headers=headers()).json()
    assert detail["match"] == jobs[0]["match"] and detail["similar"]
    roadmap = client.get(f"/me/roadmap/{jobs[0]['job_id']}", headers=headers()).json()
    assert detail["steps"] == roadmap["steps"]                                   # 직업 상세와 로드맵은 같은 단계를 쓴다
    assert client.get("/jobs/999999", headers=headers()).status_code == 404
    assert client.get("/jobs").status_code == 401


def test_report_jobs_have_match_percent(client, store):
    for card in JOB_CARDS[:5]:
        react(client, store, card, "like")
    jobs = report(client)["jobs"]
    assert all(j["match"] == round(j["score"] * 100) for j in jobs)


# ---------- AI 동료: 직업 질문 · 대화 ----------

class FakeCoach:
    def __init__(self, answer="AI 답변이에요.", reply={"reply": "AI 대답이에요.", "suggestions": ["더 알려 줘"]}, allowed=True):
        self.answer, self.reply, self.allowed, self.seen = answer, reply, allowed, []

    def answer_job(self, user_id, n_reactions, detail, question_id):
        return self.answer

    def allow(self, user_id):
        return self.allowed

    def chat(self, ctx, messages):
        self.seen.append((ctx, messages))
        return self.reply


def ask(client, job_id, question_id):
    return client.post(f"/jobs/{job_id}/ask", headers=headers(), json={"question_id": question_id})


def say(client, *texts):
    roles = ["user", "ai"]
    messages = [{"role": roles[i % 2], "text": t} for i, t in enumerate(texts)]
    return client.post("/chat", headers=headers(), json={"messages": messages})


def test_job_questions_and_fallback_answers(client, store):
    job_id = JOB_CARD.job_id
    detail = client.get(f"/jobs/{job_id}", headers=headers()).json()
    assert [q["id"] for q in detail["questions"]] == ["day", "hard", "start"]     # 성향이 없으면 '왜 맞나요' 없음
    assert ask(client, job_id, "why_match").status_code == 422
    for qid in ("day", "hard", "start"):
        body = ask(client, job_id, qid).json()
        assert body["answer"] and body["by_ai"] is False
    for card in JOB_CARDS[:5]:
        react(client, store, card, "like")
    assert client.get(f"/jobs/{job_id}", headers=headers()).json()["questions"][0]["id"] == "why_match"
    why = ask(client, job_id, "why_match").json()
    assert "%" in why["answer"] and why["by_ai"] is False
    assert ask(client, job_id, "salary").status_code == 422                        # 정해진 질문만
    assert ask(client, 999999, "day").status_code == 404


def test_job_ask_uses_ai_when_available(client, store):
    client.app.state.coach = FakeCoach()
    assert ask(client, JOB_CARD.job_id, "day").json() == {"answer": "AI 답변이에요.", "by_ai": True}
    client.app.state.coach = FakeCoach(answer=None)
    assert ask(client, JOB_CARD.job_id, "day").json()["by_ai"] is False


def test_chat_falls_back_to_records_without_ai(client, store):
    body = say(client, "안녕").json()
    assert body["by_ai"] is False and "끌려요" in body["reply"] and body["suggestions"]
    for card in JOB_CARDS[:5]:
        react(client, store, card, "like")
    assert "유형" in say(client, "나는 어떤 사람이야?").json()["reply"]


def test_chat_uses_ai_with_student_context(client, store):
    client.app.state.coach = coach = FakeCoach()
    react(client, store, JOB_CARDS[0], "save")
    body = say(client, "안녕", "반가워요", "내 성향 알려 줘").json()
    assert body == {"reply": "AI 대답이에요.", "suggestions": ["더 알려 줘"], "by_ai": True}
    ctx, messages = coach.seen[0]
    assert ctx["saved"] == [JOB_CARDS[0].title] and ctx["jobs"] and len(messages) == 3
    assert "nickname" not in ctx and "email" not in ctx


def test_chat_crisis_message_skips_ai(client, store):
    client.app.state.coach = coach = FakeCoach()
    body = say(client, "요즘 그냥 죽고 싶어").json()
    assert "1388" in body["reply"] and body["by_ai"] is False and coach.seen == []


def test_chat_limits_and_validation(client, store):
    client.app.state.coach = FakeCoach(allowed=False)
    assert say(client, "안녕").status_code == 429
    client.app.state.coach = FakeCoach(reply=None)
    assert say(client, "안녕").json()["by_ai"] is False                             # AI 실패 → 기록 기반
    assert say(client, "안녕", "마지막이 AI 말").status_code == 422
    assert say(client, "가" * 301).status_code == 422
    assert client.post("/chat", headers=headers(), json={"messages": []}).status_code == 422
    assert client.post("/chat", json={"messages": [{"role": "user", "text": "안녕"}]}).status_code == 401
