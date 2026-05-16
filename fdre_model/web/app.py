"""Flask web control room for FDRE live market recommendations."""

from __future__ import annotations

import argparse
import json
import logging
import os
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

from flask import Flask, Response, abort, flash, jsonify, redirect, render_template, request, send_file, session, url_for
from werkzeug.middleware.proxy_fix import ProxyFix

from fdre_model.config import AppConfig
from fdre_model.market.models import RuleDefinition
from fdre_model.storage.hosted import HostedPersistence
from fdre_model.storage.local import INPUT_SPECS, LocalWorkspaceStore
from fdre_model.web.auth import (
    AUTH_SESSION_KEY,
    ADMIN_ROLE,
    CurrentUser,
    OPERATOR_ROLE,
    admin_emails,
    auth0_client_id,
    auth0_client_secret,
    auth0_configured,
    auth0_domain,
    auth0_partially_configured,
    public_base_url,
    user_from_headers,
)
from fdre_model.web.auth0_management import Auth0ManagementClient, auth0_connection_name


_logger = logging.getLogger(__name__)


def create_app(
    workspace_root: str | Path | None = None,
    *,
    source_config_path: str | Path | None = None,
    auth0_management_client: Any | None = None,
) -> Flask:
    _load_local_dotenv()
    app_root = Path(__file__).resolve().parent
    app = Flask(
        __name__,
        template_folder=str(app_root / "templates"),
        static_folder=str(app_root / "static"),
    )
    app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)
    app.config["SECRET_KEY"] = _env_first("FDRE_MODEL_SECRET_KEY", "FDRE_SECRET_KEY") or "fdre-market-local"
    app.config["SESSION_COOKIE_HTTPONLY"] = True
    app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
    app.config["SESSION_COOKIE_SECURE"] = _env_flag("FDRE_MODEL_SECURE_COOKIES")
    persistence = _persistence_from_env()
    store = LocalWorkspaceStore(workspace_root, source_config_path=source_config_path, persistence=persistence)
    auth0_enabled = auth0_configured()
    if auth0_partially_configured():
        _logger.warning("Auth0 is partially configured; set domain, client id, and client secret.")

    auth0_client = None
    if auth0_enabled:
        try:
            from authlib.integrations.flask_client import OAuth
        except ImportError as exc:  # pragma: no cover - deployment dependency guard
            raise RuntimeError("Install authlib to use Auth0 login.") from exc
        oauth = OAuth(app)
        domain = auth0_domain()
        auth0_client = oauth.register(
            "auth0",
            client_id=auth0_client_id(),
            client_secret=auth0_client_secret(),
            server_metadata_url=f"https://{domain}/.well-known/openid-configuration",
            client_kwargs={"scope": "openid profile email"},
        )
    auth0_management = auth0_management_client if auth0_management_client is not None else Auth0ManagementClient.from_env()

    def workspace():
        user = current_user()
        if user is None:
            abort(401)
        return store.for_scope(user.scope).ensure()

    def current_user_email() -> str:
        user = current_user()
        if user is None:
            abort(401)
        return user.email

    def current_user() -> CurrentUser | None:
        user = authenticated_user()
        if user is None:
            return None
        if auth0_enabled:
            return apply_app_access(user, enforce=True)
        return apply_app_access(user, enforce=False)

    def authenticated_user() -> CurrentUser | None:
        if auth0_enabled:
            return CurrentUser.from_session(session.get(AUTH_SESSION_KEY))
        return user_from_headers(request.headers)

    def apply_app_access(user: CurrentUser, *, enforce: bool) -> CurrentUser | None:
        env_admins = admin_emails()
        if user.email in env_admins:
            return _user_with_role(user, ADMIN_ROLE)
        state = store.for_scope(user.scope).ensure()
        app_user = store.app_user(state, user.email)
        if app_user is None:
            return None if enforce else user
        if not app_user.active:
            return None if enforce else user
        return _user_with_role(user, app_user.role)

    def require_admin() -> None:
        user = current_user()
        if user is None or not user.is_admin:
            abort(403)

    def external_url(endpoint: str, **values: Any) -> str:
        base_url = public_base_url()
        if base_url:
            return base_url + url_for(endpoint, **values)
        return url_for(endpoint, _external=True, **values)

    @app.before_request
    def require_auth0_session() -> Response | None:
        if not auth0_enabled:
            return None
        if request.endpoint in {"health", "login", "auth0_callback", "unauthorized", "favicon", "static"}:
            return None
        if current_user() is not None:
            return None
        if authenticated_user() is not None:
            if request.path.startswith("/api/"):
                return jsonify({"error": "User is not enabled for this FDRE workspace."}), 403
            return redirect(url_for("unauthorized"))
        if request.path.startswith("/api/"):
            return jsonify({"error": "Authentication required."}), 401
        return redirect(url_for("login"))

    @app.after_request
    def apply_security_headers(response: Response) -> Response:
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault("Referrer-Policy", "same-origin")
        response.headers.setdefault("Permissions-Policy", "camera=(), microphone=(), geolocation=()")
        response.headers.setdefault("Content-Security-Policy", "base-uri 'self'; frame-ancestors 'none'; object-src 'none'")
        if request.is_secure:
            response.headers.setdefault("Strict-Transport-Security", "max-age=31536000; includeSubDomains")
        return response

    @app.context_processor
    def context() -> dict[str, Any]:
        user = current_user()
        if user is None:
            return {
                "active_page": "",
                "project": "FDRE Market Operations",
                "current_user": None,
                "is_admin": False,
                "workspace_scope": None,
                "storage_backend": _storage_backend_name(),
                "auth0_enabled": auth0_enabled,
                "auth0_management_enabled": auth0_management is not None,
                "auth0_connection_name": auth0_connection_name(),
                "syncfusion_license_key": _env_first("FDRE_SYNCFUSION_LICENSE_KEY", "SYNCFUSION_LICENSE_KEY") or "",
            }
        state = store.for_scope(user.scope).ensure()
        return {
            "active_page": "",
            "project": store.load_config(state).project.name,
            "current_user": user,
            "is_admin": user.is_admin,
            "workspace_scope": state.scope,
            "storage_backend": _storage_backend_name(),
            "auth0_enabled": auth0_enabled,
            "auth0_management_enabled": auth0_management is not None,
            "auth0_connection_name": auth0_connection_name(),
            "syncfusion_license_key": _env_first("FDRE_SYNCFUSION_LICENSE_KEY", "SYNCFUSION_LICENSE_KEY") or "",
        }

    @app.get("/")
    def live_board() -> str:
        state = workspace()
        cycle = store.latest_or_create_cycle(state)
        rows = store.read_allocation_rows(cycle)
        filters = _filters_from_request(request.args)
        market_options = _market_options(rows)
        filtered_rows = _filter_rows(rows, filters)
        acknowledgement = store.get_acknowledgement(state, cycle.cycle_id)
        return render_template(
            "live_board.html",
            active_page="live",
            cycle=cycle,
            rows=_rows_with_explanations(filtered_rows),
            total_rows=len(rows),
            filters=filters,
            market_options=market_options,
            summary=cycle.summary,
            alerts=_live_alerts(cycle, rows),
            acknowledgement=acknowledgement,
        )

    @app.post("/cycles/recalculate")
    def recalculate_cycle() -> Response:
        state = workspace()
        cycle = store.latest_or_create_cycle(state, force=True)
        flash(f"Decision cycle recalculated: {cycle.cycle_id}", "success")
        return redirect(url_for("live_board"))

    @app.get("/history")
    def history_page() -> str:
        state = workspace()
        return render_template(
            "history.html",
            active_page="history",
            cycles=store.list_cycles(state),
            acknowledgements=store.acknowledgement_map(state),
        )

    @app.get("/cycles/<cycle_id>")
    def cycle_page(cycle_id: str) -> str | Response:
        state = workspace()
        cycle = next((item for item in store.list_cycles(state) if item.cycle_id == cycle_id), None)
        if cycle is None:
            flash("Decision cycle not found.", "error")
            return redirect(url_for("history_page"))
        rows = store.read_allocation_rows(cycle)
        return render_template(
            "live_board.html",
            active_page="history",
            cycle=cycle,
            rows=_rows_with_explanations(rows),
            total_rows=len(rows),
            filters=_default_filters(),
            market_options=_market_options(rows),
            summary=cycle.summary,
            alerts=_live_alerts(cycle, rows),
            acknowledgement=store.get_acknowledgement(state, cycle.cycle_id),
        )

    @app.get("/cycles/<cycle_id>/download/<artifact_key>")
    def download_artifact(cycle_id: str, artifact_key: str) -> Response:
        state = workspace()
        cycle = next((item for item in store.list_cycles(state) if item.cycle_id == cycle_id), None)
        if cycle is None:
            flash("Decision cycle not found.", "error")
            return redirect(url_for("history_page"))
        path = cycle.artifact_paths.get(artifact_key)
        if path is None or not path.exists():
            flash("Artifact not found.", "error")
            return redirect(url_for("cycle_page", cycle_id=cycle_id))
        return send_file(path, as_attachment=True, download_name=path.name)

    @app.get("/inputs")
    def inputs_page() -> str:
        state = workspace()
        inputs = []
        for spec in INPUT_SPECS:
            active = store.active_version(state, spec.key)
            versions = store.list_versions(state, spec.key)
            inputs.append({"spec": spec, "active": active, "versions": versions})
        return render_template("inputs.html", active_page="inputs", inputs=inputs)

    @app.get("/inputs/<dataset_key>/download/<version_id>")
    def download_input(dataset_key: str, version_id: str) -> Response:
        state = workspace()
        try:
            version = store.get_version(state, dataset_key, version_id)
        except Exception as exc:
            flash(f"Input version not found: {exc}", "error")
            return redirect(url_for("inputs_page"))
        if version.raw_path is None or not version.raw_path.exists():
            flash("Input file not found.", "error")
            return redirect(url_for("inputs_page"))
        download_name = f"fdre_{version.dataset_key}_{version.version_id}.csv"
        return send_file(version.raw_path, as_attachment=True, download_name=download_name, mimetype="text/csv")

    @app.post("/inputs/<dataset_key>/upload")
    def upload_input(dataset_key: str) -> Response:
        state = workspace()
        upload = request.files.get("file")
        if upload is None or not upload.filename:
            flash("Choose a CSV file to upload.", "error")
            return redirect(url_for("inputs_page"))
        try:
            text = upload.read().decode("utf-8-sig")
            store.create_version(
                state,
                dataset_key,
                text,
                source_type="csv_upload",
                original_name=upload.filename,
                user_email=current_user_email(),
                activate=True,
            )
            flash("Input version uploaded and activated.", "success")
        except Exception as exc:
            flash(f"Upload failed: {exc}", "error")
        return redirect(url_for("inputs_page"))

    @app.post("/inputs/<dataset_key>/manual")
    def manual_input(dataset_key: str) -> Response:
        state = workspace()
        text = request.form.get("csv_text", "")
        try:
            store.create_version(
                state,
                dataset_key,
                text,
                source_type=str(request.form.get("source_type") or "manual"),
                original_name=f"manual:{dataset_key}.csv",
                user_email=current_user_email(),
                activate=True,
            )
            flash("Manual input version saved and activated.", "success")
        except Exception as exc:
            flash(f"Manual input failed: {exc}", "error")
        return redirect(url_for("inputs_page"))

    @app.post("/inputs/<dataset_key>/activate/<version_id>")
    def activate_input(dataset_key: str, version_id: str) -> Response:
        state = workspace()
        try:
            store.activate_version(state, dataset_key, version_id)
            flash("Input version activated.", "success")
        except Exception as exc:
            flash(f"Activation failed: {exc}", "error")
        return redirect(url_for("inputs_page"))

    @app.get("/rules")
    def rules_page() -> str:
        require_admin()
        state = workspace()
        return render_template(
            "rules.html",
            active_page="rules",
            rules=store.load_rules(state),
            rule_version=store.active_model_version(state, "rules"),
            rule_versions=store.list_model_versions(state, "rules"),
        )

    @app.post("/rules/save")
    def save_rules() -> Response:
        require_admin()
        state = workspace()
        existing = {rule.rule_id: rule for rule in store.load_rules(state)}
        rules: list[RuleDefinition] = []
        try:
            for rule_id, rule in existing.items():
                priority = int(request.form.get(f"priority:{rule_id}") or rule.priority)
                enabled = request.form.get(f"enabled:{rule_id}") == "on"
                condition = _json_form_mapping(request.form.get(f"condition:{rule_id}"), fallback=rule.condition)
                action = _json_form_mapping(request.form.get(f"action:{rule_id}"), fallback=rule.action)
                rules.append(
                    RuleDefinition(
                        rule_id=rule.rule_id,
                        name=rule.name,
                        priority=priority,
                        enabled=enabled,
                        description=rule.description,
                        condition=condition,
                        action=action,
                        rule_pack=rule.rule_pack,
                    )
                )
            store.save_rules(state, rules, user_email=current_user_email())
            flash("Rule order saved. Recalculate to apply the new priority.", "success")
        except Exception as exc:
            flash(f"Rule save failed: {exc}", "error")
        return redirect(url_for("rules_page"))

    @app.get("/assumptions")
    def assumptions_page() -> str:
        require_admin()
        state = workspace()
        return render_template(
            "assumptions.html",
            active_page="assumptions",
            config=store.load_config(state),
            assumption_version=store.active_model_version(state, "assumptions"),
            assumption_versions=store.list_model_versions(state, "assumptions"),
        )

    @app.post("/assumptions/save")
    def save_assumptions() -> Response:
        require_admin()
        state = workspace()
        current = store.load_config(state).to_dict()
        try:
            current["market_model"]["interval"] = request.form.get("market_model.interval", "1h")
            current["market_model"]["recent_hours"] = int(request.form.get("market_model.recent_hours") or 6)
            current["market_model"]["forecast_hours"] = int(request.form.get("market_model.forecast_hours") or 24)
            current["market_model"]["charge_loss_fraction"] = float(request.form.get("market_model.charge_loss_fraction") or 0.13)
            current["market_model"]["discharge_loss_fraction"] = float(request.form.get("market_model.discharge_loss_fraction") or 0.07)
            current["market_model"]["default_peak_hours"] = [
                int(part) for part in (request.form.get("market_model.default_peak_hours") or "").replace(",", " ").split()
            ]
            for section in ("capacities", "tariffs", "state"):
                for key in list(current[section].keys()):
                    form_key = f"{section}.{key}"
                    if form_key in request.form:
                        current[section][key] = float(request.form.get(form_key) or 0.0)
            store.save_config_from_payload(state, current, user_email=current_user_email())
            flash("Assumptions saved. Recalculate to apply them.", "success")
        except Exception as exc:
            flash(f"Failed to save assumptions: {exc}", "error")
        return redirect(url_for("assumptions_page"))

    @app.get("/users")
    def users_page() -> str:
        require_admin()
        state = workspace()
        return render_template(
            "users.html",
            active_page="users",
            users=store.list_app_users(state),
            env_admin_emails=sorted(admin_emails()),
            auth0_management_enabled=auth0_management is not None,
            auth0_connection_name=auth0_connection_name(),
        )

    @app.post("/users/save")
    def save_user() -> Response:
        require_admin()
        state = workspace()
        target_email = request.form.get("email", "")
        target_role = request.form.get("role", OPERATOR_ROLE)
        target_active = request.form.get("active") == "on"
        target_name = request.form.get("name", "")
        try:
            if _same_email(target_email, current_user_email()) and (not target_active or target_role != ADMIN_ROLE):
                raise ValueError("Admins cannot demote or deactivate their own active session.")
            store.save_app_user(
                state,
                email=target_email,
                role=target_role,
                active=target_active,
                name=target_name,
                notes=request.form.get("notes", ""),
                user_email=current_user_email(),
            )
            auth0_messages = _sync_auth0_user_from_form(auth0_management, request.form, email=target_email, name=target_name, active=target_active)
            flash("User access saved." + (" " + " ".join(auth0_messages) if auth0_messages else ""), "success")
        except Exception as exc:
            flash(f"User save failed: {exc}", "error")
        return redirect(url_for("users_page"))

    @app.post("/users/<path:email>/deactivate")
    def deactivate_user(email: str) -> Response:
        require_admin()
        state = workspace()
        try:
            if _same_email(email, current_user_email()):
                raise ValueError("Admins cannot deactivate their own active session.")
            store.deactivate_app_user(state, email, user_email=current_user_email())
            auth0_message = _block_auth0_user(auth0_management, email)
            flash("User deactivated." + (f" {auth0_message}" if auth0_message else ""), "success")
        except Exception as exc:
            flash(f"User update failed: {exc}", "error")
        return redirect(url_for("users_page"))

    @app.post("/users/<path:email>/reset-password")
    def reset_user_password(email: str) -> Response:
        require_admin()
        try:
            if auth0_management is None:
                raise ValueError("Auth0 Management API is not configured.")
            auth0_management.send_password_reset_email(email)
            flash("Password reset email sent.", "success")
        except Exception as exc:
            flash(f"Password reset failed: {exc}", "error")
        return redirect(url_for("users_page"))

    @app.post("/users/<path:email>/delete-auth0")
    def delete_auth0_user(email: str) -> Response:
        require_admin()
        state = workspace()
        try:
            if auth0_management is None:
                raise ValueError("Auth0 Management API is not configured.")
            if _same_email(email, current_user_email()):
                raise ValueError("Admins cannot delete their own Auth0 identity.")
            if _normalized_email(email) in admin_emails():
                raise ValueError("Environment admin identities cannot be deleted from this app.")
            auth0_user = auth0_management.find_user_by_email(email)
            if auth0_user is None:
                raise ValueError("Auth0 user was not found.")
            auth0_management.delete_user(auth0_user.user_id)
            _deactivate_or_record_deleted_user(store, state, email, current_user_email())
            flash("Auth0 user deleted and FDRE access deactivated.", "success")
        except Exception as exc:
            flash(f"Auth0 delete failed: {exc}", "error")
        return redirect(url_for("users_page"))

    @app.get("/api/health")
    def health() -> Response:
        return jsonify({"status": "ok"})

    @app.get("/login")
    def login() -> Response:
        if not auth0_enabled:
            return redirect(url_for("live_board"))
        if current_user() is not None:
            return redirect(url_for("live_board"))
        if auth0_client is None:
            abort(503)
        return auth0_client.authorize_redirect(redirect_uri=external_url("auth0_callback"))

    @app.get("/callback")
    def auth0_callback() -> Response:
        if not auth0_enabled or auth0_client is None:
            return redirect(url_for("live_board"))
        token = auth0_client.authorize_access_token()
        claims = token.get("userinfo") or auth0_client.userinfo(token=token)
        user = CurrentUser.from_claims(dict(claims))
        session[AUTH_SESSION_KEY] = user.to_session()
        if current_user() is None:
            return redirect(url_for("unauthorized"))
        workspace()
        return redirect(url_for("live_board"))

    @app.get("/unauthorized")
    def unauthorized() -> str:
        user = authenticated_user()
        return render_template("unauthorized.html", active_page="", user=user), 403

    @app.post("/logout")
    def logout() -> Response:
        session.clear()
        if auth0_enabled:
            query = urlencode(
                {
                    "returnTo": external_url("login"),
                    "client_id": auth0_client_id(),
                }
            )
            return redirect(f"https://{auth0_domain()}/v2/logout?{query}")
        flash("Logged out.", "success")
        return redirect(url_for("live_board"))

    @app.post("/cycles/<cycle_id>/acknowledge")
    def acknowledge_cycle(cycle_id: str) -> Response:
        state = workspace()
        try:
            store.acknowledge_cycle(
                state,
                cycle_id,
                user_email=current_user_email(),
                note=request.form.get("note", ""),
            )
            flash("Decision cycle acknowledged.", "success")
        except Exception as exc:
            flash(f"Acknowledgement failed: {exc}", "error")
        return redirect(url_for("live_board"))

    @app.get("/favicon.ico")
    def favicon() -> Response:
        return Response(status=204)

    return app


def _storage_backend_name() -> str:
    return os.environ.get("FDRE_STORAGE_BACKEND", "local").strip().lower() or "local"


def _load_local_dotenv() -> None:
    if os.environ.get("PYTEST_CURRENT_TEST"):
        return
    try:
        from dotenv import load_dotenv
    except ImportError:
        return
    load_dotenv()


def _env_first(*names: str) -> str | None:
    for name in names:
        value = os.environ.get(name)
        if value:
            return value
    return None


def _user_with_role(user: CurrentUser, role: str) -> CurrentUser:
    return CurrentUser(
        email=user.email,
        role=role,
        subject=user.subject,
        scope=user.scope,
        name=user.name,
    )


def _persistence_from_env() -> HostedPersistence | None:
    backend = _storage_backend_name()
    if backend == "local":
        return None
    if backend == "hosted":
        return HostedPersistence.from_env()
    raise ValueError("FDRE_STORAGE_BACKEND must be local or hosted.")


def _sync_auth0_user_from_form(
    auth0_management: Any | None,
    form: Any,
    *,
    email: str,
    name: str,
    active: bool,
) -> list[str]:
    if "sync_auth0" not in form and "send_reset_email" not in form:
        return []
    if auth0_management is None:
        raise ValueError("Auth0 Management API is not configured.")
    messages: list[str] = []
    if "sync_auth0" in form:
        auth0_user, created = auth0_management.ensure_user(email=email, name=name)
        if active:
            auth0_management.block_user(auth0_user.user_id, blocked=False)
        else:
            auth0_management.block_user(auth0_user.user_id, blocked=True)
        messages.append("Auth0 user created." if created else "Auth0 user synced.")
    if "send_reset_email" in form:
        auth0_management.send_password_reset_email(email)
        messages.append("Password reset email sent.")
    return messages


def _block_auth0_user(auth0_management: Any | None, email: str) -> str:
    if auth0_management is None:
        return ""
    auth0_user = auth0_management.find_user_by_email(email)
    if auth0_user is None:
        return "No matching Auth0 user was found to block."
    auth0_management.block_user(auth0_user.user_id, blocked=True)
    return "Matching Auth0 user blocked."


def _deactivate_or_record_deleted_user(store: LocalWorkspaceStore, state: Any, email: str, actor_email: str) -> None:
    try:
        store.deactivate_app_user(state, email, user_email=actor_email)
    except ValueError:
        store.save_app_user(
            state,
            email=email,
            role=OPERATOR_ROLE,
            active=False,
            user_email=actor_email,
            notes="Auth0 identity deleted.",
        )


def _same_email(first: str, second: str) -> bool:
    return _normalized_email(first) == _normalized_email(second)


def _normalized_email(value: str) -> str:
    return value.strip().lower()


def _env_flag(name: str) -> bool:
    return str(os.environ.get(name) or "").strip().lower() in {"1", "true", "yes", "on"}


def _default_filters() -> dict[str, str]:
    return {"status": "all", "peak": "all", "market": "all", "alerts": ""}


def _filters_from_request(args: Any) -> dict[str, str]:
    filters = _default_filters()
    filters["status"] = str(args.get("status") or "all")
    filters["peak"] = str(args.get("peak") or "all")
    filters["market"] = str(args.get("market") or "all")
    filters["alerts"] = "1" if args.get("alerts") else ""
    return filters


def _filter_rows(rows: list[dict[str, str]], filters: dict[str, str]) -> list[dict[str, str]]:
    result = rows
    if filters["status"] != "all":
        result = [row for row in result if row.get("status") == filters["status"]]
    if filters["peak"] == "peak":
        result = [row for row in result if row.get("is_peak") == "1"]
    elif filters["peak"] == "nonpeak":
        result = [row for row in result if row.get("is_peak") != "1"]
    if filters["market"] != "all":
        result = [row for row in result if row.get("recommended_market") == filters["market"]]
    if filters["alerts"]:
        result = [row for row in result if _row_has_alert(row)]
    return result


def _rows_with_explanations(rows: list[dict[str, str]]) -> list[dict[str, Any]]:
    return [{**row, "why": _why_for_row(row)} for row in rows]


def _why_for_row(row: dict[str, str]) -> dict[str, Any]:
    is_peak = row.get("is_peak") == "1"
    market = row.get("recommended_market") or "None"
    available = _float(row.get("available_mwh"))
    ppa = _float(row.get("ppa_sale_mwh"))
    merchant = _float(row.get("merchant_sale_mwh"))
    peak_sale = _float(row.get("peak_power_sale_mwh"))
    bess_charge = _float(row.get("bess_charge_mwh"))
    bess_discharge = _float(row.get("bess_discharge_mwh"))
    shortfall = _float(row.get("shortfall_mwh"))
    curtailment = _float(row.get("curtailment_mwh"))
    residual = _float(row.get("residual_mwh"))
    penalty = _float(row.get("penalty_value"))
    revenue = _float(row.get("revenue_value"))
    peak_text = "peak" if is_peak else "non-peak"
    summary = _why_summary(market, peak_text, available, peak_sale, ppa, merchant, bess_charge, bess_discharge, shortfall)
    steps = _why_steps(
        is_peak=is_peak,
        available=available,
        ppa=ppa,
        merchant=merchant,
        peak_sale=peak_sale,
        bess_charge=bess_charge,
        bess_discharge=bess_discharge,
        shortfall=shortfall,
        curtailment=curtailment,
        residual=residual,
    )
    outcomes = _why_outcomes(
        peak_sale=peak_sale,
        ppa=ppa,
        merchant=merchant,
        bess_charge=bess_charge,
        bess_discharge=bess_discharge,
        shortfall=shortfall,
        curtailment=curtailment,
        residual=residual,
        revenue=revenue,
        penalty=penalty,
    )
    return {
        "summary": summary,
        "context": [
            f"{row.get('status', '').title()} interval",
            "Peak" if is_peak else "Non-peak",
            f"Available {_format_mwh(available)}",
            f"SOC open {_format_mwh(_float(row.get('bess_open_mwh')))}",
        ],
        "steps": steps,
        "outcomes": outcomes,
        "technical": [
            {"label": "Applied rules", "value": _rule_text(row.get("applied_rule_ids"))},
            {"label": "Skipped/conflicting rules", "value": _rule_text(row.get("skipped_rule_ids"))},
            {"label": "Raw trace", "value": row.get("audit_trace") or "none"},
        ],
    }


def _why_summary(
    market: str,
    peak_text: str,
    available: float,
    peak_sale: float,
    ppa: float,
    merchant: float,
    bess_charge: float,
    bess_discharge: float,
    shortfall: float,
) -> str:
    if market == "Peak Power":
        if shortfall > 0:
            return (
                f"Peak obligation was prioritised for this {peak_text} interval. "
                f"{_format_mwh(peak_sale)} was delivered and {_format_mwh(shortfall)} remains short."
            )
        return f"Peak obligation was met first with {_format_mwh(peak_sale)} delivered."
    if market == "PPA":
        if merchant > 0:
            return (
                f"PPA is the primary recommendation for this {peak_text} interval. "
                f"PPA used {_format_mwh(ppa)} first and {_format_mwh(merchant)} residual went to merchant."
            )
        return f"PPA selected because {_format_mwh(available)} available energy fits the PPA priority path."
    if market == "Merchant":
        return f"Merchant selected because PPA priority was satisfied and {_format_mwh(merchant)} residual remained."
    if market == "BESS Charge":
        return f"Battery charging selected because {_format_mwh(bess_charge)} residual energy could be stored."
    if market == "Curtailment":
        return "Curtailment selected because energy remained after sale and storage rules."
    if market == "Shortfall":
        return f"Shortfall remains after available generation and BESS support; gap is {_format_mwh(shortfall)}."
    return "No market allocation was required for this interval."


def _why_steps(
    *,
    is_peak: bool,
    available: float,
    ppa: float,
    merchant: float,
    peak_sale: float,
    bess_charge: float,
    bess_discharge: float,
    shortfall: float,
    curtailment: float,
    residual: float,
) -> list[str]:
    steps: list[str] = []
    if is_peak:
        generation_to_peak = max(peak_sale - bess_discharge, 0.0)
        steps.append(f"Peak rule ran first and used {_format_mwh(generation_to_peak)} from available generation.")
        if bess_discharge > 0:
            steps.append(f"BESS discharged {_format_mwh(bess_discharge)} to reduce the peak obligation gap.")
        if shortfall > 0:
            steps.append(f"Peak obligation still has {_format_mwh(shortfall)} shortfall, so penalty exposure is recorded.")
    else:
        steps.append("Peak obligation rule was skipped because this interval is non-peak.")
    if ppa > 0:
        steps.append(f"PPA rule allocated {_format_mwh(ppa)} under its priority and capacity.")
    if merchant > 0:
        steps.append(f"Merchant rule allocated {_format_mwh(merchant)} of remaining energy.")
    if bess_charge > 0:
        steps.append(f"BESS charge rule stored {_format_mwh(bess_charge)} of remaining energy.")
    if curtailment > 0:
        steps.append(f"Curtailment rule absorbed {_format_mwh(curtailment)} that could not be sold or stored.")
    if residual <= 0:
        steps.append("No residual energy remained for lower-priority residual rules.")
    elif available <= 0:
        steps.append("No generation was available to allocate.")
    return steps


def _why_outcomes(
    *,
    peak_sale: float,
    ppa: float,
    merchant: float,
    bess_charge: float,
    bess_discharge: float,
    shortfall: float,
    curtailment: float,
    residual: float,
    revenue: float,
    penalty: float,
) -> list[dict[str, str]]:
    candidates = [
        ("Peak", peak_sale, "MWh"),
        ("PPA", ppa, "MWh"),
        ("Merchant", merchant, "MWh"),
        ("BESS charge", bess_charge, "MWh"),
        ("BESS discharge", bess_discharge, "MWh"),
        ("Shortfall", shortfall, "MWh"),
        ("Curtailment", curtailment, "MWh"),
        ("Residual", residual, "MWh"),
        ("Revenue", revenue, ""),
        ("Penalty", penalty, ""),
    ]
    return [
        {"label": label, "value": _format_mwh(value) if unit == "MWh" else f"{value:.1f}"}
        for label, value, unit in candidates
        if abs(value) > 0.000001
    ]


def _rule_text(value: str | None) -> str:
    text = (value or "").strip()
    return text.replace("_", " ") if text else "none"


def _format_mwh(value: float) -> str:
    return f"{value:.1f} MWh"


def _market_options(rows: list[dict[str, str]]) -> list[str]:
    return sorted({row.get("recommended_market", "") for row in rows if row.get("recommended_market")})


def _row_has_alert(row: dict[str, str]) -> bool:
    return _float(row.get("shortfall_mwh")) > 0.0 or _float(row.get("penalty_value")) > 0.0


def _live_alerts(cycle: Any, rows: list[dict[str, str]]) -> list[dict[str, str]]:
    alerts: list[dict[str, str]] = []
    critical_sources = [item.label for item in cycle.source_health if item.status == "critical"]
    warning_sources = [item.label for item in cycle.source_health if item.status == "warning"]
    if critical_sources:
        alerts.append(
            {
                "level": "critical",
                "title": "Critical input freshness",
                "message": ", ".join(critical_sources),
            }
        )
    if warning_sources:
        alerts.append(
            {
                "level": "warning",
                "title": "Forecast or actual coverage warning",
                "message": ", ".join(warning_sources),
            }
        )
    shortfall_rows = [row for row in rows if _float(row.get("shortfall_mwh")) > 0.0]
    if shortfall_rows:
        shortfall_mwh = sum(_float(row.get("shortfall_mwh")) for row in shortfall_rows)
        alerts.append(
            {
                "level": "critical",
                "title": "Shortfall exposure",
                "message": f"{shortfall_mwh:.1f} MWh across {len(shortfall_rows)} intervals.",
            }
        )
    if not alerts:
        alerts.append({"level": "ok", "title": "Operating window clear", "message": "No source-health or shortfall alerts."})
    return alerts


def _float(value: object) -> float:
    try:
        return float(value or 0.0)
    except (TypeError, ValueError):
        return 0.0


def _json_form_mapping(raw: str | None, *, fallback: dict[str, Any]) -> dict[str, Any]:
    text = (raw or "").strip()
    if not text:
        return dict(fallback)
    payload = json.loads(text)
    if not isinstance(payload, dict):
        raise ValueError("Rule condition/action JSON must be an object.")
    return payload


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the FDRE market operations web app.")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", default=8010, type=int)
    parser.add_argument("--workspace", default=None)
    args = parser.parse_args()
    app = create_app(workspace_root=args.workspace)
    app.run(host=args.host, port=args.port, debug=False)


if __name__ == "__main__":
    main()
