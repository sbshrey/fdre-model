"""Local workspace storage with immutable input versions and decision cycles."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
from dataclasses import dataclass
from datetime import datetime, timedelta
from importlib import resources
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from fdre_model.config import AppConfig, save_config
from fdre_model.exports import write_decision_artifacts
from fdre_model.market.aggregation import build_time_buckets, parse_interval, validate_csv_text
from fdre_model.market.engine import build_decisions, parse_active_input_texts, summary_for_decisions
from fdre_model.market.models import (
    AppUser,
    DecisionCycle,
    InputSpec,
    InputVersion,
    ModelVersion,
    OperatorAcknowledgement,
    RuleDefinition,
    SourceHealth,
)
from fdre_model.market.rules import DEFAULT_RULES
from fdre_model.storage.scope import WorkspaceScope


INPUT_SPECS: tuple[InputSpec, ...] = (
    InputSpec(
        key="solar",
        label="Solar Generation",
        description="Solar energy by source interval. Use MWh columns or kW power columns.",
        expected_headers=("timestamp", "mwh"),
        kind="generation",
    ),
    InputSpec(
        key="wind",
        label="Wind Generation",
        description="Wind energy by source interval. Use MWh columns or kW power columns.",
        expected_headers=("timestamp", "mwh"),
        kind="generation",
    ),
    InputSpec(
        key="bess_state",
        label="BESS State",
        description="Observed or forecast BESS SOC/SOH profile.",
        expected_headers=("timestamp", "soc_mwh", "soh_fraction"),
        kind="bess_state",
    ),
    InputSpec(
        key="t2_pricing",
        label="T2 / Merchant Pricing",
        description="Merchant sell price by interval.",
        expected_headers=("timestamp", "price"),
        kind="price",
    ),
    InputSpec(
        key="peak_schedule",
        label="Peak Schedule",
        description="User-configured peak/non-peak flag by interval.",
        expected_headers=("timestamp", "is_peak"),
        kind="peak_schedule",
    ),
)

SPEC_BY_KEY = {spec.key: spec for spec in INPUT_SPECS}
SAMPLE_SEED_SOURCE_TYPE = "seed_seci_reference"
SAMPLE_SEED_FILES = {
    "solar": "solar_2026_hourly.csv",
    "wind": "wind_2026_hourly.csv",
    "bess_state": "bess_state_2026_hourly.csv",
    "t2_pricing": "t2_pricing_2026_hourly.csv",
    "peak_schedule": "peak_schedule_2026_hourly.csv",
}


@dataclass(frozen=True)
class Workspace:
    root: Path
    config_dir: Path
    config_path: Path
    inputs_dir: Path
    rules_dir: Path
    cycles_dir: Path
    active_inputs_path: Path
    model_versions_dir: Path
    active_model_versions_path: Path
    source_config_path: Path
    users_path: Path
    scope: WorkspaceScope | None = None


class LocalWorkspaceStore:
    def __init__(
        self,
        root: str | Path | None = None,
        *,
        source_config_path: str | Path | None = None,
        scope: WorkspaceScope | None = None,
        persistence: Any | None = None,
    ) -> None:
        project_root = Path(__file__).resolve().parents[2]
        self.source_config_path = Path(source_config_path or project_root / "config" / "project.yaml").resolve()
        self.base_root = Path(root or project_root / ".workspace").expanduser().resolve()
        self.scope = scope
        self.persistence = persistence
        self.root = self._root_for_scope(scope)

    def for_scope(self, scope: WorkspaceScope) -> "LocalWorkspaceStore":
        return LocalWorkspaceStore(
            self.base_root,
            source_config_path=self.source_config_path,
            scope=scope,
            persistence=self.persistence,
        )

    def ensure(self) -> Workspace:
        state = Workspace(
            root=self.root,
            config_dir=self.root / "config",
            config_path=self.root / "config" / "project.yaml",
            inputs_dir=self.root / "inputs",
            rules_dir=self.root / "rules",
            cycles_dir=self.root / "decision_cycles",
            active_inputs_path=self.root / "inputs" / "active.json",
            model_versions_dir=self.root / "model_versions",
            active_model_versions_path=self.root / "model_versions" / "active.json",
            source_config_path=self.source_config_path,
            users_path=self.root / "config" / "users.json",
            scope=self.scope,
        )
        state.config_dir.mkdir(parents=True, exist_ok=True)
        state.inputs_dir.mkdir(parents=True, exist_ok=True)
        state.rules_dir.mkdir(parents=True, exist_ok=True)
        state.cycles_dir.mkdir(parents=True, exist_ok=True)
        state.model_versions_dir.mkdir(parents=True, exist_ok=True)
        if not state.config_path.exists():
            shutil.copy2(self.source_config_path, state.config_path)
        if not state.active_inputs_path.exists():
            state.active_inputs_path.write_text("{}", encoding="utf-8")
        if not state.active_model_versions_path.exists():
            state.active_model_versions_path.write_text("{}", encoding="utf-8")
        if not self.rules_path(state).exists():
            self._write_rules_file(state, list(DEFAULT_RULES))
        self._bootstrap_model_versions(state)
        self._sync_default_rules(state)
        if not state.users_path.exists():
            self._restore_user_access(state)
        self._sync_env_admin_users(state)
        self._bootstrap_seed_inputs(state)
        return state

    def load_config(self, state: Workspace) -> AppConfig:
        return AppConfig.from_yaml(state.config_path)

    def save_config_from_payload(
        self,
        state: Workspace,
        payload: dict[str, Any],
        *,
        user_email: str = "system",
        source_type: str = "admin_ui",
    ) -> AppConfig:
        config = _config_from_payload(payload, state.config_path)
        self._create_model_version(
            state,
            "assumptions",
            config.to_dict(),
            user_email=user_email,
            source_type=source_type,
            activate=True,
        )
        return config

    def list_specs(self) -> list[InputSpec]:
        return list(INPUT_SPECS)

    def list_versions(self, state: Workspace, dataset_key: str) -> list[InputVersion]:
        spec = self._require_spec(dataset_key)
        del spec
        versions_dir = self._versions_dir(state, dataset_key)
        versions: list[InputVersion] = []
        for meta_path in sorted(versions_dir.glob("*/metadata.json")):
            try:
                versions.append(InputVersion.from_json(json.loads(meta_path.read_text(encoding="utf-8"))))
            except Exception:
                continue
        versions.sort(key=lambda item: item.created_at, reverse=True)
        return versions

    def active_version_id(self, state: Workspace, dataset_key: str) -> str | None:
        active = self._load_active_inputs(state)
        return active.get(dataset_key)

    def active_version(self, state: Workspace, dataset_key: str) -> InputVersion | None:
        version_id = self.active_version_id(state, dataset_key)
        if not version_id:
            return None
        return self.get_version(state, dataset_key, version_id)

    def get_version(self, state: Workspace, dataset_key: str, version_id: str) -> InputVersion:
        meta_path = self._version_dir(state, dataset_key, version_id) / "metadata.json"
        if not meta_path.exists():
            raise FileNotFoundError(f"Input version not found: {dataset_key}/{version_id}")
        return InputVersion.from_json(json.loads(meta_path.read_text(encoding="utf-8")))

    def create_version(
        self,
        state: Workspace,
        dataset_key: str,
        text: str,
        *,
        source_type: str,
        original_name: str,
        user_email: str,
        activate: bool = True,
    ) -> InputVersion:
        spec = self._require_spec(dataset_key)
        parsed = validate_csv_text(text, dataset_kind=spec.kind)
        checksum = hashlib.sha256(text.encode("utf-8")).hexdigest()
        parent_id = self.active_version_id(state, dataset_key)
        version_id = f"{datetime.utcnow().strftime('%Y%m%d%H%M%S%f')}-{checksum[:10]}"
        version_dir = self._version_dir(state, dataset_key, version_id)
        version_dir.mkdir(parents=True, exist_ok=False)
        raw_path = version_dir / "raw.csv"
        raw_path.write_text(text, encoding="utf-8")
        version = InputVersion(
            dataset_key=dataset_key,
            version_id=version_id,
            source_type=source_type,
            original_name=original_name,
            user_email=user_email,
            created_at=_iso_now(),
            checksum=checksum,
            row_count=parsed.row_count,
            coverage_start=parsed.coverage_start.isoformat(sep=" ") if parsed.coverage_start else None,
            coverage_end=parsed.coverage_end.isoformat(sep=" ") if parsed.coverage_end else None,
            validation_status="ok",
            validation_message="Validated.",
            parent_version_id=parent_id,
            raw_path=raw_path,
        )
        (version_dir / "metadata.json").write_text(json.dumps(version.to_json(), indent=2, sort_keys=True), encoding="utf-8")
        if activate:
            self.activate_version(state, dataset_key, version_id)
        self._persist_input_version(state, version)
        return version

    def activate_version(self, state: Workspace, dataset_key: str, version_id: str) -> None:
        self.get_version(state, dataset_key, version_id)
        active = self._load_active_inputs(state)
        active[dataset_key] = version_id
        state.active_inputs_path.write_text(json.dumps(active, indent=2, sort_keys=True), encoding="utf-8")

    def list_app_users(self, state: Workspace) -> list[AppUser]:
        self._sync_env_admin_users(state)
        users = self._load_app_users(state)
        users.sort(key=lambda item: (not item.active, item.role != "admin", item.email))
        return users

    def app_user(self, state: Workspace, email: str) -> AppUser | None:
        normalized_email = _normalize_email(email)
        if not normalized_email:
            return None
        self._sync_env_admin_users(state)
        return next((user for user in self._load_app_users(state) if user.email == normalized_email), None)

    def save_app_user(
        self,
        state: Workspace,
        *,
        email: str,
        role: str,
        active: bool,
        user_email: str,
        name: str = "",
        notes: str = "",
    ) -> AppUser:
        normalized_email = _normalize_email(email)
        if not normalized_email or "@" not in normalized_email:
            raise ValueError("Enter a valid email address.")
        normalized_role = str(role or "").strip().lower()
        if normalized_role not in {"admin", "operator"}:
            raise ValueError("Role must be admin or operator.")
        if normalized_email in _env_admin_emails() and (normalized_role != "admin" or not active):
            raise ValueError("Environment admin emails must remain active admins.")

        now = _iso_now()
        users = {user.email: user for user in self._load_app_users(state)}
        existing = users.get(normalized_email)
        saved = AppUser(
            email=normalized_email,
            role=normalized_role,
            active=active,
            name=name.strip(),
            source=existing.source if existing and existing.source == "env" else "admin_ui",
            created_at=existing.created_at if existing else now,
            created_by=existing.created_by if existing else user_email,
            updated_at=now,
            updated_by=user_email,
            notes=notes.strip(),
        )
        users[normalized_email] = saved
        self._write_app_users(state, list(users.values()))
        return saved

    def deactivate_app_user(self, state: Workspace, email: str, *, user_email: str) -> AppUser:
        normalized_email = _normalize_email(email)
        if normalized_email in _env_admin_emails():
            raise ValueError("Environment admin emails must remain active admins.")
        existing = self.app_user(state, normalized_email)
        if existing is None:
            raise ValueError("User not found.")
        saved = AppUser(
            email=existing.email,
            role=existing.role,
            active=False,
            name=existing.name,
            source=existing.source,
            created_at=existing.created_at,
            created_by=existing.created_by,
            updated_at=_iso_now(),
            updated_by=user_email,
            notes=existing.notes,
        )
        users = {user.email: user for user in self._load_app_users(state)}
        users[normalized_email] = saved
        self._write_app_users(state, list(users.values()))
        return saved

    def load_rules(self, state: Workspace) -> list[RuleDefinition]:
        path = self.rules_path(state)
        payload = json.loads(path.read_text(encoding="utf-8")) if path.exists() else []
        return [RuleDefinition.from_json(item) for item in payload]

    def save_rules(
        self,
        state: Workspace,
        rules: list[RuleDefinition],
        *,
        user_email: str = "system",
        source_type: str = "admin_ui",
    ) -> ModelVersion:
        payload = _rules_payload(rules)
        return self._create_model_version(
            state,
            "rules",
            payload,
            user_email=user_email,
            source_type=source_type,
            activate=True,
        )

    def list_model_versions(self, state: Workspace, version_type: str) -> list[ModelVersion]:
        self._require_model_version_type(version_type)
        versions: list[ModelVersion] = []
        for meta_path in sorted(self._model_versions_dir(state, version_type).glob("*/metadata.json")):
            try:
                versions.append(ModelVersion.from_json(json.loads(meta_path.read_text(encoding="utf-8"))))
            except Exception:
                continue
        versions.sort(key=lambda item: item.created_at, reverse=True)
        return versions

    def active_model_version_id(self, state: Workspace, version_type: str) -> str | None:
        self._require_model_version_type(version_type)
        return self._load_active_model_versions(state).get(version_type)

    def active_model_version(self, state: Workspace, version_type: str) -> ModelVersion | None:
        version_id = self.active_model_version_id(state, version_type)
        if not version_id:
            return None
        try:
            return self.get_model_version(state, version_type, version_id)
        except FileNotFoundError:
            return None

    def get_model_version(self, state: Workspace, version_type: str, version_id: str) -> ModelVersion:
        self._require_model_version_type(version_type)
        meta_path = self._model_version_dir(state, version_type, version_id) / "metadata.json"
        if not meta_path.exists():
            raise FileNotFoundError(f"Model version not found: {version_type}/{version_id}")
        return ModelVersion.from_json(json.loads(meta_path.read_text(encoding="utf-8")))

    def create_decision_cycle(
        self,
        state: Workspace,
        *,
        now: datetime | None = None,
        window_start: datetime | None = None,
        window_end: datetime | None = None,
    ) -> DecisionCycle:
        config = self.load_config(state)
        rules = self.load_rules(state)
        effective_now = now or _configured_now(config)
        active_texts: dict[str, str] = {}
        active_versions: list[InputVersion] = []
        version_ids: dict[str, str] = {}
        model_versions = self._active_model_version_ids(state)
        model_version_records = [
            version
            for version_type in ("assumptions", "rules")
            if (version := self.active_model_version(state, version_type)) is not None
        ]
        for spec in INPUT_SPECS:
            version = self.active_version(state, spec.key)
            if version is None or version.raw_path is None:
                active_texts[spec.key] = ""
                continue
            active_versions.append(version)
            version_ids[spec.key] = version.version_id
            text, validation_error = _read_validated_input_text(version, spec.kind)
            active_texts[spec.key] = "" if validation_error else text

        inputs = parse_active_input_texts(active_texts, version_ids)
        decisions, buckets = build_decisions(config, inputs, rules, now=effective_now, window_start=window_start, window_end=window_end)
        for decision in decisions:
            decision.audit_trace.append(f"model_versions={model_versions}")
        source_health = self.source_health(state, now=effective_now, buckets=buckets)
        summary = summary_for_decisions(decisions)
        summary.update(_window_summary_values(buckets, effective_now, custom=window_start is not None or window_end is not None))
        summary.update(_source_health_summary(source_health))
        summary.update(_model_version_summary_values(model_versions))
        summary.update(_scope_json(state))
        cycle_id = f"cycle-{datetime.utcnow().strftime('%Y%m%d%H%M%S%f')}"
        cycle_dir = state.cycles_dir / cycle_id
        artifacts = write_decision_artifacts(
            cycle_dir,
            decisions=decisions,
            summary=summary,
            input_versions=active_versions,
            model_versions=model_version_records,
        )
        cycle = DecisionCycle(
            cycle_id=cycle_id,
            created_at=_iso_now(),
            window_start=str(summary.get("window_start", "")),
            window_end=str(summary.get("window_end", "")),
            workspace_scope=_scope_json(state),
            input_versions=version_ids,
            model_versions=model_versions,
            rule_order=[rule.rule_id for rule in sorted(rules, key=lambda item: item.priority) if rule.enabled],
            artifact_paths=artifacts,
            summary=summary,
            source_health=source_health,
        )
        (cycle_dir / "decision_cycle.json").write_text(json.dumps(cycle.to_json(), indent=2, sort_keys=True), encoding="utf-8")
        self._persist_decision_cycle(state, cycle)
        return cycle

    def latest_or_create_cycle(
        self,
        state: Workspace,
        *,
        now: datetime | None = None,
        window_start: datetime | None = None,
        window_end: datetime | None = None,
        force: bool = False,
    ) -> DecisionCycle:
        cycle = None if force else self.latest_cycle(state)
        if cycle is None or self.cycle_is_stale(state, cycle, now=now, window_start=window_start, window_end=window_end):
            return self.create_decision_cycle(state, now=now, window_start=window_start, window_end=window_end)
        return cycle

    def cycle_is_stale(
        self,
        state: Workspace,
        cycle: DecisionCycle,
        *,
        now: datetime | None = None,
        window_start: datetime | None = None,
        window_end: datetime | None = None,
    ) -> bool:
        if not cycle.source_health:
            return True
        config = self.load_config(state)
        effective_now = now or _configured_now(config)
        buckets = build_time_buckets(config, now=effective_now, window_start=window_start, window_end=window_end)
        expected_window_start = buckets[0].start.isoformat(sep=" ") if buckets else ""
        expected_window_end = buckets[-1].end.isoformat(sep=" ") if buckets else ""
        if cycle.window_start != expected_window_start or cycle.window_end != expected_window_end:
            return True

        if cycle.input_versions != self._active_input_version_ids(state):
            return True
        if cycle.model_versions != self._active_model_version_ids(state):
            return True

        created_at = _parse_datetime(cycle.created_at)
        if created_at is None:
            return True
        refresh_after_seconds = parse_interval(config.market_model.interval).total_seconds()
        return (datetime.utcnow() - created_at).total_seconds() >= refresh_after_seconds

    def source_health(
        self,
        state: Workspace,
        *,
        now: datetime | None = None,
        buckets: list[Any] | None = None,
    ) -> list[SourceHealth]:
        config = self.load_config(state)
        effective_buckets = buckets or build_time_buckets(config, now=now or _configured_now(config))
        if not effective_buckets:
            return []
        window_start = effective_buckets[0].start
        live_bucket = next((bucket for bucket in effective_buckets if bucket.status == "live"), effective_buckets[0])
        forecast_horizon_start = effective_buckets[-1].start
        required_start = window_start.isoformat(sep=" ")
        required_end = forecast_horizon_start.isoformat(sep=" ")
        health: list[SourceHealth] = []
        for spec in INPUT_SPECS:
            try:
                version = self.active_version(state, spec.key)
            except Exception as exc:
                health.append(
                    SourceHealth(
                        dataset_key=spec.key,
                        label=spec.label,
                        status="critical",
                        message=f"Active input cannot be read: {exc}",
                        active_version_id=self.active_version_id(state, spec.key),
                        source_type=None,
                        row_count=0,
                        coverage_start=None,
                        coverage_end=None,
                        required_start=required_start,
                        required_end=required_end,
                    )
                )
                continue
            if version is None:
                health.append(
                    SourceHealth(
                        dataset_key=spec.key,
                        label=spec.label,
                        status="critical",
                        message="No active input version.",
                        active_version_id=None,
                        source_type=None,
                        row_count=0,
                        coverage_start=None,
                        coverage_end=None,
                        required_start=required_start,
                        required_end=required_end,
                    )
                )
                continue

            coverage_start = _parse_datetime(version.coverage_start)
            coverage_end = _parse_datetime(version.coverage_end)
            issues: list[tuple[str, str]] = []
            if version.validation_status != "ok":
                issues.append(("critical", version.validation_message or "Input failed validation."))
            if version.row_count <= 0:
                issues.append(("critical", "Input has no rows."))
            _raw_text, validation_error = _read_validated_input_text(version, spec.kind)
            if validation_error:
                issues.append(("critical", f"Active input fails current validation: {validation_error}"))
            if coverage_start is None or coverage_end is None:
                issues.append(("critical", "Input coverage cannot be determined."))
            else:
                if coverage_start > live_bucket.start:
                    issues.append(("critical", "Input starts after the current live interval."))
                elif coverage_start > window_start:
                    issues.append(("warning", "Input does not cover all recent actual intervals."))

                if coverage_end < live_bucket.start:
                    issues.append(("critical", "Latest input row is before the current live interval."))
                elif coverage_end < forecast_horizon_start:
                    issues.append(("warning", "Input does not cover the full forecast horizon."))

            status = "ok"
            if any(level == "critical" for level, _message in issues):
                status = "critical"
            elif issues:
                status = "warning"
            health.append(
                SourceHealth(
                    dataset_key=spec.key,
                    label=spec.label,
                    status=status,
                    message="; ".join(message for _level, message in issues) if issues else "Covers the rolling window.",
                    active_version_id=version.version_id,
                    source_type=version.source_type,
                    row_count=version.row_count,
                    coverage_start=version.coverage_start,
                    coverage_end=version.coverage_end,
                    required_start=required_start,
                    required_end=required_end,
                )
            )
        return health

    def list_cycles(self, state: Workspace) -> list[DecisionCycle]:
        cycles: list[DecisionCycle] = []
        for meta_path in sorted(state.cycles_dir.glob("*/decision_cycle.json")):
            try:
                cycles.append(DecisionCycle.from_json(json.loads(meta_path.read_text(encoding="utf-8"))))
            except Exception:
                continue
        cycles.sort(key=lambda item: item.created_at, reverse=True)
        return cycles

    def latest_cycle(self, state: Workspace) -> DecisionCycle | None:
        cycles = self.list_cycles(state)
        return cycles[0] if cycles else None

    def read_allocation_rows(self, cycle: DecisionCycle) -> list[dict[str, str]]:
        path = cycle.artifact_paths["allocation_csv"]
        import csv

        with path.open("r", newline="", encoding="utf-8") as handle:
            return list(csv.DictReader(handle))

    def acknowledge_cycle(
        self,
        state: Workspace,
        cycle_id: str,
        *,
        user_email: str,
        note: str = "",
    ) -> OperatorAcknowledgement:
        cycle_dir = state.cycles_dir / cycle_id
        if not (cycle_dir / "decision_cycle.json").exists():
            raise FileNotFoundError(f"Decision cycle not found: {cycle_id}")
        acknowledgement = OperatorAcknowledgement(
            cycle_id=cycle_id,
            acknowledged_at=_iso_now(),
            user_email=user_email,
            note=note.strip(),
            workspace_scope=_scope_json(state),
        )
        self._acknowledgement_path(state, cycle_id).write_text(
            json.dumps(acknowledgement.to_json(), indent=2, sort_keys=True),
            encoding="utf-8",
        )
        return acknowledgement

    def get_acknowledgement(self, state: Workspace, cycle_id: str) -> OperatorAcknowledgement | None:
        path = self._acknowledgement_path(state, cycle_id)
        if not path.exists():
            return None
        return OperatorAcknowledgement.from_json(json.loads(path.read_text(encoding="utf-8")))

    def acknowledgement_map(self, state: Workspace) -> dict[str, OperatorAcknowledgement]:
        acknowledgements: dict[str, OperatorAcknowledgement] = {}
        for path in state.cycles_dir.glob("*/acknowledgement.json"):
            try:
                acknowledgement = OperatorAcknowledgement.from_json(json.loads(path.read_text(encoding="utf-8")))
            except Exception:
                continue
            acknowledgements[acknowledgement.cycle_id] = acknowledgement
        return acknowledgements

    def rules_path(self, state: Workspace) -> Path:
        return state.rules_dir / "rules.json"

    def _load_app_users(self, state: Workspace) -> list[AppUser]:
        if not state.users_path.exists():
            return []
        try:
            payload = json.loads(state.users_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return []
        rows = payload.get("users") if isinstance(payload, dict) else payload
        if not isinstance(rows, list):
            return []
        users: list[AppUser] = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            try:
                users.append(AppUser.from_json(row))
            except Exception:
                continue
        return users

    def _write_app_users(self, state: Workspace, users: list[AppUser]) -> None:
        unique = {user.email: user for user in users if user.email}
        payload = {
            "updated_at": _iso_now(),
            "users": [user.to_json() for user in sorted(unique.values(), key=lambda item: item.email)],
        }
        state.users_path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
        self._persist_user_access(state, payload)

    def _sync_env_admin_users(self, state: Workspace) -> None:
        env_admins = _env_admin_emails()
        users = {user.email: user for user in self._load_app_users(state)}
        changed = not state.users_path.exists()
        now = _iso_now()
        for email in env_admins:
            existing = users.get(email)
            if existing is None:
                users[email] = AppUser(
                    email=email,
                    role="admin",
                    active=True,
                    source="env",
                    created_at=now,
                    created_by="environment",
                    updated_at=now,
                    updated_by="environment",
                    notes="Seeded from FDRE_ADMIN_EMAILS/FDRE_MODEL_ADMIN_EMAILS.",
                )
                changed = True
            elif existing.role != "admin" or not existing.active or existing.source != "env":
                users[email] = AppUser(
                    email=email,
                    role="admin",
                    active=True,
                    name=existing.name,
                    source="env",
                    created_at=existing.created_at or now,
                    created_by=existing.created_by or "environment",
                    updated_at=now,
                    updated_by="environment",
                    notes=existing.notes or "Seeded from FDRE_ADMIN_EMAILS/FDRE_MODEL_ADMIN_EMAILS.",
                )
                changed = True
        if changed:
            self._write_app_users(state, list(users.values()))

    def _restore_user_access(self, state: Workspace) -> None:
        if self.persistence is None or state.scope is None or not hasattr(self.persistence, "load_user_access"):
            return
        try:
            payload = self.persistence.load_user_access(state.scope)
        except Exception:
            return
        if isinstance(payload, dict) and isinstance(payload.get("users"), list):
            state.users_path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")

    def _bootstrap_model_versions(self, state: Workspace) -> None:
        if self.active_model_version(state, "assumptions") is None:
            config = self.load_config(state)
            self._create_model_version(
                state,
                "assumptions",
                config.to_dict(),
                user_email="system",
                source_type="bootstrap",
                activate=True,
            )
        if self.active_model_version(state, "rules") is None:
            self._create_model_version(
                state,
                "rules",
                _rules_payload(self.load_rules(state)),
                user_email="system",
                source_type="bootstrap",
                activate=True,
            )

    def _sync_default_rules(self, state: Workspace) -> None:
        existing = self.load_rules(state)
        merged, changed = _merge_default_rule_metadata(existing)
        if not changed:
            return
        self._create_model_version(
            state,
            "rules",
            _rules_payload(merged),
            user_email="system",
            source_type="migration",
            activate=True,
        )

    def _bootstrap_seed_inputs(self, state: Workspace) -> None:
        active = self._load_active_inputs(state)
        seed_specs = [spec for spec in INPUT_SPECS if self._should_seed_input(state, spec, active)]
        if not seed_specs:
            return
        config = self.load_config(state)
        seed_texts = _sample_seed_texts(config)
        for spec in seed_specs:
            self.create_version(
                state,
                spec.key,
                seed_texts[spec.key],
                source_type=SAMPLE_SEED_SOURCE_TYPE,
                original_name=_sample_seed_original_name(spec.key),
                user_email="system",
                activate=True,
            )

    def _should_seed_input(self, state: Workspace, spec: InputSpec, active: dict[str, str]) -> bool:
        if spec.key not in active:
            return True
        try:
            version = self.active_version(state, spec.key)
        except Exception:
            return True
        if version is None:
            return True
        if str(version.source_type).startswith("seed") and version.original_name != _sample_seed_original_name(spec.key):
            return True
        return False

    def _load_active_inputs(self, state: Workspace) -> dict[str, str]:
        if not state.active_inputs_path.exists():
            return {}
        return {str(k): str(v) for k, v in json.loads(state.active_inputs_path.read_text(encoding="utf-8")).items()}

    def _load_active_model_versions(self, state: Workspace) -> dict[str, str]:
        if not state.active_model_versions_path.exists():
            return {}
        return {str(k): str(v) for k, v in json.loads(state.active_model_versions_path.read_text(encoding="utf-8")).items()}

    def _save_active_model_versions(self, state: Workspace, active: dict[str, str]) -> None:
        state.active_model_versions_path.write_text(json.dumps(active, indent=2, sort_keys=True), encoding="utf-8")

    def _create_model_version(
        self,
        state: Workspace,
        version_type: str,
        payload: dict[str, Any] | list[dict[str, Any]],
        *,
        user_email: str,
        source_type: str,
        activate: bool,
    ) -> ModelVersion:
        self._require_model_version_type(version_type)
        normalized_payload = _normalize_model_payload(version_type, payload)
        checksum = hashlib.sha256(_canonical_json(normalized_payload).encode("utf-8")).hexdigest()
        parent_id = self.active_model_version_id(state, version_type)
        version_id = f"{version_type}-{datetime.utcnow().strftime('%Y%m%d%H%M%S%f')}-{checksum[:10]}"
        version_dir = self._model_version_dir(state, version_type, version_id)
        version_dir.mkdir(parents=True, exist_ok=False)
        payload_path = version_dir / _model_payload_filename(version_type)
        _write_model_payload(payload_path, version_type, normalized_payload)
        version = ModelVersion(
            version_type=version_type,
            version_id=version_id,
            source_type=source_type,
            user_email=user_email,
            created_at=_iso_now(),
            checksum=checksum,
            summary=_model_payload_summary(version_type, normalized_payload),
            parent_version_id=parent_id,
            payload_path=payload_path,
        )
        (version_dir / "metadata.json").write_text(json.dumps(version.to_json(), indent=2, sort_keys=True), encoding="utf-8")
        if activate:
            if version_type == "assumptions":
                if not isinstance(normalized_payload, dict):
                    raise ValueError("Assumption payload must be a mapping.")
                config = _config_from_payload(normalized_payload, state.config_path)
                save_config(state.config_path, config)
            else:
                if not isinstance(normalized_payload, list):
                    raise ValueError("Rule payload must be a list.")
                self._write_rules_file(state, [RuleDefinition.from_json(item) for item in normalized_payload])
            active = self._load_active_model_versions(state)
            active[version_type] = version_id
            self._save_active_model_versions(state, active)
        self._persist_model_version(state, version)
        return version

    def _write_rules_file(self, state: Workspace, rules: list[RuleDefinition]) -> None:
        self.rules_path(state).write_text(json.dumps(_rules_payload(rules), indent=2, sort_keys=True), encoding="utf-8")

    def _versions_dir(self, state: Workspace, dataset_key: str) -> Path:
        return state.inputs_dir / "versions" / dataset_key

    def _version_dir(self, state: Workspace, dataset_key: str, version_id: str) -> Path:
        return self._versions_dir(state, dataset_key) / version_id

    def _model_versions_dir(self, state: Workspace, version_type: str) -> Path:
        return state.model_versions_dir / version_type

    def _model_version_dir(self, state: Workspace, version_type: str, version_id: str) -> Path:
        return self._model_versions_dir(state, version_type) / version_id

    def _acknowledgement_path(self, state: Workspace, cycle_id: str) -> Path:
        return state.cycles_dir / cycle_id / "acknowledgement.json"

    def _require_spec(self, dataset_key: str) -> InputSpec:
        try:
            return SPEC_BY_KEY[dataset_key]
        except KeyError as exc:
            raise ValueError(f"Unsupported input dataset: {dataset_key}") from exc

    def _active_input_version_ids(self, state: Workspace) -> dict[str, str]:
        return {spec.key: version_id for spec in INPUT_SPECS if (version_id := self.active_version_id(state, spec.key))}

    def _active_model_version_ids(self, state: Workspace) -> dict[str, str]:
        return {
            version_type: version_id
            for version_type in ("assumptions", "rules")
            if (version_id := self.active_model_version_id(state, version_type))
        }

    def _require_model_version_type(self, version_type: str) -> None:
        if version_type not in {"assumptions", "rules"}:
            raise ValueError(f"Unsupported model version type: {version_type}")

    def _root_for_scope(self, scope: WorkspaceScope | None) -> Path:
        if scope is None:
            return self.base_root
        return self.base_root.joinpath(*scope.path_parts())

    def _persist_input_version(self, state: Workspace, version: InputVersion) -> None:
        if self.persistence is not None and state.scope is not None:
            self.persistence.persist_input_version(state.scope, version)

    def _persist_model_version(self, state: Workspace, version: ModelVersion) -> None:
        if self.persistence is not None and state.scope is not None:
            self.persistence.persist_model_version(state.scope, version)

    def _persist_decision_cycle(self, state: Workspace, cycle: DecisionCycle) -> None:
        if self.persistence is not None and state.scope is not None:
            self.persistence.persist_decision_cycle(state.scope, cycle)

    def _persist_user_access(self, state: Workspace, payload: dict[str, Any]) -> None:
        if self.persistence is not None and state.scope is not None and hasattr(self.persistence, "persist_user_access"):
            self.persistence.persist_user_access(state.scope, payload)


def _read_validated_input_text(version: InputVersion, dataset_kind: str) -> tuple[str, str | None]:
    if version.raw_path is None:
        return "", "Raw input file path is missing."
    try:
        text = Path(version.raw_path).read_text(encoding="utf-8")
    except OSError as exc:
        return "", f"Raw input file cannot be read: {exc}"
    try:
        validate_csv_text(text, dataset_kind=dataset_kind)
    except ValueError as exc:
        return text, str(exc)
    return text, None


def _sample_seed_texts(config: AppConfig) -> dict[str, str]:
    seed_texts: dict[str, str] = {}
    try:
        sample_root = resources.files("fdre_model.sample_data")
        for dataset_key, filename in SAMPLE_SEED_FILES.items():
            seed_texts[dataset_key] = sample_root.joinpath(filename).read_text(encoding="utf-8")
    except (FileNotFoundError, ModuleNotFoundError):
        start = _configured_now(config).replace(minute=0, second=0, microsecond=0) - timedelta(hours=24)
        rows = [start + timedelta(hours=index) for index in range(73)]
        seed_texts = {
            "solar": _seed_generation(rows, base="solar"),
            "wind": _seed_generation(rows, base="wind"),
            "bess_state": _seed_bess(rows, config.state.initial_bess_soc_mwh, config.state.initial_bess_soh_fraction),
            "t2_pricing": _seed_prices(rows),
            "peak_schedule": _seed_peak(rows, config.market_model.default_peak_hours),
        }
    return seed_texts


def _sample_seed_original_name(dataset_key: str) -> str:
    return f"seed:seci-reference-2026:{SAMPLE_SEED_FILES[dataset_key]}"


def _seed_generation(rows: list[datetime], *, base: str) -> str:
    lines = ["timestamp,mwh"]
    for ts in rows:
        if base == "solar":
            daylight = max(0.0, 1.0 - abs(ts.hour - 12) / 6.0)
            value = 120.0 * daylight
        else:
            value = 55.0 + (ts.hour % 6) * 6.0
        lines.append(f"{ts:%Y-%m-%d %H:%M},{value:.3f}")
    return "\n".join(lines) + "\n"


def _seed_bess(rows: list[datetime], soc: float, soh: float) -> str:
    lines = ["timestamp,soc_mwh,soh_fraction"]
    for ts in rows:
        lines.append(f"{ts:%Y-%m-%d %H:%M},{soc:.3f},{soh:.4f}")
    return "\n".join(lines) + "\n"


def _seed_prices(rows: list[datetime]) -> str:
    lines = ["timestamp,price"]
    for ts in rows:
        price = 11.0 if ts.hour in {18, 19, 20, 21} else 6.5 + (ts.hour % 5) * 0.4
        lines.append(f"{ts:%Y-%m-%d %H:%M},{price:.3f}")
    return "\n".join(lines) + "\n"


def _seed_peak(rows: list[datetime], peak_hours: tuple[int, ...]) -> str:
    lines = ["timestamp,is_peak"]
    for ts in rows:
        lines.append(f"{ts:%Y-%m-%d %H:%M},{1 if ts.hour in peak_hours else 0}")
    return "\n".join(lines) + "\n"


def _config_from_payload(payload: dict[str, Any], target_path: Path) -> AppConfig:
    temp = target_path.with_suffix(".tmp.yaml")
    import yaml

    temp.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")
    try:
        return AppConfig.from_yaml(temp)
    finally:
        temp.unlink(missing_ok=True)


def _iso_now() -> str:
    return datetime.utcnow().replace(microsecond=0).isoformat() + "Z"


def _env_admin_emails() -> set[str]:
    raw_values = [
        os.environ.get("FDRE_ADMIN_EMAILS") or "",
        os.environ.get("FDRE_MODEL_ADMIN_EMAILS") or "",
    ]
    emails: set[str] = set()
    for raw in raw_values:
        emails.update(_normalize_email(part) for part in raw.replace(";", ",").split(",") if _normalize_email(part))
    return emails


def _normalize_email(value: str) -> str:
    return value.strip().lower()


def _configured_now(config: AppConfig) -> datetime:
    try:
        return datetime.now(ZoneInfo(config.project.timezone)).replace(tzinfo=None)
    except Exception:
        return datetime.now()


def _rules_payload(rules: list[RuleDefinition]) -> list[dict[str, Any]]:
    return [rule.to_json() for rule in sorted(rules, key=lambda item: (item.priority, item.rule_id))]


def _merge_default_rule_metadata(rules: list[RuleDefinition]) -> tuple[list[RuleDefinition], bool]:
    defaults = {rule.rule_id: rule for rule in DEFAULT_RULES}
    existing = {rule.rule_id: rule for rule in rules}
    merged: list[RuleDefinition] = []
    changed = False
    for rule in rules:
        default = defaults.get(rule.rule_id)
        if default is None:
            merged.append(rule)
            continue
        condition = rule.condition or default.condition
        action = rule.action or default.action
        rule_pack = rule.rule_pack if rule.rule_pack != "v1" or default.rule_pack == "v1" else default.rule_pack
        updated = RuleDefinition(
            rule.rule_id,
            rule.name,
            rule.priority,
            rule.enabled,
            rule.description,
            condition=condition,
            action=action,
            rule_pack=rule_pack,
        )
        changed = changed or updated != rule
        merged.append(updated)
    for rule in DEFAULT_RULES:
        if rule.rule_id not in existing:
            merged.append(rule)
            changed = True
    return merged, changed


def _normalize_model_payload(version_type: str, payload: dict[str, Any] | list[dict[str, Any]]) -> dict[str, Any] | list[dict[str, Any]]:
    if version_type == "assumptions":
        if not isinstance(payload, dict):
            raise ValueError("Assumption payload must be a mapping.")
        return _config_from_payload(payload, Path("project.yaml")).to_dict()
    if version_type == "rules":
        if not isinstance(payload, list):
            raise ValueError("Rule payload must be a list.")
        return _rules_payload([RuleDefinition.from_json(item) for item in payload])
    raise ValueError(f"Unsupported model version type: {version_type}")


def _canonical_json(payload: dict[str, Any] | list[dict[str, Any]]) -> str:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"))


def _model_payload_filename(version_type: str) -> str:
    return "project.yaml" if version_type == "assumptions" else "rules.json"


def _write_model_payload(path: Path, version_type: str, payload: dict[str, Any] | list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if version_type == "assumptions":
        import yaml

        path.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")
    else:
        path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")


def _model_payload_summary(version_type: str, payload: dict[str, Any] | list[dict[str, Any]]) -> str:
    if version_type == "assumptions" and isinstance(payload, dict):
        market = dict(payload.get("market_model") or {})
        capacities = dict(payload.get("capacities") or {})
        tariffs = dict(payload.get("tariffs") or {})
        return (
            f"interval={market.get('interval')}; "
            f"recent={market.get('recent_hours')}h; "
            f"forecast={market.get('forecast_hours')}h; "
            f"ppa={capacities.get('ppa_mwh')}MWh; "
            f"merchant={capacities.get('merchant_mwh')}MWh; "
            f"peak={capacities.get('peak_power_mwh')}MWh; "
            f"ppa_tariff={tariffs.get('ppa')}"
        )
    if version_type == "rules" and isinstance(payload, list):
        enabled = [str(item.get("rule_id")) for item in payload if item.get("enabled")]
        return f"{len(enabled)} enabled rules: {', '.join(enabled)}"
    return ""


def _model_version_summary_values(model_versions: dict[str, str]) -> dict[str, str]:
    return {
        "assumption_version_id": model_versions.get("assumptions", ""),
        "rule_version_id": model_versions.get("rules", ""),
    }


def _window_summary_values(buckets: list[Any], effective_now: datetime, *, custom: bool) -> dict[str, str | int]:
    live_bucket = next((bucket for bucket in buckets if getattr(bucket, "status", "") == "live"), None)
    return {
        "window_mode": "custom" if custom else "default",
        "live_interval": (
            getattr(live_bucket, "start").isoformat(sep=" ") if live_bucket is not None else effective_now.isoformat(sep=" ")
        ),
        "actual_rows": sum(1 for bucket in buckets if getattr(bucket, "status", "") == "actual"),
        "live_rows": sum(1 for bucket in buckets if getattr(bucket, "status", "") == "live"),
        "forecast_rows": sum(1 for bucket in buckets if getattr(bucket, "status", "") == "forecast"),
    }


def _scope_json(state: Workspace) -> dict[str, str]:
    return state.scope.to_json() if state.scope is not None else {}


def _parse_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    text = str(value).strip()
    if text.endswith("Z"):
        text = text[:-1]
    try:
        parsed = datetime.fromisoformat(text.replace("T", " "))
    except ValueError:
        return None
    return parsed.replace(tzinfo=None)


def _source_health_summary(source_health: list[SourceHealth]) -> dict[str, int]:
    return {
        "source_health_ok": sum(1 for item in source_health if item.status == "ok"),
        "source_health_warning": sum(1 for item in source_health if item.status == "warning"),
        "source_health_critical": sum(1 for item in source_health if item.status == "critical"),
    }
