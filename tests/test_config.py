from app.core.config import Settings, get_settings


def test_get_settings_returns_cached_instance():
    a = get_settings()
    b = get_settings()
    assert a is b


def test_settings_have_sane_defaults():
    settings = Settings()
    assert settings.environment in ("local", "test", "staging", "production")
    assert settings.api_v1_prefix.startswith("/")
    assert settings.max_upload_size_mb > 0
    assert ".pdf" in settings.allowed_upload_extensions


def test_settings_override_via_env(monkeypatch):
    monkeypatch.setenv("APP_NAME", "Test Override App")
    monkeypatch.setenv("ENVIRONMENT", "test")
    settings = Settings()
    assert settings.app_name == "Test Override App"
    assert settings.environment == "test"
