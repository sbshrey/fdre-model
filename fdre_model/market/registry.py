"""Workbook-aligned variable and case registry.

The FDRE workbook uses compact codes in the Notes sheet. This module keeps
those codes visible in the app so assumptions, rule configuration, and future
workbook-parity tests can refer to the same business language.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from fdre_model.config import AppConfig


@dataclass(frozen=True)
class WorkbookVariableDefinition:
    code: str
    group: str
    label: str
    app_field: str
    app_value_path: str = ""
    status: str = "implemented"
    notes: str = ""


@dataclass(frozen=True)
class WorkbookCaseDefinition:
    code: str
    scope: str
    workbook_logic: str
    app_reference: str
    status: str
    next_work: str


WORKBOOK_VARIABLES: tuple[WorkbookVariableDefinition, ...] = (
    WorkbookVariableDefinition("M1", "Markets", "PPA Sale", "MarketDecision.ppa_sale_mwh", status="implemented"),
    WorkbookVariableDefinition("M2", "Markets", "Merchant Sale", "MarketDecision.merchant_sale_mwh", status="implemented"),
    WorkbookVariableDefinition("M3", "Markets", "Peak Power Sale", "MarketDecision.peak_power_sale_mwh", status="implemented"),
    WorkbookVariableDefinition(
        "M4",
        "Markets",
        "BESS",
        "MarketDecision.bess_charge_mwh / bess_discharge_mwh",
        status="partial",
        notes="Charge and discharge are represented; workbook residual/arbitrage behavior is later backlog.",
    ),
    WorkbookVariableDefinition("M5", "Markets", "GDAM", "planned.market.gdam", status="planned"),
    WorkbookVariableDefinition("G1", "Generation", "Wind", "input.wind + capacities.wind_mwh", "capacities.wind_mwh"),
    WorkbookVariableDefinition("G2", "Generation", "Solar", "input.solar + capacities.solar_mwh", "capacities.solar_mwh"),
    WorkbookVariableDefinition(
        "G3",
        "Generation",
        "BESS Discharge",
        "MarketDecision.bess_discharge_mwh",
        "capacities.bess_discharge_limit_mwh",
        status="partial",
        notes="Uses live SOC and discharge loss; degradation/SOH capacity adjustment is later backlog.",
    ),
    WorkbookVariableDefinition("G4", "Generation", "Merchant Buy", "planned.generation.merchant_buy", status="planned"),
    WorkbookVariableDefinition("C1", "Consumption", "PPA", "capacities.ppa_mwh", "capacities.ppa_mwh"),
    WorkbookVariableDefinition("C2", "Consumption", "Merchant", "capacities.merchant_mwh", "capacities.merchant_mwh"),
    WorkbookVariableDefinition("C3", "Consumption", "Peak Power", "capacities.peak_power_mwh", "capacities.peak_power_mwh"),
    WorkbookVariableDefinition("C4", "Consumption", "BESS Charge", "capacities.bess_charge_limit_mwh", "capacities.bess_charge_limit_mwh"),
    WorkbookVariableDefinition(
        "C5",
        "Consumption",
        "Charge Loss",
        "market_model.charge_loss_fraction",
        "market_model.charge_loss_fraction",
        notes="Workbook Notes labels this row as C6; app keeps C5/C6 unique for auditability.",
    ),
    WorkbookVariableDefinition(
        "C6",
        "Consumption",
        "Discharge Loss",
        "market_model.discharge_loss_fraction",
        "market_model.discharge_loss_fraction",
    ),
    WorkbookVariableDefinition("T1", "Tariffs", "PPA", "tariffs.ppa", "tariffs.ppa"),
    WorkbookVariableDefinition(
        "T2",
        "Tariffs",
        "Merchant Sell",
        "input.t2_pricing + tariffs.merchant_sell_default",
        "tariffs.merchant_sell_default",
        notes="Hourly price comes from T2 / Merchant Pricing input; this value is only fallback.",
    ),
    WorkbookVariableDefinition("T3", "Tariffs", "Peak Power", "tariffs.peak_power", "tariffs.peak_power"),
    WorkbookVariableDefinition(
        "T4",
        "Tariffs",
        "Penalty",
        "tariffs.penalty_multiplier x tariffs.ppa",
        "tariffs.penalty_multiplier",
        notes="Penalty value is computed as shortfall x T1 x multiplier.",
    ),
    WorkbookVariableDefinition("T5", "Tariffs", "Merchant Buy", "planned.tariff.merchant_buy", status="planned"),
    WorkbookVariableDefinition("T6", "Tariffs", "GDAM", "planned.tariff.gdam", status="planned"),
    WorkbookVariableDefinition("T7", "Tariffs", "Others", "planned.tariff.others", status="planned"),
    WorkbookVariableDefinition("Cap1", "Capacity", "Wind", "capacities.wind_mwh", "capacities.wind_mwh"),
    WorkbookVariableDefinition("Cap2", "Capacity", "Solar", "capacities.solar_mwh", "capacities.solar_mwh"),
    WorkbookVariableDefinition("Cap3", "Capacity", "BESS", "capacities.bess_capacity_mwh", "capacities.bess_capacity_mwh"),
    WorkbookVariableDefinition("Cap4", "Capacity", "PPA", "capacities.ppa_mwh", "capacities.ppa_mwh"),
    WorkbookVariableDefinition("Cap5", "Capacity", "Merchant", "capacities.merchant_mwh", "capacities.merchant_mwh"),
    WorkbookVariableDefinition(
        "Cap6",
        "Capacity",
        "Live BESS SOC",
        "input.bess_state.soc_mwh + state.initial_bess_soc_mwh",
        "state.initial_bess_soc_mwh",
        notes="Hourly SOC comes from BESS State input; this value is fallback.",
    ),
    WorkbookVariableDefinition(
        "Cap7",
        "Capacity",
        "Live BESS SOH",
        "input.bess_state.soh_fraction + state.initial_bess_soh_fraction",
        "state.initial_bess_soh_fraction",
        status="partial",
        notes="SOH is loaded, but not yet applied to usable capacity.",
    ),
    WorkbookVariableDefinition("Cap8", "Capacity", "Evacuation", "capacities.evacuation_mwh", "capacities.evacuation_mwh"),
    WorkbookVariableDefinition(
        "Cap9",
        "Capacity",
        "Live Peak Power",
        "planned.capacity.live_peak_power",
        status="planned",
        notes="Current app uses Cap10 for peak target until live peak-power input is added.",
    ),
    WorkbookVariableDefinition("Cap10", "Capacity", "Peak Power", "capacities.peak_power_mwh", "capacities.peak_power_mwh"),
    WorkbookVariableDefinition(
        "Cap11",
        "Capacity",
        "Charge as C Rate",
        "planned.capacity.charge_c_rate",
        status="planned",
    ),
    WorkbookVariableDefinition(
        "Cap12",
        "Capacity",
        "Discharge as per C Rate",
        "planned.capacity.discharge_c_rate",
        status="planned",
    ),
    WorkbookVariableDefinition("F1", "Forecast", "Wind", "input.wind", status="implemented"),
    WorkbookVariableDefinition("F2", "Forecast", "Solar", "input.solar", status="implemented"),
    WorkbookVariableDefinition(
        "F3",
        "Forecast",
        "BESS Degradation Profile",
        "input.bess_state.soh_fraction",
        status="partial",
        notes="SOH is versioned but not yet used to derate capacity.",
    ),
    WorkbookVariableDefinition("F4", "Forecast", "T2 Pricing", "input.t2_pricing", status="implemented"),
    WorkbookVariableDefinition("F5", "Forecast", "Peak Schedule", "input.peak_schedule", status="implemented"),
    WorkbookVariableDefinition(
        "P1",
        "Parameters",
        "Curtailed Energy Before Peak Hours",
        "planned.parameter.forecast_pre_peak_curtailment",
        status="planned",
    ),
    WorkbookVariableDefinition(
        "P2",
        "Parameters",
        "Curtailed Energy Now",
        "MarketDecision.curtailment_mwh",
        status="partial",
        notes="Current interval curtailment exists; forecast lookahead definition is later backlog.",
    ),
    WorkbookVariableDefinition(
        "P3",
        "Parameters",
        "Expected Deficit in 90% Monthly Compliance",
        "planned.parameter.monthly_peak_deficit",
        status="planned",
    ),
    WorkbookVariableDefinition(
        "P4",
        "Parameters",
        "5% of Annual PPA Generation",
        "planned.parameter.annual_ppa_procurement_cap",
        status="planned",
    ),
    WorkbookVariableDefinition(
        "P5",
        "Parameters",
        "Live Generation + 365D Generation",
        "planned.parameter.live_plus_365d_generation",
        status="planned",
    ),
)


WORKBOOK_CASES: tuple[WorkbookCaseDefinition, ...] = (
    WorkbookCaseDefinition(
        "Case 2",
        "Non-peak",
        "If forecast curtailed energy can meet BESS headroom, allocate residual across PPA, merchant, and BESS.",
        "ppa_sale / merchant_sale / bess_charge",
        "partial",
        "Implement forecast-derived P1 and case-specific allocation order.",
    ),
    WorkbookCaseDefinition(
        "Case 3",
        "Non-peak",
        "If forecast curtailed energy is insufficient, prioritize BESS charging before other residual markets.",
        "bess_charge",
        "partial",
        "Implement P1 < BESS headroom branch.",
    ),
    WorkbookCaseDefinition(
        "Cases 4/5",
        "Non-peak",
        "Choose PPA vs merchant order based on T1 > T2 comparison.",
        "rule priority + min_merchant_price/max_merchant_price",
        "partial",
        "Add tariff-comparison rule conditions using workbook variable aliases.",
    ),
    WorkbookCaseDefinition(
        "Case 6/7",
        "Peak",
        "During peak, meet peak power first, then allocate excess to PPA, merchant, and BESS.",
        "peak_power_obligation",
        "partial",
        "Add 90% monthly compliance and Cap9 live peak-power logic.",
    ),
    WorkbookCaseDefinition(
        "Clause 1/iii",
        "Peak",
        "Use merchant power for peak power when workbook compliance and tariff conditions allow.",
        "merchant_buy_shortfall placeholder",
        "planned",
        "Implement merchant-for-peak procurement rule.",
    ),
    WorkbookCaseDefinition(
        "Case 8",
        "Non-peak",
        "Use excess BESS cycles for merchant buy/sell arbitrage when future T5 beats current loss-adjusted T5.",
        "forecast_peak_charge placeholder",
        "planned",
        "Implement BESS arbitrage lookahead.",
    ),
    WorkbookCaseDefinition(
        "Cases 9/10",
        "Peak procurement",
        "Procure RE to mitigate 90% peak penalty if live T2 is below penalty threshold.",
        "penalty_procurement_monitor placeholder",
        "planned",
        "Implement penalty procurement action.",
    ),
    WorkbookCaseDefinition(
        "Cases 11/12",
        "Annual CUF",
        "Procure RE up to annual CUF cap when lowest T2 is below PPA tariff.",
        "annual_cuf_monitor placeholder",
        "planned",
        "Implement annual CUF procurement action.",
    ),
)


def workbook_variable_rows(config: AppConfig) -> list[dict[str, str]]:
    return [
        {
            "group": item.group,
            "code": item.code,
            "label": item.label,
            "app_field": item.app_field,
            "current_value": _current_value(config, item.app_value_path),
            "status": item.status,
            "notes": item.notes,
        }
        for item in WORKBOOK_VARIABLES
    ]


def workbook_case_rows() -> list[dict[str, str]]:
    return [
        {
            "code": item.code,
            "scope": item.scope,
            "workbook_logic": item.workbook_logic,
            "app_reference": item.app_reference,
            "status": item.status,
            "next_work": item.next_work,
        }
        for item in WORKBOOK_CASES
    ]


def _current_value(config: AppConfig, path: str) -> str:
    if not path:
        return ""
    value: Any = config
    try:
        for part in path.split("."):
            value = getattr(value, part)
    except AttributeError:
        return ""
    if isinstance(value, float):
        return f"{value:g}"
    if isinstance(value, (tuple, list)):
        return " ".join(str(item) for item in value)
    return str(value)
