import pytest

from campwatch.sessions import EnvSessionProvider, SessionError


def test_env_session_provider_reads_material():
    provider = EnvSessionProvider(
        {"SESSION_WA_PRIMARY_COOKIE": "c", "SESSION_WA_PRIMARY_CSRF": "t"}
    )
    session = provider.get_session("wa_primary")
    assert session.cookie == "c"
    assert session.csrf == "t"


def test_missing_material_raises_with_key_names():
    provider = EnvSessionProvider({"SESSION_WA_PRIMARY_COOKIE": "c"})
    with pytest.raises(SessionError, match="SESSION_WA_PRIMARY_CSRF"):
        provider.get_session("wa_primary")
