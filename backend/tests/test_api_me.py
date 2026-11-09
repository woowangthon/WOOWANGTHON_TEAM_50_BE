import time
from types import SimpleNamespace
from uuid import UUID, uuid4

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import ec
from fastapi.testclient import TestClient
from jwt.exceptions import PyJWKClientConnectionError

from app.content import N_DIM
from app.core.auth import TokenVerifier
from app.core.config import Settings
from app.db.users import ProfileExists
from app.main import create_app

URL = "https://test.supabase.co"
ISSUER = f"{URL}/auth/v1"
PRIVATE = ec.generate_private_key(ec.SECP256R1())
OTHER_PRIVATE = ec.generate_private_key(ec.SECP256R1())
USER_ID = uuid4()
NOW = "2026-11-09T00:00:00+00:00"


class FakeJwks:
    def __init__(self, error: Exception | None = None):
        self.error = error

    def get_signing_key_from_jwt(self, token):
        if self.error:
            raise self.error
        return SimpleNamespace(key=PRIVATE.public_key())


class FakeUsers:
    def __init__(self):
        self.profiles: dict[UUID, dict] = {}
        self.vectors: dict[UUID, dict] = {}

    def get_profile(self, user_id):
        return self.profiles.get(user_id)

    def create_profile(self, user_id, fields):
        if user_id in self.profiles:
            raise ProfileExists
        self.profiles[user_id] = {**fields, "created_at": NOW, "updated_at": NOW}
        return self.profiles[user_id]

    def update_profile(self, user_id, fields):
        if user_id not in self.profiles:
            return None
        self.profiles[user_id].update(fields)
        return self.profiles[user_id]

    def get_vector(self, user_id):
        return self.vectors.get(user_id)


def token(key=PRIVATE, alg="ES256", **overrides) -> str:
    claims = {
        "sub": str(USER_ID),
        "email": "student@example.com",
        "aud": "authenticated",
        "iss": ISSUER,
        "exp": int(time.time()) + 600,
        **overrides,
    }
    return jwt.encode({k: v for k, v in claims.items() if v is not None}, key, algorithm=alg)


def auth(value: str | None = None) -> dict[str, str]:
    return {"Authorization": f"Bearer {value or token()}"}


@pytest.fixture
def app():
    settings = Settings(_env_file=None, supabase_url=URL, supabase_service_role_key="test-key")
    return create_app(settings)


@pytest.fixture
def client(app):
    with TestClient(app) as c:
        app.state.verifier = TokenVerifier(URL, FakeJwks())
        app.state.users = FakeUsers()
        yield c


@pytest.mark.parametrize(
    "bad",
    [
        pytest.param(lambda: token(exp=int(time.time()) - 10), id="expired"),
        pytest.param(lambda: token(exp=None), id="no-exp"),
        pytest.param(lambda: token(sub=None), id="no-sub"),
        pytest.param(lambda: token(sub="not-a-uuid"), id="sub-not-uuid"),
        pytest.param(lambda: token(aud="anon"), id="wrong-audience"),
        pytest.param(lambda: token(iss="https://evil.example/auth/v1"), id="wrong-issuer"),
        pytest.param(lambda: token(key=OTHER_PRIVATE), id="wrong-key"),
        pytest.param(lambda: token(key="x" * 32, alg="HS256"), id="symmetric-alg"),
        pytest.param(lambda: "garbage", id="malformed"),
    ],
)
def test_bad_tokens_get_401(client, bad):
    res = client.get("/me", headers=auth(bad()))
    assert res.status_code == 401
    assert res.json() == {"detail": "invalid_token"}


def test_missing_token_gets_401(client):
    res = client.get("/me")
    assert res.status_code == 401
    assert res.headers["www-authenticate"] == "Bearer"


def test_jwks_outage_is_503_not_401(app, client):
    app.state.verifier = TokenVerifier(URL, FakeJwks(PyJWKClientConnectionError("down")))
    assert client.get("/me", headers=auth()).status_code == 503


def test_get_before_signup_is_404(client):
    res = client.get("/me", headers=auth())
    assert res.status_code == 404
    assert res.json() == {"detail": "profile_not_found"}


def test_create_returns_profile_with_email_from_token_and_empty_traits(client):
    res = client.post("/me", headers=auth(), json={"nickname": "  우왕이 ", "grade": 2})
    body = res.json()
    assert res.status_code == 201
    assert body["user_id"] == str(USER_ID)
    assert body["email"] == "student@example.com"
    assert body["nickname"] == "우왕이"
    assert body["grade"] == 2
    assert body["n_reactions"] == 0
    assert len(body["traits"]) == N_DIM
    assert {t["score"] for t in body["traits"]} == {0.0}


def test_create_ignores_user_id_in_body(app, client):
    other = str(uuid4())
    client.post("/me", headers=auth(), json={"nickname": "a", "user_id": other})
    assert list(app.state.users.profiles) == [USER_ID]


def test_create_twice_is_409(client):
    client.post("/me", headers=auth(), json={"nickname": "a"})
    res = client.post("/me", headers=auth(), json={"nickname": "b"})
    assert res.status_code == 409
    assert res.json() == {"detail": "profile_exists"}


@pytest.mark.parametrize(
    "payload",
    [{}, {"nickname": "   "}, {"nickname": "가" * 21}, {"nickname": "a", "grade": 0}, {"nickname": "a", "grade": 4}],
)
def test_create_rejects_invalid_input(client, payload):
    assert client.post("/me", headers=auth(), json=payload).status_code == 422


def test_get_maps_stored_vector_to_attribute_codes(app, client):
    client.post("/me", headers=auth(), json={"nickname": "a"})
    vector = [i / 10 for i in range(N_DIM)]
    app.state.users.vectors[USER_ID] = {"vector": vector, "n_reactions": 7}

    body = client.get("/me", headers=auth()).json()

    assert body["n_reactions"] == 7
    assert body["traits"][0]["code"] == "R"
    assert body["traits"][10]["code"] == "impact"
    assert body["traits"][10]["score"] == pytest.approx(1.0)


def test_patch_changes_only_sent_fields(client):
    client.post("/me", headers=auth(), json={"nickname": "a", "grade": 1})
    body = client.patch("/me", headers=auth(), json={"grade": 3}).json()
    assert (body["nickname"], body["grade"]) == ("a", 3)


def test_patch_can_clear_grade_but_not_nickname(client):
    client.post("/me", headers=auth(), json={"nickname": "a", "grade": 1})
    assert client.patch("/me", headers=auth(), json={"grade": None}).json()["grade"] is None
    assert client.patch("/me", headers=auth(), json={"nickname": None}).status_code == 422


def test_patch_empty_body_returns_current_profile(client):
    client.post("/me", headers=auth(), json={"nickname": "a"})
    res = client.patch("/me", headers=auth(), json={})
    assert res.status_code == 200
    assert res.json()["nickname"] == "a"


@pytest.mark.parametrize("payload", [{"grade": 2}, {}])
def test_patch_without_profile_is_404(client, payload):
    assert client.patch("/me", headers=auth(), json=payload).status_code == 404
