from __future__ import annotations

from datetime import datetime

from fdre_model.config import AppConfig, CapacitySettings, StateSettings, TariffSettings
from fdre_model.market.engine import ActiveInputData, build_decisions
from fdre_model.market.models import RuleDefinition
from fdre_model.market.rules import DEFAULT_RULES


def test_rule_priority_changes_market_allocation() -> None:
    config = AppConfig()
    inputs = ActiveInputData(
        solar_rows=[{"timestamp": datetime(2026, 4, 1, 12, 0), "mwh": 120.0}],
        wind_rows=[],
        bess_rows=[],
        price_rows=[{"timestamp": datetime(2026, 4, 1, 12, 0), "price": 20.0}],
        peak_rows=[{"timestamp": datetime(2026, 4, 1, 12, 0), "is_peak": False}],
        version_ids={},
    )

    merchant_first = []
    for rule in DEFAULT_RULES:
        priority = 5 if rule.rule_id == "merchant_sale" else rule.priority
        merchant_first.append(
            RuleDefinition(rule.rule_id, rule.name, priority, rule.enabled, rule.description)
        )
    decisions, _ = build_decisions(config, inputs, merchant_first, now=datetime(2026, 4, 1, 12, 0))
    current = next(item for item in decisions if item.interval_start == datetime(2026, 4, 1, 12, 0))

    assert current.merchant_sale_mwh == 35.0
    assert current.ppa_sale_mwh == 85.0
    assert current.applied_rule_ids[0] == "merchant_sale"


def test_peak_rule_uses_bess_and_records_shortfall() -> None:
    config = AppConfig()
    inputs = ActiveInputData(
        solar_rows=[{"timestamp": datetime(2026, 4, 1, 18, 0), "mwh": 80.0}],
        wind_rows=[],
        bess_rows=[{"timestamp": datetime(2026, 4, 1, 18, 0), "soc_mwh": 10.0, "soh_fraction": 1.0}],
        price_rows=[],
        peak_rows=[{"timestamp": datetime(2026, 4, 1, 18, 0), "is_peak": True}],
        version_ids={},
    )

    decisions, _ = build_decisions(config, inputs, list(DEFAULT_RULES), now=datetime(2026, 4, 1, 18, 0))
    current = next(item for item in decisions if item.interval_start == datetime(2026, 4, 1, 18, 0))

    assert current.peak_power_sale_mwh > 80.0
    assert current.bess_discharge_mwh > 0.0
    assert current.shortfall_mwh > 0.0
    assert "peak_power_obligation" in current.applied_rule_ids


def test_configurable_action_type_drives_allocation_independent_of_rule_id() -> None:
    config = AppConfig()
    inputs = ActiveInputData(
        solar_rows=[{"timestamp": datetime(2026, 4, 1, 12, 0), "mwh": 90.0}],
        wind_rows=[],
        bess_rows=[],
        price_rows=[{"timestamp": datetime(2026, 4, 1, 12, 0), "price": 18.0}],
        peak_rows=[{"timestamp": datetime(2026, 4, 1, 12, 0), "is_peak": False}],
        version_ids={},
    )
    rules = [
        RuleDefinition(
            "custom_merchant_first",
            "Custom Merchant First",
            5,
            True,
            "Use merchant action from JSON config.",
            condition={"is_peak": False, "min_residual_mwh": 0.000001},
            action={"type": "sell_merchant"},
        )
    ]

    decisions, _ = build_decisions(config, inputs, rules, now=datetime(2026, 4, 1, 12, 0))
    current = next(item for item in decisions if item.interval_start == datetime(2026, 4, 1, 12, 0))

    assert current.merchant_sale_mwh == 35.0
    assert current.applied_rule_ids == ["custom_merchant_first"]


def test_conditions_skip_rules_without_allocation() -> None:
    config = AppConfig()
    inputs = ActiveInputData(
        solar_rows=[{"timestamp": datetime(2026, 4, 1, 12, 0), "mwh": 90.0}],
        wind_rows=[],
        bess_rows=[],
        price_rows=[],
        peak_rows=[{"timestamp": datetime(2026, 4, 1, 12, 0), "is_peak": False}],
        version_ids={},
    )
    rules = [
        RuleDefinition(
            "peak_only_ppa",
            "Peak Only PPA",
            10,
            True,
            "Only sell during peak.",
            condition={"is_peak": True},
            action={"type": "sell_ppa"},
        )
    ]

    decisions, _ = build_decisions(config, inputs, rules, now=datetime(2026, 4, 1, 12, 0))
    current = next(item for item in decisions if item.interval_start == datetime(2026, 4, 1, 12, 0))

    assert current.ppa_sale_mwh == 0.0
    assert "peak_only_ppa:condition_false" in current.skipped_rule_ids


def test_conflict_trace_names_higher_priority_residual_allocator() -> None:
    config = AppConfig()
    inputs = ActiveInputData(
        solar_rows=[{"timestamp": datetime(2026, 4, 1, 12, 0), "mwh": 100.0}],
        wind_rows=[],
        bess_rows=[],
        price_rows=[],
        peak_rows=[{"timestamp": datetime(2026, 4, 1, 12, 0), "is_peak": False}],
        version_ids={},
    )

    decisions, _ = build_decisions(config, inputs, list(DEFAULT_RULES), now=datetime(2026, 4, 1, 12, 0))
    current = next(item for item in decisions if item.interval_start == datetime(2026, 4, 1, 12, 0))

    assert current.bess_charge_mwh == 50.0
    assert current.merchant_sale_mwh == 35.0
    assert current.ppa_sale_mwh == 15.0
    assert current.applied_rule_ids == ["non_peak_workbook_dispatch"]
    assert "ppa_sale:conflict:residual_allocated_by=non_peak_workbook_dispatch" in current.skipped_rule_ids
    assert any("case3_charge_bess_first" in item for item in current.audit_trace)


def test_non_peak_case2_sells_before_charging_when_future_curtailment_covers_bess() -> None:
    config = AppConfig(
        capacities=CapacitySettings(
            wind_mwh=300.0,
            solar_mwh=300.0,
            bess_capacity_mwh=100.0,
            bess_charge_limit_mwh=50.0,
            bess_discharge_limit_mwh=50.0,
            ppa_mwh=60.0,
            merchant_mwh=20.0,
            evacuation_mwh=185.0,
            peak_power_mwh=150.0,
        ),
        tariffs=TariffSettings(ppa=6.0, merchant_sell_default=3.0, peak_power=7.0, penalty_multiplier=1.5),
        state=StateSettings(initial_bess_soc_mwh=50.0, initial_bess_soh_fraction=1.0),
    )
    inputs = ActiveInputData(
        solar_rows=[
            {"timestamp": datetime(2026, 4, 1, 12, 0), "mwh": 100.0},
            {"timestamp": datetime(2026, 4, 1, 13, 0), "mwh": 300.0},
        ],
        wind_rows=[{"timestamp": datetime(2026, 4, 1, 13, 0), "mwh": 300.0}],
        bess_rows=[],
        price_rows=[{"timestamp": datetime(2026, 4, 1, 12, 0), "price": 3.0}],
        peak_rows=[],
        version_ids={},
    )

    decisions, _ = build_decisions(config, inputs, list(DEFAULT_RULES), now=datetime(2026, 4, 1, 12, 0))
    current = next(item for item in decisions if item.interval_start == datetime(2026, 4, 1, 12, 0))

    assert current.ppa_sale_mwh == 60.0
    assert current.merchant_sale_mwh == 20.0
    assert current.bess_charge_mwh == 20.0
    assert any("case2_forecast_curtailment_covers_bess" in item for item in current.audit_trace)
    assert any("tariff_order=ppa>merchant" in item for item in current.audit_trace)


def test_non_peak_cases4_and5_choose_sale_order_from_t1_vs_t2() -> None:
    config = AppConfig(
        capacities=CapacitySettings(
            ppa_mwh=60.0,
            merchant_mwh=35.0,
            bess_capacity_mwh=100.0,
            bess_charge_limit_mwh=50.0,
            bess_discharge_limit_mwh=50.0,
        ),
        tariffs=TariffSettings(ppa=6.0, merchant_sell_default=10.0, peak_power=7.0, penalty_multiplier=1.5),
        state=StateSettings(initial_bess_soc_mwh=100.0, initial_bess_soh_fraction=1.0),
    )
    inputs = ActiveInputData(
        solar_rows=[{"timestamp": datetime(2026, 4, 1, 12, 0), "mwh": 80.0}],
        wind_rows=[],
        bess_rows=[{"timestamp": datetime(2026, 4, 1, 12, 0), "soc_mwh": 100.0, "soh_fraction": 1.0}],
        price_rows=[{"timestamp": datetime(2026, 4, 1, 12, 0), "price": 10.0}],
        peak_rows=[{"timestamp": datetime(2026, 4, 1, 12, 0), "is_peak": False}],
        version_ids={},
    )

    decisions, _ = build_decisions(config, inputs, list(DEFAULT_RULES), now=datetime(2026, 4, 1, 12, 0))
    current = next(item for item in decisions if item.interval_start == datetime(2026, 4, 1, 12, 0))

    assert current.merchant_sale_mwh == 35.0
    assert current.ppa_sale_mwh == 45.0
    assert current.bess_charge_mwh == 0.0
    assert any("case7_sufficient_bess" in item for item in current.audit_trace)
    assert any("tariff_order=merchant>ppa" in item for item in current.audit_trace)


def test_future_rule_pack_placeholders_are_available_but_disabled() -> None:
    future_rule_ids = {
        "annual_cuf_monitor",
        "monthly_compliance_monitor",
        "merchant_buy_shortfall",
        "penalty_procurement_monitor",
        "forecast_peak_charge",
    }
    defaults = {rule.rule_id: rule for rule in DEFAULT_RULES}

    assert future_rule_ids.issubset(defaults)
    assert all(not defaults[rule_id].enabled for rule_id in future_rule_ids)
    assert all(defaults[rule_id].action["type"] == "monitor" for rule_id in future_rule_ids)
