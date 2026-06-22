from app.core.config import Settings


def test_clerk_settings_load_from_env(monkeypatch):
    monkeypatch.setenv("CLERK_ISSUER", "https://example.clerk.accounts.dev")
    monkeypatch.setenv(
        "CLERK_JWKS_URL", "https://example.clerk.accounts.dev/.well-known/jwks.json"
    )
    s = Settings()
    assert s.clerk_issuer == "https://example.clerk.accounts.dev"
    assert s.clerk_jwks_url.endswith("/.well-known/jwks.json")


def test_supabase_auth_settings_removed():
    s = Settings()
    assert not hasattr(s, "supabase_jwt_secret")
    assert not hasattr(s, "supabase_url")
