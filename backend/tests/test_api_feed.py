import time
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
    assert res.json() == {"status": "recorded"}
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
    assert res.json() == {"status": "recorded"}
    assert store.vectors[USER_ID]["version"] == 2


def test_duplicate_reaction_does_not_change_vector(client, store):
    impression = impression_for(store, JOB_CARD)
    post_reaction(client, impression, "like")
    before = dict(store.vectors[USER_ID])
    res = post_reaction(client, impression, "like")
    assert res.status_code == 200
    assert res.json() == {"status": "duplicate"}
    assert store.vectors[USER_ID] == before


def test_conflict_is_retried(client, store):
    store.conflicts_left = 2
    assert post_reaction(client, impression_for(store, JOB_CARD), "like").json() == {"status": "recorded"}


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
