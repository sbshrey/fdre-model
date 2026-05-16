from __future__ import annotations

from datetime import datetime

from fdre_model.config import AppConfig, CapacitySettings
from fdre_model.market.engine import ActiveInputData, build_decisions
from fdre_model.market.metrics import workbook_metric_summary
from fdre_model.market.rules import DEFAULT_RULES


def test_workbook_metrics_calculate_compliance_forecast_and_cuf_values() -> None:
    config = AppConfig(
        capacities=CapacitySettings(
            wind_mwh=300.0,
            solar_mwh=300.0,
            bess_capacity_mwh=100.0,
            bess_charge_limit_mwh=50.0,
            bess_discharge_limit_mwh=50.0,
            ppa_mwh=150.0,
            merchant_mwh=35.0,
            evacuation_mwh=185.0,
            peak_power_mwh=100.0,
        )
    )
    now = datetime(2026, 1, 1, 18, 0)
    inputs = ActiveInputData(
        solar_rows=[
            {"timestamp": datetime(2026, 1, 1, 18, 0), "mwh": 100.0},
            {"timestamp": datetime(2026, 1, 1, 19, 0), "mwh": 90.0},
            {"timestamp": datetime(2026, 1, 1, 20, 0), "mwh": 90.0},
            {"timestamp": datetime(2026, 1, 1, 21, 0), "mwh": 110.0},
        ],
        wind_rows=[
            {"timestamp": datetime(2026, 1, 1, 18, 0), "mwh": 120.0},
            {"timestamp": datetime(2026, 1, 1, 19, 0), "mwh": 110.0},
            {"timestamp": datetime(2026, 1, 1, 20, 0), "mwh": 80.0},
            {"timestamp": datetime(2026, 1, 1, 21, 0), "mwh": 100.0},
        ],
        bess_rows=[],
        price_rows=[],
        peak_rows=[],
        version_ids={"solar": "solar-v1", "wind": "wind-v1"},
    )

    decisions, buckets = build_decisions(config, inputs, list(DEFAULT_RULES), now=now)
    summary = workbook_metric_summary(config, inputs, decisions, buckets, now=now)

    assert summary["p2_live_curtailment_mwh"] == 35.0
    assert summary["p1_forecast_22h_curtailment_mwh"] == 40.0
    assert summary["monthly_peak_obligation_mwh"] == 12400.0
    assert summary["monthly_peak_90_obligation_mwh"] == 11160.0
    assert summary["monthly_peak_possible_mwh"] == 400.0
    assert summary["p3_monthly_peak_90_deficit_mwh"] == 10760.0
    assert summary["monthly_peak_90_compliance_pct"] == 3.584229
    assert summary["forecast_30d_peak_possible_mwh"] == 400.0
    assert summary["annual_generation_mwh"] == 800.0
    assert summary["p4_annual_ppa_generation_5pct_mwh"] == 30.0
    assert summary["p5_365d_generation_mwh"] == 800.0
    assert summary["annual_cuf_pct"] == 0.015221
    assert summary["metrics_live_interval"] == "2026-01-01 18:00:00"


def test_workbook_metrics_aggregate_subhourly_generation_inputs() -> None:
    config = AppConfig(capacities=CapacitySettings(evacuation_mwh=185.0))
    now = datetime(2026, 1, 1, 18, 0)
    inputs = ActiveInputData(
        solar_rows=[
            {"timestamp": datetime(2026, 1, 1, 18, 0), "mwh": 25.0},
            {"timestamp": datetime(2026, 1, 1, 18, 15), "mwh": 25.0},
            {"timestamp": datetime(2026, 1, 1, 18, 30), "mwh": 25.0},
            {"timestamp": datetime(2026, 1, 1, 18, 45), "mwh": 25.0},
        ],
        wind_rows=[
            {"timestamp": datetime(2026, 1, 1, 18, 0), "mwh": 30.0},
            {"timestamp": datetime(2026, 1, 1, 18, 15), "mwh": 30.0},
            {"timestamp": datetime(2026, 1, 1, 18, 30), "mwh": 30.0},
            {"timestamp": datetime(2026, 1, 1, 18, 45), "mwh": 30.0},
        ],
        bess_rows=[],
        price_rows=[],
        peak_rows=[],
        version_ids={},
    )

    decisions, buckets = build_decisions(config, inputs, list(DEFAULT_RULES), now=now)
    summary = workbook_metric_summary(config, inputs, decisions, buckets, now=now)

    assert summary["p2_live_curtailment_mwh"] == 35.0
