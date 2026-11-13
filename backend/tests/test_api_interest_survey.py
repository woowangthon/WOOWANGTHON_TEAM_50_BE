import time
from types import SimpleNamespace
from uuid import uuid4

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import ec
from fastapi.testclient import TestClient

from app.content import N_DIM
from app.core.auth import TokenVerifier
from app.core.config import Settings
from app.interest_survey import QUESTIONS, STEP
from app.main import create_app

URL = "https://test.supabase.co"
PRIVATE = ec.generate_private_key(ec.SECP256R1())
USER_ID = uuid4()
R, I, A, S, E, C = range(6)


class FakeJwks:
    def get_signing_key_from_jwt(self, token):
        return SimpleNamespace(key=PRIVATE.public_key())


class FakeUsers:
    def __init__(self):
        self.vectors: dict = {}
        self.surveys: dict = {}
        self.conflicts_left = 0

    def get_vector(self, user_id):
        return self.vectors.get(user_id)

    def get_interest_survey(self, user_id):
        s = self.surveys.get(user_id)
        return {**s, "created_at": "2026-11-13T00:00:00+00:00"} if s else None

    def record_interest_survey(self, user_id, version, answers, vector, expected_version):
        if self.conflicts_left:
            self.conflicts_left -= 1
            return "conflict"
        if user_id in self.surveys:
            return "duplicate"
        row = self.vectors.get(user_id)
        assert expected_version == (row["version"] if row else None)
        self.surveys[user_id] = {"version": version, "answers": answers}
        self.vectors[user_id] = {
            "vector": vector,
            "n_reactions": row["n_reactions"] if row else 0,
            "version": (row["version"] if row else 0) + 1,
        }
        return "recorded"


def headers() -> dict[str, str]:
    claims = {"sub": str(USER_ID), "aud": "authenticated", "iss": f"{URL}/auth/v1", "exp": int(time.time()) + 600}
    return {"Authorization": f"Bearer {jwt.encode(claims, PRIVATE, algorithm='ES256')}"}


def answers(default="neutral", **overrides) -> dict[str, str]:
    return {q: overrides.get(q, default) for q in QUESTIONS}


@pytest.fixture
def users():
    return FakeUsers()


@pytest.fixture
def client(users):
    settings = Settings(_env_file=None, supabase_url=URL, supabase_service_role_key="test-key")
    app = create_app(settings)
    with TestClient(app) as c:
        app.state.verifier = TokenVerifier(URL, FakeJwks())
        app.state.users = users
        yield c


def post(client, body):
    return client.post("/interest-survey", headers=headers(), json=body)


def test_requires_token(client):
    assert client.post("/interest-survey", json={"version": 1, "answers": answers()}).status_code == 401


def test_new_user_gets_plus_scores_and_minus_is_floored(client, users):
    res = post(client, {"version": 1, "answers": answers(
        make="strongly_like", repair="like", experiment="strongly_dislike", stories="dislike"
    )})
    assert res.status_code == 200 and res.json() == {"status": "recorded"}
    vec = users.vectors[USER_ID]["vector"]
    assert vec[R] == pytest.approx(3 * STEP)
    assert vec[I] == 0 and vec[A] == 0
    assert users.vectors[USER_ID]["n_reactions"] == 0


def test_existing_vector_is_raised_and_lowered(client, users):
    start = [0.5] * N_DIM
    users.vectors[USER_ID] = {"vector": start, "n_reactions": 7, "version": 4}
    post(client, {"version": 1, "answers": answers(
        explain="strongly_like", support="strongly_like", invite="dislike", coordinate="strongly_dislike"
    )})
    vec = users.vectors[USER_ID]["vector"]
    assert vec[S] == pytest.approx(1.0)              # 0.5 + 4·STEP
    assert vec[E] == pytest.approx(0.5 - 3 * STEP)
    assert vec[C] == pytest.approx(0.5)
    assert vec[6:] == pytest.approx(start[6:])       # 흥미 외 축은 그대로
    assert users.vectors[USER_ID]["n_reactions"] == 7


def test_second_submit_is_duplicate_and_does_not_change_vector(client, users):
    post(client, {"version": 1, "answers": answers("like")})
    before = list(users.vectors[USER_ID]["vector"])
    res = post(client, {"version": 1, "answers": answers("strongly_like")})
    assert res.json() == {"status": "duplicate"}
    assert users.vectors[USER_ID]["vector"] == before


def test_conflict_is_retried(client, users):
    users.conflicts_left = 2
    assert post(client, {"version": 1, "answers": answers()}).json() == {"status": "recorded"}


def test_gives_up_after_repeated_conflicts(client, users):
    users.conflicts_left = 3
    res = post(client, {"version": 1, "answers": answers()})
    assert res.status_code == 409 and res.json() == {"detail": "survey_conflict"}


@pytest.mark.parametrize(
    "body",
    [
        pytest.param({"version": 2, "answers": answers()}, id="wrong-version"),
        pytest.param({"version": 1, "answers": {k: v for k, v in answers().items() if k != "make"}}, id="missing"),
        pytest.param({"version": 1, "answers": {**answers(), "extra": "like"}}, id="unknown-question"),
        pytest.param({"version": 1, "answers": answers(make="love")}, id="unknown-value"),
    ],
)
def test_invalid_survey_is_422_and_not_stored(client, users, body):
    res = post(client, body)
    assert res.status_code == 422 and res.json() == {"detail": "invalid_survey"}
    assert USER_ID not in users.surveys


def test_status_before_and_after_survey(client):
    res = client.get("/interest-survey", headers=headers())
    assert res.status_code == 200
    assert res.json() == {"done": False, "version": None, "answers": None, "created_at": None}
    post(client, {"version": 1, "answers": answers("like")})
    body = client.get("/interest-survey", headers=headers()).json()
    assert body["done"] is True and body["version"] == 1 and body["answers"] == answers("like")
    assert body["created_at"]


def test_status_requires_token(client):
    assert client.get("/interest-survey").status_code == 401
