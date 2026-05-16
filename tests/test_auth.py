from __future__ import annotations

from fdre_model.web.auth import CurrentUser, user_from_headers


def test_user_scope_comes_from_trusted_headers_and_is_sanitized() -> None:
    user = user_from_headers(
        {
            "X-User-Email": "Operator@Example.com",
            "X-User-Role": "operator",
            "X-Customer-Id": "Acme Energy Ltd.",
            "X-Workspace-Id": "Plant A / North",
            "X-Auth-Subject": "auth0|123",
        }
    )

    assert user.email == "operator@example.com"
    assert user.subject == "auth0|123"
    assert user.scope.customer_id == "acme-energy-ltd"
    assert user.scope.workspace_id == "plant-a-north"


def test_auth0_claims_create_admin_user_with_default_scope(monkeypatch) -> None:
    monkeypatch.setenv("FDRE_MODEL_ADMIN_EMAILS", "Admin@Example.com")
    monkeypatch.setenv("FDRE_MODEL_CUSTOMER_ID", "Cargill Demo")
    monkeypatch.setenv("FDRE_MODEL_WORKSPACE_ID", "FDRE Ops")

    user = CurrentUser.from_claims(
        {
            "sub": "auth0|abc",
            "email": "Admin@Example.com",
            "name": "Admin User",
        }
    )
    loaded = CurrentUser.from_session(user.to_session())

    assert user.is_admin
    assert user.email == "admin@example.com"
    assert user.scope.customer_id == "cargill-demo"
    assert user.scope.workspace_id == "fdre-ops"
    assert loaded == user


def test_fdre_admin_email_alias_creates_admin_user(monkeypatch) -> None:
    monkeypatch.delenv("FDRE_MODEL_ADMIN_EMAILS", raising=False)
    monkeypatch.setenv("FDRE_ADMIN_EMAILS", "Srinivas.Sista@Digitised.Energy;sbshrey@gmail.com")

    user = CurrentUser.from_claims(
        {
            "sub": "auth0|alias",
            "email": "srinivas.sista@digitised.energy",
        }
    )

    assert user.is_admin
