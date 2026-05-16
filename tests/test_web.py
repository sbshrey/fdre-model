from __future__ import annotations

import re
from types import SimpleNamespace
from pathlib import Path

from fdre_model.web.app import create_app
from fdre_model.web.auth import AUTH_SESSION_KEY, CurrentUser


class FakeAuth0Management:
    def __init__(self) -> None:
        self.users: dict[str, SimpleNamespace] = {}
        self.created: list[str] = []
        self.blocked: list[tuple[str, bool]] = []
        self.reset: list[str] = []
        self.deleted: list[str] = []

    def ensure_user(self, *, email: str, name: str = "") -> tuple[SimpleNamespace, bool]:
        normalized = email.strip().lower()
        if normalized in self.users:
            return self.users[normalized], False
        user = SimpleNamespace(
            user_id=f"auth0|{normalized}",
            email=normalized,
            raw={
                "user_id": f"auth0|{normalized}",
                "email": normalized,
                "name": name or normalized,
                "blocked": False,
                "email_verified": True,
                "created_at": "2026-05-16T00:00:00.000Z",
            },
        )
        self.users[normalized] = user
        self.created.append(normalized)
        return user, True

    def list_users(self, *, limit: int = 100) -> list[SimpleNamespace]:
        return sorted(self.users.values(), key=lambda user: user.email)[:limit]

    def find_user_by_email(self, email: str) -> SimpleNamespace | None:
        return self.users.get(email.strip().lower())

    def block_user(self, user_id: str, *, blocked: bool = True) -> None:
        self.blocked.append((user_id, blocked))
        for user in self.users.values():
            if user.user_id == user_id:
                user.raw["blocked"] = blocked

    def send_password_reset_email(self, email: str) -> str:
        self.reset.append(email.strip().lower())
        return "reset sent"

    def delete_user(self, user_id: str) -> None:
        self.deleted.append(user_id)


def test_live_board_inputs_rules_and_history_flow(tmp_path: Path) -> None:
    app = create_app(workspace_root=tmp_path / ".workspace")
    client = app.test_client()
    admin_headers = {"X-User-Email": "admin@example.com", "X-User-Role": "admin"}

    health = client.get("/api/health")
    assert health.status_code == 200

    live = client.get("/")
    assert live.status_code == 200
    assert b"Live Board" in live.data
    assert b"Decision Cycle" in live.data
    assert b"Source Health" in live.data
    assert b"Operations Alerts" in live.data
    assert b"Workbook Metrics" in live.data
    assert b"P1 forecast curtailment" in live.data
    assert b"Annual CUF" in live.data
    assert b"Why" in live.data
    assert b"default 6 actual + 1 live + 24 forecast = 31" in live.data
    assert b'data-syncfusion-grid="live-board"' in live.data
    assert b'class="why-column" data-grid-width="360">Why' in live.data
    assert b"Rule path" in live.data
    assert b"Technical audit" in live.data
    assert b"PPA selected" in live.data or b"Peak obligation" in live.data
    assert b"cdn.syncfusion.com/ej2/33.2.3" not in live.data
    assert b"syncfusion-tables.js" not in live.data

    inputs = client.get("/inputs")
    assert inputs.status_code == 200
    assert b"Solar Generation" in inputs.data
    assert b"input-versions-solar" in inputs.data
    assert b"Edit Active" in inputs.data

    upload = client.post(
        "/inputs/solar/manual",
        data={"csv_text": "timestamp,mwh\n2026-04-01 12:00,30\n", "source_type": "manual_1h"},
        follow_redirects=True,
    )
    assert upload.status_code == 200
    assert b"Manual input version saved" in upload.data

    rules = client.get("/rules", headers=admin_headers)
    assert rules.status_code == 200
    assert b"Peak Power Obligation" in rules.data
    assert b"Rule Versions" in rules.data
    assert b"Condition" in rules.data
    assert b"Action" in rules.data
    assert b"Rule Case Reference" in rules.data
    assert b"Case 6/7" in rules.data
    assert b"Rule Input Dictionary" in rules.data

    assumptions = client.get("/assumptions", headers=admin_headers)
    assert assumptions.status_code == 200
    assert b"Assumption Versions" in assumptions.data
    assert b"Variable Registry" in assumptions.data
    assert b"Cap10" in assumptions.data
    assert b"capacities.peak_power_mwh" in assumptions.data

    users = client.get("/users", headers=admin_headers)
    assert users.status_code == 200
    assert b"Access Control" in users.data

    recalc = client.post("/cycles/recalculate", follow_redirects=True)
    assert recalc.status_code == 200
    assert b"Decision cycle recalculated" in recalc.data

    history = client.get("/history")
    assert history.status_code == 200
    assert b"Cycles" in history.data


def test_live_board_can_preview_custom_date_range(tmp_path: Path) -> None:
    app = create_app(workspace_root=tmp_path / ".workspace")
    client = app.test_client()

    preview = client.get(
        "/?window_start=2026-01-01T08:00&live_at=2026-01-01T10:00&window_end=2026-01-01T13:00"
    )

    assert preview.status_code == 200
    assert b"2 actual + 1 live + 2 forecast = 5" in preview.data
    assert b"custom" in preview.data
    assert b"2026-01-01T08:00" in preview.data
    assert b"2026-01-01T10:00" in preview.data
    assert b"2026-01-01T13:00" in preview.data
    assert b"5 / 5 rows" in preview.data
    assert b"2026-01-01 08:00:00" in preview.data
    assert b"2026-01-01 12:00:00" in preview.data


def test_custom_live_board_recalculate_preserves_preview_range(tmp_path: Path) -> None:
    app = create_app(workspace_root=tmp_path / ".workspace")
    client = app.test_client()

    recalculated = client.post(
        "/cycles/recalculate",
        data={
            "window_start": "2026-01-01T08:00",
            "live_at": "2026-01-01T10:00",
            "window_end": "2026-01-01T13:00",
        },
        follow_redirects=True,
    )

    assert recalculated.status_code == 200
    assert b"Decision cycle recalculated" in recalculated.data
    assert b"2 actual + 1 live + 2 forecast = 5" in recalculated.data
    assert b"custom" in recalculated.data


def test_syncfusion_assets_load_only_with_license_key(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("FDRE_SYNCFUSION_LICENSE_KEY", "test-license-key")
    app = create_app(workspace_root=tmp_path / ".workspace")
    client = app.test_client()

    live = client.get("/")

    assert live.status_code == 200
    assert b"cdn.syncfusion.com/ej2/33.2.3" in live.data
    assert b"syncfusion-tables.js" in live.data
    assert b"test-license-key" in live.data


def test_input_versions_can_be_downloaded(tmp_path: Path) -> None:
    app = create_app(workspace_root=tmp_path / ".workspace")
    client = app.test_client()

    inputs = client.get("/inputs")
    assert inputs.status_code == 200
    assert b"Download Active CSV" in inputs.data
    assert b"Download" in inputs.data

    seeded_download_path = re.search(rb'href="(/inputs/solar/download/[^"]+)"', inputs.data)
    assert seeded_download_path is not None
    seeded_download = client.get(seeded_download_path.group(1).decode("utf-8"))
    assert seeded_download.status_code == 200
    assert seeded_download.headers["Content-Disposition"].startswith("attachment;")
    assert "fdre_solar_" in seeded_download.headers["Content-Disposition"]
    assert seeded_download.headers["Content-Type"].startswith("text/csv")
    assert seeded_download.data.startswith(b"timestamp,mwh")

    overridden = client.post(
        "/inputs/solar/manual",
        data={"csv_text": "timestamp,mwh\n2026-04-01 12:00,30\n", "source_type": "manual_1h"},
        follow_redirects=True,
    )
    assert overridden.status_code == 200
    manual_download_path = re.search(rb'href="(/inputs/solar/download/[^"]+)"', overridden.data)
    assert manual_download_path is not None
    manual_download = client.get(manual_download_path.group(1).decode("utf-8"))
    assert manual_download.status_code == 200
    assert b"2026-04-01 12:00,30" in manual_download.data


def test_active_input_can_be_edited_in_app_as_new_version(tmp_path: Path) -> None:
    app = create_app(workspace_root=tmp_path / ".workspace")
    client = app.test_client()

    editor = client.get("/inputs/solar/edit?start=2026-01-01T06:00&end=2026-01-01T08:00")

    assert editor.status_code == 200
    assert b"Edit Solar Generation" in editor.data
    assert b"2026-01-01 06:00:00" in editor.data
    assert b"Save Edited Version" in editor.data
    assert b'data-syncfusion-grid' not in editor.data

    saved = client.post(
        "/inputs/solar/edit",
        data={
            "start": "2026-01-01T06:00",
            "end": "2026-01-01T08:00",
            "timestamp": ["2026-01-01 06:00:00"],
            "mwh": ["123.456"],
        },
        follow_redirects=True,
    )

    assert saved.status_code == 200
    assert b"Saved 1 edited rows as active version" in saved.data
    assert b"in_app_table_edit" in saved.data

    download_path = re.search(rb'href="(/inputs/solar/download/[^"]+)"', saved.data)
    assert download_path is not None
    downloaded = client.get(download_path.group(1).decode("utf-8"))

    assert downloaded.status_code == 200
    assert b"2026-01-01 06:00:00,123.456" in downloaded.data


def test_peak_schedule_can_be_replaced_with_pasted_csv(tmp_path: Path) -> None:
    app = create_app(workspace_root=tmp_path / ".workspace")
    client = app.test_client()

    saved = client.post(
        "/inputs/peak_schedule/edit",
        data={
            "start": "2026-01-01T18:00",
            "end": "2026-01-01T19:00",
            "paste_csv": "timestamp,is_peak\n2026-01-01 18:00,0\n",
        },
        follow_redirects=True,
    )

    assert saved.status_code == 200
    assert b"Saved 1 edited rows as active version" in saved.data
    assert b"in_app_paste" in saved.data

    download_path = re.search(rb'href="(/inputs/peak_schedule/download/[^"]+)"', saved.data)
    assert download_path is not None
    downloaded = client.get(download_path.group(1).decode("utf-8"))

    assert downloaded.status_code == 200
    assert b"2026-01-01 18:00:00,0" in downloaded.data


def test_live_board_filters_and_acknowledgement_flow(tmp_path: Path) -> None:
    app = create_app(workspace_root=tmp_path / ".workspace")
    client = app.test_client()
    headers = {"X-User-Email": "operator@example.com", "X-User-Role": "operator"}

    live = client.get("/", headers=headers)
    cycle_id = re.search(rb"cycle-\d+", live.data).group(0).decode("utf-8")

    alert_rows = client.get("/?alerts=1&status=forecast", headers=headers)
    assert alert_rows.status_code == 200
    assert b"Shortfall exposure" in alert_rows.data
    assert b"forecast" in alert_rows.data

    acknowledged = client.post(
        f"/cycles/{cycle_id}/acknowledge",
        headers=headers,
        data={"note": "Reviewed live board."},
        follow_redirects=True,
    )
    assert acknowledged.status_code == 200
    assert b"Decision cycle acknowledged" in acknowledged.data
    assert b"Acknowledged by operator@example.com" in acknowledged.data

    history = client.get("/history", headers=headers)
    assert b"ack" in history.data
    assert b"operator@example.com" in history.data


def test_admin_routes_require_admin_role(tmp_path: Path) -> None:
    app = create_app(workspace_root=tmp_path / ".workspace")
    client = app.test_client()
    operator_headers = {"X-User-Email": "operator@example.com", "X-User-Role": "operator"}
    admin_headers = {"X-User-Email": "admin@example.com", "X-User-Role": "admin"}

    assert client.get("/rules", headers=operator_headers).status_code == 403
    assert client.post("/rules/save", headers=operator_headers).status_code == 403
    assert client.get("/assumptions", headers=operator_headers).status_code == 403
    assert client.post("/assumptions/save", headers=operator_headers).status_code == 403
    assert client.get("/users", headers=operator_headers).status_code == 403
    assert client.post("/users/save", headers=operator_headers).status_code == 403
    assert client.post("/users/operator@example.com/activate", headers=operator_headers).status_code == 403

    assert client.get("/rules", headers=admin_headers).status_code == 200
    assert client.get("/assumptions", headers=admin_headers).status_code == 200
    assert client.get("/users", headers=admin_headers).status_code == 200


def test_operator_nav_hides_admin_pages(tmp_path: Path) -> None:
    app = create_app(workspace_root=tmp_path / ".workspace")
    client = app.test_client()
    response = client.get("/", headers={"X-User-Email": "operator@example.com", "X-User-Role": "operator"})

    assert response.status_code == 200
    assert b">Rules<" not in response.data
    assert b">Assumptions<" not in response.data
    assert b">Users<" not in response.data


def test_admin_can_manage_workspace_users(tmp_path: Path) -> None:
    app = create_app(workspace_root=tmp_path / ".workspace")
    client = app.test_client()
    admin_headers = {"X-User-Email": "admin@example.com", "X-User-Role": "admin"}

    users_page = client.get("/users", headers=admin_headers)
    assert b"Activate by Email" in users_page.data
    assert b"Add or Update User" not in users_page.data
    assert b"Save User" not in users_page.data
    assert b"Reset Password" not in users_page.data
    assert b"Delete Auth0" not in users_page.data
    assert b"Create or sync Auth0 identity" not in users_page.data

    saved = client.post(
        "/users/save",
        headers=admin_headers,
        data={"email": "Operator@Example.com"},
        follow_redirects=True,
    )

    assert saved.status_code == 200
    assert b"User activated for this FDRE workspace" in saved.data
    assert b"operator@example.com" in saved.data
    assert b"operator" in saved.data

    deactivated = client.post(
        "/users/operator@example.com/deactivate",
        headers=admin_headers,
        follow_redirects=True,
    )

    assert deactivated.status_code == 200
    assert b"User deactivated" in deactivated.data
    assert b"inactive" in deactivated.data
    assert b"Activate" in deactivated.data

    reactivated = client.post(
        "/users/operator@example.com/activate",
        headers=admin_headers,
        follow_redirects=True,
    )

    assert reactivated.status_code == 200
    assert b"User activated for this FDRE workspace" in reactivated.data
    assert b"active" in reactivated.data


def test_admin_can_activate_existing_auth0_user_only(tmp_path: Path) -> None:
    auth0_management = FakeAuth0Management()
    auth0_management.ensure_user(email="operator@example.com")
    auth0_management.created.clear()
    app = create_app(workspace_root=tmp_path / ".workspace", auth0_management_client=auth0_management)
    client = app.test_client()
    admin_headers = {"X-User-Email": "admin@example.com", "X-User-Role": "admin"}

    listed = client.get("/users", headers=admin_headers)
    assert listed.status_code == 200
    assert b"Available Auth0 Users" in listed.data
    assert b"operator@example.com" in listed.data
    assert b"Default active" in listed.data
    assert b"auth0" in listed.data

    saved = client.post(
        "/users/save",
        headers=admin_headers,
        data={
            "email": "Operator@Example.com",
            "role": "admin",
            "sync_auth0": "on",
            "send_reset_email": "on",
        },
        follow_redirects=True,
    )

    assert saved.status_code == 200
    assert b"User activated for this FDRE workspace" in saved.data
    assert b"operator@example.com" in saved.data
    assert auth0_management.created == []
    assert auth0_management.reset == []
    assert auth0_management.blocked == [("auth0|operator@example.com", False)]

    missing = client.post(
        "/users/save",
        headers=admin_headers,
        data={"email": "missing@example.com"},
        follow_redirects=True,
    )

    assert b"Auth0 user was not found" in missing.data
    assert "missing@example.com" not in auth0_management.users


def test_deactivate_blocks_auth0_user_and_self_deactivation_is_denied(tmp_path: Path) -> None:
    auth0_management = FakeAuth0Management()
    auth0_management.ensure_user(email="operator@example.com")
    app = create_app(workspace_root=tmp_path / ".workspace", auth0_management_client=auth0_management)
    client = app.test_client()
    admin_headers = {"X-User-Email": "admin@example.com", "X-User-Role": "admin"}

    client.post(
        "/users/save",
        headers=admin_headers,
        data={"email": "operator@example.com", "role": "operator", "active": "on"},
    )
    deactivated = client.post(
        "/users/operator@example.com/deactivate",
        headers=admin_headers,
        follow_redirects=True,
    )

    assert deactivated.status_code == 200
    assert b"Matching Auth0 user blocked" in deactivated.data
    assert auth0_management.blocked[-1] == ("auth0|operator@example.com", True)

    denied = client.post(
        "/users/admin@example.com/deactivate",
        headers=admin_headers,
        follow_redirects=True,
    )

    assert b"Admins cannot deactivate their own active session" in denied.data


def test_password_reset_and_auth0_delete_routes_are_removed(tmp_path: Path) -> None:
    auth0_management = FakeAuth0Management()
    auth0_management.ensure_user(email="operator@example.com")
    app = create_app(workspace_root=tmp_path / ".workspace", auth0_management_client=auth0_management)
    client = app.test_client()
    admin_headers = {"X-User-Email": "admin@example.com", "X-User-Role": "admin"}

    reset = client.post("/users/operator@example.com/reset-password", headers=admin_headers)
    deleted = client.post("/users/operator@example.com/delete-auth0", headers=admin_headers)

    assert reset.status_code == 404
    assert deleted.status_code == 404
    assert auth0_management.reset == []
    assert auth0_management.deleted == []


def test_workspace_headers_isolate_live_board_state(tmp_path: Path) -> None:
    app = create_app(workspace_root=tmp_path / ".workspace")
    client = app.test_client()
    plant_a_headers = {
        "X-User-Email": "operator@example.com",
        "X-User-Role": "operator",
        "X-Customer-Id": "Acme",
        "X-Workspace-Id": "Plant A",
    }
    plant_b_headers = {
        "X-User-Email": "operator@example.com",
        "X-User-Role": "operator",
        "X-Customer-Id": "Acme",
        "X-Workspace-Id": "Plant B",
    }

    plant_a = client.get("/", headers=plant_a_headers)
    plant_b = client.get("/", headers=plant_b_headers)

    assert plant_a.status_code == 200
    assert plant_b.status_code == 200
    assert b"Workspace acme/plant-a" in plant_a.data
    assert b"Workspace acme/plant-b" in plant_b.data
    assert (tmp_path / ".workspace" / "customers" / "acme" / "workspaces" / "plant-a").exists()
    assert (tmp_path / ".workspace" / "customers" / "acme" / "workspaces" / "plant-b").exists()


def test_auth0_mode_requires_session_but_keeps_health_public(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("FDRE_AUTH0_DOMAIN", "example.auth0.com")
    monkeypatch.setenv("FDRE_AUTH0_CLIENT_ID", "client-id")
    monkeypatch.setenv("FDRE_AUTH0_CLIENT_SECRET", "client-secret")
    monkeypatch.setenv("FDRE_MODEL_PUBLIC_BASE_URL", "https://fdre.example.com")

    app = create_app(workspace_root=tmp_path / ".workspace")
    client = app.test_client()

    assert client.get("/api/health").status_code == 200
    live = client.get("/")
    assert live.status_code == 302
    assert live.headers["Location"] == "/login"
    api = client.post("/cycles/recalculate")
    assert api.status_code == 302


def test_auth0_mode_uses_app_managed_users(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("FDRE_AUTH0_DOMAIN", "example.auth0.com")
    monkeypatch.setenv("FDRE_AUTH0_CLIENT_ID", "client-id")
    monkeypatch.setenv("FDRE_AUTH0_CLIENT_SECRET", "client-secret")
    monkeypatch.setenv("FDRE_MODEL_PUBLIC_BASE_URL", "https://fdre.example.com")
    monkeypatch.setenv("FDRE_ADMIN_EMAILS", "admin@example.com")

    app = create_app(workspace_root=tmp_path / ".workspace")
    client = app.test_client()

    with client.session_transaction() as session:
        session[AUTH_SESSION_KEY] = CurrentUser.from_claims(
            {"sub": "auth0|admin", "email": "admin@example.com"}
        ).to_session()

    with client.session_transaction() as session:
        session[AUTH_SESSION_KEY] = CurrentUser.from_claims(
            {"sub": "auth0|operator", "email": "operator@example.com"}
        ).to_session()

    assert client.get("/").status_code == 200
    assert client.get("/rules").status_code == 403

    with client.session_transaction() as session:
        session[AUTH_SESSION_KEY] = CurrentUser.from_claims(
            {"sub": "auth0|admin", "email": "admin@example.com"}
        ).to_session()

    removed = client.post("/users/operator@example.com/deactivate", follow_redirects=True)
    assert removed.status_code == 200
    assert b"User deactivated" in removed.data

    with client.session_transaction() as session:
        session[AUTH_SESSION_KEY] = CurrentUser.from_claims(
            {"sub": "auth0|operator", "email": "operator@example.com"}
        ).to_session()

    blocked = client.get("/")
    assert blocked.status_code == 302
    assert blocked.headers["Location"] == "/unauthorized"
    assert client.get("/unauthorized").status_code == 403
