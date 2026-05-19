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

    landing = client.get("/")
    assert landing.status_code == 302
    assert landing.headers["Location"] == "/portfolio"

    portfolio = client.get("/portfolio")
    assert portfolio.status_code == 200
    assert b"FDRE Project Operations" in portfolio.data
    assert b"Portfolio range: Running window" in portfolio.data
    assert b"Risk level" in portfolio.data
    assert b"Shortfall MWh" not in portfolio.data
    assert b'name="project_id" value="default"' not in portfolio.data
    assert b"date-range-menu" in portfolio.data
    assert b"app-ui.js" in portfolio.data

    live = client.get("/live")
    assert live.status_code == 200
    assert b"Live Board" in live.data
    assert b"Decision Cycle" in live.data
    assert b"Input Readiness" in live.data
    assert b"Source Health" not in live.data
    assert b"Operations Alerts" in live.data
    assert b"Performance Indicators" in live.data
    assert b"Workbook Metrics" not in live.data
    assert b"Forecast risk MWh" in live.data
    assert b"P1 forecast curtailment" not in live.data
    assert b"Annual CUF" in live.data
    assert b"Decision basis" in live.data
    assert b"Non-peak workbook dispatch" in live.data
    assert b"More" in live.data
    assert b"Current operating mix" in live.data
    assert b"default 6 actual + 1 live + 24 forecast = 31" not in live.data
    assert b'data-syncfusion-grid="live-board"' in live.data
    assert b'data-collapsible' in live.data
    assert b'id="live-window-trigger"' in live.data
    assert b'id="live-source-health-trigger"' in live.data
    assert b'<th data-grid-width="190">Interval</th>' in live.data
    assert b'class="why-column" data-grid-width="320">Decision basis' in live.data
    assert b'data-grid-hidden="true">Wind' not in live.data
    assert b"Run details" not in live.data
    assert b"Export" in live.data
    assert b"Report CSV" in live.data
    assert b"Report XLSX" in live.data
    assert b'href="/live/download/allocation_csv"' in live.data
    assert b'href="/live/download/workbook"' in live.data
    client_csv = client.get("/live/download/allocation_csv")
    assert client_csv.status_code == 200
    assert client_csv.headers["Content-Disposition"] == "attachment; filename=market_allocation_public.csv"
    assert b"audit_trace" not in client_csv.data
    client_xlsx = client.get("/live/download/workbook")
    assert client_xlsx.status_code == 200
    assert client_xlsx.headers["Content-Disposition"].startswith("attachment; filename=fdre_market_model_public.xlsx")
    assert b"Rule path" not in live.data
    assert b"Technical audit" not in live.data
    assert b"cycle-" not in live.data
    assert b"PPA selected" in live.data or b"Peak obligation" in live.data
    assert b"cdn.syncfusion.com/ej2/33.2.3" not in live.data
    assert b"syncfusion-tables.js" not in live.data

    admin_live = client.get("/live", headers=admin_headers)
    assert admin_live.status_code == 200
    assert b"Source Health" in admin_live.data
    assert b"Workbook Metrics" in admin_live.data
    assert b"P1 forecast curtailment" in admin_live.data
    assert b"default 6 actual + 1 live + 24 forecast = 31" in admin_live.data
    assert b'data-grid-hidden="true">Wind' in admin_live.data
    assert b"Rule path" in admin_live.data
    assert b"Technical audit" in admin_live.data

    inputs = client.get("/inputs")
    assert inputs.status_code == 200
    assert b"Solar Generation" in inputs.data
    assert b"Input Readiness" in inputs.data
    assert b"Update Solar Generation" in inputs.data
    assert b"Guided Update" in inputs.data
    assert b"input-versions-solar" not in inputs.data
    assert b"Manage Solar Generation" not in inputs.data
    assert b"Expected CSV" not in inputs.data
    assert b"Edit Active" not in inputs.data
    assert b"Download Active CSV" not in inputs.data
    assert b"Active sources" in inputs.data
    assert b"Operator overrides" in inputs.data

    admin_inputs = client.get("/inputs", headers=admin_headers)
    assert b"input-versions-solar" in admin_inputs.data
    assert b"Manage Solar Generation" in admin_inputs.data
    assert b"Expected CSV" in admin_inputs.data
    assert b"Edit Active" in admin_inputs.data
    assert b"Download Active CSV" in admin_inputs.data

    upload = client.post(
        "/inputs/solar/manual",
        data={"csv_text": "timestamp,mwh\n2026-04-01 12:00,30\n", "source_type": "manual_1h"},
        follow_redirects=True,
    )
    assert upload.status_code == 200
    assert b"Manual input update saved" in upload.data

    rules = client.get("/rules", headers=admin_headers)
    assert rules.status_code == 200
    assert b"Peak Power Obligation" in rules.data
    assert b"Rule Versions" in rules.data
    assert b"Active Rule Version" in rules.data
    assert b"Condition" in rules.data
    assert b"Action" in rules.data
    assert b"Rule Case Reference" in rules.data
    assert b"Case 6/7" in rules.data
    assert b"Rule Input Dictionary" in rules.data

    assumptions = client.get("/assumptions", headers=admin_headers)
    assert assumptions.status_code == 200
    assert b"Assumption Versions" in assumptions.data
    assert b"Market Model" in assumptions.data
    assert b"Capacities" in assumptions.data
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
    assert b"Decision Reports" in history.data
    assert b"Cycle details" not in history.data
    assert b"Workspace local-customer/default" not in history.data
    assert b"Assumptions" not in history.data
    admin_history = client.get("/history", headers=admin_headers)
    assert b"Cycles" in admin_history.data
    assert b"Cycle details" in admin_history.data
    assert b"Assumptions" in admin_history.data


def test_live_board_can_preview_custom_date_range(tmp_path: Path) -> None:
    app = create_app(workspace_root=tmp_path / ".workspace")
    client = app.test_client()
    admin_headers = {"X-User-Email": "admin@example.com", "X-User-Role": "admin"}

    preview = client.get(
        "/live?window_start=2026-01-01T08:00&live_at=2026-01-01T10:00&window_end=2026-01-01T13:00",
        headers=admin_headers,
    )

    assert preview.status_code == 200
    assert b"2 actual + 1 live + 2 forecast = 5" in preview.data
    assert b"custom" in preview.data
    assert b"2026-01-01T08:00" in preview.data
    assert b"2026-01-01T10:00" in preview.data
    assert b"2026-01-01T13:00" in preview.data
    assert b"System live interval" in preview.data
    assert b"Live Interval</span><input" not in preview.data
    assert b"5 / 5 rows" in preview.data
    assert b"2026-01-01 08:00:00" in preview.data
    assert b"2026-01-01 12:00:00" in preview.data


def test_global_date_range_context_applies_to_date_aware_pages(tmp_path: Path) -> None:
    app = create_app(workspace_root=tmp_path / ".workspace")
    client = app.test_client()

    applied = client.post(
        "/date-range",
        data={
            "preset": "custom",
            "window_start": "2026-01-01T08:00",
            "window_end": "2026-01-01T13:00",
            "next": "/live",
        },
        follow_redirects=True,
    )

    assert applied.status_code == 200
    assert b"custom" in applied.data
    assert b"Custom 2026-01-01 08:00 to 2026-01-01 13:00" in applied.data
    assert b">5h</span>" in applied.data
    assert b"Jan 1, 8:00 am - Jan 1, 1:00 pm" in applied.data
    assert b'name="live_at"' not in applied.data
    assert b"date-menu-screen" in applied.data
    assert b"date-direct-form" in applied.data
    assert b"data-calendar-screen-open" in applied.data
    assert b"data-date-calendar-screen" in applied.data
    assert b"date-range-editor" in applied.data
    assert b"data-range-start" in applied.data
    assert b"data-range-end" in applied.data
    assert b"data-range-duration" in applied.data
    assert b"date-range-calendar" in applied.data
    assert b"data-calendar-grid" in applied.data
    assert b"data-range-start-time" in applied.data
    assert b'value="23:59"' in applied.data

    portfolio = client.get("/portfolio")
    assert b"Portfolio range: Custom 2026-01-01 08:00 to 2026-01-01 13:00" in portfolio.data

    history = client.get("/history")
    assert b"Showing Custom 2026-01-01 08:00 to 2026-01-01 13:00" in history.data


def test_custom_live_board_recalculate_preserves_preview_range(tmp_path: Path) -> None:
    app = create_app(workspace_root=tmp_path / ".workspace")
    client = app.test_client()
    admin_headers = {"X-User-Email": "admin@example.com", "X-User-Role": "admin"}

    recalculated = client.post(
        "/cycles/recalculate",
        headers=admin_headers,
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

    live = client.get("/live")

    assert live.status_code == 200
    assert b"cdn.syncfusion.com/ej2/33.2.3" in live.data
    assert b"syncfusion-tables.js" in live.data
    assert b"test-license-key" in live.data


def test_input_versions_can_be_downloaded(tmp_path: Path) -> None:
    app = create_app(workspace_root=tmp_path / ".workspace")
    client = app.test_client()
    admin_headers = {"X-User-Email": "admin@example.com", "X-User-Role": "admin"}

    inputs = client.get("/inputs")
    assert inputs.status_code == 200
    assert b"Download Active CSV" not in inputs.data

    inputs = client.get("/inputs", headers=admin_headers)
    assert inputs.status_code == 200
    assert b"Download Active CSV" in inputs.data

    seeded_download_path = re.search(rb'href="(/inputs/solar/download/[^"]+)"', inputs.data)
    assert seeded_download_path is not None
    assert client.get(seeded_download_path.group(1).decode("utf-8")).status_code == 403
    seeded_download = client.get(seeded_download_path.group(1).decode("utf-8"), headers=admin_headers)
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
    admin_inputs = client.get("/inputs", headers=admin_headers)
    manual_download_path = re.search(rb'href="(/inputs/solar/download/[^"]+)"', admin_inputs.data)
    assert manual_download_path is not None
    manual_download = client.get(manual_download_path.group(1).decode("utf-8"), headers=admin_headers)
    assert manual_download.status_code == 200
    assert b"2026-04-01 12:00,30" in manual_download.data


def test_operator_cycle_downloads_hide_internal_rule_audit(tmp_path: Path) -> None:
    app = create_app(workspace_root=tmp_path / ".workspace")
    client = app.test_client()
    admin_headers = {"X-User-Email": "admin@example.com", "X-User-Role": "admin"}

    live = client.get("/live", headers=admin_headers)
    cycle_id = re.search(rb"cycle-\d+", live.data).group(0).decode("utf-8")

    operator_csv = client.get(f"/cycles/{cycle_id}/download/allocation_csv")

    assert operator_csv.status_code == 200
    assert operator_csv.headers["Content-Disposition"] == "attachment; filename=market_allocation_public.csv"
    assert b"reason" in operator_csv.data
    assert b"rule" not in operator_csv.data
    assert b"wind_mwh" not in operator_csv.data
    assert b"ppa_sale_mwh" not in operator_csv.data
    assert b"bess_open_mwh" not in operator_csv.data
    assert b"audit_trace" not in operator_csv.data
    assert b"skipped_rule_ids" not in operator_csv.data
    assert b"input_versions=" not in operator_csv.data

    blocked_json = client.get(f"/cycles/{cycle_id}/download/input_versions_json")
    assert blocked_json.status_code == 403

    admin_csv = client.get(f"/cycles/{cycle_id}/download/allocation_csv", headers=admin_headers)
    assert admin_csv.status_code == 200
    assert b"audit_trace" in admin_csv.data
    assert b"skipped_rule_ids" in admin_csv.data


def test_active_input_can_be_edited_in_app_as_new_version(tmp_path: Path) -> None:
    app = create_app(workspace_root=tmp_path / ".workspace")
    client = app.test_client()
    admin_headers = {"X-User-Email": "admin@example.com", "X-User-Role": "admin"}

    editor = client.get("/inputs/solar/edit?start=2026-01-01T06:00&end=2026-01-01T08:00")

    assert editor.status_code == 200
    assert b"Update Solar Generation" in editor.data
    assert b"2026-01-01 06:00:00" in editor.data
    assert b"Save Update" in editor.data
    assert b"Download Active CSV" not in editor.data
    assert b"Expected CSV" not in editor.data
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
    assert b"Input updates saved and activated" in saved.data
    assert b"in_app_table_edit" not in saved.data

    admin_inputs = client.get("/inputs", headers=admin_headers)
    download_path = re.search(rb'href="(/inputs/solar/download/[^"]+)"', admin_inputs.data)
    assert download_path is not None
    downloaded = client.get(download_path.group(1).decode("utf-8"), headers=admin_headers)

    assert downloaded.status_code == 200
    assert b"2026-01-01 06:00:00,123.456" in downloaded.data


def test_peak_schedule_can_be_replaced_with_pasted_csv(tmp_path: Path) -> None:
    app = create_app(workspace_root=tmp_path / ".workspace")
    client = app.test_client()
    admin_headers = {"X-User-Email": "admin@example.com", "X-User-Role": "admin"}

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
    assert b"Input updates saved and activated" in saved.data
    assert b"in_app_paste" not in saved.data

    admin_inputs = client.get("/inputs", headers=admin_headers)
    download_path = re.search(rb'href="(/inputs/peak_schedule/download/[^"]+)"', admin_inputs.data)
    assert download_path is not None
    downloaded = client.get(download_path.group(1).decode("utf-8"), headers=admin_headers)

    assert downloaded.status_code == 200
    assert b"2026-01-01 18:00:00,0" in downloaded.data


def test_live_board_filters_and_acknowledgement_flow(tmp_path: Path) -> None:
    app = create_app(workspace_root=tmp_path / ".workspace")
    client = app.test_client()
    headers = {"X-User-Email": "operator@example.com", "X-User-Role": "operator"}

    live = client.get("/live", headers=headers)
    assert live.status_code == 200
    assert b"cycle-" not in live.data

    alert_rows = client.get("/live?alerts=1&status=forecast", headers=headers)
    assert alert_rows.status_code == 200
    assert b"Shortfall exposure" in alert_rows.data
    assert b"forecast" in alert_rows.data

    acknowledged = client.post(
        "/cycles/current/acknowledge",
        headers=headers,
        data={"note": "Reviewed live board."},
        follow_redirects=True,
    )
    assert acknowledged.status_code == 200
    assert b"Decision cycle acknowledged" in acknowledged.data
    assert b"Acknowledged by operator@example.com" in acknowledged.data

    history = client.get("/history", headers=headers)
    assert b"Acknowledged" in history.data
    assert b'<span class="muted">operator@example.com' not in history.data


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


def test_auth0_client_admin_is_not_model_admin_by_default(tmp_path: Path, monkeypatch) -> None:
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

    portfolio = client.get("/portfolio")

    assert portfolio.status_code == 200
    assert b">Rules<" not in portfolio.data
    assert b">Assumptions<" not in portfolio.data
    assert b">Users<" in portfolio.data
    assert b"Project code" in portfolio.data
    assert b"Offtaker" not in portfolio.data
    assert b"Capacity Summary" not in portfolio.data
    assert client.get("/rules").status_code == 403
    assert client.post("/rules/save").status_code == 403
    assert client.get("/assumptions").status_code == 403
    assert client.post("/assumptions/save").status_code == 403
    users_page = client.get("/users")
    assert users_page.status_code == 200
    assert b"Inactive access" in users_page.data
    assert b"Auth0" not in users_page.data
    assert b"Connection" not in users_page.data
    assert b"Environment Admins" not in users_page.data
    assert b"data-grid-hidden=\"true\">Source" not in users_page.data


def test_auth0_internal_admin_can_access_model_admin_pages(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("FDRE_AUTH0_DOMAIN", "example.auth0.com")
    monkeypatch.setenv("FDRE_AUTH0_CLIENT_ID", "client-id")
    monkeypatch.setenv("FDRE_AUTH0_CLIENT_SECRET", "client-secret")
    monkeypatch.setenv("FDRE_MODEL_PUBLIC_BASE_URL", "https://fdre.example.com")
    monkeypatch.setenv("FDRE_ADMIN_EMAILS", "admin@example.com")
    monkeypatch.setenv("FDRE_MODEL_INTERNAL_EMAILS", "admin@example.com")

    app = create_app(workspace_root=tmp_path / ".workspace")
    client = app.test_client()
    with client.session_transaction() as session:
        session[AUTH_SESSION_KEY] = CurrentUser.from_claims(
            {"sub": "auth0|admin", "email": "admin@example.com"}
        ).to_session()

    portfolio = client.get("/portfolio")

    assert portfolio.status_code == 200
    assert b">Rules<" in portfolio.data
    assert b">Assumptions<" in portfolio.data
    assert client.get("/rules").status_code == 200
    assert client.get("/assumptions").status_code == 200


def test_operator_nav_hides_admin_pages(tmp_path: Path) -> None:
    app = create_app(workspace_root=tmp_path / ".workspace")
    client = app.test_client()
    response = client.get("/portfolio", headers={"X-User-Email": "operator@example.com", "X-User-Role": "operator"})

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

    plant_a = client.get("/live", headers=plant_a_headers)
    plant_b = client.get("/live", headers=plant_b_headers)

    assert plant_a.status_code == 200
    assert plant_b.status_code == 200
    assert b"Workspace acme/plant-a" not in plant_a.data
    assert b"Workspace acme/plant-b" not in plant_b.data
    assert (tmp_path / ".workspace" / "customers" / "acme" / "workspaces" / "plant-a").exists()
    assert (tmp_path / ".workspace" / "customers" / "acme" / "workspaces" / "plant-b").exists()


def test_portfolio_page_creates_selects_and_scopes_projects(tmp_path: Path) -> None:
    app = create_app(workspace_root=tmp_path / ".workspace")
    client = app.test_client()
    admin_headers = {
        "X-User-Email": "admin@example.com",
        "X-User-Role": "admin",
        "X-Customer-Id": "Acme",
        "X-Workspace-Id": "Plant A",
    }

    portfolio = client.get("/portfolio", headers=admin_headers)
    assert portfolio.status_code == 200
    assert b"FDRE Project Operations" in portfolio.data
    assert b"Add Project" in portfolio.data
    assert b'id="add-project"' in portfolio.data
    assert b"Plant A" in portfolio.data or b"plant-a" in portfolio.data

    created = client.post(
        "/portfolio/projects/create",
        headers=admin_headers,
        data={
            "project_id": "Plant B",
            "name": "Plant B FDRE",
            "offtaker": "SJVN",
            "location": "North",
            "contract_label": "FDRE PPA",
            "capacity_summary": "Wind 100 MW | Solar 50 MW | BESS 50 MWh",
        },
        follow_redirects=True,
    )

    assert created.status_code == 200
    assert b"Project created" in created.data
    assert b"Plant B FDRE" in created.data

    duplicate = client.post(
        "/portfolio/projects/create",
        headers=admin_headers,
        data={"project_id": "Plant B", "name": "Duplicate"},
        follow_redirects=True,
    )
    assert b"Project already exists" in duplicate.data

    selected = client.post(
        "/portfolio/select",
        headers=admin_headers,
        data={"project_id": "plant-b", "next": "/inputs"},
        follow_redirects=True,
    )

    assert selected.status_code == 200
    assert b"Solar Generation" in selected.data
    live = client.get("/live", headers=admin_headers)
    assert b"Workspace acme/plant-b" in live.data
    assert (tmp_path / ".workspace" / "customers" / "acme" / "workspaces" / "plant-a").exists()
    assert (tmp_path / ".workspace" / "customers" / "acme" / "workspaces" / "plant-b").exists()


def test_live_board_run_presets(tmp_path: Path) -> None:
    app = create_app(workspace_root=tmp_path / ".workspace")
    client = app.test_client()
    admin_headers = {"X-User-Email": "admin@example.com", "X-User-Role": "admin"}

    intraday = client.get("/live?preset=intraday&live_at=2026-01-01T10:00", headers=admin_headers)
    assert intraday.status_code == 200
    assert b"intraday" in intraday.data
    assert b"0 actual + 1 live + 8 forecast = 9" in intraday.data

    day_ahead = client.get("/live?preset=day_ahead&live_at=2026-01-01T10:00", headers=admin_headers)
    assert day_ahead.status_code == 200
    assert b"day-ahead" in day_ahead.data
    assert b"0 actual + 0 live + 24 forecast = 24" in day_ahead.data


def test_data_quality_gate_marks_degraded_advisory_cycles(tmp_path: Path) -> None:
    app = create_app(workspace_root=tmp_path / ".workspace")
    client = app.test_client()

    saved = client.post(
        "/inputs/bess_state/manual",
        data={
            "source_type": "manual_1h",
            "csv_text": "timestamp,soc_mwh,soh_fraction\n2026-01-01 10:00,150,1\n",
        },
        follow_redirects=True,
    )
    assert saved.status_code == 200

    live = client.get("/live?window_start=2026-01-01T10:00&live_at=2026-01-01T10:00&window_end=2026-01-01T11:00")

    assert live.status_code == 200
    assert b"Data Quality Gate" in live.data
    assert b"Critical advisory status" in live.data
    assert b"SOC outside physical capacity" in live.data


def test_feed_catalog_page_is_viewable_and_admin_editable(tmp_path: Path) -> None:
    app = create_app(workspace_root=tmp_path / ".workspace")
    client = app.test_client()
    admin_headers = {"X-User-Email": "admin@example.com", "X-User-Role": "admin"}

    operator_view = client.get("/feeds")
    assert operator_view.status_code == 200
    assert b"Integration Readiness" in operator_view.data
    assert b"Solar Yield Forecast" in operator_view.data
    assert b"Save Feed Catalog" not in operator_view.data
    assert b"Protocol" not in operator_view.data
    assert b"Frequency" not in operator_view.data
    assert b"Fallback" not in operator_view.data
    assert b"Owner" not in operator_view.data

    saved = client.post(
        "/feeds/save",
        headers=admin_headers,
        data={
            "feed_key": ["solar_forecast"],
            "name": ["Solar API"],
            "protocol": ["REST later"],
            "update_frequency": ["Every 6 hours"],
            "fallback_method": ["Manual CSV"],
            "owner": ["Ops"],
            "enabled": ["0"],
        },
        follow_redirects=True,
    )

    assert saved.status_code == 200
    assert b"Feed catalog saved" in saved.data
    assert b"REST later" in saved.data
    assert b"disabled" in saved.data


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

    assert client.get("/", follow_redirects=True).status_code == 200
    assert client.get("/login").headers["Location"] == "/portfolio"
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
