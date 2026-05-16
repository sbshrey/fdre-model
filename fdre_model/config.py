"""Configuration loading for the FDRE market model."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml


@dataclass(frozen=True)
class ProjectSettings:
    name: str = "FDRE Live Market Operations"
    timezone: str = "Asia/Kolkata"


@dataclass(frozen=True)
class MarketModelSettings:
    interval: str = "1h"
    recent_hours: int = 6
    forecast_hours: int = 24
    charge_loss_fraction: float = 0.13
    discharge_loss_fraction: float = 0.07
    default_peak_hours: tuple[int, ...] = (18, 19, 20, 21)


@dataclass(frozen=True)
class CapacitySettings:
    wind_mwh: float = 211.2
    solar_mwh: float = 162.0
    bess_capacity_mwh: float = 100.0
    bess_charge_limit_mwh: float = 50.0
    bess_discharge_limit_mwh: float = 50.0
    ppa_mwh: float = 150.0
    merchant_mwh: float = 35.0
    evacuation_mwh: float = 185.0
    peak_power_mwh: float = 150.0


@dataclass(frozen=True)
class TariffSettings:
    ppa: float = 6.0
    merchant_sell_default: float = 10.0
    peak_power: float = 7.0
    penalty_multiplier: float = 1.5


@dataclass(frozen=True)
class StateSettings:
    initial_bess_soc_mwh: float = 50.0
    initial_bess_soh_fraction: float = 1.0


@dataclass(frozen=True)
class AppConfig:
    project: ProjectSettings = field(default_factory=ProjectSettings)
    market_model: MarketModelSettings = field(default_factory=MarketModelSettings)
    capacities: CapacitySettings = field(default_factory=CapacitySettings)
    tariffs: TariffSettings = field(default_factory=TariffSettings)
    state: StateSettings = field(default_factory=StateSettings)
    config_path: Path | None = None

    @classmethod
    def from_yaml(cls, path: str | Path) -> "AppConfig":
        config_path = Path(path).expanduser().resolve()
        payload = _load_yaml(config_path)
        market_payload = dict(payload.get("market_model") or {})
        peak_hours = market_payload.get("default_peak_hours", [18, 19, 20, 21])
        config = cls(
            project=ProjectSettings(**dict(payload.get("project") or {})),
            market_model=MarketModelSettings(
                interval=str(market_payload.get("interval", "1h")),
                recent_hours=int(market_payload.get("recent_hours", 6)),
                forecast_hours=int(market_payload.get("forecast_hours", 24)),
                charge_loss_fraction=float(market_payload.get("charge_loss_fraction", 0.13)),
                discharge_loss_fraction=float(market_payload.get("discharge_loss_fraction", 0.07)),
                default_peak_hours=tuple(int(item) for item in peak_hours),
            ),
            capacities=CapacitySettings(**_float_mapping(payload.get("capacities") or {})),
            tariffs=TariffSettings(**_float_mapping(payload.get("tariffs") or {})),
            state=StateSettings(**_float_mapping(payload.get("state") or {})),
            config_path=config_path,
        )
        config.validate()
        return config

    def to_dict(self) -> dict[str, Any]:
        return {
            "project": {
                "name": self.project.name,
                "timezone": self.project.timezone,
            },
            "market_model": {
                "interval": self.market_model.interval,
                "recent_hours": self.market_model.recent_hours,
                "forecast_hours": self.market_model.forecast_hours,
                "charge_loss_fraction": self.market_model.charge_loss_fraction,
                "discharge_loss_fraction": self.market_model.discharge_loss_fraction,
                "default_peak_hours": list(self.market_model.default_peak_hours),
            },
            "capacities": self.capacities.__dict__,
            "tariffs": self.tariffs.__dict__,
            "state": self.state.__dict__,
        }

    def validate(self) -> None:
        if self.market_model.interval not in {"1m", "15m", "1h"}:
            raise ValueError("market_model.interval must be one of: 1m, 15m, 1h")
        if self.market_model.recent_hours < 0 or self.market_model.forecast_hours < 0:
            raise ValueError("recent_hours and forecast_hours must be non-negative.")
        for hour in self.market_model.default_peak_hours:
            if hour < 0 or hour > 23:
                raise ValueError("Peak hours must be between 0 and 23.")
        for name, value in self.capacities.__dict__.items():
            if float(value) < 0:
                raise ValueError(f"Capacity {name} must be non-negative.")
        if self.capacities.bess_capacity_mwh <= 0:
            raise ValueError("bess_capacity_mwh must be positive.")
        if self.tariffs.penalty_multiplier < 0:
            raise ValueError("penalty_multiplier must be non-negative.")


def save_config(path: str | Path, config: AppConfig) -> None:
    path = Path(path).expanduser().resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        yaml.safe_dump(config.to_dict(), handle, sort_keys=False)


def load_config_payload(path: str | Path) -> dict[str, Any]:
    return _load_yaml(Path(path).expanduser().resolve())


def save_config_payload(path: str | Path, payload: dict[str, Any]) -> None:
    temp = _write_temp_yaml(path, payload)
    try:
        config = AppConfig.from_yaml(temp)
    finally:
        temp.unlink(missing_ok=True)
    save_config(path, config)


def _write_temp_yaml(path: str | Path, payload: dict[str, Any]) -> Path:
    target = Path(path).expanduser().resolve()
    temp = target.with_suffix(".validate.tmp.yaml")
    with temp.open("w", encoding="utf-8") as handle:
        yaml.safe_dump(payload, handle, sort_keys=False)
    return temp


def _load_yaml(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        payload = yaml.safe_load(handle) or {}
    if not isinstance(payload, dict):
        raise ValueError("Configuration root must be a mapping.")
    return payload


def _float_mapping(payload: dict[str, Any]) -> dict[str, float]:
    return {str(key): float(value) for key, value in payload.items()}
