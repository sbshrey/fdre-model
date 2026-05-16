"""Decision-cycle orchestration for the live market board."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from fdre_model.config import AppConfig
from fdre_model.market.aggregation import (
    aggregate_generation,
    aggregate_peak_schedule,
    aggregate_prices,
    build_time_buckets,
    latest_bess_state,
    parse_csv_text,
)
from fdre_model.market.models import MarketDecision, OperatingState, RuleDefinition, TimeBucket
from fdre_model.market.rules import RuleContext, evaluate_rules


@dataclass(frozen=True)
class ActiveInputData:
    solar_rows: list[dict[str, object]]
    wind_rows: list[dict[str, object]]
    bess_rows: list[dict[str, object]]
    price_rows: list[dict[str, object]]
    peak_rows: list[dict[str, object]]
    version_ids: dict[str, str]


def build_decisions(
    config: AppConfig,
    active_inputs: ActiveInputData,
    rules: list[RuleDefinition],
    *,
    now: datetime | None = None,
) -> tuple[list[MarketDecision], list[TimeBucket]]:
    buckets = build_time_buckets(config, now=now)
    solar = aggregate_generation(active_inputs.solar_rows, buckets)
    wind = aggregate_generation(active_inputs.wind_rows, buckets)
    prices = aggregate_prices(
        active_inputs.price_rows,
        buckets,
        default=config.tariffs.merchant_sell_default,
    )
    peak_schedule = aggregate_peak_schedule(active_inputs.peak_rows, buckets) if active_inputs.peak_rows else {
        bucket.start: bucket.is_peak for bucket in buckets
    }
    bess_states = latest_bess_state(
        active_inputs.bess_rows,
        buckets,
        default_soc_mwh=config.state.initial_bess_soc_mwh,
        default_soh_fraction=config.state.initial_bess_soh_fraction,
    )

    context = RuleContext(
        ppa_cap_mwh=config.capacities.ppa_mwh,
        merchant_cap_mwh=config.capacities.merchant_mwh,
        peak_cap_mwh=config.capacities.peak_power_mwh,
        bess_capacity_mwh=config.capacities.bess_capacity_mwh,
        bess_charge_limit_mwh=config.capacities.bess_charge_limit_mwh,
        bess_discharge_limit_mwh=config.capacities.bess_discharge_limit_mwh,
        charge_loss_fraction=config.market_model.charge_loss_fraction,
        discharge_loss_fraction=config.market_model.discharge_loss_fraction,
        ppa_tariff=config.tariffs.ppa,
        peak_tariff=config.tariffs.peak_power,
        penalty_multiplier=config.tariffs.penalty_multiplier,
    )

    decisions: list[MarketDecision] = []
    rolling_state: OperatingState | None = None
    for bucket in buckets:
        source_soc, source_soh = bess_states[bucket.start]
        if rolling_state is None or bucket.status in {"actual", "live"}:
            rolling_state = OperatingState(
                bess_soc_mwh=min(source_soc, config.capacities.bess_capacity_mwh),
                bess_soh_fraction=source_soh,
            )
        is_peak = bool(peak_schedule.get(bucket.start, bucket.is_peak))
        wind_mwh = min(max(wind.get(bucket.start, 0.0), 0.0), config.capacities.wind_mwh)
        solar_mwh = min(max(solar.get(bucket.start, 0.0), 0.0), config.capacities.solar_mwh)
        available = min(wind_mwh + solar_mwh, config.capacities.evacuation_mwh)
        decision = MarketDecision(
            interval_start=bucket.start,
            interval_end=bucket.end,
            status=bucket.status,
            is_peak=is_peak,
            wind_mwh=wind_mwh,
            solar_mwh=solar_mwh,
            available_mwh=available,
            merchant_price=prices.get(bucket.start, config.tariffs.merchant_sell_default),
            bess_open_mwh=rolling_state.bess_soc_mwh,
            bess_close_mwh=rolling_state.bess_soc_mwh,
            audit_trace=[f"input_versions={active_inputs.version_ids}"],
        )
        decisions.append(evaluate_rules(decision, rolling_state, rules, context))
    return decisions, buckets


def summary_for_decisions(decisions: list[MarketDecision]) -> dict[str, float | int | str]:
    def total(attr: str) -> float:
        return sum(float(getattr(decision, attr)) for decision in decisions)

    return {
        "rows": len(decisions),
        "window_start": decisions[0].interval_start.isoformat(sep=" ") if decisions else "",
        "window_end": decisions[-1].interval_end.isoformat(sep=" ") if decisions else "",
        "ppa_sale_mwh": total("ppa_sale_mwh"),
        "merchant_sale_mwh": total("merchant_sale_mwh"),
        "peak_power_sale_mwh": total("peak_power_sale_mwh"),
        "bess_charge_mwh": total("bess_charge_mwh"),
        "bess_discharge_mwh": total("bess_discharge_mwh"),
        "curtailment_mwh": total("curtailment_mwh"),
        "shortfall_mwh": total("shortfall_mwh"),
        "penalty_value": total("penalty_value"),
        "revenue_value": total("revenue_value"),
    }


def parse_active_input_texts(texts: dict[str, str], version_ids: dict[str, str]) -> ActiveInputData:
    return ActiveInputData(
        solar_rows=parse_csv_text(texts.get("solar", ""), dataset_kind="generation").rows if texts.get("solar") else [],
        wind_rows=parse_csv_text(texts.get("wind", ""), dataset_kind="generation").rows if texts.get("wind") else [],
        bess_rows=parse_csv_text(texts.get("bess_state", ""), dataset_kind="bess_state").rows if texts.get("bess_state") else [],
        price_rows=parse_csv_text(texts.get("t2_pricing", ""), dataset_kind="price").rows if texts.get("t2_pricing") else [],
        peak_rows=parse_csv_text(texts.get("peak_schedule", ""), dataset_kind="peak_schedule").rows if texts.get("peak_schedule") else [],
        version_ids=version_ids,
    )
