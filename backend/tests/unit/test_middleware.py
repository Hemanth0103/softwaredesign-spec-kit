from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from app.api.middleware import configure_middleware
from app.config import load_settings


def make_app(test_environment, **changes):
    settings = load_settings(test_environment).model_copy(update=changes)
    app = FastAPI()
    configure_middleware(app, settings)

    @app.post("/api/v1/chat/probe")
    def probe(request: Request, count: int = 1):
        return {"scheme": request.url.scheme, "count": count}

    @app.get("/api/health")
    def health():
        return {"status": "ok"}

    return app


def test_cors_is_exact_and_errors_are_no_store(test_environment):
    with TestClient(
        make_app(test_environment, cors_origins=["https://chat.example.edu"])
    ) as client:
        headers = {"origin": "https://chat.example.edu"}
        response = client.post("/api/v1/chat/probe?count=private-secret", headers=headers)
        assert response.status_code == 400
        assert "private-secret" not in response.text
        assert response.headers["access-control-allow-origin"] == headers["origin"]
        assert response.headers["cache-control"] == "no-store"
        assert (
            "access-control-allow-origin"
            not in client.post(
                "/api/v1/chat/probe", headers={"origin": "https://evil.example.edu"}
            ).headers
        )
        assert (
            client.options(
                "/api/v1/chat/probe", headers={**headers, "access-control-request-method": "POST"}
            ).status_code
            == 200
        )


def test_rate_limit_and_health_exemption(test_environment):
    with TestClient(make_app(test_environment, rate_limit_requests=2)) as client:
        assert client.post("/api/v1/chat/probe").status_code == 200
        assert client.post("/api/v1/chat/probe").status_code == 200
        response = client.post("/api/v1/chat/probe")
        assert response.status_code == 429
        assert int(response.headers["retry-after"]) > 0
        assert response.headers["cache-control"] == "no-store"
        assert client.get("/api/health").status_code == 200


def test_forwarded_https_requires_trusted_peer(test_environment):
    for trusted, status in [([], 400), (["127.0.0.1/32"], 200)]:
        with TestClient(
            make_app(test_environment, trusted_proxy_networks=trusted, require_https=True),
            client=("127.0.0.1", 123),
        ) as client:
            response = client.post("/api/v1/chat/probe", headers={"x-forwarded-proto": "https"})
            assert response.status_code == status
            if status == 200:
                assert response.json()["scheme"] == "https"
            assert client.get("/api/health").status_code == 200


def test_rate_window_resets(test_environment, monkeypatch):
    import app.api.middleware as middleware

    clock = [0.0]
    monkeypatch.setattr(middleware.time, "monotonic", lambda: clock[0])
    with TestClient(
        make_app(test_environment, rate_limit_requests=1, rate_limit_window_seconds=10.0)
    ) as client:
        assert client.post("/api/v1/chat/probe").status_code == 200
        assert client.post("/api/v1/chat/probe").status_code == 429
        clock[0] = 11.0
        assert client.post("/api/v1/chat/probe").status_code == 200
