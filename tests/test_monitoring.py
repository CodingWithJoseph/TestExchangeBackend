from app.core.config import Settings
from app.core.monitoring import configure_error_tracking


def test_error_tracking_is_disabled_without_a_dsn() -> None:
    assert configure_error_tracking(Settings()) is False


def test_error_tracking_uses_safe_defaults(monkeypatch) -> None:
    captured: dict[str, object] = {}
    monkeypatch.setattr(
        "app.core.monitoring.sentry_sdk.init", lambda **values: captured.update(values)
    )
    settings = Settings(
        app_env="staging",
        database_url="postgresql://user:password@example.com/testexchange",
        supabase_url="https://example.supabase.co",
        cors_origins=["https://beta.example.com"],
        moderator_user_ids=["10000000-0000-0000-0000-000000000001"],
        sentry_dsn="https://public@example.ingest.sentry.io/1",
        sentry_release="testexchange-api@abc123",
        sentry_traces_sample_rate=0.1,
    )

    assert configure_error_tracking(settings) is True
    assert captured["environment"] == "staging"
    assert captured["release"] == "testexchange-api@abc123"
    assert captured["traces_sample_rate"] == 0.1
    assert captured["send_default_pii"] is False
