"""Identity and RBAC helpers for local trusted-header and hosted Auth0 modes."""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any, Mapping

from fdre_model.storage.scope import WorkspaceScope


ADMIN_ROLE = "admin"
OPERATOR_ROLE = "operator"
AUTH_SESSION_KEY = "auth_user"


@dataclass(frozen=True)
class CurrentUser:
    email: str
    role: str
    subject: str
    scope: WorkspaceScope
    name: str = ""

    @property
    def is_admin(self) -> bool:
        return self.role == ADMIN_ROLE

    @classmethod
    def from_claims(cls, claims: Mapping[str, Any]) -> "CurrentUser":
        subject = str(claims.get("sub") or "").strip()
        if not subject:
            raise ValueError("Auth0 profile is missing subject.")
        email = _normalize_email(str(claims.get("email") or ""))
        if not email:
            raise ValueError("Auth0 profile is missing email.")
        role = ADMIN_ROLE if email in admin_emails() else OPERATOR_ROLE
        return cls(
            email=email,
            role=role,
            subject=subject,
            scope=_default_scope(),
            name=str(claims.get("name") or claims.get("nickname") or email),
        )

    @classmethod
    def from_session(cls, payload: object) -> "CurrentUser | None":
        if not isinstance(payload, dict):
            return None
        email = _normalize_email(str(payload.get("email") or ""))
        subject = str(payload.get("subject") or payload.get("sub") or "").strip()
        if not email or not subject:
            return None
        role = str(payload.get("role") or OPERATOR_ROLE).strip().lower()
        if email in admin_emails():
            role = ADMIN_ROLE
        if role not in {ADMIN_ROLE, OPERATOR_ROLE}:
            role = OPERATOR_ROLE
        scope_payload = payload.get("scope") if isinstance(payload.get("scope"), dict) else {}
        scope = WorkspaceScope.from_values(
            str(scope_payload.get("customer_id") or "") or os.environ.get("FDRE_MODEL_CUSTOMER_ID"),
            str(scope_payload.get("workspace_id") or "") or os.environ.get("FDRE_MODEL_WORKSPACE_ID"),
        )
        return cls(
            email=email,
            role=role,
            subject=subject,
            scope=scope,
            name=str(payload.get("name") or email),
        )

    def to_session(self) -> dict[str, object]:
        return {
            "email": self.email,
            "role": self.role,
            "subject": self.subject,
            "name": self.name or self.email,
            "scope": self.scope.to_json(),
        }


def user_from_headers(headers: Mapping[str, str]) -> CurrentUser:
    email = (
        headers.get("X-User-Email")
        or os.environ.get("FDRE_MODEL_USER_EMAIL")
        or "local.operator@example.com"
    ).strip().lower()
    role = (
        headers.get("X-User-Role")
        or os.environ.get("FDRE_MODEL_USER_ROLE")
        or OPERATOR_ROLE
    ).strip().lower()
    env_admin_emails = admin_emails()
    if email in env_admin_emails:
        role = ADMIN_ROLE
    if role not in {ADMIN_ROLE, OPERATOR_ROLE}:
        role = OPERATOR_ROLE
    subject = (
        headers.get("X-Auth-Subject")
        or headers.get("X-User-Subject")
        or os.environ.get("FDRE_MODEL_AUTH_SUBJECT")
        or email
    ).strip()
    scope = WorkspaceScope.from_values(
        headers.get("X-Customer-Id") or os.environ.get("FDRE_MODEL_CUSTOMER_ID"),
        headers.get("X-Workspace-Id") or os.environ.get("FDRE_MODEL_WORKSPACE_ID"),
    )
    return CurrentUser(email=email, role=role, subject=subject, scope=scope, name=email)


def auth0_configured() -> bool:
    return bool(auth0_domain() and auth0_client_id() and auth0_client_secret())


def auth0_partially_configured() -> bool:
    values = [auth0_domain(), auth0_client_id(), auth0_client_secret()]
    return any(values) and not all(values)


def auth0_domain() -> str:
    domain = (_env_first("FDRE_AUTH0_DOMAIN", "AUTH0_DOMAIN") or "").strip()
    if domain.startswith("https://"):
        domain = domain[len("https://") :]
    return domain.rstrip("/")


def auth0_client_id() -> str:
    return (_env_first("FDRE_AUTH0_CLIENT_ID", "AUTH0_CLIENT_ID") or "").strip()


def auth0_client_secret() -> str:
    return _env_first("FDRE_AUTH0_CLIENT_SECRET", "AUTH0_CLIENT_SECRET") or ""


def public_base_url() -> str:
    return (
        _env_first(
            "FDRE_MODEL_PUBLIC_BASE_URL",
            "FDRE_PUBLIC_BASE_URL",
            "SECI_FDRE_V_PUBLIC_BASE_URL",
        )
        or ""
    ).rstrip("/")


def admin_emails() -> set[str]:
    raw_values = [
        os.environ.get("FDRE_ADMIN_EMAILS") or "",
        os.environ.get("FDRE_MODEL_ADMIN_EMAILS") or "",
    ]
    emails: set[str] = set()
    for raw in raw_values:
        emails.update(_normalize_email(part) for part in raw.replace(";", ",").split(",") if _normalize_email(part))
    return emails


def _default_scope() -> WorkspaceScope:
    return WorkspaceScope.from_values(
        os.environ.get("FDRE_MODEL_CUSTOMER_ID"),
        os.environ.get("FDRE_MODEL_WORKSPACE_ID"),
    )


def _normalize_email(value: str) -> str:
    return value.strip().lower()


def _env_first(*names: str) -> str | None:
    for name in names:
        value = os.environ.get(name)
        if value:
            return value
    return None
