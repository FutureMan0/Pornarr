from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import SecretStr

from pornarr_shared.config import MINIMUM_SECRET_LENGTH, Settings, load_settings
from pornarr_shared.errors import ConfigurationError

VALID_ENVIRONMENT = {
    "APP_SECRET": "a" * MINIMUM_SECRET_LENGTH,
    "DATABASE_URL": "postgresql+psycopg://pornarr:pornarr@localhost:5432/pornarr",
    "REDIS_URL": "redis://localhost:6379/0",
}


@pytest.fixture(autouse=True)
def _isolate_environment(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Settings read a .env file by default; tests must not pick up the developer's."""
    for key in (*VALID_ENVIRONMENT, "DATA_PATH", "BASE_PATH", "LOG_LEVEL"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.chdir(tmp_path)


def test_missing_secret_reports_the_field_and_how_to_fix_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for key, value in VALID_ENVIRONMENT.items():
        if key != "APP_SECRET":
            monkeypatch.setenv(key, value)

    with pytest.raises(ConfigurationError) as caught:
        load_settings()

    message = caught.value.message
    assert "APP_SECRET" in message
    assert ".env.example" in message


def test_short_secret_is_rejected_with_a_usable_instruction(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for key, value in VALID_ENVIRONMENT.items():
        monkeypatch.setenv(key, value)
    monkeypatch.setenv("APP_SECRET", "tooshort")

    with pytest.raises(ConfigurationError) as caught:
        load_settings()

    assert "openssl rand -hex 32" in caught.value.message


def test_valid_environment_produces_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    for key, value in VALID_ENVIRONMENT.items():
        monkeypatch.setenv(key, value)

    settings = load_settings()

    assert settings.app_env == "development"
    assert settings.default_auto_downloads_enabled is False
    assert settings.min_free_disk_percent == 15
    assert settings.oidc_allow_private_issuers is False
    assert settings.rss_sync_interval_minutes == 15


def test_empty_optional_number_settings_use_none(monkeypatch: pytest.MonkeyPatch) -> None:
    for key, value in VALID_ENVIRONMENT.items():
        monkeypatch.setenv(key, value)
    for key in (
        "BACKUP_MAX_AGE_HOURS",
        "TRANSCODE_MAX_HW_SESSIONS",
        "TRANSCODE_MAX_SW_SESSIONS",
        "AUDIT_RETENTION_DAYS",
    ):
        monkeypatch.setenv(key, "")

    settings = load_settings()

    assert settings.backup_max_age_hours is None
    assert settings.transcode_max_hw_sessions is None
    assert settings.transcode_max_sw_sessions is None
    assert settings.audit_retention_days is None


def test_rss_sync_interval_must_divide_an_hour(monkeypatch: pytest.MonkeyPatch) -> None:
    for key, value in VALID_ENVIRONMENT.items():
        monkeypatch.setenv(key, value)
    monkeypatch.setenv("RSS_SYNC_INTERVAL_MINUTES", "7")

    with pytest.raises(ConfigurationError, match="RSS_SYNC_INTERVAL_MINUTES must divide 60"):
        load_settings()


def test_private_oidc_issuers_require_an_explicit_opt_in(monkeypatch: pytest.MonkeyPatch) -> None:
    for key, value in VALID_ENVIRONMENT.items():
        monkeypatch.setenv(key, value)
    monkeypatch.setenv("OIDC_ALLOW_PRIVATE_ISSUERS", "true")

    assert load_settings().oidc_allow_private_issuers is True


@pytest.mark.parametrize("given", ["pornarr", "/pornarr", "/pornarr/", "  /pornarr  "])
def test_base_path_accepts_every_form_an_operator_might_type(given: str) -> None:
    assert build_settings(base_path=given).base_path == "/pornarr"


def test_empty_base_path_stays_empty() -> None:
    assert build_settings(base_path="/").base_path == ""


def test_derived_paths_all_sit_under_one_data_root() -> None:
    settings = build_settings(data_path=Path("/srv/pornarr"))

    derived = [
        settings.torrents_path,
        settings.usenet_path,
        settings.library_path,
        settings.quarantine_path,
        settings.thumbnail_path,
        settings.transcode_path,
    ]

    assert all(path.parent == Path("/srv/pornarr") for path in derived)
    assert len(set(derived)) == len(derived)


def test_secret_does_not_appear_in_repr_or_str() -> None:
    settings = build_settings()
    rendered = f"{settings!r} {settings}"
    assert VALID_ENVIRONMENT["APP_SECRET"] not in rendered
    assert settings.app_secret.get_secret_value() == VALID_ENVIRONMENT["APP_SECRET"]


def test_settings_are_immutable() -> None:
    settings = build_settings()
    with pytest.raises(ValueError, match="frozen"):
        settings.log_level = "debug"  # type: ignore[misc]


def build_settings(base_path: str = "", data_path: Path = Path("/data")) -> Settings:
    """Construct settings with explicit arguments.

    Deliberately not `Settings(**kwargs)`: the type checker expands a `**` unpack
    against every keyword parameter of `BaseSettings.__init__` and reports one
    diagnostic per mismatch, which buried 105 errors that were all this.
    """
    return Settings(
        app_secret=SecretStr(VALID_ENVIRONMENT["APP_SECRET"]),
        database_url=VALID_ENVIRONMENT["DATABASE_URL"],
        redis_url=VALID_ENVIRONMENT["REDIS_URL"],
        base_path=base_path,
        data_path=data_path,
    )
