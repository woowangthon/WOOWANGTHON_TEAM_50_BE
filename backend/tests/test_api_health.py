import pytest
from fastapi.testclient import TestClient

from app.content import load_content
from app.core.config import Settings
from app.main import create_app

FRONT = "http://localhost:5173"


def make_settings(**kwargs) -> Settings:
    return Settings(
        _env_file=None,
        cors_origins=FRONT,
        supabase_url="https://test.supabase.co",
        supabase_service_role_key="test-key",
        **kwargs,
    )


@pytest.fixture
def client():
    with TestClient(create_app(make_settings())) as c:
        yield c


def test_health_reports_loaded_content(client):
    res = client.get("/health")
    content = load_content()
    assert res.status_code == 200
    assert res.json() == {"status": "ok", "cards": len(content.cards), "jobs": len(content.jobs)}


def test_cors_preflight_allows_front_with_auth_header(client):
    res = client.options(
        "/health",
        headers={
            "Origin": FRONT,
            "Access-Control-Request-Method": "GET",
            "Access-Control-Request-Headers": "authorization",
        },
    )
    assert res.status_code == 200
    assert res.headers["access-control-allow-origin"] == FRONT


def test_cors_rejects_unknown_origin(client):
    res = client.get("/health", headers={"Origin": "https://evil.example"})
    assert "access-control-allow-origin" not in res.headers


def test_cors_origins_split_on_comma():
    s = Settings(_env_file=None, cors_origins="http://a.test, http://b.test,")
    assert s.cors_origin_list == ["http://a.test", "http://b.test"]


def test_server_does_not_start_without_supabase_settings():
    with pytest.raises(RuntimeError, match="SUPABASE_URL"):
        with TestClient(create_app(Settings(_env_file=None))):
            pass


def test_server_does_not_start_when_content_is_invalid(monkeypatch):
    def broken(**_):
        raise ValueError("cards.json 오류")

    monkeypatch.setattr("app.main.load_content", broken)
    with pytest.raises(ValueError, match="cards.json"):
        with TestClient(create_app(make_settings())):
            pass
