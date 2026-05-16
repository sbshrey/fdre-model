"""Workbook-aligned compliance and forecast metrics."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from fdre_model.config import AppConfig
from fdre_model.market.aggregation import build_time_buckets, floor_datetime, parse_interval
from fdre_model.market.engine import ActiveInputData
from fdre_model.market.models import MarketDecision, TimeBucket


def workbook_metric_summary(
    config: AppConfig,
    active_inputs: ActiveInputData,
    decisions: list[MarketDecision],
    buckets: list[TimeBucket],
    *,
    now: datetime,
) -> dict[str, float | int | str]:
    if not decisions or not buckets:
        return _empty_metrics()

    live_bucket = next((bucket for bucket in buckets if bucket.status == "live"), buckets[0])
    live_at = live_bucket.start
    forecast_buckets = [bucket for bucket in buckets if bucket.status == "forecast"][:22]
    month_start, month_end = _month_bounds(live_at)
    year_start, year_end = datetime(live_at.year, 1, 1), datetime(live_at.year + 1, 1, 1)

    live_generation = _generation_totals(config, active_inputs, [live_bucket])
    forecast_22h_generation = _generation_totals(config, active_inputs, forecast_buckets)
    month_buckets = _period_buckets(config, now=now, start=month_start, end=month_end)
    month_generation = _generation_totals(config, active_inputs, month_buckets)
    month_peak = _peak_obligation_metrics(config, active_inputs, month_buckets, month_generation)
    forecast_30d_buckets = _period_buckets(config, now=now, start=live_at, end=live_at + timedelta(days=30))
    forecast_30d_generation = _generation_totals(config, active_inputs, forecast_30d_buckets)
    forecast_30d_peak = _peak_obligation_metrics(config, active_inputs, forecast_30d_buckets, forecast_30d_generation)
    annual_buckets = _period_buckets(config, now=now, start=year_start, end=year_end)
    annual_generation = _generation_totals(config, active_inputs, annual_buckets)
    p5_buckets = _period_buckets(config, now=now, start=live_at, end=live_at + timedelta(days=365))
    p5_generation = _generation_totals(config, active_inputs, p5_buckets)

    monthly_obligation = month_peak["peak_obligation_mwh"] * 0.9
    monthly_possible = month_peak["peak_possible_mwh"]
    monthly_deficit = max(monthly_obligation - monthly_possible, 0.0)
    annual_capacity_base = (config.capacities.wind_mwh + config.capacities.solar_mwh) * max(len(annual_buckets), 1)
    annual_cuf_fraction = annual_generation["total_generation_mwh"] / annual_capacity_base if annual_capacity_base > 0 else 0.0
    annual_ppa_generation = sum(
        min(total, config.capacities.ppa_mwh)
        for total in annual_generation["generation_by_bucket"].values()
    )

    return {
        "p1_forecast_22h_curtailment_mwh": round(forecast_22h_generation["evacuation_excess_mwh"], 6),
        "p2_live_curtailment_mwh": round(live_generation["evacuation_excess_mwh"], 6),
        "p3_monthly_peak_90_deficit_mwh": round(monthly_deficit, 6),
        "p4_annual_ppa_generation_5pct_mwh": round(annual_ppa_generation * 0.05, 6),
        "p5_365d_generation_mwh": round(p5_generation["total_generation_mwh"], 6),
        "monthly_peak_90_compliance_pct": round(_pct(monthly_possible, monthly_obligation), 6),
        "monthly_peak_obligation_mwh": round(month_peak["peak_obligation_mwh"], 6),
        "monthly_peak_90_obligation_mwh": round(monthly_obligation, 6),
        "monthly_peak_possible_mwh": round(monthly_possible, 6),
        "monthly_peak_intervals": int(month_peak["peak_intervals"]),
        "forecast_30d_peak_possible_mwh": round(forecast_30d_peak["peak_possible_mwh"], 6),
        "forecast_30d_peak_intervals": int(forecast_30d_peak["peak_intervals"]),
        "annual_generation_mwh": round(annual_generation["total_generation_mwh"], 6),
        "annual_cuf_pct": round(annual_cuf_fraction * 100.0, 6),
        "metrics_live_interval": live_at.isoformat(sep=" "),
    }


def _empty_metrics() -> dict[str, float | int | str]:
    return {
        "p1_forecast_22h_curtailment_mwh": 0.0,
        "p2_live_curtailment_mwh": 0.0,
        "p3_monthly_peak_90_deficit_mwh": 0.0,
        "p4_annual_ppa_generation_5pct_mwh": 0.0,
        "p5_365d_generation_mwh": 0.0,
        "monthly_peak_90_compliance_pct": 0.0,
        "monthly_peak_obligation_mwh": 0.0,
        "monthly_peak_90_obligation_mwh": 0.0,
        "monthly_peak_possible_mwh": 0.0,
        "monthly_peak_intervals": 0,
        "forecast_30d_peak_possible_mwh": 0.0,
        "forecast_30d_peak_intervals": 0,
        "annual_generation_mwh": 0.0,
        "annual_cuf_pct": 0.0,
        "metrics_live_interval": "",
    }


def _period_buckets(config: AppConfig, *, now: datetime, start: datetime, end: datetime) -> list[TimeBucket]:
    return build_time_buckets(config, now=now, window_start=start, window_end=end)


def _generation_totals(
    config: AppConfig,
    active_inputs: ActiveInputData,
    buckets: list[TimeBucket],
) -> dict[str, Any]:
    wind = _aggregate_generation_fast(config, active_inputs.wind_rows, buckets)
    solar = _aggregate_generation_fast(config, active_inputs.solar_rows, buckets)
    total_generation = 0.0
    evacuation_excess = 0.0
    generation_by_bucket: dict[datetime, float] = {}
    for bucket in buckets:
        wind_mwh = min(max(wind.get(bucket.start, 0.0), 0.0), config.capacities.wind_mwh)
        solar_mwh = min(max(solar.get(bucket.start, 0.0), 0.0), config.capacities.solar_mwh)
        total = wind_mwh + solar_mwh
        generation_by_bucket[bucket.start] = total
        total_generation += total
        evacuation_excess += max(total - config.capacities.evacuation_mwh, 0.0)
    return {
        "generation_by_bucket": generation_by_bucket,
        "total_generation_mwh": total_generation,
        "evacuation_excess_mwh": evacuation_excess,
    }


def _peak_obligation_metrics(
    config: AppConfig,
    active_inputs: ActiveInputData,
    buckets: list[TimeBucket],
    generation: dict[str, Any],
) -> dict[str, float | int]:
    peak_schedule = _aggregate_peak_schedule_fast(config, active_inputs.peak_rows, buckets)
    peak_intervals = 0
    peak_possible = 0.0
    for bucket in buckets:
        if not bool(peak_schedule.get(bucket.start, bucket.is_peak)):
            continue
        peak_intervals += 1
        generation_mwh = float(generation["generation_by_bucket"].get(bucket.start, 0.0))
        peak_possible += min(generation_mwh, config.capacities.peak_power_mwh)
    return {
        "peak_intervals": peak_intervals,
        "peak_possible_mwh": peak_possible,
        "peak_obligation_mwh": peak_intervals * config.capacities.peak_power_mwh,
    }


def _aggregate_generation_fast(
    config: AppConfig,
    rows: list[dict[str, object]],
    buckets: list[TimeBucket],
) -> dict[datetime, float]:
    interval = parse_interval(config.market_model.interval)
    bucket_starts = {bucket.start for bucket in buckets}
    result = {bucket.start: 0.0 for bucket in buckets}
    source_hours = _infer_source_hours(rows)
    for row in rows:
        timestamp = row.get("timestamp")
        if not isinstance(timestamp, datetime):
            continue
        bucket_start = floor_datetime(timestamp, interval)
        if bucket_start not in bucket_starts:
            continue
        if "mwh" in row:
            mwh = float(row["mwh"])
        else:
            mwh = float(row.get("kw") or 0.0) * source_hours / 1000.0
        result[bucket_start] += max(mwh, 0.0)
    return result


def _aggregate_peak_schedule_fast(
    config: AppConfig,
    rows: list[dict[str, object]],
    buckets: list[TimeBucket],
) -> dict[datetime, bool]:
    result = {bucket.start: bucket.is_peak for bucket in buckets}
    if not rows:
        return result
    interval = parse_interval(config.market_model.interval)
    bucket_starts = set(result)
    for row in rows:
        timestamp = row.get("timestamp")
        if not isinstance(timestamp, datetime):
            continue
        bucket_start = floor_datetime(timestamp, interval)
        if bucket_start in bucket_starts:
            result[bucket_start] = bool(row.get("is_peak"))
    return result


def _month_bounds(value: datetime) -> tuple[datetime, datetime]:
    start = datetime(value.year, value.month, 1)
    if value.month == 12:
        end = datetime(value.year + 1, 1, 1)
    else:
        end = datetime(value.year, value.month + 1, 1)
    return start, end


def _pct(numerator: float, denominator: float) -> float:
    if denominator <= 0.0:
        return 0.0
    return max(numerator, 0.0) / denominator * 100.0


def _infer_source_hours(rows: list[dict[str, object]]) -> float:
    timestamps = sorted(row["timestamp"] for row in rows if isinstance(row.get("timestamp"), datetime))
    if len(timestamps) < 2:
        return 1.0
    deltas = [
        (timestamps[index + 1] - timestamps[index]).total_seconds() / 3600.0
        for index in range(len(timestamps) - 1)
        if (timestamps[index + 1] - timestamps[index]).total_seconds() > 0
    ]
    return max(min(deltas), 1.0 / 60.0) if deltas else 1.0
