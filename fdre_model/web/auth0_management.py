"""Small Auth0 Management API adapter for FDRE admin user operations."""

from __future__ import annotations

import os
import time
from dataclasses import dataclass
from typing import Any
from urllib.parse import quote

import requests

from fdre_model.web.auth import auth0_domain


DEFAULT_DATABASE_CONNECTION = "Username-Password-Authentication"


class Auth0ManagementError(RuntimeError):
    """Raised when an Auth0 user-management operation fails."""


@dataclass(frozen=True)
class Auth0UserResult:
    user_id: str
    email: str
    raw: dict[str, Any]


class Auth0ManagementClient:
    def __init__(
        self,
        *,
        domain: str,
        client_id: str,
        client_secret: str,
        connection_name: str,
        session: requests.Session | None = None,
    ) -> None:
        self.domain = _normalize_domain(domain)
        self.client_id = client_id.strip()
        self.client_secret = client_secret
        self.connection_name = connection_name.strip() or DEFAULT_DATABASE_CONNECTION
        self.session = session or requests.Session()
        self._token: str | None = None
        self._token_expires_at = 0.0

    @classmethod
    def from_env(cls) -> "Auth0ManagementClient | None":
        _load_local_dotenv()
        domain = auth0_domain()
        client_id = _env_first("FDRE_AUTH0_MGMT_CLIENT_ID", "AUTH0_MGMT_CLIENT_ID")
        client_secret = _env_first("FDRE_AUTH0_MGMT_CLIENT_SECRET", "AUTH0_MGMT_CLIENT_SECRET")
        if not domain or not client_id or not client_secret:
            return None
        return cls(
            domain=domain,
            client_id=client_id,
            client_secret=client_secret,
            connection_name=auth0_connection_name(),
        )

    def find_user_by_email(self, email: str) -> Auth0UserResult | None:
        response = self._management_request("GET", "/api/v2/users-by-email", params={"email": email.strip().lower()})
        users = response if isinstance(response, list) else []
        for user in users:
            if str(user.get("email") or "").strip().lower() == email.strip().lower():
                return _user_result(user)
        return _user_result(users[0]) if users else None

    def list_users(self, *, limit: int = 100) -> list[Auth0UserResult]:
        users: list[Auth0UserResult] = []
        page = 0
        remaining = max(1, limit)
        while remaining > 0:
            page_size = min(remaining, 100)
            response = self._management_request(
                "GET",
                "/api/v2/users",
                params={
                    "page": page,
                    "per_page": page_size,
                    "include_totals": "false",
                    "fields": "user_id,email,name,nickname,blocked,email_verified,last_login,created_at,identities",
                    "include_fields": "true",
                },
            )
            batch = response if isinstance(response, list) else []
            if not batch:
                break
            users.extend(_user_result(user) for user in batch if str(user.get("email") or "").strip())
            if len(batch) < page_size:
                break
            remaining -= len(batch)
            page += 1
        users.sort(key=lambda item: item.email)
        return users

    def block_user(self, user_id: str, *, blocked: bool = True) -> None:
        self._management_request(
            "PATCH",
            f"/api/v2/users/{quote(user_id, safe='')}",
            json={"blocked": blocked},
            expected_statuses={200},
        )

    def _management_request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json: dict[str, Any] | None = None,
        expected_statuses: set[int] | None = None,
    ) -> Any:
        expected = expected_statuses or {200}
        response = self.session.request(
            method,
            f"https://{self.domain}{path}",
            headers={"Authorization": f"Bearer {self._access_token()}"},
            params=params,
            json=json,
            timeout=20,
        )
        if response.status_code not in expected:
            raise Auth0ManagementError(_response_error(response, "Auth0 Management API request failed."))
        if response.status_code == 204 or not response.content:
            return None
        try:
            return response.json()
        except ValueError as exc:
            raise Auth0ManagementError("Auth0 Management API returned invalid JSON.") from exc

    def _access_token(self) -> str:
        if self._token and time.time() < self._token_expires_at:
            return self._token
        response = self.session.post(
            f"https://{self.domain}/oauth/token",
            json={
                "grant_type": "client_credentials",
                "client_id": self.client_id,
                "client_secret": self.client_secret,
                "audience": f"https://{self.domain}/api/v2/",
            },
            timeout=20,
        )
        if response.status_code != 200:
            raise Auth0ManagementError(_response_error(response, "Auth0 Management API token request failed."))
        try:
            payload = response.json()
        except ValueError as exc:
            raise Auth0ManagementError("Auth0 token endpoint returned invalid JSON.") from exc
        token = str(payload.get("access_token") or "")
        if not token:
            raise Auth0ManagementError("Auth0 token endpoint did not return an access token.")
        expires_in = int(payload.get("expires_in") or 3600)
        self._token = token
        self._token_expires_at = time.time() + max(expires_in - 60, 60)
        return token


def auth0_connection_name() -> str:
    return (
        _env_first(
            "FDRE_AUTH0_CONNECTION_NAME",
            "FDRE_AUTH0_CONNECTION",
            "AUTH0_CONNECTION_NAME",
            "AUTH0_CONNECTION",
        )
        or DEFAULT_DATABASE_CONNECTION
    ).strip()


def _user_result(payload: dict[str, Any]) -> Auth0UserResult:
    return Auth0UserResult(
        user_id=str(payload.get("user_id") or ""),
        email=str(payload.get("email") or "").strip().lower(),
        raw=dict(payload),
    )


def _response_error(response: requests.Response, fallback: str) -> str:
    message = fallback
    try:
        payload = response.json()
    except ValueError:
        payload = {}
    if isinstance(payload, dict):
        message = str(payload.get("message") or payload.get("error_description") or payload.get("error") or message)
    return f"{message} (HTTP {response.status_code})"


def _normalize_domain(domain: str) -> str:
    result = domain.strip()
    if result.startswith("https://"):
        result = result[len("https://") :]
    return result.rstrip("/")


def _env_first(*names: str) -> str | None:
    for name in names:
        value = os.environ.get(name)
        if value:
            return value
    return None


def _load_local_dotenv() -> None:
    if os.environ.get("PYTEST_CURRENT_TEST"):
        return
    try:
        from dotenv import load_dotenv
    except ImportError:
        return
    load_dotenv()
