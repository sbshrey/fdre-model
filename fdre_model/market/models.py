"""Typed models used by the FDRE market engine."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class InputSpec:
    key: str
    label: str
    description: str
    expected_headers: tuple[str, ...]
    kind: str


@dataclass(frozen=True)
class InputVersion:
    dataset_key: str
    version_id: str
    source_type: str
    original_name: str
    user_email: str
    created_at: str
    checksum: str
    row_count: int
    coverage_start: str | None
    coverage_end: str | None
    validation_status: str
    validation_message: str
    parent_version_id: str | None = None
    raw_path: Path | None = None

    def to_json(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["raw_path"] = str(self.raw_path) if self.raw_path is not None else None
        return payload

    @classmethod
    def from_json(cls, payload: dict[str, Any]) -> "InputVersion":
        data = dict(payload)
        if data.get("raw_path"):
            data["raw_path"] = Path(str(data["raw_path"]))
        return cls(**data)


@dataclass(frozen=True)
class SourceHealth:
    dataset_key: str
    label: str
    status: str
    message: str
    active_version_id: str | None
    source_type: str | None
    row_count: int
    coverage_start: str | None
    coverage_end: str | None
    required_start: str
    required_end: str

    def to_json(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_json(cls, payload: dict[str, Any]) -> "SourceHealth":
        return cls(
            dataset_key=str(payload["dataset_key"]),
            label=str(payload["label"]),
            status=str(payload["status"]),
            message=str(payload.get("message", "")),
            active_version_id=str(payload["active_version_id"]) if payload.get("active_version_id") else None,
            source_type=str(payload["source_type"]) if payload.get("source_type") else None,
            row_count=int(payload.get("row_count") or 0),
            coverage_start=str(payload["coverage_start"]) if payload.get("coverage_start") else None,
            coverage_end=str(payload["coverage_end"]) if payload.get("coverage_end") else None,
            required_start=str(payload.get("required_start") or ""),
            required_end=str(payload.get("required_end") or ""),
        )


@dataclass(frozen=True)
class OperatorAcknowledgement:
    cycle_id: str
    acknowledged_at: str
    user_email: str
    note: str
    workspace_scope: dict[str, str] = field(default_factory=dict)

    def to_json(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_json(cls, payload: dict[str, Any]) -> "OperatorAcknowledgement":
        return cls(
            cycle_id=str(payload["cycle_id"]),
            acknowledged_at=str(payload["acknowledged_at"]),
            user_email=str(payload["user_email"]),
            note=str(payload.get("note", "")),
            workspace_scope={str(k): str(v) for k, v in dict(payload.get("workspace_scope") or {}).items()},
        )


@dataclass(frozen=True)
class AppUser:
    email: str
    role: str
    active: bool
    name: str = ""
    source: str = "admin_ui"
    created_at: str = ""
    created_by: str = ""
    updated_at: str = ""
    updated_by: str = ""
    notes: str = ""

    def to_json(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_json(cls, payload: dict[str, Any]) -> "AppUser":
        return cls(
            email=str(payload["email"]).strip().lower(),
            role=str(payload.get("role") or "operator").strip().lower(),
            active=bool(payload.get("active", True)),
            name=str(payload.get("name") or ""),
            source=str(payload.get("source") or "admin_ui"),
            created_at=str(payload.get("created_at") or ""),
            created_by=str(payload.get("created_by") or ""),
            updated_at=str(payload.get("updated_at") or ""),
            updated_by=str(payload.get("updated_by") or ""),
            notes=str(payload.get("notes") or ""),
        )


@dataclass(frozen=True)
class ModelVersion:
    version_type: str
    version_id: str
    source_type: str
    user_email: str
    created_at: str
    checksum: str
    summary: str
    parent_version_id: str | None = None
    payload_path: Path | None = None

    def to_json(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["payload_path"] = str(self.payload_path) if self.payload_path is not None else None
        return payload

    @classmethod
    def from_json(cls, payload: dict[str, Any]) -> "ModelVersion":
        data = dict(payload)
        if data.get("payload_path"):
            data["payload_path"] = Path(str(data["payload_path"]))
        return cls(**data)


@dataclass(frozen=True)
class TimeBucket:
    start: datetime
    end: datetime
    status: str
    is_peak: bool


@dataclass
class OperatingState:
    bess_soc_mwh: float
    bess_soh_fraction: float


@dataclass(frozen=True)
class RuleDefinition:
    rule_id: str
    name: str
    priority: int
    enabled: bool
    description: str
    condition: dict[str, Any] = field(default_factory=dict)
    action: dict[str, Any] = field(default_factory=dict)
    rule_pack: str = "v1"

    def to_json(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_json(cls, payload: dict[str, Any]) -> "RuleDefinition":
        return cls(
            rule_id=str(payload["rule_id"]),
            name=str(payload["name"]),
            priority=int(payload["priority"]),
            enabled=bool(payload["enabled"]),
            description=str(payload.get("description", "")),
            condition=dict(payload.get("condition") or {}),
            action=dict(payload.get("action") or {}),
            rule_pack=str(payload.get("rule_pack") or "v1"),
        )


@dataclass
class MarketDecision:
    interval_start: datetime
    interval_end: datetime
    status: str
    is_peak: bool
    wind_mwh: float
    solar_mwh: float
    available_mwh: float
    merchant_price: float
    bess_open_mwh: float
    bess_close_mwh: float
    ppa_sale_mwh: float = 0.0
    merchant_sale_mwh: float = 0.0
    peak_power_sale_mwh: float = 0.0
    bess_charge_mwh: float = 0.0
    bess_discharge_mwh: float = 0.0
    curtailment_mwh: float = 0.0
    shortfall_mwh: float = 0.0
    penalty_value: float = 0.0
    revenue_value: float = 0.0
    recommended_market: str = "None"
    applied_rule_ids: list[str] = field(default_factory=list)
    skipped_rule_ids: list[str] = field(default_factory=list)
    residual_mwh: float = 0.0
    audit_trace: list[str] = field(default_factory=list)

    def to_row(self) -> dict[str, Any]:
        return {
            "interval_start": self.interval_start.isoformat(sep=" "),
            "interval_end": self.interval_end.isoformat(sep=" "),
            "status": self.status,
            "is_peak": int(self.is_peak),
            "wind_mwh": round(self.wind_mwh, 6),
            "solar_mwh": round(self.solar_mwh, 6),
            "available_mwh": round(self.available_mwh, 6),
            "merchant_price": round(self.merchant_price, 6),
            "bess_open_mwh": round(self.bess_open_mwh, 6),
            "bess_close_mwh": round(self.bess_close_mwh, 6),
            "ppa_sale_mwh": round(self.ppa_sale_mwh, 6),
            "merchant_sale_mwh": round(self.merchant_sale_mwh, 6),
            "peak_power_sale_mwh": round(self.peak_power_sale_mwh, 6),
            "bess_charge_mwh": round(self.bess_charge_mwh, 6),
            "bess_discharge_mwh": round(self.bess_discharge_mwh, 6),
            "curtailment_mwh": round(self.curtailment_mwh, 6),
            "shortfall_mwh": round(self.shortfall_mwh, 6),
            "penalty_value": round(self.penalty_value, 6),
            "revenue_value": round(self.revenue_value, 6),
            "recommended_market": self.recommended_market,
            "applied_rule_ids": ",".join(self.applied_rule_ids),
            "skipped_rule_ids": ",".join(self.skipped_rule_ids),
            "residual_mwh": round(self.residual_mwh, 6),
            "audit_trace": " | ".join(self.audit_trace),
        }


@dataclass(frozen=True)
class DecisionCycle:
    cycle_id: str
    created_at: str
    window_start: str
    window_end: str
    workspace_scope: dict[str, str]
    input_versions: dict[str, str]
    model_versions: dict[str, str]
    rule_order: list[str]
    artifact_paths: dict[str, Path]
    summary: dict[str, float | int | str]
    source_health: list[SourceHealth] = field(default_factory=list)

    def to_json(self) -> dict[str, Any]:
        return {
            "cycle_id": self.cycle_id,
            "created_at": self.created_at,
            "window_start": self.window_start,
            "window_end": self.window_end,
            "workspace_scope": self.workspace_scope,
            "input_versions": self.input_versions,
            "model_versions": self.model_versions,
            "rule_order": self.rule_order,
            "artifact_paths": {key: str(value) for key, value in self.artifact_paths.items()},
            "summary": self.summary,
            "source_health": [item.to_json() for item in self.source_health],
        }

    @classmethod
    def from_json(cls, payload: dict[str, Any]) -> "DecisionCycle":
        return cls(
            cycle_id=str(payload["cycle_id"]),
            created_at=str(payload["created_at"]),
            window_start=str(payload["window_start"]),
            window_end=str(payload["window_end"]),
            workspace_scope={str(k): str(v) for k, v in dict(payload.get("workspace_scope") or {}).items()},
            input_versions={str(k): str(v) for k, v in dict(payload.get("input_versions") or {}).items()},
            model_versions={str(k): str(v) for k, v in dict(payload.get("model_versions") or {}).items()},
            rule_order=[str(item) for item in payload.get("rule_order") or []],
            artifact_paths={str(k): Path(str(v)) for k, v in dict(payload.get("artifact_paths") or {}).items()},
            summary=dict(payload.get("summary") or {}),
            source_health=[SourceHealth.from_json(item) for item in payload.get("source_health") or []],
        )
