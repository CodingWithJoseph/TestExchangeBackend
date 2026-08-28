from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.core.config import Settings
from app.core.rate_limit import RateLimitMiddleware


def limited_client(*, read_limit: int = 2, write_limit: int = 1) -> TestClient:
    application = FastAPI()
    application.add_middleware(
        RateLimitMiddleware,
        settings=Settings(
            rate_limit_read_requests=read_limit,
            rate_limit_write_requests=write_limit,
            rate_limit_window_seconds=60,
        ),
    )

    @application.get("/resource")
    def read_resource() -> dict[str, bool]:
        return {"ok": True}

    @application.post("/resource")
    def write_resource() -> dict[str, bool]:
        return {"ok": True}

    return TestClient(application)


def test_read_requests_are_limited_with_retry_information() -> None:
    with limited_client() as client:
        assert client.get("/resource").status_code == 200
        second = client.get("/resource")
        blocked = client.get("/resource")

    assert second.headers["X-RateLimit-Remaining"] == "0"
    assert blocked.status_code == 429
    assert blocked.json()["detail"] == "Too many requests. Try again later."
    assert int(blocked.headers["Retry-After"]) > 0


def test_read_and_write_limits_use_separate_buckets() -> None:
    with limited_client() as client:
        assert client.get("/resource").status_code == 200
        assert client.post("/resource").status_code == 200
        assert client.post("/resource").status_code == 429
        assert client.get("/resource").status_code == 200
